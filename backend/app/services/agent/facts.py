"""把一张报销单组装成「事实块」—— 即真正会发给云端模型的内容。

**数据最小化（本模块的核心职责）**

语义分析用不到的字段一律不外发：

| 字段 | 是否外发 | 原因 |
| :--- | :--- | :--- |
| 发票影像 | ❌ | 需求 5.2 要求影像不出本机；语义分析也读不了图 |
| 购买方名称 / 税号 | ❌ | 抬头核验已由 preaudit 规则确定性地做了，无需模型参与 |
| 发票代码 / 发票号码 | ❌ | 查重已由 dedup_key + 数据库唯一索引负责；外发只会诱使模型编造号码 |
| 报销单号 / 申请人姓名 | ❌ | 与语义判断无关 |
| 销售方名称 | ⚠️ 脱敏 | 默认替换为「销售方A/B…」（`LLM_MASK_PII=true`），返回前端前再还原 |
| 报销事由 | ✅ 原样 | 事由一致性分析必需。**这是本方案残留的隐私风险**，需业务方接受 |
| 金额 / 日期 / 类别 / 置信度 | ✅ | 判断支出合理性的依据 |

**为什么不重跑 run_preaudit**

对已提交（PENDING）的单据，`check_budget` 会把 `used`（已含本单）再加上本单金额，
导致「预计累计」翻倍、预警文案失真。所以这里用**提交时留存的** `risk_detail` 作为预警来源，
只补一个纯函数、不会重复计算的金额勾稽检查。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Invoice, Reimbursement
from app.models.user import Department, User
from app.services import preaudit

settings = get_settings()

# 销售方化名用的后缀，够用即可（超出会回绕到 A..Z 后拼数字）
_MASK_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _mask_label(index: int) -> str:
    letter = _MASK_ALPHABET[index % len(_MASK_ALPHABET)]
    suffix = "" if index < len(_MASK_ALPHABET) else str(index // len(_MASK_ALPHABET))
    return f"销售方{letter}{suffix}"


def collect_facts(db: Session, reimb: Reimbursement) -> tuple[dict[str, Any], dict[str, str]]:
    """返回 (事实块, 脱敏映射)。

    脱敏映射形如 ``{"销售方A": "杭州云启网络科技有限公司"}``，供调用方在拿到模型输出后还原名称。
    """
    invoices = list(
        db.execute(
            select(Invoice)
            .where(Invoice.reimbursement_id == reimb.id)
            .order_by(Invoice.id)
        ).scalars().all()
    )

    # 销售方脱敏：按首次出现顺序编号，保证同一家公司在同一份事实里始终是同一个化名
    seller_alias: dict[str, str] = {}  # 真名 -> 化名
    unmask: dict[str, str] = {}  # 化名 -> 真名
    if settings.LLM_MASK_PII:
        for inv in invoices:
            name = (inv.seller_name or "").strip()
            if name and name not in seller_alias:
                alias = _mask_label(len(seller_alias))
                seller_alias[name] = alias
                unmask[alias] = name

    def seller_of(inv: Invoice) -> str:
        name = (inv.seller_name or "").strip()
        if not name:
            return ""
        return seller_alias.get(name, name)

    applicant = db.get(User, reimb.applicant_id)
    dept: Department | None = db.get(Department, applicant.dept_id) if applicant and applicant.dept_id else None

    # 提交时留存的预警（risk_detail 是用「；」拼起来的字符串）
    stored_warnings = [w for w in (reimb.risk_detail or "").split("；") if w.strip()]

    amount_problems = preaudit.check_amount_consistency(invoices)

    facts: dict[str, Any] = {
        "reimbursement": {
            "reason": reimb.reason or "",
            "total_amount": float(reimb.total_amount or 0),
            "invoice_count": reimb.invoice_count,
            "version": reimb.version,
            "previously_rejected": bool(reimb.review_comment),
            "previous_reject_reason": (reimb.review_comment or "")[:200],
            "risk_level": reimb.risk_level,
            "preaudit": {
                "hard_blocks": amount_problems,
                "warnings": stored_warnings,
                "note": (
                    "hard_blocks 只包含本次重新校验的金额勾稽问题；"
                    "重复报销与预算拦截已在提交时由规则引擎校验通过。"
                ),
            },
        },
        "department": {
            "name": (dept.name if dept else "") or "未分配",
            "single_invoice_limit": float(dept.single_limit or 0) if dept else 0.0,
            "monthly_limit": float(dept.monthly_limit or 0) if dept else 0.0,
        },
        "invoices": [
            {
                "index": idx + 1,
                "invoice_type": inv.invoice_type,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else "",
                "seller_name": seller_of(inv),
                "category": inv.category or "",
                "amount_excl_tax": float(inv.amount_excl_tax or 0),
                "tax_amount": float(inv.tax_amount or 0),
                "total_amount": float(inv.total_amount or 0),
                "ocr_confidence": float(inv.ocr_confidence or 0),
                "need_confirm": bool(inv.need_confirm),
            }
            for idx, inv in enumerate(invoices)
        ],
    }
    return facts, unmask


def unmask_text(text: str, unmask: dict[str, str]) -> str:
    """把模型输出里的「销售方A」还原成真实名称。

    长化名优先替换，避免 ``销售方A`` 命中 ``销售方A1`` 的前缀。
    """
    if not text or not unmask:
        return text
    for alias in sorted(unmask, key=len, reverse=True):
        text = text.replace(alias, unmask[alias])
    return text
