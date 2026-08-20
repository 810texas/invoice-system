"""安全工具：BCrypt 密码散列、口令强度、JWT 双令牌、CSRF 双提交 Token。"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

settings = get_settings()


# ---------- 密码 ----------
def hash_password(raw: str) -> str:
    """BCrypt 散列（需求 4.2：加密密码 BCrypt 存储）。"""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# 口令里不允许出现的弱密码（仅做最基本的拦截，不做字典库）
COMMON_WEAK_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "password123",
    "passw0rd", "qwerty123", "qwerty123456", "abc12345", "abcd1234", "a1234567",
    "admin123", "admin1234", "iloveyou", "11111111", "88888888", "1qaz2wsx",
}


def password_policy_error(raw: str, username: str = "") -> str | None:
    """校验密码强度，符合要求返回 None，否则返回中文原因。

    注册 / 自助重置 / 管理员建号与重置**共用这一条口径** ——
    之前管理员重置只查「≥6 位」、新建用户根本不查，是两套标准。
    """
    if not raw:
        return "密码不能为空"
    if len(raw) < settings.PASSWORD_MIN_LENGTH:
        return f"密码至少 {settings.PASSWORD_MIN_LENGTH} 位"
    if len(raw) > 128:
        return "密码过长（最多 128 位）"
    # 弱口令清单放在「字母+数字」之前：像 `password` 这种虽然也不含数字，
    # 但告诉用户「过于常见」比「需要字母和数字」更有指导意义。
    if raw.lower() in COMMON_WEAK_PASSWORDS:
        return "该密码过于常见，请更换"
    if not any(ch.isalpha() for ch in raw) or not any(ch.isdigit() for ch in raw):
        return "密码需同时包含字母和数字"
    if username and raw.lower() == username.lower():
        return "密码不能与用户名相同"
    return None


# ---------- JWT ----------
def _encode(payload: dict[str, Any], expires: timedelta, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    body = {**payload, "type": token_type, "iat": now, "exp": now + expires}
    return jwt.encode(body, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int, role_code: str, token_version: int) -> str:
    # jti 随机因子：避免同一秒内重复签发产生完全相同的令牌
    return _encode(
        {
            "sub": str(user_id),
            "role": role_code,
            "ver": token_version,
            "jti": secrets.token_urlsafe(12),
        },
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(user_id: int, token_version: int) -> str:
    return _encode(
        {
            "sub": str(user_id),
            "ver": token_version,
            "jti": secrets.token_urlsafe(16),
        },
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """解码并校验令牌类型；失败抛 jwt 异常由调用方转 401。"""
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("令牌类型不匹配")
    return payload


def token_fingerprint(token: str) -> str:
    """Refresh Token / 重置令牌只存哈希入库（需求 5.2：入库可撤销）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_reset_token() -> str:
    """忘记密码用的一次性重置令牌（明文只出现在邮件/控制台里，库里只存哈希）。"""
    return secrets.token_urlsafe(32)


# 邮箱：故意只用轻量正则，不引入 email-validator 依赖（本项目依赖尽量少）
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def is_valid_email(value: str) -> bool:
    return bool(value) and len(value) <= 120 and bool(_EMAIL_RE.match(value))


# ---------- CSRF ----------
def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
