"""LLM provider 抽象与注册：mock（默认，确定性）/ deepseek / qwen / glm / openai。

设计要点（与 services/ocr/provider.py 保持同一套工程惯例）：

- **provider 懒加载**：``_REGISTRY`` 在 import 时实例化 provider，所以构造函数里不能建连接、
  也不能加载重依赖；云端 HTTP 客户端在首次调用时才创建。
- **mock 必须确定性**：按事实块内容哈希派生结论，保证 e2e_check / check_agent 不依赖网络与密钥。
- **只做建议层**：本层的输出永远不参与硬拦截判定（硬拦截只由 preaudit 规则 + 数据库唯一约束决定）。
- **模型不产出业务数字**：事实块里给出权威金额，模型只负责解读；发票引用用「序号 index」，
  由后端按序号还原成真实发票，避免模型编造发票号。
- 各家都走 OpenAI 兼容协议，所以共用 ``OpenAiCompatLlmProvider``，只有默认接入点与模型不同。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import get_settings

settings = get_settings()

# 结论与置信度的取值域：模型输出必须落在这里，否则整条建议作废（走降级）
CONCLUSIONS = ("APPROVE", "REJECT", "NEED_MORE_INFO")
CONFIDENCES = ("high", "mid", "low")

# 云 provider 的默认接入点与模型。都是 OpenAI 兼容协议，所以共用一套客户端。
# 模型名以各家当前文档为准（例如 DeepSeek 现用 deepseek-flash，旧的 deepseek-chat 已不在文档里）。
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "deepseek": {
        # 官方文档的 curl 示例就是 https://api.deepseek.com/chat/completions（不带 /v1）
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}


@dataclass
class LlmResult:
    """一次建议生成的结果。``payload`` 为校验通过的结构化建议，``raw`` 为原始文本。"""

    payload: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    provider: str = "mock"
    model: str = ""
    elapsed_ms: int = 0


class LlmProvider(Protocol):
    name: str

    def advise(self, system_prompt: str, user_prompt: str) -> LlmResult: ...


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型回复里取 JSON 对象。

    容忍常见的 ```json 代码块与前后多余说明文字；取不到合法对象就返回 None（由调用方降级）。
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        # 去掉首行 ```json 与结尾 ```
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    # 兜底：截取第一个 { 到最后一个 } 之间的内容再试一次
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(candidate[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class MockLlmProvider:
    """开发/验收用 provider：按事实块内容确定性地产出建议，无需网络与密钥。

    产出的结论是「规则摘要的再表述」，不是真实模型输出 —— 目的是让整条链路
    （取数 → 提示词 → 输出校验 → 留痕 → 前端展示）可以在没有密钥的情况下被完整验收。
    """

    name = "mock"

    def advise(self, system_prompt: str, user_prompt: str) -> LlmResult:
        facts = _extract_json(user_prompt) or {}
        seed = int(
            hashlib.sha256(
                json.dumps(facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:12],
            16,
        )

        # 注意：事实块是嵌套结构 —— reason 与 preaudit 都在 reimbursement 下面，
        # 只有 invoices 在顶层。读错层级会让预审预警「消失」（曾经踩过）。
        reimb = facts.get("reimbursement") or {}
        preaudit = reimb.get("preaudit") or {}
        invoices = facts.get("invoices") or []
        hard_blocks = preaudit.get("hard_blocks") or []
        warnings = preaudit.get("warnings") or []
        threshold = settings.OCR_CONFIDENCE_THRESHOLD
        missing_category = [
            i + 1 for i, inv in enumerate(invoices) if not inv.get("category")
        ]
        low_conf = [
            i + 1
            for i, inv in enumerate(invoices)
            if inv.get("need_confirm") or (inv.get("ocr_confidence") or 0) < threshold
        ]

        reasons: list[str] = []
        risk_points: list[str] = []
        missing: list[str] = []

        if hard_blocks:
            conclusion = "REJECT"
            confidence = "high"
            reasons.append("存在硬拦截项：建议退回申请人修正后重新提交。")
            risk_points.extend(str(x) for x in hard_blocks[:5])
        elif warnings or low_conf:
            conclusion = "NEED_MORE_INFO"
            confidence = "mid" if warnings else "low"
            reasons.append("单据本身可提交，但存在需要人工确认的项。")
            if warnings:
                risk_points.extend(str(x) for x in warnings[:5])
            if low_conf:
                risk_points.append(
                    f"第 {'、'.join(str(i) for i in low_conf[:5])} 张发票的识别置信度偏低。"
                )
        else:
            conclusion = "APPROVE"
            confidence = "high" if len(invoices) else "low"
            reasons.append("未命中预审规则，金额勾稽一致，未发现明显异常。")
            if not invoices:
                missing.append("本单没有发票明细。")

        if missing_category:
            missing.append(
                f"第 {'、'.join(str(i) for i in missing_category[:5])} 张发票未填费用类别。"
            )

        reason_text = (reimb.get("reason") or "").strip()
        if not reason_text:
            missing.append("报销事由为空，无法判断支出用途与票据内容是否一致。")
        elif invoices and not hard_blocks:
            reasons.append("报销事由与票据的支出用途未见明显冲突（mock 不做事由语义比对）。")

        if seed % 2 == 0 and conclusion == "APPROVE":
            risk_points.append("建议抽查一张发票的原件核对（mock 提示，非真实风险判断）。")

        payload = {
            "conclusion": conclusion,
            "confidence": confidence,
            "summary": {
                "APPROVE": "未发现明显异常，可正常审核。",
                "REJECT": "存在硬拦截项，建议驳回并要求补正。",
                "NEED_MORE_INFO": "需要人工确认若干项后再决定。",
            }[conclusion],
            "reasons": reasons[:5],
            "risk_points": risk_points[:5],
            "missing": missing[:5],
            "category_hints": [
                {"index": i, "suggested_category": "", "why": "mock provider 不提供类别建议"}
                for i in missing_category[:3]
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        return LlmResult(payload=payload, raw=raw, provider=self.name, model="mock")


class OpenAiCompatLlmProvider:
    """通用 OpenAI 兼容客户端：qwen（DashScope 兼容模式）/ glm（智谱）/ openai 都走这里。

    客户端懒建（见模块 docstring），且**同步**调用 —— 端点跑在线程池里，
    与现有 OCR 同步端点的处理方式一致。异常一律向上抛，由 agent 层降级。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        defaults = PROVIDER_DEFAULTS.get(name, PROVIDER_DEFAULTS["openai"])
        self.model = settings.LLM_MODEL or defaults["model"]
        self.base_url = (settings.LLM_BASE_URL or defaults["base_url"]).rstrip("/")
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确指引
                raise RuntimeError(
                    "未安装 httpx。请在 backend 目录执行 "
                    "`.venv\\Scripts\\python.exe -m pip install -r requirements.txt`"
                ) from exc
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                headers={
                    "Authorization": f"Bearer {settings.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def advise(self, system_prompt: str, user_prompt: str) -> LlmResult:
        if not settings.LLM_API_KEY:
            raise RuntimeError(
                f"LLM_PROVIDER={self.name} 需要 LLM_API_KEY，请在 backend/.env 中配置后重启后端"
            )

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": settings.LLM_MAX_TOKENS,
            # 要求返回 JSON 对象；不是所有兼容实现都支持，失败会走上面的降级路径
            "response_format": {"type": "json_object"},
        }

        # JSON Output 模式在部分实现下**有概率返回空 content**（DeepSeek 官方文档明确提到），
        # 所以对「拿不到可解析 JSON」重试有限次。网络错误 / 4xx / 5xx 不重试（重试没意义）。
        attempts = max(1, settings.LLM_JSON_RETRY + 1)
        last_error: Exception | None = None
        text = ""
        for attempt in range(attempts):
            resp = self._get_client().post("/chat/completions", json=body)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"LLM 接口返回 {resp.status_code}：{resp.text[:200]}"
                )
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(
                    f"LLM 返回结构不符合 OpenAI 兼容格式：{str(data)[:200]}"
                ) from exc

            payload = _extract_json(text)
            if payload is not None:
                return LlmResult(
                    payload=payload, raw=text, provider=self.name, model=self.model
                )

            last_error = RuntimeError(
                "LLM 未返回可解析的 JSON 建议"
                + ("（返回内容为空）" if not (text or "").strip() else "")
            )

        raise last_error or RuntimeError("LLM 未返回可解析的 JSON 建议")


_REGISTRY: dict[str, LlmProvider] = {
    "mock": MockLlmProvider(),
    "deepseek": OpenAiCompatLlmProvider("deepseek"),
    "qwen": OpenAiCompatLlmProvider("qwen"),
    "glm": OpenAiCompatLlmProvider("glm"),
    "openai": OpenAiCompatLlmProvider("openai"),
}


def get_provider(name: str | None = None) -> LlmProvider:
    key = (name or settings.LLM_PROVIDER or "mock").strip().lower()
    provider = _REGISTRY.get(key)
    if provider is None:
        raise RuntimeError(
            f"未知的 LLM_PROVIDER：{key}（可选 {'/'.join(_REGISTRY)}）"
        )
    return provider


def provider_label() -> str:
    """给前端展示的 provider 描述，例如 mock / qwen:qwen-plus。"""
    provider = get_provider()
    model = getattr(provider, "model", "")
    return f"{provider.name}:{model}" if model else provider.name
