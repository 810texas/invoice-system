"""管理员与部门相关接口（需求 3.3）。

注意这里混了两类归属：
- **用户管理 / 基础数据** —— 管理员（`admin:user` / `admin:base`）
- **部门维护** —— 归**财务**（`dept:manage`，预算限额本就是财务的决定）；
  管理员只有 `dept:view`，因为他的「用户管理」要按部门给用户归属，需要读这份列表。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import guard, guard_read
from app.core.database import get_db
from app.core.permissions import (
    ADMIN_BASE_DATA,
    ADMIN_USER_MANAGE,
    DEPT_MANAGE,
    DEPT_VIEW,
    ROLE_PERMISSIONS,
)
from app.core.security import hash_password, is_valid_email, password_policy_error
from app.models.rbac import Menu, Role
from app.models.user import Department, User
from app.services import auth as auth_service

router = APIRouter()


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=50)
    email: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=128)
    role_id: int
    dept_id: int | None = None
    status: str = "NORMAL"


class DeptIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    single_limit: float = 0
    monthly_limit: float = 0


def _check_dept_limits(payload: DeptIn) -> None:
    """预算上限不能为负数（界面已用 :min="0" 限制，这里做接口层兜底）。

    注意：**不校验**「单笔上限 ≤ 月度上限」。单笔高于月度并非矛盾配置——
    月度累计规则（used + amount 与上限比较）会优先生效、单笔上限不起作用，
    这是表达「只关心月度总量」的合法写法，e2e_check.py 的月度硬拦截用例就依赖它。
    """
    if payload.single_limit < 0 or payload.monthly_limit < 0:
        raise HTTPException(status_code=400, detail="预算上限不能为负数")


def user_row(db: Session, u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "name": u.name,
        "email": u.email or "",
        "role_id": u.role_id,
        "role": u.role.code,
        "dept_id": u.dept_id,
        "dept": u.dept.name if u.dept else "",
        "status": u.status,
        "locked_until": u.locked_until,
        "failed_attempts": u.failed_attempts,
    }


def _check_email(db: Session, email: str | None, exclude_user_id: int | None = None) -> str | None:
    """校验邮箱格式与唯一性，返回清洗后的值（空串归一为 None）。"""
    value = (email or "").strip() or None
    if value is None:
        return None
    if not is_valid_email(value):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    stmt = select(User).where(User.email == value)
    if exclude_user_id:
        stmt = stmt.where(User.id != exclude_user_id)
    if db.execute(stmt).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该邮箱已被其他账号使用")
    return value


@router.get("/admin/users")
def list_users(
    db: Session = Depends(get_db), user: User = Depends(guard(ADMIN_USER_MANAGE))
):
    rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return [user_row(db, u) for u in rows]


@router.post("/admin/users")
def create_user(
    payload: UserIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(ADMIN_USER_MANAGE)),
):
    if db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if not payload.password:
        raise HTTPException(status_code=400, detail="新建用户必须设置初始密码")
    # 与注册/自助重置共用同一条密码强度口径（原先只查 ≥6 位，是两套标准）
    policy_error = password_policy_error(payload.password, payload.username)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)
    email = _check_email(db, payload.email)
    role = db.get(Role, payload.role_id)
    if role is None:
        # 先查一下再建，比让外键报错更清楚
        raise HTTPException(status_code=400, detail="角色不存在")
    new_user = User(
        username=payload.username,
        name=payload.name,
        email=email,
        password_hash=hash_password(payload.password),
        role_id=payload.role_id,
        # 财务的部门没有实际作用（不提交报销单、数据范围本就 ALL），
        # 所以不传部门时按角色补默认值（财务 → 财务部）
        dept_id=auth_service.resolve_dept_id(db, role.code, payload.dept_id),
        status=payload.status,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return user_row(db, new_user)


@router.put("/admin/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(ADMIN_USER_MANAGE)),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == user.id and payload.status != "NORMAL":
        raise HTTPException(status_code=400, detail="不能停用当前登录账号")
    role = db.get(Role, payload.role_id)
    if role is None:
        raise HTTPException(status_code=400, detail="角色不存在")
    target.name = payload.name
    target.role_id = payload.role_id
    # 改成财务人员且没给部门时，默认落到财务部（部门对财务只是展示信息）
    target.dept_id = auth_service.resolve_dept_id(db, role.code, payload.dept_id)
    target.status = payload.status
    target.email = _check_email(db, payload.email, exclude_user_id=target.id)
    if target.status == "NORMAL":
        # 停用解除时清空锁定
        target.failed_attempts = 0
        target.locked_until = None
    db.commit()
    db.refresh(target)
    return user_row(db, target)


@router.post("/admin/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(guard(ADMIN_USER_MANAGE)),
):
    """重置密码：密码版本 +1，旧令牌与未用的重置链接全部失效。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    new_password = (payload or {}).get("new_password") or ""
    policy_error = password_policy_error(new_password, target.username)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)
    auth_service.set_password(db, target, new_password)
    # 管理员重置之后，之前用户自己申请的重置链接就不该再有效
    auth_service.revoke_reset_tokens(db, target.id)
    return {"ok": True}


