"""OCR 服务包：provider 抽象 + 任务执行与留痕。"""
from app.services.ocr.provider import OcrField, OcrResult, get_provider
from app.services.ocr.service import (
    FIELD_MAP,
    create_task,
    dedup_key_of,
    log_field_changes,
    recognize_attachment,
)

__all__ = [
    "FIELD_MAP",
    "OcrField",
    "OcrResult",
    "create_task",
    "dedup_key_of",
    "get_provider",
    "log_field_changes",
    "recognize_attachment",
]
