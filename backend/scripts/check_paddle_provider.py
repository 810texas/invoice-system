"""真机冒烟：合成一张「发票样式」图 → 走真实 PaddleOCR → 校验 10 个字段。

用法（在 backend 目录下；需要先 pip install -r requirements.txt）：
    .venv\\Scripts\\python.exe scripts\\check_paddle_provider.py
    .venv\\Scripts\\python.exe scripts\\check_paddle_provider.py --image 我的发票.png
    .venv\\Scripts\\python.exe scripts\\check_paddle_provider.py --via-api     # 走真实上传接口

为什么能自己造图：脚本用本机中文字体把票面文字画成 PNG 再交给引擎识别，
所以不需要你提供真实发票，也能验证「引擎初始化 → 图片解码 → OCR → 字段提取」整条链路。
带 --image 时改成识别你给的真实票样（此时只校验链路不报错 + 字段齐全，不比对具体值）。
带 --via-api 时直接调后端上传接口（需后端已启动，且 OCR_PROVIDER=paddle），
验证「上传 → 识别 → 落库回显」整条业务链路。
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))  # backend/ → 可 import app.*
sys.path.insert(0, str(SCRIPT_DIR))  # 复用同目录的 e2e_check.Client

from app.services.ocr.invoice_fields import FIELD_NAMES  # noqa: E402
from app.services.ocr.provider import PaddleOcrProvider  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 合成票面：与 check_ocr_fields.py 的样例 1 对齐，便于对比「纯提取」与「真机识别」的差异
INVOICE_LINES: list[tuple[str, int]] = [
    ("浙江增值税专用发票", 34),
    ("发票代码 044001900111", 26),
    ("发票号码 12345678", 26),
    ("开票日期 2026年09月24日", 26),
    ("购买方信息", 26),
    ("名称: 示例科技有限公司", 26),
    ("纳税人识别号: 91330100MA2XXXXXXX", 24),
    ("销售方信息", 26),
    ("名称: 杭州云启网络科技有限公司", 26),
    ("纳税人识别号: 91330100123456789X", 24),
    ("金额 838.57", 26),
    ("税额 50.31", 26),
    ("价税合计(小写) ¥888.88", 26),
]

EXPECTED = {
    "invoice_type": "蓝字",
    "invoice_code": "044001900111",
    "invoice_no": "12345678",
    "invoice_date": "2026-09-24",
    "buyer_name": "示例科技有限公司",
    "buyer_tax_no": "91330100MA2XXXXXXX",
    "seller_name": "杭州云启网络科技有限公司",
    "amount_excl_tax": "838.57",
    "tax_amount": "50.31",
    "total_amount": "888.88",
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]


def _font_path() -> str:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    raise RuntimeError(f"找不到可用字体，请把字体路径加到 FONT_CANDIDATES：{FONT_CANDIDATES}")


def make_invoice_png() -> bytes:
    """画一张白底黑字的「发票样式」PNG（1200×1400，字号够大便于识别）。"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = _font_path()
    image = Image.new("RGB", (1200, 1500), "white")
    draw = ImageDraw.Draw(image)

    y = 60
    for text, size in INVOICE_LINES:
        draw.text((60, y), text, fill="black", font=ImageFont.truetype(font_path, size))
        y += size + 34

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _as_text(value: object) -> str:
    """接口回的金额是 float、空字段是 None，统一成与 EXPECTED 同口径的字符串再比。"""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def run_via_api(png_bytes: bytes) -> int:
    """走真实上传接口：登录 → 建草稿 → 上传 → 看服务端回显的字段。"""
    import e2e_check  # 同目录，复用现成的 Cookie/CSRF 客户端

    client = e2e_check.Client()
    status, body = e2e_check.login(client, e2e_check.accounts.EMPLOYEE)
    if status != 200:
        print(f"[FAIL] 登录失败（后端没起？默认 127.0.0.1:8000）：{status} {body}")
        return 2

    status, draft = client.post_json("/reimbursements", {"reason": "PaddleOCR 真机验证"})
    if status != 200:
        print(f"[FAIL] 建草稿失败：{status} {draft}")
        return 2
    rid = draft["id"]
    print(f"草稿单据：{draft['code']}（id={rid}）")

    started = time.time()
    status, up = client.upload(
        [("fapiao_paddle.png", png_bytes, "image/png")], {"reimbursement_id": str(rid)}
    )
    elapsed_ms = int((time.time() - started) * 1000)
    item = (up.get("items") or [{}])[0]
    print(f"上传接口耗时（含服务端识别）：{elapsed_ms} ms")
    print(f"服务端返回 status={item.get('status')} provider={item.get('provider')} "
          f"elapsed_ms={item.get('elapsed_ms')}")

    if item.get("status") != "DONE":
        print(f"[FAIL] 识别失败：{item.get('error')}")
        return 1

    invoice = item.get("invoice") or {}
    print("\n--- 服务端落库并回显的发票字段 ---")
    for name in FIELD_NAMES:
        print(f"  {name:<16} = {invoice.get(name)!r}")
    print(f"  整体置信度 ocr_confidence = {invoice.get('ocr_confidence')} "
          f"｜ need_confirm = {invoice.get('need_confirm')}")

    matched = sum(1 for k, v in EXPECTED.items() if _as_text(invoice.get(k)) == v)
    print(f"\n[结果] 与期望一致 {matched}/{len(EXPECTED)} 个字段")
    if matched >= 6:
        print("[PASS] 上传接口已返回真实 OCR 结果（引擎 → 提取 → 落库 → 接口回显）")
        return 0
    print("[FAIL] 命中过少，请检查 OCR_PROVIDER 是否已切到 paddle")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="用真实发票图片代替合成图")
    parser.add_argument(
        "--via-api", action="store_true", help="走真实上传接口（需后端已启动且 OCR_PROVIDER=paddle）"
    )
    args = parser.parse_args()

    try:
        from paddleocr import __version__ as ocr_version  # noqa: F401
    except Exception:
        print("未安装 paddleocr：请先执行 .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        return 2

    if args.image:
        path = Path(args.image)
        if not path.exists():
            print(f"图片不存在：{path}")
            return 2
        data = path.read_bytes()
        print(f"识别真实图片：{path}（{len(data) / 1024:.0f} KB）")
        expected: dict[str, str] = {}
    else:
        try:
            data = make_invoice_png()
        except Exception as exc:  # noqa: BLE001
            print(f"合成图片失败（多为缺 Pillow 或中文字体）：{exc}")
            return 2
        print(f"识别合成图：{len(data) / 1024:.0f} KB")
        expected = EXPECTED

    if args.via_api:
        if args.image:
            print("--via-api 目前只用合成图，忽略 --image")
        return run_via_api(make_invoice_png())

    print("\n=== 识别（首次运行会下载检测/识别/方向分类三个模型，可能耗时较久）===")
    provider = PaddleOcrProvider()
    started = time.time()
    try:
        result = provider.recognize(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 识别失败：{type(exc).__name__}: {exc}")
        return 1
    elapsed_ms = int((time.time() - started) * 1000)

    print(f"引擎：{result.provider}｜整体置信度：{result.confidence}｜端到端耗时：{elapsed_ms} ms")
    print("\n--- OCR 原始文本行 ---")
    for line in result.raw_text.splitlines():
        print("  " + line)

    print("\n--- 提取到的 10 个字段 ---")
    missing_keys = [name for name in FIELD_NAMES if name not in result.fields]
    matched = 0
    for name in FIELD_NAMES:
        field = result.fields.get(name)
        value = field.value if field else "<缺失>"
        conf = f"{field.confidence:.2f}" if field else "-"
        mark = ""
        if expected and name in expected:
            ok = value == expected[name]
            matched += int(ok)
            mark = "  ✅" if ok else f"  ❌ 期望 {expected[name]!r}"
        print(f"  {name:<16} = {value!r:<32} 置信度 {conf}{mark}")

    print()
    if missing_keys:
        print(f"[FAIL] 返回里缺少字段：{missing_keys}（service 层会因此写入空值）")
        return 1
    if expected:
        print(f"[结果] 真机识别命中期望值 {matched}/{len(expected)} 个字段")
        if matched < 6:
            print("[FAIL] 命中过少：合成图很干净，通常应命中 8 个以上；请检查正则与版式假设")
            return 1
        if matched == len(expected):
            print("[PASS] 全链路正常：引擎 → 解码 → OCR → 字段提取，10 个字段全部正确")
        else:
            print("[PASS] 全链路可用（部分字段与期望不一致，属可接受的正则迭代空间）")
        return 0

    non_empty = [n for n in FIELD_NAMES if result.fields[n].value]
    print(f"[结果] 实测非空字段 {len(non_empty)}/10：{non_empty}")
    print("[PASS] 真实图片识别链路跑通（不比对具体值）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
