"""FastAPI 依赖：当前用户解析、权限码校验、CSRF 双提交校验。"""
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import require_permission
from app.core.security import decode_token
from app.models.user import User

settings = get_settings()

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _load_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias=settings.ACCESS_COOKIE),
) -> User:
    """Access Token 存 HttpOnly Cookie（需求 5.2）。"""
    if not access_token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = decode_token(access_token, "access")
    except Exception:
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")

    user = _load_user(db, int(payload["sub"]))
    if payload.get("ver") != user.token_version:
        raise HTTPException(status_code=401, detail="凭证已撤销，请重新登录")
    if user.status != "NORMAL":
        raise HTTPException(status_code=403, detail="账号已被停用")
    return user


def require_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    csrf_token: str | None = Cookie(default=None, alias=settings.CSRF_COOKIE),
) -> None:
    """写操作校验 CSRF 双提交 Token（需求 5.2）。"""
    if request.method not in WRITE_METHODS:
        return
    if not csrf_token or not x_csrf_token or csrf_token != x_csrf_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败"
        )


def current_user_with_csrf(
    user: User = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> User:
    """写接口统一依赖：先验登录，再验 CSRF。"""
    return user


def guard(permission: str):
    """生成「登录 + CSRF + 权限码」依赖，用于写/敏感读接口。"""

    def _dep(user: User = Depends(current_user_with_csrf)) -> User:
        require_permission(user.role.code, permission)
        return user

    return _dep


def guard_read(permission: str):
    """只读接口：登录 + 权限码，不校验 CSRF。"""

    def _dep(user: User = Depends(get_current_user)) -> User:
        require_permission(user.role.code, permission)
        return user

    return _dep
