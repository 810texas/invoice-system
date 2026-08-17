"""模型包：导入即注册到 Base.metadata，供建表脚本使用。

导入顺序有依赖：rbac（角色/菜单）→ user（部门/用户）→ document（附件/发票/单据/留痕）
→ ai（AI 建议留痕，外键依赖 reimbursement 与 user）。
"""
from app.models.rbac import Menu, PasswordResetToken, RefreshToken, Role, role_menu
from app.models.user import Department, User
from app.models.document import (
    EXPENSE_CATEGORIES,
    INVOICE_TYPES,
    REIMB_STATUS,
    Attachment,
    FieldChangeLog,
    Invoice,
    OcrTask,
    Reimbursement,
    ReviewLog,
)
from app.models.ai import AiSuggestion

__all__ = [
    "AiSuggestion",
    "Attachment",
    "Department",
    "EXPENSE_CATEGORIES",
    "FieldChangeLog",
    "INVOICE_TYPES",
    "Invoice",
    "Menu",
    "OcrTask",
    "PasswordResetToken",
    "REIMB_STATUS",
    "RefreshToken",
    "Reimbursement",
    "ReviewLog",
    "Role",
    "User",
    "role_menu",
]
