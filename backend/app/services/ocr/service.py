"""OCR 任务：入队识别、字段落库、置信度打标。

约定：
- 开发期（OCR_PROVIDER=mock）同步执行，便于联调；
- OCR_PROVIDER=paddle 时在同一函数内执行真实识别，生产部署应改为进程池消费
  ocr_task 表（需求 2.1/5.1），此处保留 task 表与状态流转，便于平滑替换。
"""
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Attachment, FieldChangeLog, Invoice, OcrTask
from app.services.ocr.provider import get_provider

settings = get_settings()

# OCR 字段 → invoice 表字段
FIELD_MAP = [
    "invoice_type",
    "invoice_code",
    "invoice_no",
    "invoice_date",
    "buyer_name",
    "buyer_tax_no",
    "seller_name",
    "amount_excl_tax",
    "tax_amount",
    "total_amount",
]

NUMERIC_FIELDS = {"amount_excl_tax", "tax_amount", "total_amount"}


def dedup_key_of(invoice_type: str, invoice_code: str | None, invoice_no: str) -> str:
    """查重键：数电票取号码，其余取「代码-号码」（需求 4.4）。"""
    if invoice_type == "数电票" or not invoice_code:
        return invoice_no
    return f"{invoice_code}-{invoice_no}"


def _coerce(field: str, value: str):
    if field in NUMERIC_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    if field == "invoice_date":
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    if field == "invoice_code":
        return value or None
    return value


def recognize_attachment(db: Session, task: OcrTask, image_bytes: bytes) -> Invoice:
    """执行识别 → 写入 invoice 表 → 回填 ocr_task 状态。"""
    started = datetime.now()
    task.status = "RUNNING"
    db.flush()

    try:
        provider = get_provider(settings.OCR_PROVIDER)
        result = provider.recognize(image_bytes)
    except Exception as exc:  # provider 失败不阻断上传，任务标记 FAILED
        task.status = "FAILED"
        task.error = str(exc)[:255]
        task.finished_at = datetime.now()
        db.commit()
        raise

    invoice = Invoice(
        attachment_id=task.attachment_id,
        reimbursement_id=None,
        ocr_confidence=result.confidence,
        need_confirm=1 if result.confidence < settings.OCR_CONFIDENCE_THRESHOLD else 0,
        occupy_state="FREE",
    )
    for field in FIELD_MAP:
        ocr_field = result.fields.get(field)
        if ocr_field is None:
            continue
        setattr(invoice, field, _coerce(field, ocr_field.value))

    invoice.dedup_key = dedup_key_of(
        invoice.invoice_type, invoice.invoice_code, invoice.invoice_no
    )
    db.add(invoice)
    db.flush()

    task.invoice_id = invoice.id
    task.status = "DONE"
    task.elapsed_ms = int((datetime.now() - started).total_seconds() * 1000)
    task.finished_at = datetime.now()
    db.commit()
    db.refresh(invoice)
    return invoice


def create_task(db: Session, attachment: Attachment) -> OcrTask:
    task = OcrTask(
        attachment_id=attachment.id, status="PENDING", provider=settings.OCR_PROVIDER
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def log_field_changes(
    db: Session,
    invoice: Invoice,
    changes: dict[str, tuple[str, str]],
    operator_id: int,
) -> None:
    """记录字段修改痕迹（需求 4.5）。"""
    for field, (old, new) in changes.items():
        db.add(
            FieldChangeLog(
                invoice_id=invoice.id,
                field_name=field,
                old_value=str(old)[:255],
                new_value=str(new)[:255],
                operator_id=operator_id,
            )
        )
