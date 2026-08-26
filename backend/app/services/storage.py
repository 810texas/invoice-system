"""上传落盘：扩展名 + magic bytes 双重校验、UUID 重命名（需求 5.2）。"""
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import get_settings

settings = get_settings()

# 支持 JPG / PNG / PDF（PDF 会在 upload 接口里拆成逐页 PNG，见 services/pdf_pages.py）
MAGIC_SIGNATURES = {
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "pdf": [b"%PDF-"],
}
ALLOWED_EXT = set(MAGIC_SIGNATURES)


def upload_root() -> Path:
    root = Path(settings.UPLOAD_DIR)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_and_read(file: UploadFile, data: bytes) -> str:
    """返回规范化扩展名；不合法直接 400 并给出明确原因（验收标准：上传校验）。"""
    name = file.filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400, detail=f"仅支持 JPG/PNG/PDF 格式，当前文件扩展名为 .{ext or '未知'}"
        )

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小 {len(data) / 1024 / 1024:.1f}MB 超过 {settings.MAX_UPLOAD_MB}MB 限制",
        )
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")

    # 扩展名与真实内容双重校验，拦截伪装扩展名
    if not any(data.startswith(sig) for sig in MAGIC_SIGNATURES[ext]):
        raise HTTPException(
            status_code=400, detail="文件真实内容与扩展名不符（magic bytes 校验失败）"
        )
    return "jpg" if ext == "jpeg" else ext


def save_bytes(data: bytes, ext: str) -> str:
    """UUID 重命名后落盘，返回存储文件名。"""
    stored = f"{uuid.uuid4().hex}.{ext}"
    (upload_root() / stored).write_bytes(data)
    return stored


def path_of(stored_name: str) -> Path:
    return upload_root() / stored_name


# 缩略图：列表/审核页里的缩略图只有 40–44px，没必要把 10MB 的原图整张传给浏览器。
# 上传文件是 UUID 命名、写入后不再改动，所以缩略图可以安全地按边长缓存到磁盘，无需失效逻辑。
THUMB_MIN_EDGE = 48
THUMB_MAX_EDGE_LIMIT = 1024


def clamp_thumb_edge(edge: int) -> int:
    return max(THUMB_MIN_EDGE, min(int(edge), THUMB_MAX_EDGE_LIMIT))


def thumbnail_bytes(path: Path, stored_name: str, edge: int) -> bytes:
    """生成（或读取已缓存的）JPEG 缩略图。

    Pillow 在这里按需导入：与项目里 OCR provider 的做法一致，
    避免把重依赖挂在模块导入路径上。
    """
    edge = clamp_thumb_edge(edge)
    cache = upload_root() / f"{stored_name}.thumb{edge}.jpg"
    if cache.exists():
        return cache.read_bytes()

    from io import BytesIO

    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((edge, edge))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
    data = buf.getvalue()
    try:
        cache.write_bytes(data)
    except OSError:
        pass  # 缓存写不进去不影响本次返回
    return data
