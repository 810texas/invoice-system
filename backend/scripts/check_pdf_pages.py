"""PDF 拆页自测：不需要人准备 PDF —— 脚本自己造。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_pdf_pages.py            # 离线：造 PDF → 拆页 → 断言
    .venv\\Scripts\\python.exe scripts\\check_pdf_pages.py --via-api  # 再走一遍真实上传接口（需后端已启动）

覆盖：
1. 1 页 / 3 页 PDF → 拆出对应张数的合法 PNG；
2. 超页数上限（MAX_PDF_PAGES+1 页）→ 明确报错；
3. 0 页 PDF、损坏字节、非 PDF 字节 → 明确报错而不是崩；
4. --via-api：把 3 页 PDF 传到真实接口，断言「3 页 → 3 条发票记录」。
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))  # backend/ → 可 import app.*
sys.path.insert(0, str(SCRIPT_DIR))  # 复用同目录的 e2e_check.Client

from app.core.config import get_settings  # noqa: E402
from app.services import pdf_pages  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

settings = get_settings()
PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def invoice_jpeg() -> tuple[bytes, tuple[int, int]]:
    """用现成的合成发票图（check_paddle_provider 里那张）转成 JPEG，当作 PDF 页内容。"""
    from PIL import Image
    from check_paddle_provider import make_invoice_png

    image = Image.open(io.BytesIO(make_invoice_png())).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue(), image.size


def build_pdf(pages: int, jpeg: bytes, size: tuple[int, int]) -> bytes:
    """用 pypdfium2 自己生成一份 pages 页的 PDF（每页铺同一张发票图）。

    注意：``load_jpeg()`` 只接受「文件路径或可读流」，直接给 bytes 会抛
    ValueError（而且会把整段字节打进报错信息里，很难看），所以这里用 BytesIO。
    """
    import pypdfium2 as pdfium

    width, height = size
    doc = pdfium.PdfDocument.new()
    keep_alive = []  # 防止 Python 侧对象被提前回收
    try:
        for _ in range(pages):
            image_obj = pdfium.PdfImage.new(doc)
            keep_alive.append(image_obj)
            image_obj.load_jpeg(io.BytesIO(jpeg))
            page = doc.new_page(width, height)
            keep_alive.append(page)
            page.insert_obj(image_obj)
            page.gen_content()

        buffer = io.BytesIO()
        doc.save(buffer)
        return buffer.getvalue()
    finally:
        doc.close()


def png_ok(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.width > 100 and img.height > 100
    except Exception:
        return False


def run_via_api(pdf_bytes: bytes, pages: int) -> int:
    """走真实上传接口：3 页 PDF 应当产生 3 条发票记录。"""
    import e2e_check

    client = e2e_check.Client()
    status, body = e2e_check.login(client, e2e_check.accounts.EMPLOYEE)
    if status != 200:
        print(f"[FAIL] 登录失败（后端没起？）：{status} {body}")
        return 2

    status, draft = client.post_json("/reimbursements", {"reason": "PDF 拆页验证"})
    rid = draft["id"]
    print(f"\n草稿单据：{draft['code']}（id={rid}）")

    status, up = client.upload(
        [("fapiao.pdf", pdf_bytes, "application/pdf")], {"reimbursement_id": str(rid)}
    )
    items = up.get("items") or []
    print(f"接口返回 {len(items)} 条记录，HTTP {status}")
    for item in items:
        inv = item.get("invoice") or {}
        print(
            f"  {item.get('origin_name')}  status={item.get('status')}  "
            f"发票号={inv.get('invoice_no')}  来源页数={item.get('source_pages')}"
        )

    check(f"一份 {pages} 页 PDF → {pages} 条发票记录", len(items) == pages, f"实际 {len(items)}")
    check(
        "每条记录都识别成功",
        all(i.get("status") == "DONE" for i in items),
        str([i.get("status") for i in items]),
    )
    check(
        "每页都识别出发票号码（说明页图确实被逐页送进了 OCR）",
        all((i.get("invoice") or {}).get("invoice_no") for i in items),
    )
    check(
        "来源文件名带页号",
        all("第" in (i.get("origin_name") or "") for i in items),
        str([i.get("origin_name") for i in items]),
    )
    return 0 if not FAILED else 1


def expect_error(name: str, data: bytes, keyword: str) -> None:
    try:
        pdf_pages.render_pages(data)
        check(name, False, "没有抛错")
    except pdf_pages.PdfError as exc:
        check(name, keyword in str(exc), f"报错文案：{str(exc)[:70]}")
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"抛了非 PdfError：{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--via-api", action="store_true", help="再走一遍真实上传接口")
    args = parser.parse_args()

    print("=== 1. 造 PDF 并拆页 ===")
    jpeg, size = invoice_jpeg()
    one_page = build_pdf(1, jpeg, size)
    three_page = build_pdf(3, jpeg, size)
    print(f"  1 页 PDF {len(one_page) / 1024:.0f} KB｜3 页 PDF {len(three_page) / 1024:.0f} KB")

    check("1 页 PDF → 页数 1", pdf_pages.page_count(one_page) == 1)
    check("3 页 PDF → 页数 3", pdf_pages.page_count(three_page) == 3)

    pages1 = pdf_pages.render_pages(one_page)
    pages3 = pdf_pages.render_pages(three_page)
    check("1 页 PDF 拆出 1 张图", len(pages1) == 1, f"{len(pages1)} 张")
    check("3 页 PDF 拆出 3 张图", len(pages3) == 3, f"{len(pages3)} 张")
    check("拆出来的是合法 PNG（尺寸正常）", all(png_ok(p) for p in pages3))

    from PIL import Image as _Image

    with _Image.open(io.BytesIO(pages1[0])) as rendered:
        rendered_size = rendered.size
    expected_size = (
        round(size[0] * settings.PDF_RENDER_SCALE),
        round(size[1] * settings.PDF_RENDER_SCALE),
    )
    check(
        "渲染尺寸 = 页尺寸 × PDF_RENDER_SCALE",
        abs(rendered_size[0] - expected_size[0]) <= 2 and abs(rendered_size[1] - expected_size[1]) <= 2,
        f"实际 {rendered_size}，期望 {expected_size}（scale={settings.PDF_RENDER_SCALE}）",
    )

    print("\n=== 2. 边界与报错 ===")
    expect_error("损坏的 PDF 字节 → 明确报错", b"%PDF-1.4 this is broken", "PDF 打开失败")
    expect_error("非 PDF 字节 → 明确报错", b"just a text file", "PDF 打开失败")
    over = build_pdf(settings.MAX_PDF_PAGES + 1, jpeg, size)
    expect_error(
        f"超过页数上限（{settings.MAX_PDF_PAGES}+1 页）→ 明确报错",
        over,
        "超过上限",
    )
    zero_page = build_pdf(0, jpeg, size)
    # 0 页 PDF 其实不是合法 PDF，PDFium 在打开阶段就会拒（代码里 total == 0 的分支是防御性兜底）
    expect_error("0 页 PDF → 明确报错", zero_page, "PDF 打开失败")

    if args.via_api:
        print("\n=== 3. 走真实上传接口 ===")
        run_via_api(three_page, 3)

    total = len(PASSED) + len(FAILED)
    print(f"\n===== 结果：{len(PASSED)}/{total} 通过 =====")
    if FAILED:
        print("失败项：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
