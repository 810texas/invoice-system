"""登录 / 注册 / 忘记密码 / 登出 / 当前用户 / 刷新令牌。

公开接口（登录、注册、忘记密码、重置）按需求 5.2 的既有口径豁免 CSRF 双提交校验 ——
它们本来就没有登录态可依附；代价是用**限流**来防刷（见 core/ratelimit.py）。
"""
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user_with_csrf, get_current_user
from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import get_db
from app.core.permissions import (
    DATA_SCOPE_TEXT,
    data_scope_of,
    permissions_of,
)
from app.core.security import password_policy_error, verify_password
from app.models.user import User
from app.services import auth as auth_service
from app.services import mailer

router = APIRouter()
settings = get_settings()

COOKIE_COMMON = {"httponly": True, "samesite": "lax", "path": "/"}


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class RegisterIn(BaseModel):
    """自助注册入参。**刻意不含角色与部门字段** —— 见 services/auth.register 的说明。"""

    username: str = Field(min_length=3, max_length=50)
    name: str = Field(min_length=1, max_length=50)
    email: str | None = Field(default=None, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class ForgotIn(BaseModel):
    account: str = Field(min_length=1, max_length=120)


class ResetIn(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


def _set_auth_cookies(response: Response, access: str, refresh: str, csrf: str) -> None:
    """Access/Refresh 存 HttpOnly Cookie，CSRF Token 需前端可读（非 HttpOnly）。"""
    response.set_cookie(
        settings.ACCESS_COOKIE,
        access,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        **COOKIE_COMMON,
    )
    response.set_cookie(
        settings.REFRESH_COOKIE,
        refresh,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        **COOKIE_COMMON,
    )
    response.set_cookie(settings.CSRF_COOKIE, csrf, samesite="lax", path="/")


def user_profile(user: User) -> dict:
    """登录态自述：身份 + 菜单（第一层）+ 权限码（第二层）+ 数据范围（第三层）。

    前端拿 `permissions` 做路由守卫与按钮控制，就不再需要硬编码角色名。
    """
    role_code = user.role.code
    scope = data_scope_of(role_code)
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "email": user.email or "",
        "role": role_code,
        "role_name": user.role.name,
        "dept": user.dept.name if user.dept else "",
        "dept_id": user.dept_id,
        "permissions": permissions_of(role_code),
        "data_scope": scope,
        "data_scope_text": DATA_SCOPE_TEXT.get(scope, ""),
        "menus": [
            {"title": m.title, "path": m.path, "icon": m.icon, "sort": m.sort}
            for m in sorted(user.role.menus, key=lambda m: m.sort)
        ],
    }


@router.post("/auth/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    try:
        user = auth_service.authenticate(db, payload.username, payload.password)
    except HTTPException as exc:
        raise exc
    access, refresh, csrf = auth_service.issue_tokens(db, user)
    _set_auth_cookies(response, access, refresh, csrf)
    return {"user": user_profile(user), "csrf_token": csrf}


@router.post("/auth/register")
def register(
    request: Request,
    response: Response,
    payload: RegisterIn,
    db: Session = Depends(get_db),
):
    """自助注册：注册即登录，返回结构与 /auth/login 一致，前端可复用同一套处理。

    只能注册出**员工**账号：角色由服务端强制，部门固定进「待分配」，
    财务/管理员必须由管理员创建（涉及钱的权限不能自助获取）。
    """
    ratelimit.limit_public(request, "register", limit=settings.REGISTER_RATE_LIMIT_PER_MINUTE)
    user = auth_service.register(
        db,
        username=payload.username,
        name=payload.name,
        email=payload.email,
        password=payload.password,
    )
    access, refresh, csrf = auth_service.issue_tokens(db, user)
    _set_auth_cookies(response, access, refresh, csrf)
    return {"user": user_profile(user), "csrf_token": csrf}


@router.post("/auth/forgot-password")
def forgot_password(
    request: Request,
    payload: ForgotIn,
    db: Session = Depends(get_db),
):
    """忘记密码：提交重置申请。

    **不区分账号是否存在**（否则这个接口就成了账号枚举器）；
    提示文案只取决于服务端有没有配 SMTP，不取决于传入的账号。
    """
    # 按 IP 限流（防遍历账号），再按账号收紧（防拿同一个邮箱反复轰炸）
    ratelimit.limit_public(request, "forgot", limit=settings.FORGOT_RATE_LIMIT_PER_MINUTE)
    ratelimit.hit(
        f"forgot-account:{payload.account.strip().lower()}",
        limit=settings.FORGOT_ACCOUNT_RATE_LIMIT_PER_MINUTE,
    )
    _, reset_token = auth_service.request_password_reset(
        db, payload.account, ratelimit.client_ip(request)
    )

    if reset_token and settings.RESET_TOKEN_ECHO:
        # 仅供本地演示/排障：生产必须保持 RESET_TOKEN_ECHO=false
        print(
            f"[forgot-password] 重置链接（{settings.RESET_TOKEN_EXPIRE_MINUTES} 分钟内有效）："
            f"{mailer.reset_link(reset_token)}"
        )

    if mailer.is_configured():
        message = "如果该账号存在，我们已向它的绑定邮箱发送重置链接，请查收（含垃圾邮件箱）。"
    else:
        message = "如果该账号存在，重置申请已提交管理员处理，请联系管理员获取新密码。"
    return {"message": message}


@router.post("/auth/reset-password")
def reset_password(
    request: Request,
    payload: ResetIn,
    db: Session = Depends(get_db),
):
    """凭重置令牌设置新密码。令牌本身即凭证，所以是公开接口。

    重置成功后该账号所有已登录设备会立即失效（token_version +1）。
    """
    ratelimit.limit_public(request, "reset", limit=settings.RESET_RATE_LIMIT_PER_MINUTE)
    user = auth_service.reset_password_with_token(db, payload.token, payload.new_password)
    return {"message": "密码已重置，请用新密码登录", "username": user.username}


@router.post("/auth/change-password")
def change_password(
    response: Response,
    payload: ChangePasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_with_csrf),
):
    """已登录用户自助改密码。

    为什么必须有这个接口：引导管理员的初始密码是**一次性随机**的，
    种子里写着「登录后请立即修改」，但之前系统里根本没有地方能改；
    而且管理员账号通常没有邮箱，连「忘记密码」也走不通。

    改完 `token_version` +1：**其他设备立即失效**（这也是"改了密码要把别人踢下线"的正解），
    本机按新版本重新签发令牌，继续用着不被打断。
    """
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if payload.new_password == payload.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    policy_error = password_policy_error(payload.new_password, user.username)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)

    auth_service.set_password(db, user, payload.new_password)
    access, refresh, csrf = auth_service.issue_tokens(db, user)
    _set_auth_cookies(response, access, refresh, csrf)
    return {
        "message": "密码已修改，其他设备需重新登录",
        "user": user_profile(user),
        "csrf_token": csrf,
    }


@router.post("/auth/refresh")
def refresh(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=settings.REFRESH_COOKIE),
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 Refresh Token")
    access, new_refresh, user = auth_service.rotate(db, refresh_token)
    from app.core.security import new_csrf_token

    csrf = new_csrf_token()
    _set_auth_cookies(response, access, new_refresh, csrf)
    return {"user": user_profile(user), "csrf_token": csrf}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return user_profile(user)


@router.post("/auth/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=settings.REFRESH_COOKIE),
):
    auth_service.revoke(db, refresh_token, None)
    for name in (settings.ACCESS_COOKIE, settings.REFRESH_COOKIE, settings.CSRF_COOKIE):
        response.delete_cookie(name, path="/")
    return {"ok": True}
