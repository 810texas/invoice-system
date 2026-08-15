"""附件、发票、报销单、审核轨迹、字段修改痕迹模型（需求 4.1/4.2）。"""
from datetime import datetime, date

from sqlalchemy import (
    Computed,
    DateTime,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 费用类别枚举（用户手选，必填）
EXPENSE_CATEGORIES = ["餐饮", "交通", "住宿", "办公", "其他"]
# 发票类型：蓝字 / 红字 / 数电票
INVOICE_TYPES = ["蓝字", "红字", "数电票"]
# 报销单状态机（需求 4.3）
REIMB_STATUS = ["DRAFT", "PENDING", "APPROVED", "REJECTED"]


class Attachment(Base):
    """上传附件：UUID 重命名后落盘，仅记录元数据。"""

    __tablename__ = "attachment"

    id: Mapped[int] = mapped_column(primary_key=True)
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    origin_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(50))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Invoice(Base):
    """发票表：查重键为生成列，唯一索引仅在占用态生效（需求 4.2）。"""

    __tablename__ = "invoice"

    id: Mapped[int] = mapped_column(primary_key=True)
    reimbursement_id: Mapped[int | None] = mapped_column(
        ForeignKey("reimbursement.id"), nullable=True, index=True
    )
    attachment_id: Mapped[int] = mapped_column(ForeignKey("attachment.id"), index=True)

    invoice_type: Mapped[str] = mapped_column(String(10), default="蓝字")
    invoice_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    invoice_no: Mapped[str] = mapped_column(String(30), index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    buyer_name: Mapped[str] = mapped_column(String(120), default="")
    buyer_tax_no: Mapped[str] = mapped_column(String(30), default="")
    seller_name: Mapped[str] = mapped_column(String(120), default="")

    amount_excl_tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    category: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ocr_confidence: Mapped[float] = mapped_column(Numeric(4, 2), default=0)
    need_confirm: Mapped[int] = mapped_column(Integer, default=0)
    # FREE 未占用 / OCCUPIED 已占用（审核中或已通过）
    occupy_state: Mapped[str] = mapped_column(String(10), default="FREE")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 查重键：数电票取号码，其余取「代码-号码」
    dedup_key: Mapped[str] = mapped_column(String(60), default="")
    # 占用态唯一键：未占用恒为 NULL，故同一发票可被驳回后重新提交
    occupy_key: Mapped[str | None] = mapped_column(
        String(60),
        Computed(
            "CASE WHEN occupy_state = 'OCCUPIED' THEN dedup_key END",
            persisted=True,
        ),
        nullable=True,
        unique=True,
    )
    attachment = relationship("Attachment")
    reimbursement = relationship("Reimbursement", back_populates="invoices")


class Reimbursement(Base):
    """报销单：状态机 DRAFT → PENDING → APPROVED / REJECTED。"""

    __tablename__ = "reimbursement"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT", index=True)
    # LOW 低（无预警）/ MID 中（有预警）
    risk_level: Mapped[str] = mapped_column(String(10), default="LOW")
    risk_detail: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), nullable=True, index=True
    )
    review_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    applicant = relationship(
        "User", back_populates="reimbursements", foreign_keys=[applicant_id]
    )
    invoices = relationship("Invoice", back_populates="reimbursement")


class ReviewLog(Base):
    """审核轨迹：提交 / 通过 / 驳回 / 重新提交，全程保留。"""

    __tablename__ = "review_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    reimbursement_id: Mapped[int] = mapped_column(
        ForeignKey("reimbursement.id"), index=True
    )
    operator_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(20), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FieldChangeLog(Base):
    """字段修改痕迹：原值 / 新值 / 修改人 / 时间，不可删除。"""

    __tablename__ = "field_change_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[str] = mapped_column(String(255), default="")
    new_value: Mapped[str] = mapped_column(String(255), default="")
    operator_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OcrTask(Base):
    """OCR 异步任务表：Worker 进程池消费，前端轮询进度。"""

    __tablename__ = "ocr_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("attachment.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice.id"), nullable=True, index=True
    )
    # PENDING 排队 / RUNNING 识别中 / DONE 完成 / FAILED 失败
    status: Mapped[str] = mapped_column(String(10), default="PENDING", index=True)
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
