"""审核助手编排：取数 → 组装提示词 → 调 LLM → 校验结构化输出 → 留痕。

**三条不可违背的设计约束**（都来自本项目的既有设计）：

1. **绝不进入硬拦截路径**：本模块的返回值只用于展示，不参与 `blocked` 判定。
   硬拦截仍由 `services/preaudit.py` 的规则 + `invoice.occupy_key` 唯一约束决定。
2. **LLM 失败不能让审核页崩**：任何异常都被吞掉并降级为「规则摘要」，
   `degraded=True`，前端会明确标注「AI 不可用，以下是规则摘要」。
3. **业务数字只来自数据库**：模型用「序号」引用发票，序号由后端还原成真实发票号；
   模型文案里的数字一律不作为权威展示（界面上的金额始终取库里的值）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai import AiSuggestion
from app.models.document import EXPENSE_CATEGORIES, Invoice, Reimbursement
from app.services.agent.facts import collect_facts, unmask_text
from app.services.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from app.services.llm.provider import (
    CONCLUSIONS,
    CONFIDENCES,
    get_provider,
)

settings = get_settings()

PROMPT_VERSION = "v1"

MAX_ITEMS = 5
MAX_ITEM_CHARS = 120


@dataclass
class Advice:
    """对外暴露的建议结果（同时也是写库前的载体）。"""

    payload: dict[str, Any] = field(default_factory=dict)
    provider: str = "mock"
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    elapsed_ms: int = 0
    degraded: bool = False
    error: str = ""
    # 模型原始输出（留痕用，排查「它当时到底回了什么」）
    raw: str = ""
    suggestion_id: int | None = None
    created_at: datetime | None = None
    masked: bool = False
    unmask_applied: bool = False


def _clip(value: Any, limit: int = MAX_ITEM_CHARS) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())  # 折叠换行与多余空白，避免写进 JSON 后带出一堆空行
    return text[:limit]


def _str_list(value: Any) -> list[str]:
    """把模型给的任意形态收敛成「最多 5 条、每条不超过 120 字」的字符串列表。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clip(item)
        if text:
            out.append(text)
        if len(out) >= MAX_ITEMS:
            break
    return out


def rule_conclusion(facts: dict[str, Any]) -> str:
    """纯规则推导的结论，用作降级结果与非法输出的兜底。"""
    pre = facts["reimbursement"]["preaudit"]
    if pre["hard_blocks"]:
        return "REJECT"
    low_conf = any(
        inv.get("need_confirm") or (inv.get("ocr_confidence") or 0) < settings.OCR_CONFIDENCE_THRESHOLD
        for inv in facts["invoices"]
    )
    if pre["warnings"] or low_conf:
        return "NEED_MORE_INFO"
    return "APPROVE"


def build_rule_summary(facts: dict[str, Any]) -> dict[str, Any]:
    """降级路径：不调模型，直接用事实块拼一份规则摘要。

    这份摘要由后端自己生成，所以可以安全地带上权威数字。
    """
    rb = facts["reimbursement"]
    pre = rb["preaudit"]
    invoices = facts["invoices"]
    conclusion = rule_conclusion(facts)

    reasons: list[str] = []
    risk_points: list[str] = [*pre["hard_blocks"], *pre["warnings"]]
    missing: list[str] = []

    if pre["hard_blocks"]:
        reasons.append("命中硬性校验项，建议要求申请人补正后重新提交。")
    elif pre["warnings"]:
        reasons.append("未命中硬拦截，但存在预审预警项，需要人工确认。")
    else:
        reasons.append("未命中预审规则，金额勾稽一致。")

    low_conf = [
        inv["index"]
        for inv in invoices
        if inv.get("need_confirm")
        or (inv.get("ocr_confidence") or 0) < settings.OCR_CONFIDENCE_THRESHOLD
    ]
    if low_conf:
        risk_points.append(
            f"第 {'、'.join(str(i) for i in low_conf)} 张发票识别置信度低于阈值，建议核对原件。"
        )

    no_category = [inv["index"] for inv in invoices if not inv.get("category")]
    if no_category:
        missing.append(f"第 {'、'.join(str(i) for i in no_category)} 张发票未填费用类别。")

    if not (rb.get("reason") or "").strip():
        missing.append("报销事由为空，建议要求申请人补充支出用途说明。")

    return {
        "conclusion": conclusion,
        "confidence": "mid" if conclusion == "NEED_MORE_INFO" else "high",
        "summary": "（规则摘要）" + {
            "APPROVE": "未发现明显异常。",
            "REJECT": "存在硬性问题，建议驳回补正。",
            "NEED_MORE_INFO": "需要人工确认若干项。",
        }[conclusion],
        "reasons": reasons,
        "risk_points": risk_points,
        "missing": missing,
        "category_hints": [],
    }


