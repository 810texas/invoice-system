"""鉴权业务：登录、锁定策略、注册、忘记密码、Refresh Token 轮换与撤销。"""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import ROLE_EMPLOYEE, ROLE_FINANCE
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    is_valid_email,
    new_reset_token,
    password_policy_error,
    token_fingerprint,
    verify_password,
)
from app.models.rbac import PasswordResetToken, RefreshToken, Role
from app.models.user import Department, User
from app.services import mailer

settings = get_settings()


def authenticate(db: Session, username: str, password: str) -> User:
    """密码校验 + 连续错误 5 次锁定 15 分钟（需求 3.1）。"""
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    now = datetime.now()
    if user.locked_until and user.locked_until > now:
        minutes = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(status_code=423, detail=f"账号已锁定，请 {minutes} 分钟后重试")
    if user.status != "NORMAL":
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")

    if not verify_password(password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= settings.LOGIN_MAX_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
            user.failed_attempts = 0
            db.commit()
            raise HTTPException(
                status_code=423,
                detail=f"密码连续错误 {settings.LOGIN_MAX_ATTEMPTS} 次，账号锁定 "
                f"{settings.LOGIN_LOCK_MINUTES} 分钟",
            )
        db.commit()
        left = settings.LOGIN_MAX_ATTEMPTS - user.failed_attempts
        raise HTTPException(status_code=401, detail=f"用户名或密码错误，剩余 {left} 次尝试机会")

    # 登录成功清零计数与锁定
    user.failed_attempts = 0
    user.locked_until = None
    db.commit()
    db.refresh(user)
    return user


def issue_tokens(db: Session, user: User) -> tuple[str, str, str]:
    """返回 (access, refresh, csrf)。Refresh Token 哈希入库以便撤销。"""
    access = create_access_token(user.id, user.role.code, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_fingerprint(refresh),
            expires_at=datetime.now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    db.commit()
    from app.core.security import new_csrf_token

    return access, refresh, new_csrf_token()


def rotate(db: Session, raw_refresh: str) -> tuple[str, str, User]:
    """Refresh 轮换：旧令牌撤销，换发新 access/refresh。"""
    from app.core.security import decode_token

    try:
        payload = decode_token(raw_refresh, "refresh")
    except Exception:
        raise HTTPException(status_code=401, detail="Refresh Token 无效或已过期")

    fp = token_fingerprint(raw_refresh)
    record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == fp)
    ).scalar_one_or_none()
    if record is None or record.revoked:
        raise HTTPException(status_code=401, detail="Refresh Token 已撤销")

    user = db.get(User, int(payload["sub"]))
    if user is None or payload.get("ver") != user.token_version:
        raise HTTPException(status_code=401, detail="凭证已撤销，请重新登录")

    record.revoked = 1
    db.commit()
    access = create_access_token(user.id, user.role.code, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_fingerprint(refresh),
            expires_at=datetime.now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    db.commit()
    return access, refresh, user


def revoke(db: Session, raw_refresh: str | None, user: User | None) -> None:
    """登出：撤销该 Refresh Token；解析失败时按用户全量撤销。"""
    if raw_refresh:
        record = db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_fingerprint(raw_refresh))
        ).scalar_one_or_none()
        if record:
            record.revoked = 1
            db.commit()
            return
    if user:
        for record in db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id, RefreshToken.revoked == 0
            )
        ).scalars().all():
            record.revoked = 1
        db.commit()


def set_password(db: Session, user: User, new_password: str) -> None:
    """改密码：密码版本 +1，旧令牌全部失效（需求 3.3 / 自助重置共用）。"""
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    user.failed_attempts = 0
    user.locked_until = None
    db.commit()


# ---------------- 角色与部门的默认对应 ----------------
# 为什么需要：部门只影响两件事 —— 「**申请人**所在部门的预算上限」与数据范围。
# 财务人员既没有 reimb:submit（不提交报销单）、数据范围又是 ALL，
# 所以部门对财务**没有任何实际作用**，纯属展示信息。
# 既然没有作用，就不该让管理员每次建号/改角色时手动选一遍 —— 按角色给个默认值。
ROLE_DEFAULT_DEPT: dict[str, str] = {
    ROLE_FINANCE: settings.FINANCE_DEPT_NAME,
}


def default_dept_name_for(role_code: str) -> str | None:
    """该角色的默认部门名；没有约定则返回 None。"""
    return ROLE_DEFAULT_DEPT.get(role_code)


def resolve_dept_id(db: Session, role_code: str, dept_id: int | None) -> int | None:
    """部门为空时按角色补默认部门（目前只有 财务 → 财务部）。

    **只在为空时补**：管理员显式选了别的部门就尊重他的选择。
    找不到那个部门时返回 None 而不是抛错 —— 没必要因为种子数据缺失把建号整个打断。
    """
    if dept_id:
        return dept_id
    name = default_dept_name_for(role_code)
    if not name:
        return None
    dept = db.execute(
        select(Department).where(Department.name == name)
    ).scalar_one_or_none()
    return dept.id if dept else None


