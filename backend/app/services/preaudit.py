"""提交预审规则（需求 4.4）：硬拦截 + 预警 + 部门预算分级。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Invoice, Reimbursement

settings = get_settings()


@dataclass
class PreAuditResult:
    """预审结果：blocked 为真时阻止提交。"""

    blocked: bool = False
    hard_blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def risk_level(self) -> str:
        return "MID" if self.warnings else "LOW"

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "hard_blocks": self.hard_blocks,
            "warnings": self.warnings,
            "risk_level": self.risk_level,
        }


def check_amount_consistency(invoices: list[Invoice]) -> list[str]:
    """金额勾稽：|不含税 + 税额 − 价税合计| > 0.01 硬拦截。"""
    problems: list[str] = []
    for inv in invoices:
        excl = float(inv.amount_excl_tax or 0)
        tax = float(inv.tax_amount or 0)
        total = float(inv.total_amount or 0)
        if abs(excl + tax - total) > settings.AMOUNT_TOLERANCE:
            problems.append(
                f"发票 {inv.invoice_no} 金额勾稽不符："
                f"不含税 {excl:.2f} + 税额 {tax:.2f} = {excl + tax:.2f}，价税合计 {total:.2f}"
            )
    return problems


def check_duplicates(
    db: Session, invoices: list[Invoice], current_id: int | None = None
) -> list[str]:
    """重复报销：仅对审核中/已通过单据占用，红字（负数）发票单独放行。"""
    problems: list[str] = []
    for inv in invoices:
        if inv.invoice_type == "红字" or float(inv.total_amount or 0) < 0:
            continue
        stmt = (
            select(Reimbursement.code)
            .join(Invoice, Invoice.reimbursement_id == Reimbursement.id)
            .where(
                Invoice.dedup_key == inv.dedup_key,
                Invoice.occupy_state == "OCCUPIED",
                Invoice.id != (inv.id or 0),
                Reimbursement.status.in_(["PENDING", "APPROVED"]),
            )
            .limit(1)
        )
        if current_id:
            stmt = stmt.where(Reimbursement.id != current_id)
        other = db.execute(stmt).scalar_one_or_none()
        if other:
            problems.append(f"发票 {inv.invoice_no} 已在单据 {other} 中报销，存在重复")
    return problems


def check_buyer(invoices: list[Invoice]) -> list[str]:
    """抬头核验：购买方名称 / 税号 ≠ 公司主体时预警。

    可用 ``PREAUDIT_BUYER_CHECK=false`` 关闭。公司主体（``COMPANY_NAME`` /
    ``COMPANY_TAX_NO``）还是占位值时，这条会对所有真实发票误报，建议先关掉，
    等业务方给出真实主体再打开。
    """
    if not settings.PREAUDIT_BUYER_CHECK:
        return []

    warns: list[str] = []
    for inv in invoices:
        if inv.buyer_name and inv.buyer_name != settings.COMPANY_NAME:
            warns.append(f"发票 {inv.invoice_no} 购买方名称「{inv.buyer_name}」与公司主体不一致")
        if inv.buyer_tax_no and inv.buyer_tax_no != settings.COMPANY_TAX_NO:
            warns.append(f"发票 {inv.invoice_no} 购买方税号与公司主体不一致")
    return warns


def month_range(ref: date | None = None) -> tuple[datetime, datetime]:
    today = ref or date.today()
    start = datetime(today.year, today.month, 1)
    end = datetime(today.year + (today.month == 12), (today.month % 12) + 1, 1)
    return start, end


def check_budget(
    db: Session, dept_id: int | None, dept_name: str, amount: float
) -> tuple[list[str], list[str], float]:
    """部门预算：单笔上限直接拦截；月度累计 80% 预警、100% 拦截。

    返回 (hard_blocks, warnings, monthly_used)。
    """
    from app.models.user import Department, User

    if not dept_id:
        return [], [], 0.0
    dept = db.get(Department, dept_id)
    if dept is None:
        return [], [], 0.0

    start, end = month_range()
    used = db.execute(
        select(func.coalesce(func.sum(Reimbursement.total_amount), 0))
        .join(User, User.id == Reimbursement.applicant_id)
        .where(
            User.dept_id == dept_id,
            Reimbursement.status.in_(["PENDING", "APPROVED"]),
            Reimbursement.submitted_at >= start,
            Reimbursement.submitted_at < end,
        )
    ).scalar_one()
    used = float(used or 0)
    single_limit = float(dept.single_limit or 0)
    monthly_limit = float(dept.monthly_limit or 0)

    hard: list[str] = []
    warns: list[str] = []

    if single_limit and amount > single_limit:
        hard.append(
            f"本单金额 {amount:.2f} 元超过 {dept_name} 单笔上限 {single_limit:.2f} 元"
        )
    if monthly_limit:
        projected = used + amount
        ratio = projected / monthly_limit
        if ratio > 1:
            hard.append(
                f"{dept_name} 本月累计将达 {projected:.2f} 元，超过月度上限 {monthly_limit:.2f} 元"
            )
        elif ratio >= settings.BUDGET_WARN_RATIO:
            warns.append(
                f"{dept_name} 本月累计将达 {projected:.2f} 元，已超月度预算 "
                f"{settings.BUDGET_WARN_RATIO * 100:.0f}%（上限 {monthly_limit:.2f} 元）"
            )
    return hard, warns, used


def run_preaudit(
    db: Session,
    reimbursement: Reimbursement,
    invoices: list[Invoice],
    amount: float,
    dept_id: int | None,
    dept_name: str,
) -> PreAuditResult:
    """汇总全部规则，硬拦截优先。"""
    result = PreAuditResult()
    result.hard_blocks.extend(check_duplicates(db, invoices, reimbursement.id))
    result.hard_blocks.extend(check_amount_consistency(invoices))
    budget_hard, budget_warn, _ = check_budget(db, dept_id, dept_name, amount)
    result.hard_blocks.extend(budget_hard)
    result.warnings.extend(check_buyer(invoices))
    result.warnings.extend(budget_warn)
    result.blocked = bool(result.hard_blocks)
    return result
