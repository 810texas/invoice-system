"""云 LLM 接入冒烟：用配置好的 provider 发一次**真实**请求，确认 Key、接入点、模型都对。

这是「填完 LLM_API_KEY 之后第一件该跑的事」—— 不用起后端、不用开浏览器、不碰数据库，
直接验证 .env 里的 provider 配置能不能通。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_llm_provider.py

注意：LLM_PROVIDER != mock 时，本脚本会真的调用云端接口（按量计费，一次调用约几百 token）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.core.config import get_settings  # noqa: E402
from app.services.agent.service import normalize_payload, rule_conclusion  # noqa: E402
from app.services.llm.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from app.services.llm.provider import get_provider  # noqa: E402

settings = get_settings()

# 合成事实块：不依赖数据库，字段与 services/agent/facts.py 的产物保持一致
SAMPLE_FACTS = {
    "reimbursement": {
        "reason": "客户拜访交通费",
        "total_amount": 888.88,
        "invoice_count": 2,
        "version": 1,
        "previously_rejected": False,
        "previous_reject_reason": "",
        "risk_level": "LOW",
        "preaudit": {"hard_blocks": [], "warnings": [], "note": "冒烟用例：无预警"},
    },
    "department": {"name": "研发部", "single_invoice_limit": 5000.0, "monthly_limit": 50000.0},
    "invoices": [
        {
            "index": 1,
            "invoice_type": "数电票",
            "invoice_date": "2026-09-02",
            "seller_name": "销售方A",
            "category": "",
            "amount_excl_tax": 42.20,
            "tax_amount": 0.42,
            "total_amount": 42.62,
            "ocr_confidence": 0.97,
            "need_confirm": False,
        },
        {
            "index": 2,
            "invoice_type": "蓝字",
            "invoice_date": "2026-09-20",
            "seller_name": "销售方B",
            "category": "住宿",
            "amount_excl_tax": 800.00,
            "tax_amount": 48.00,
            "total_amount": 848.00,
            "ocr_confidence": 0.93,
            "need_confirm": False,
        },
    ],
}


def mask_key(key: str) -> str:
    if not key:
        return "(未配置)"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}…{key[-4:]}（长度 {len(key)}）"


def hint_for(err: str) -> str:
    low = err.lower()
    if "llm_api_key" in low:
        return "→ 请在 backend/.env 里填 LLM_API_KEY，然后重跑本脚本（改完 .env 必须重启后端才影响服务）"
    if "401" in low or "unauthorized" in low or "authentication" in low or "invalid api key" in low:
        return "→ Key 无效或与 LLM_PROVIDER 不匹配（例如拿 qwen 的 Key 去调 deepseek）"
    if "402" in low or "insufficient" in low or "balance" in low or "quota" in low:
        return "→ 账户余额/配额不足，去对应平台充值"
    if "429" in low:
        return "→ 触发限流，稍后重试"
    if "timeout" in low or "timed out" in low:
        return "→ 请求超时：检查网络，或调大 .env 里的 LLM_TIMEOUT_SECONDS"
    if "connect" in low or "getaddrinfo" in low or "ssl" in low or "proxy" in low:
        return "→ 连不上接入点：检查网络/代理，或把 LLM_BASE_URL 改成可用的中转地址"
    if "json" in low:
        return "→ 模型没按 JSON 返回（已自动重试过）：可调大 LLM_MAX_TOKENS 或换模型"
    return ""


def main() -> int:
    provider = get_provider()
    model = getattr(provider, "model", "(mock 无模型)")
    base_url = getattr(provider, "base_url", "(mock 不联网)")

    print("===== 当前 LLM 配置 =====")
    print(f"  LLM_PROVIDER      : {settings.LLM_PROVIDER}")
    print(f"  实际使用的 provider: {provider.name}")
    print(f"  模型              : {model}")
    print(f"  接入点            : {base_url}")
    print(f"  API Key           : {mask_key(settings.LLM_API_KEY)}")
    print(f"  超时 / 最大 token  : {settings.LLM_TIMEOUT_SECONDS}s / {settings.LLM_MAX_TOKENS}")
    print(f"  JSON 重试次数      : {settings.LLM_JSON_RETRY}")
    print(f"  销售方脱敏         : {settings.LLM_MASK_PII}")

    if provider.name == "mock":
        print("\n提示：当前是 mock provider —— 确定性输出、不联网，本脚本不会产生任何费用。")
        print("     要验证真实云 API，请把 .env 的 LLM_PROVIDER 改成 deepseek/qwen/glm/openai 并填 LLM_API_KEY。")
    elif not settings.LLM_API_KEY:
        # 没填 Key 时下面会直接报错返回，没必要先提示「即将产生费用」
        print("\n提示：LLM_API_KEY 还没填，本次不会真正发出请求。")
    else:
        print("\n注意：即将向云端发起 1 次真实请求（按量计费，约几百 token）。")

    prompt = build_user_prompt(SAMPLE_FACTS)
    print("\n===== 发起请求 =====")
    started = time.time()
    try:
        result = provider.advise(SYSTEM_PROMPT, prompt)
    except Exception as exc:
        elapsed = int((time.time() - started) * 1000)
        print(f"[失败] {elapsed}ms | {type(exc).__name__}: {exc}")
        tip = hint_for(str(exc))
        if tip:
            print(f"  {tip}")
        return 1

    elapsed = int((time.time() - started) * 1000)
    normalized, hints = normalize_payload(result.payload, SAMPLE_FACTS, [])

    print(f"[成功] {elapsed}ms | provider={result.provider} | model={result.model}")
    print("\n----- 模型原始输出（截断） -----")
    print((result.raw or "")[:600])
    print("\n----- 校验后的建议（这才是前端会展示的形态） -----")
    print(json.dumps(normalized, ensure_ascii=False, indent=2))
    print(f"\n规则推导的结论（用于对照）: {rule_conclusion(SAMPLE_FACTS)}")
    print(f"类别建议条数: {len(hints)}（本用例第 1 张未填类别，模型应给出建议或说明无法判断）")

    print(f"\n===== 结果：链路可用 =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