# ---------------- 自助注册 ----------------
def ensure_default_dept(db: Session) -> Department:
    """「待分配」部门：自助注册用户的临时归属。

    为什么必须存在：部门决定预算上限，而**不能让注册者自选部门**（否则等于让他自己改预算），
    所以先落到一个受限部门，再由管理员调岗。
    为什么上限不能是 0：预审规则写的是 `if monthly_limit:`，
    上限为 0 会被当成「没设上限」，反而完全不检查预算 —— 那才是真的洞。
    """
    dept = db.execute(
        select(Department).where(Department.name == settings.DEFAULT_DEPT_NAME)
    ).scalar_one_or_none()
    if dept is None:
        dept = Department(
            name=settings.DEFAULT_DEPT_NAME,
            single_limit=settings.DEFAULT_DEPT_SINGLE_LIMIT,
            monthly_limit=settings.DEFAULT_DEPT_MONTHLY_LIMIT,
        )
        db.add(dept)
        db.commit()
        db.refresh(dept)
    return dept


def register(
    db: Session, *, username: str, name: str, email: str | None, password: str
) -> User:
    """自助注册。

    **安全口径（写在代码里而不是文档里，免得被后来者改掉）**：
    - 角色**强制员工**。调用方即使传了 role/role_id 也不读 —— 否则等于把财务权限敞开给公网。
    - 部门由系统指定为「待分配」，不接受客户端传值。
    """
    username = (username or "").strip()
    name = (name or "").strip()
    email = (email or "").strip() or None

    if len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少 3 个字符")
    if not name:
        raise HTTPException(status_code=400, detail="请填写姓名")
    # 邮箱格式必须在这里校验：管理员建号那条路有校验，注册这条路原先漏了，
    # 结果能注册出 email="not-an-email" 这种脏数据（忘记密码时也没法发信）。
    if email and not is_valid_email(email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")

    # MySQL 的 utf8mb4_unicode_ci 对唯一索引是大小写不敏感的，
    # 所以 ZhangWei / zhangwei 会自动冲突，这里不必额外转换大小写
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该用户名已被注册")
    if email and db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该邮箱已被注册")

    policy_error = password_policy_error(password, username)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)

    role = db.execute(
        select(Role).where(Role.code == ROLE_EMPLOYEE)
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=500, detail="系统角色数据缺失，请先运行 scripts/init_db.py"
        )

    user = User(
        username=username,
        name=name,
        email=email,
        password_hash=hash_password(password),
        role_id=role.id,
        dept_id=ensure_default_dept(db).id,
        status="NORMAL",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------- 忘记密码 ----------------
def find_by_account(db: Session, account: str) -> User | None:
    """账号可能是用户名，也可能是邮箱（真实系统两种都支持）。"""
    account = (account or "").strip()
    if not account:
        return None
    if "@" in account:
        return db.execute(select(User).where(User.email == account)).scalar_one_or_none()
    return db.execute(select(User).where(User.username == account)).scalar_one_or_none()


def request_password_reset(db: Session, account: str, ip: str = "") -> tuple[bool, str]:
    """提交忘记密码申请，返回 (是否已通过邮件投递, 明文令牌)。

    **不管账号是否存在，外部表现都一样** —— 否则这个接口就成了「账号枚举器」。
    账号不存在时返回 (False, "")，调用方照样回同一句提示。
    """
    user = find_by_account(db, account)
    if user is None or user.status != "NORMAL":
        return False, ""

    token = new_reset_token()
    # 同一用户未使用的旧令牌作废，避免同时存在多份有效链接
    for old in db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id, PasswordResetToken.used == 0
        )
    ).scalars().all():
        old.used = 1

    row = PasswordResetToken(
        user_id=user.id,
        token_hash=token_fingerprint(token),
        expires_at=datetime.now()
        + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES),
        delivery="MANUAL",
        requested_ip=ip[:64],
    )
    db.add(row)
    db.commit()

    delivered = False
    if user.email:
        delivered = mailer.send(
            user.email,
            "重置你的报销系统密码",
            mailer.reset_mail_body(user.name, token),
        )
    if delivered:
        row.delivery = "EMAIL"
        db.commit()
    return delivered, token


def reset_password_with_token(db: Session, token: str, new_password: str) -> User:
    """凭邮件里的令牌重置密码。令牌单次有效、过期即废。"""
    row = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_fingerprint(token or "")
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="重置链接无效，请重新申请")
    if row.used:
        raise HTTPException(status_code=400, detail="该重置链接已被使用，请重新申请")
    if row.expires_at < datetime.now():
        raise HTTPException(status_code=400, detail="重置链接已过期，请重新申请")

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="账号不存在")

    policy_error = password_policy_error(new_password, user.username)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)

    row.used = 1
    db.commit()
    # set_password 会把 token_version +1 → 该账号所有已登录设备立即失效
    set_password(db, user, new_password)
    return user


def revoke_reset_tokens(db: Session, user_id: int) -> None:
    """管理员重置密码后，把该用户尚未使用的重置令牌一并作废。"""
    for row in db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id, PasswordResetToken.used == 0
        )
    ).scalars().all():
        row.used = 1
    db.commit()


def pending_reset_requests(db: Session) -> list[tuple[User, PasswordResetToken]]:
    """待管理员处理的重置申请：未使用 + 未过期 + 当时没能发出邮件。"""
    rows = db.execute(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.used == 0,
            PasswordResetToken.expires_at > datetime.now(),
            PasswordResetToken.delivery == "MANUAL",
        )
        .order_by(PasswordResetToken.id.desc())
    ).scalars().all()
    out: list[tuple[User, PasswordResetToken]] = []
    for row in rows:
        user = db.get(User, row.user_id)
        if user is not None:
            out.append((user, row))
    return out