@router.get("/admin/password-reset-requests")
def password_reset_requests(
    db: Session = Depends(get_db), user: User = Depends(guard(ADMIN_USER_MANAGE))
):
    """待管理员处理的重置申请。

    忘记密码在**没配 SMTP** 时不会发邮件，而是落一条申请到这里 ——
    这样离线/内网环境也能把"忘记密码"走完，而不是给用户一个空承诺。
    """
    return [
        {
            "request_id": row.id,
            "user_id": u.id,
            "username": u.username,
            "name": u.name,
            "dept": u.dept.name if u.dept else "",
            "email": u.email or "",
            "requested_ip": row.requested_ip,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
        }
        for u, row in auth_service.pending_reset_requests(db)
    ]


@router.post("/admin/users/{user_id}/unlock")
def unlock(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(guard(ADMIN_USER_MANAGE)),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    target.status = "NORMAL"
    target.failed_attempts = 0
    target.locked_until = None
    db.commit()
    return {"ok": True}


@router.get("/admin/departments")
def list_depts(
    db: Session = Depends(get_db), user: User = Depends(guard_read(DEPT_VIEW))
):
    """部门列表（含预算上限）。

    读用 DEPT_VIEW：**财务**要看要改，**管理员**的「用户管理」也要用它填部门下拉框，
    所以两边都能读；但新增/修改是 DEPT_MANAGE，只有财务有。
    """
    rows = db.execute(select(Department).order_by(Department.id)).scalars().all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "single_limit": float(d.single_limit or 0),
            "monthly_limit": float(d.monthly_limit or 0),
        }
        for d in rows
    ]


@router.post("/admin/departments")
def create_dept(
    payload: DeptIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(DEPT_MANAGE)),
):
    if db.execute(select(Department).where(Department.name == payload.name)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="部门已存在")
    _check_dept_limits(payload)
    dept = Department(
        name=payload.name,
        single_limit=payload.single_limit,
        monthly_limit=payload.monthly_limit,
    )
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return {"id": dept.id, "name": dept.name}


@router.put("/admin/departments/{dept_id}")
def update_dept(
    dept_id: int,
    payload: DeptIn,
    db: Session = Depends(get_db),
    user: User = Depends(guard(DEPT_MANAGE)),
):
    dept = db.get(Department, dept_id)
    if dept is None:
        raise HTTPException(status_code=404, detail="部门不存在")
    _check_dept_limits(payload)
    dept.name = payload.name
    dept.single_limit = payload.single_limit
    dept.monthly_limit = payload.monthly_limit
    db.commit()
    db.refresh(dept)
    return {
        "id": dept.id,
        "name": dept.name,
        "single_limit": float(dept.single_limit or 0),
        "monthly_limit": float(dept.monthly_limit or 0),
    }


@router.get("/admin/roles")
def list_roles(
    db: Session = Depends(get_db), user: User = Depends(guard(ADMIN_BASE_DATA))
):
    rows = db.execute(select(Role).order_by(Role.id)).scalars().all()
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "permissions": sorted(ROLE_PERMISSIONS.get(r.code, [])),
            # 前端据此在管理员把角色选成财务时自动带出部门（部门对财务只是展示信息）
            "default_dept_name": auth_service.default_dept_name_for(r.code),
            "menus": [
                {"title": m.title, "path": m.path, "sort": m.sort}
                for m in sorted(r.menus, key=lambda m: m.sort)
            ],
        }
        for r in rows
    ]


@router.get("/admin/menus")
def list_menus(
    db: Session = Depends(get_db), user: User = Depends(guard(ADMIN_BASE_DATA))
):
    rows = db.execute(select(Menu).order_by(Menu.sort)).scalars().all()
    return [
        {
            "id": m.id,
            "title": m.title,
            "path": m.path,
            "icon": m.icon,
            "sort": m.sort,
            "parent_id": m.parent_id,
            "roles": [r.code for r in m.roles],
        }
        for m in rows
    ]
