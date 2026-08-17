"""用户与部门模型。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Department(Base):
    """部门：单笔上限 / 月度累计上限是预算拦截依据（需求 3.3）。"""

    __tablename__ = "department"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    single_limit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    monthly_limit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    users = relationship("User", back_populates="dept")


class User(Base):
    """用户表：BCrypt 散列存储密码；status 支持锁定态。"""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(50))
    # 邮箱：用于忘记密码发送重置链接。唯一（MySQL 下多个 NULL 不冲突），可为空（管理员代建的账号可能没有）
    email: Mapped[str | None] = mapped_column(
        String(120), nullable=True, unique=True, index=True
    )
    dept_id: Mapped[int | None] = mapped_column(
        ForeignKey("department.id"), nullable=True, index=True
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"), index=True)
    # NORMAL 正常 / LOCKED 锁定（连续 5 次错误密码锁定 15 分钟）
    status: Mapped[str] = mapped_column(String(10), default="NORMAL")
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 撤销 Refresh Token 用的密码版本：重置密码时 +1
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    dept = relationship("Department", back_populates="users")
    role = relationship("Role", back_populates="users")
    reimbursements = relationship(
        "Reimbursement",
        back_populates="applicant",
        foreign_keys="Reimbursement.applicant_id",
    )
