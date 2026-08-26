"""发票上传与 OCR 任务查询（需求 3.1：批量上传 + 自动识别）。"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, guard, guard_read
from app.core.database import get_db
from app.core.permissions import INVOICE_UPLOAD
from app.core.security import file_sha256
from app.models.document import Attachment, Invoice, OcrTask
from app.models.user import User
from app.services import ocr as ocr_service
from app.services import pdf_pages
from app.services import reimbursement as reimb_service
from app.services import storage

router = APIRouter()

MAX_BATCH = 5


def invoice_dict(inv: Invoice) -> dict:
    return {
        "id": inv.id,
        "invoice_type": inv.invoice_type,
        "invoice_code": inv.invoice_code,
        "invoice_no": inv.invoice_no,
        "invoice_date": inv.invoice_date,
        "buyer_name": inv.buyer_name,
        "buyer_tax_no": inv.buyer_tax_no,
        "seller_name": inv.seller_name,
        "amount_excl_tax": float(inv.amount_excl_tax or 0),
        "tax_amount": float(inv.tax_amount or 0),
        "total_amount": float(inv.total_amount or 0),
        "category": inv.category,
        "ocr_confidence": float(inv.ocr_confidence or 0),
        "need_confirm": bool(inv.need_confirm),
        "occupy_state": inv.occupy_state,
        "attachment_id": inv.attachment_id,
        "reimbursement_id": inv.reimbursement_id,
    }


@router.post("/invoices/upload")
async def upload(
    files: list[UploadFile] = File(...),
    reimbursement_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(guard(INVOICE_UPLOAD)),
):
    """批量上传 → 校验 → 落盘 → 入队识别 → 返回字段与置信度。"""
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")
    if len(files) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {MAX_BATCH} 张")

    reimb = None
    if reimbursement_id:
        reimb = reimb_service.get_owned(db, reimbursement_id, user)
        if reimb.status != "DRAFT":
            raise HTTPException(status_code=400, detail="仅草稿状态可上传发票")

    results = []
    for file in files:
        data = await file.read()
        ext = storage.validate_and_read(file, data)
        origin = file.filename or "upload"

        # PDF 先拆成逐页 PNG，每页当一张发票处理（结构不变，前端预览照旧是图片）
        if ext == "pdf":
            try:
                page_images = pdf_pages.render_pages(data)
            except pdf_pages.PdfError as exc:
                results.append(
                    {"origin_name": origin, "status": "FAILED", "error": str(exc)}
                )
                continue
            jobs = [
                (f"{origin} 第 {index + 1} 页", "image/png", page_data)
                for index, page_data in enumerate(page_images)
            ]
            total_pages = len(jobs)
        else:
            jobs = [(origin, file.content_type or "application/octet-stream", data)]
            total_pages = 1

        for origin_name, content_type, page_data in jobs:
            stored = storage.save_bytes(page_data, "png" if ext == "pdf" else ext)
            attachment = Attachment(
                stored_name=stored,
                origin_name=origin_name,
                content_type=content_type,
                size_bytes=len(page_data),
                sha256=file_sha256(page_data),
                uploader_id=user.id,
            )
            db.add(attachment)
            db.commit()
            db.refresh(attachment)

            task = ocr_service.create_task(db, attachment)
            try:
                invoice = ocr_service.recognize_attachment(db, task, page_data)
            except Exception as exc:
                results.append(
                    {
                        "attachment_id": attachment.id,
                        "origin_name": origin_name,
                        "status": "FAILED",
                        "error": str(exc),
                    }
                )
                continue

            if reimb is not None:
                reimb_service.attach_invoice(db, reimb, invoice)
                db.commit()

            results.append(
                {
                    "attachment_id": attachment.id,
                    "origin_name": origin_name,
                    "source_pages": total_pages,
                    "status": "DONE",
                    "task_id": task.id,
                    "elapsed_ms": task.elapsed_ms,
                    "provider": task.provider,
                    "invoice": invoice_dict(invoice),
                }
            )

    if reimb is not None:
        reimb_service.recalc(db, reimb)
        db.commit()

    return {"items": results}


@router.get("/invoices/{invoice_id}")
def invoice_detail(
    invoice_id: int, db: Session = Depends(get_db), user: User = Depends(guard_read(INVOICE_UPLOAD))
):
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="发票不存在")
    if inv.reimbursement_id:
        reimb_service.get_owned(db, inv.reimbursement_id, user)
    elif inv.attachment.uploader_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该发票")
    return invoice_dict(inv)


@router.get("/attachments/{attachment_id}/raw")
def attachment_raw(
    attachment_id: int,
    thumb: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发票影像：本人附件或财务/管理员可看，其他人 403（需求 5.2 数据边界）。

    带 ``?thumb=300`` 时返回降采样后的 JPEG 缩略图（列表页用），
    不带则返回原图（点击放大的预览用）。
    """
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    # 数据范围判定：本人 / （按 ROLE_DATA_SCOPE）可跨用户查看的角色。
    # 原来是 is_finance_like() 直接判角色名，现在走统一的 scope 入口。
    if not reimb_service.can_access_owner(db, user, attachment.uploader_id):
        raise HTTPException(status_code=403, detail="无权访问该发票影像")
    path = storage.path_of(attachment.stored_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="影像文件缺失")

    if thumb:
        try:
            data = storage.thumbnail_bytes(path, attachment.stored_name, thumb)
        except Exception:
            # 缩略图生成失败（缺 Pillow、图片损坏等）不应让列表页看不到图，回退原图
            return FileResponse(path, media_type=attachment.content_type)
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    return FileResponse(path, media_type=attachment.content_type)


@router.get("/ocr/tasks")
def tasks(db: Session = Depends(get_db), user: User = Depends(guard_read(INVOICE_UPLOAD))):
    """OCR 任务进度：前端轮询排队/识别状态（需求 3.1）。"""
    rows = db.execute(
        select(OcrTask).order_by(OcrTask.id.desc()).limit(50)
    ).scalars().all()
    return [
        {
            "id": t.id,
            "attachment_id": t.attachment_id,
            "invoice_id": t.invoice_id,
            "status": t.status,
            "provider": t.provider,
            "elapsed_ms": t.elapsed_ms,
            "error": t.error,
            "created_at": t.created_at,
        }
        for t in rows
    ]
