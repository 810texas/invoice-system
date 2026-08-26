"""发票字段修正与修改留痕（需求 3.1 第 4 项 / 4.5）。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import guard, guard_read
from app.api.v1.upload import invoice_dict
from app.core.database import get_db
from app.core.permissions import INVOICE_EDIT
from app.models.document import EXPENSE_CATEGORIES, FieldChangeLog, Invoice
from app.models.user import User
from app.services import ocr as ocr_service
from app.services import reimbursement as reimb_service

router = APIRouter()

EDITABLE = {
    "invoice_type",
    "invoice_code",
    "invoice_no",
    "invoice_date",
    "buyer_name",
    "buyer_tax_no",
    "seller_name",
    "amount_excl_tax",
    "tax_amount",
    "total_amount",
    "category",
}


class InvoicePatch(BaseModel):
    invoice_type: str | None = None
    invoice_code: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    buyer_name: str | None = None
    buyer_tax_no: str | None = None
    seller_name: str | None = None
    amount_excl_tax: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    category: str | None = None


def _owned_invoice(db: Session, invoice_id: int, user: User) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="发票不存在")
    if inv.reimbursement_id:
        reimb_service.get_owned(db, inv.reimbursement_id, user)
    elif inv.attachment.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="无权修改该发票")
    return inv


@router.patch("/invoices/{invoice_id}")
def update_invoice(
    invoice_id: int,
    payload: InvoicePatch,
    db: Session = Depends(get_db),
    user: User = Depends(guard(INVOICE_EDIT)),
):
    """人工确认与修正：字段可改，费用类别必填，全程记录原值/新值。"""
    inv = _owned_invoice(db, invoice_id, user)
    if inv.reimbursement and inv.reimbursement.status != "DRAFT":
        raise HTTPException(status_code=400, detail="单据已提交，不可再修改发票")

    data = payload.model_dump(exclude_unset=True)
    changes: dict[str, tuple[str, str]] = {}

    for field, value in data.items():
        if field not in EDITABLE:
            continue
        if field == "invoice_date" and value:
            try:
                value = date.fromisoformat(value)
            except ValueError:
                raise HTTPException(status_code=400, detail="开票日期格式应为 YYYY-MM-DD")
        if field == "category" and value not in EXPENSE_CATEGORIES:
            raise HTTPException(
                status_code=400, detail=f"费用类别须为 {'/'.join(EXPENSE_CATEGORIES)} 之一"
            )
        old = getattr(inv, field)
        old_repr = "" if old is None else str(old)
        new_repr = "" if value is None else str(value)
        if old_repr != new_repr:
            changes[field] = (old_repr, new_repr)
            setattr(inv, field, value)

    if changes:
        # 查重键随号码变化重算
        inv.dedup_key = ocr_service.dedup_key_of(
            inv.invoice_type, inv.invoice_code, inv.invoice_no
        )
        ocr_service.log_field_changes(db, inv, changes, user.id)
        if inv.reimbursement:
            reimb_service.recalc(db, inv.reimbursement)
        db.commit()
        db.refresh(inv)

    return {"invoice": invoice_dict(inv), "changed": list(changes.keys())}


@router.get("/invoices/{invoice_id}/changes")
def changes(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(INVOICE_EDIT)),
):
    """修改留痕查询：原值 / 新值 / 修改人 / 时间（验收标准：修改留痕）。"""
    inv = _owned_invoice(db, invoice_id, user)
    rows = db.execute(
        select(FieldChangeLog)
        .where(FieldChangeLog.invoice_id == inv.id)
        .order_by(FieldChangeLog.id.asc())
    ).scalars().all()
    return [
        {
            "field_name": r.field_name,
            "old_value": r.old_value,
            "new_value": r.new_value,
            "operator": (db.get(User, r.operator_id).name if db.get(User, r.operator_id) else ""),
            "created_at": r.created_at,
        }
        for r in rows
    ]
