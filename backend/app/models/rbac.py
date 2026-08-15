"""RBAC 模型：角色、菜单、角色-菜单关联、Refresh Token 台账。"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 角色-菜单多对多关联表
role_menu = Table(
    "role_menu",
    Base.metadata,
    Column("role_id", ForeignKey("role.id"), primary_key=True),
    Column("menu_id", ForeignKey("menu.id"), primary_key=True),
)


class Role(Base):
    """角色：EMPLOYEE 员工 / FINANCE 财务 / ADMIN 管理员。"""

    __tablename__ = "role"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(30))

    menus = relationship("Menu", secondary=role_menu, back_populates="roles")
    users = relationship("User", back_populates="role")


class Menu(Base):
    """菜单：控制前端可见性。"""

    __tablename__ = "menu"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    path: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(50), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("menu.id"), nullable=True)

    roles = relationship("Role", secondary=role_menu, back_populates="menus")


class RefreshToken(Base):
    """Refresh Token 入库（存哈希），支持撤销（需求 5.2）。"""

    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PasswordResetToken(Base):
    """忘记密码的重置令牌：**只存哈希**、单次有效、限时过期。

    与 RefreshToken 同一套安全口径：库里没有明文，即便数据库泄露也无法直接重置他人密码。
    ``delivery`` 记录投递方式，让管理员能区分「邮件已发出」和「待人工处理」。
    """

    __tablename__ = "password_reset_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    # 1 表示已使用（用过即作废，防止重放）
    used: Mapped[int] = mapped_column(Integer, default=0)
    # EMAIL 已发邮件 / MANUAL 未配置 SMTP，等管理员处理
    delivery: Mapped[str] = mapped_column(String(10), default="MANUAL")
    # 申请来源，便于审计「谁在什么时候申请过重置」
    requested_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
