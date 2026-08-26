"""PDF 发票拆页：把上传的 PDF 渲染成逐页 PNG，交给现有 OCR 链路。

设计取舍（为什么不是直接把 PDF 存下来再识别）：
- 前端预览是 ``<img src="/api/v1/attachments/{id}/raw">``，``<img>`` 显示不了 PDF；
- 拆成图片后，attachment / invoice 的结构与图片发票**完全一致 → 零数据库变更**；
- 多页 PDF 天然变成多条发票记录，人工逐页确认即可。

引擎选 pypdfium2：自带 wheel、**不需要 poppler 之类的系统二进制**，许可是 BSD-3/Apache-2.0
（刻意避开 AGPL 的 PyMuPDF 与需要外部二进制的 pdf2image）。
"""
from __future__ import annotations

import io
from typing import Any

from app.core.config import get_settings

settings = get_settings()


class PdfError(RuntimeError):
    """PDF 无法处理（损坏 / 加密 / 页数超限等）；文案会直接回传前端。"""


def _load(data: bytes) -> Any:
    """打开 PDF；依赖缺失或文件损坏都转成带排查建议的 PdfError。"""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise PdfError(
            "未安装 pypdfium2，无法处理 PDF。请执行："
            ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    try:
        return pdfium.PdfDocument(data)
    except Exception as exc:
        raise PdfError(
            "PDF 打开失败：文件可能已损坏、不是有效的 PDF，或设了打开密码。"
            f"原始错误：{exc}"
        ) from exc


def page_count(data: bytes) -> int:
    """返回页数（供调用方预估耗时/提示用户）。"""
    doc = _load(data)
    try:
        return len(doc)
    finally:
        doc.close()


def render_pages(data: bytes) -> list[bytes]:
    """把 PDF 逐页渲染成 PNG 字节列表；超页数上限或渲染失败都抛 PdfError。"""
    doc = _load(data)
    try:
        total = len(doc)
        if total == 0:
            raise PdfError("PDF 里没有任何页面，无法识别")

        limit = settings.MAX_PDF_PAGES
        if limit and total > limit:
            raise PdfError(
                f"PDF 共 {total} 页，超过上限 {limit} 页。请拆分后上传，"
                f"或调大 .env 的 MAX_PDF_PAGES"
                f"（注意 OCR 目前是同步识别，页数越多等待越久）"
            )

        pages: list[bytes] = []
        for index in range(total):
            try:
                page = doc.get_page(index)
                try:
                    bitmap = page.render(scale=settings.PDF_RENDER_SCALE)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            except Exception as exc:
                raise PdfError(f"PDF 第 {index + 1} 页渲染失败：{exc}") from exc

            try:
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                pages.append(buffer.getvalue())
            except Exception as exc:  # 多为缺 Pillow
                raise PdfError(
                    f"PDF 第 {index + 1} 页转 PNG 失败（多为缺少 Pillow）：{exc}"
                ) from exc
            finally:
                image.close()
        return pages
    finally:
        doc.close()
