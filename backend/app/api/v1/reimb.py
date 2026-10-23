"""报销单：建单、编辑、提交预审、我的单据、详情、重新提交。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import guard, guard_read
from app.api.v1.upload import invoice_dict
from app.core.database import get_db
from app.core.permissions import REIMB_SUBMIT, REIMB_VIEW_OWN
from app.models.document import Invoice
from app.models.user import User
from app.services import preaudit
from app.services import reimbursement as svc

router = APIRouter()


class DraftIn(BaseModel):
    reason: str = ""


class ReasonIn(BaseModel):
    reason: str


def summary(reimb) -> dict:
    return {
        "id": reimb.id,
        "code": reimb.code,
        "reason": reimb.reason,
        "total_amount": float(reimb.total_amount or 0),
        "invoice_count": reimb.invoice_count,
        "status": reimb.status,
        "risk_level": reimb.risk_level,
        "risk_detail": reimb.risk_detail,
        "version": reimb.version,
        "submitted_at": reimb.submitted_at,
        "reviewed_at": reimb.reviewed_at,
        "review_comment": reimb.review_comment,
    }


@router.post("/reimbursements")
def create_draft(
    payload: DraftIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_SUBMIT)),
):
    """先存草稿再提交（需求 3.1 第 5 项）。"""
    reimb = svc.create_draft(db, user, payload.reason)
    return summary(reimb)


@router.get("/reimbursements/mine")
def mine(
    response: Response,
    status: str | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REIMB_VIEW_OWN)),
):
    """我的报销单：含状态、金额、驳回原因（需求 3.1 第 6 项）。

    可选 ``status`` / ``page`` / ``page_size``；总数放在 ``X-Total-Count`` 响应头里，
    响应体保持数组形态（既有调用方与验收脚本不受影响）。
    """
    rows, total = svc.list_for_user(db, user.id, status, page, page_size)
    response.headers["X-Total-Count"] = str(total)
    return [summary(r) for r in rows]


@router.get("/reimbursements/audit")
def audit_preview(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REIMB_SUBMIT)),
):
    """提交前预审预览：不改变任何状态，供前端展示拦截与预警。"""
    reimb = svc.get_owned(db, reimbursement_id, user)
    invoices = [i for i in reimb.invoices]
    dept_name = user.dept.name if user.dept else "未知部门"
    result = preaudit.run_preaudit(
        db, reimb, invoices, float(reimb.total_amount or 0), user.dept_id, dept_name
    )
    return result.as_dict()


@router.get("/reimbursements/{reimbursement_id}")
def detail(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REIMB_VIEW_OWN)),
):
    reimb = svc.get_owned(db, reimbursement_id, user)
    return svc.build_detail(db, reimb)


@router.put("/reimbursements/{reimbursement_id}")
def update_reason(
    reimbursement_id: int,
    payload: ReasonIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_SUBMIT)),
):
    reimb = svc.get_owned(db, reimbursement_id, user)
    if reimb.status != "DRAFT":
        raise HTTPException(status_code=400, detail="仅草稿状态可修改报销事由")
    reimb.reason = payload.reason
    db.commit()
    db.refresh(reimb)
    return summary(reimb)


@router.post("/reimbursements/{reimbursement_id}/invoices/{invoice_id}")
def add_invoice(
    reimbursement_id: int,
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_SUBMIT)),
):
    reimb = svc.get_owned(db, reimbursement_id, user)
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="发票不存在")
    # 归属校验：只能使用**自己上传**的发票。
    # 原先只判「是否已被别的单据占用」，导致未占用的他人发票可以被挂到自己单上
    # （发票 id 是自增的，猜得到）—— 口径与 invoices.py 的 _owned_invoice 保持一致。
    if inv.attachment.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="无权使用该发票")
    if inv.reimbursement_id and inv.reimbursement_id != reimb.id:
        raise HTTPException(status_code=400, detail="该发票已属于其他报销单")
    svc.attach_invoice(db, reimb, inv)
    db.commit()
    db.refresh(reimb)
    return {"reimbursement": summary(reimb), "invoice": invoice_dict(inv)}


@router.delete("/reimbursements/{reimbursement_id}/invoices/{invoice_id}")
def remove_invoice(
    reimbursement_id: int,
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_SUBMIT)),
):
    reimb = svc.get_owned(db, reimbursement_id, user)
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.reimbursement_id != reimb.id:
        raise HTTPException(status_code=404, detail="该发票不在本单据中")
    svc.detach_invoice(db, reimb, inv)
    db.commit()
    db.refresh(reimb)
    return summary(reimb)


@router.post("/reimbursements/{reimbursement_id}/submit")
def submit(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_SUBMIT)),
):
    """提交时执行预审；命中硬拦截返回 409 与原因列表。"""
    reimb = svc.get_owned(db, reimbursement_id, user)
    result = svc.submit(db, reimb, user)
    if result.blocked:
        raise HTTPException(
            status_code=409,
            detail={"message": "提交被拦截", "reasons": result.hard_blocks},
        )
    db.refresh(reimb)
    return {
        "reimbursement": summary(reimb),
        "preaudit": result.as_dict(),
    }


@router.post("/reimbursements/{reimbursement_id}/resubmit")
def resubmit(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_SUBMIT)),
):
    """驳回后重新提交：版本号 +1，历史轨迹保留（验收标准：状态机）。"""
    reimb = svc.get_owned(db, reimbursement_id, user)
    reimb = svc.resubmit_prepare(db, reimb, user)
    result = svc.submit(db, reimb, user)
    if result.blocked:
        raise HTTPException(
            status_code=409,
            detail={"message": "提交被拦截", "reasons": result.hard_blocks},
        )
    db.refresh(reimb)
    return {"reimbursement": summary(reimb), "preaudit": result.as_dict()}
