"""财务审核：待办列表、单据详情、通过/驳回、审核轨迹查询。

注意路由顺序：/review/logs/query 与 /review/all/list 必须声明在
/review/{reimbursement_id} 之前，否则字面量路径会被动态段吃掉。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import guard, guard_read
from app.core.database import get_db
from app.core.permissions import REIMB_REVIEW, REIMB_VIEW_ALL, REVIEW_LOG_VIEW
from app.models.document import Reimbursement, ReviewLog
from app.models.user import User
from app.services import reimbursement as svc

router = APIRouter()


class DecisionIn(BaseModel):
    comment: str = ""


def todo_item(db: Session, reimb: Reimbursement) -> dict:
    applicant = db.get(User, reimb.applicant_id)
    return {
        "id": reimb.id,
        "code": reimb.code,
        "applicant": applicant.name if applicant else "",
        "dept": applicant.dept.name if applicant and applicant.dept else "",
        "reason": reimb.reason,
        "total_amount": float(reimb.total_amount or 0),
        "invoice_count": reimb.invoice_count,
        "status": reimb.status,
        "risk_level": reimb.risk_level,
        "risk_detail": reimb.risk_detail,
        "version": reimb.version,
        "submitted_at": reimb.submitted_at,
    }


@router.get("/review/todos")
def todos(
    response: Response,
    status: str | None = None,
    applicant: str | None = None,
    order: str = "submitted_at",
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REIMB_REVIEW)),
):
    """待办列表：按提交时间 / 金额 / 风险等级排序（需求 3.2）。

    可选 ``page`` / ``page_size``；总数放在 ``X-Total-Count`` 响应头，响应体保持数组形态。
    """
    rows, total = svc.list_pending(db, status, applicant, order, page, page_size)
    response.headers["X-Total-Count"] = str(total)
    return [todo_item(db, r) for r in rows]


@router.get("/review/logs/query")
def query_logs(
    status: str | None = None,
    applicant: str | None = None,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REVIEW_LOG_VIEW)),
):
    """审核轨迹查询：按时间段、申请人、审核状态（需求 3.2 第 5 项）。"""
    stmt = (
        select(ReviewLog, Reimbursement, User)
        .join(Reimbursement, Reimbursement.id == ReviewLog.reimbursement_id)
        .join(User, User.id == Reimbursement.applicant_id)
        .order_by(ReviewLog.id.desc())
        .limit(200)
    )
    if status:
        stmt = stmt.where(Reimbursement.status == status)
    if applicant:
        stmt = stmt.where(User.name.like(f"%{applicant}%"))
    if start:
        stmt = stmt.where(ReviewLog.created_at >= datetime.fromisoformat(start))
    if end:
        stmt = stmt.where(ReviewLog.created_at <= datetime.fromisoformat(end))

    rows = db.execute(stmt).all()
    return [
        {
            "log_id": lg.id,
            "reimbursement_id": rb.id,
            "code": rb.code,
            "applicant": ap.name,
            "action": lg.action,
            "result": lg.result,
            "reason": lg.reason,
            "operator": (
                db.get(User, lg.operator_id).name if db.get(User, lg.operator_id) else ""
            ),
            "status": rb.status,
            "created_at": lg.created_at,
            "version": rb.version,
        }
        for lg, rb, ap in rows
    ]


@router.get("/review/all/list")
def all_reimbursements(
    response: Response,
    status: str | None = None,
    applicant: str | None = None,
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REIMB_VIEW_ALL)),
):
    """跨用户查询数据（需求 3.2 第 1 项）。"""
    rows, total = svc.list_pending(db, status or None, applicant, "submitted_at", page, page_size)
    response.headers["X-Total-Count"] = str(total)
    return [todo_item(db, r) for r in rows]


@router.get("/review/{reimbursement_id}")
def detail(
    reimbursement_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard_read(REIMB_REVIEW)),
):
    """单据详情：影像、OCR 字段与置信度、待人工确认标记、预审预警。

    这里走 `get_visible()` 而不是直接 `db.get()`：财务的数据范围是 ALL，行为不变，
    但「为什么能看到」从此有据可依 —— 将来加一个 DEPT 范围的角色，这条会自动收紧。
    """
    reimb = svc.get_visible(db, reimbursement_id, user)
    return svc.build_detail(db, reimb)


@router.post("/review/{reimbursement_id}/approve")
def approve(
    reimbursement_id: int,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_REVIEW)),
):
    reimb = db.get(Reimbursement, reimbursement_id)
    if reimb is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    reimb = svc.review(db, reimb, user, "APPROVE", payload.comment)
    return {"id": reimb.id, "status": reimb.status}


@router.post("/review/{reimbursement_id}/reject")
def reject(
    reimbursement_id: int,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(REIMB_REVIEW)),
):
    """驳回必须填写原因（验收标准：审核流程）。"""
    if not payload.comment.strip():
        raise HTTPException(status_code=400, detail="驳回必须填写原因")
    reimb = db.get(Reimbursement, reimbursement_id)
    if reimb is None:
        raise HTTPException(status_code=404, detail="报销单不存在")
    reimb = svc.review(db, reimb, user, "REJECT", payload.comment)
    return {"id": reimb.id, "status": reimb.status}
