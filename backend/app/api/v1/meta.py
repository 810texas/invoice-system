"""基础数据与元信息：费用类别、发票类型、部门、用户。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import can_view_all_data
from app.models.document import EXPENSE_CATEGORIES, INVOICE_TYPES
from app.models.user import Department, User

router = APIRouter()
settings = get_settings()


@router.get("/meta")
def meta(user: User = Depends(get_current_user)):
    """前端下拉与字典数据。

    注意 ``ocr_confidence_threshold``：前端用它判断「待人工确认」标红，
    必须与后端预审/打标用的同一个配置项一致（原先前端在三处硬编码 0.85）。

    这里**只放前端真正会用的字段**。原先还返回过：
    - ``statuses``：前端筛选下拉用的是 `utils/format.js` 的展示表（顺序 + 中文标签 + 颜色），
      后端这份数组没人读；
    - ``can_review``：与 `/auth/me` 的 ``permissions`` 重复，前端也没用。
    两个都删了，避免出现「两份状态枚举 / 两处权限判断」这种会漂移的冗余。
    """
    return {
        "expense_categories": EXPENSE_CATEGORIES,
        "invoice_types": INVOICE_TYPES,
        "ocr_confidence_threshold": settings.OCR_CONFIDENCE_THRESHOLD,
    }


@router.get("/departments")
def departments(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    depts = db.execute(select(Department).order_by(Department.id)).scalars().all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "single_limit": float(d.single_limit or 0),
            "monthly_limit": float(d.monthly_limit or 0),
        }
        for d in depts
    ]


@router.get("/users")
def users(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户列表：数据范围为「全部」的角色可查全部；其余人只能看到自己。"""
    if not can_view_all_data(user.role.code):
        rows = [user]
    else:
        rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "name": u.name,
            "role": u.role.code,
            "dept": u.dept.name if u.dept else "",
            "status": u.status,
        }
        for u in rows
    ]