def normalize_payload(
    payload: dict[str, Any], facts: dict[str, Any], invoices: list[Invoice]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """校验并收敛模型输出。

    返回 (规范化后的建议, 解析后的类别建议)。任何越界取值都被拉回合法域，
    非法但可修复的字段用规则推导值兜底 —— 目的是「宁可少说，不可乱说」。
    """
    fallback = rule_conclusion(facts)

    conclusion = str(payload.get("conclusion") or "").strip().upper()
    if conclusion not in CONCLUSIONS:
        conclusion = fallback

    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCES:
        confidence = "low"

    hints_raw = payload.get("category_hints")
    hints: list[dict[str, Any]] = []
    if isinstance(hints_raw, list):
        for item in hints_raw:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if not (1 <= index <= len(invoices)):
                continue  # 序号越界：模型在指一张不存在的发票，直接丢弃
            suggested = str(item.get("suggested_category") or "").strip()
            if suggested and suggested not in EXPENSE_CATEGORIES:
                suggested = ""  # 不在字典里的类别不采纳（避免自由发挥出「差旅」这类非标准值）
            inv = invoices[index - 1]
            hints.append(
                {
                    "index": index,
                    # 下面是后端从数据库补的真实标识，不是模型给的
                    "invoice_id": inv.id,
                    "invoice_no": inv.invoice_no,
                    "seller_name": inv.seller_name,
                    "current_category": inv.category or "",
                    "suggested_category": suggested,
                    "why": _clip(item.get("why"), 80),
                }
            )
            if len(hints) >= MAX_ITEMS:
                break

    normalized = {
        "conclusion": conclusion,
        "confidence": confidence,
        "summary": _clip(payload.get("summary"), 100),
        "reasons": _str_list(payload.get("reasons")),
        "risk_points": _str_list(payload.get("risk_points")),
        "missing": _str_list(payload.get("missing")),
        "category_hints": hints,
    }
    if not normalized["summary"]:
        normalized["summary"] = {
            "APPROVE": "未发现明显异常，可正常审核。",
            "REJECT": "存在硬性问题，建议驳回补正。",
            "NEED_MORE_INFO": "需要人工确认若干项后再决定。",
        }[conclusion]
    return normalized, hints


def _apply_unmask(advice_payload: dict[str, Any], unmask: dict[str, str]) -> bool:
    """把输出里的「销售方A」还原成真名（就地修改），返回是否真的替换过。"""
    if not unmask:
        return False
    changed = False
    for key in ("summary",):
        before = advice_payload.get(key) or ""
        after = unmask_text(before, unmask)
        changed = changed or after != before
        advice_payload[key] = after
    for key in ("reasons", "risk_points", "missing"):
        new_list = []
        for item in advice_payload.get(key) or []:
            after = unmask_text(item, unmask)
            changed = changed or after != item
            new_list.append(after)
        advice_payload[key] = new_list
    for hint in advice_payload.get("category_hints") or []:
        before = hint.get("why") or ""
        after = unmask_text(before, unmask)
        changed = changed or after != before
        hint["why"] = after
    return changed


def generate_advice(db: Session, reimb: Reimbursement, requester_id: int) -> Advice:
    """生成一次审核建议并留痕。**任何异常都不会向外抛**。"""
    facts, unmask = collect_facts(db, reimb)
    invoices = list(
        db.execute(
            select(Invoice).where(Invoice.reimbursement_id == reimb.id).order_by(Invoice.id)
        ).scalars().all()
    )
    provider = get_provider()
    advice = Advice(provider=provider.name, masked=bool(unmask))

    try:
        started = datetime.now()
        result = provider.advise(SYSTEM_PROMPT, build_user_prompt(facts))
        advice.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
        advice.model = result.model
        payload, _ = normalize_payload(result.payload, facts, invoices)
        advice.payload = payload
        advice.unmask_applied = _apply_unmask(advice.payload, unmask)
        advice.raw = result.raw
    except Exception as exc:
        advice.degraded = True
        advice.error = str(exc)[:255]
        advice.payload = build_rule_summary(facts)
        _apply_unmask(advice.payload, unmask)
        advice.model = ""
        advice.raw = ""

    # 留痕：无论成功还是降级都写一条，这样「AI 当时说了什么」永远可查。
    # 写库失败（例如还没跑 init_db、ai_suggestion 表不存在）不能让建议本身丢掉 ——
    # 与「LLM 不可用要降级」同一条原则：AI 层的任何问题都不该把审核页搞崩。
    try:
        row = AiSuggestion(
            reimbursement_id=reimb.id,
            requester_id=requester_id,
            provider=advice.provider,
            model=advice.model,
            prompt_version=advice.prompt_version,
            conclusion=advice.payload.get("conclusion", ""),
            confidence=advice.payload.get("confidence", ""),
            payload=json.dumps(advice.payload, ensure_ascii=False),
            raw=advice.raw or "",
            elapsed_ms=advice.elapsed_ms,
            degraded=1 if advice.degraded else 0,
            error=advice.error,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        advice.suggestion_id = row.id
        advice.created_at = row.created_at
        advice.payload["adopted"] = row.adopted
    except Exception as exc:
        db.rollback()
        advice.suggestion_id = None
        note = f"建议未能留痕（{str(exc)[:120]}）；请确认已执行 scripts/init_db.py 建出 ai_suggestion 表"
        advice.error = (advice.error + "；" + note) if advice.error else note
        advice.payload["adopted"] = None
    return advice


# 判断「建议是否被采纳」的映射：AI 说要谨慎（REJECT/NEED_MORE_INFO）而审核人也驳回了，
# 视为采纳 —— 因为它的作用正是让人停下来再看一眼。
_ADOPTED_BY_ACTION = {
    "APPROVE": {"APPROVE"},
    "REJECT": {"REJECT"},
    "NEED_MORE_INFO": {"REJECT"},
}


def mark_adoption(db: Session, reimbursement_id: int, action: str) -> None:
    """审核完成后回填最新一条建议的采纳情况（用于统计 AI 命中率）。"""
    row = db.execute(
        select(AiSuggestion)
        .where(AiSuggestion.reimbursement_id == reimbursement_id)
        .order_by(AiSuggestion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return
    row.adopted = 1 if action in _ADOPTED_BY_ACTION.get(row.conclusion, set()) else 0


def latest_advice(db: Session, reimbursement_id: int) -> AiSuggestion | None:
    return db.execute(
        select(AiSuggestion)
        .where(AiSuggestion.reimbursement_id == reimbursement_id)
        .order_by(AiSuggestion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
