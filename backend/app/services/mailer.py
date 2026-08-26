"""邮件发送：只服务于「忘记密码」的重置链接。

**没配 SMTP 时不报错**，`send()` 返回 False，由上层降级为「提交重置申请 → 管理员在用户管理里重置」。
这样内网/离线环境也能把完整流程走通 —— 需求里本来就没有邮件服务，这是工程折中，不是缺陷。
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import get_settings

settings = get_settings()


def is_configured() -> bool:
    """是否具备发信条件（有服务器地址，且有发件人或账号）。"""
    return bool(settings.SMTP_HOST and (settings.SMTP_FROM or settings.SMTP_USER))


def send(to: str, subject: str, body: str) -> bool:
    """发一封纯文本邮件。任何异常都只返回 False —— 发信失败不能把接口打成 500。"""
    if not is_configured() or not to:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(
        (settings.SMTP_FROM_NAME, settings.SMTP_FROM or settings.SMTP_USER)
    )
    msg["To"] = to
    msg.set_content(body)

    try:
        if settings.SMTP_USE_SSL:
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                context=ssl.create_default_context(),
                timeout=10,
            ) as server:
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.starttls(context=ssl.create_default_context())
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception:
        return False


def reset_link(token: str) -> str:
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}/reset-password?token={token}"


def reset_mail_body(name: str, token: str) -> str:
    minutes = settings.RESET_TOKEN_EXPIRE_MINUTES
    return (
        f"{name}，你好：\n\n"
        f"我们收到了重置「智能发票报销审核系统」密码的请求。\n"
        f"请打开下面的链接设置新密码（{minutes} 分钟内有效，且只能使用一次）：\n\n"
        f"{reset_link(token)}\n\n"
        f"如果这不是你本人操作，忽略本邮件即可，你的密码不会被修改。\n\n"
        f"—— 智能发票报销审核系统"
    )
