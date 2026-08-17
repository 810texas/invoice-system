"""OCR 抽象与 provider 注册：mock（默认）/ paddle（本地部署，需自装依赖）。

paddle provider 说明（需求 2.1 / 5.2）：
- 模型与推理**全部在本机**，发票影像不外发；
- 引擎**懒加载**（首次识别时才初始化）：因为下方 ``_REGISTRY`` 会在 import 时实例化
  provider，构造函数里加载模型会让「mock 模式」也被迫加载模型与重依赖；
- 字段抽取逻辑独立在 ``invoice_fields.py``（纯标准库，可离线单测）。
"""
from __future__ import annotations

import hashlib
import random
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

from app.core.config import get_settings
from app.services.ocr.invoice_fields import (
    FIELD_NAMES,
    INVOICE_TYPE_ESIGN,
    TextLine,
    extract_invoice_fields,
)

settings = get_settings()


@dataclass
class OcrField:
    """单个识别字段：值 + 置信度。"""

    value: str = ""
    confidence: float = 0.0


@dataclass
class OcrResult:
    """识别结果：字段字典 + 整体置信度 + provider 标识。"""

    fields: dict[str, OcrField] = field(default_factory=dict)
    confidence: float = 0.0
    provider: str = "mock"
    raw_text: str = ""


class OcrProvider(Protocol):
    name: str

    def recognize(self, image_bytes: bytes, hint: str = "") -> OcrResult: ...


class MockOcrProvider:
    """开发期 provider：由文件哈希派生稳定结果，无需模型与网络（需求 2.2）。"""

    name = "mock"

    SELLERS = ["杭州云启网络科技有限公司", "上海远景办公用品有限公司", "北京中科出行服务有限公司"]
    ITEMS = ["技术服务费", "办公用品", "差旅住宿费", "城市交通费", "会议餐饮费"]

    def recognize(self, image_bytes: bytes, hint: str = "") -> OcrResult:
        seed = int(hashlib.sha256(image_bytes).hexdigest()[:12], 16)
        rnd = random.Random(seed)

        invoice_type = rnd.choices(["蓝字", "红字", "数电票"], weights=[60, 10, 30])[0]
        invoice_no = str(rnd.randint(10_000_000, 99_999_999))
        # 数电票无发票代码（需求 4.2）
        invoice_code = None if invoice_type == "数电票" else str(rnd.randint(0, 9999)).zfill(10)

        amount_excl = round(rnd.uniform(30, 2000), 2)
        tax = round(amount_excl * rnd.choice([0.03, 0.06, 0.09, 0.13]), 2)
        total = round(amount_excl + tax, 2)
        if invoice_type == "红字":
            amount_excl, tax, total = -amount_excl, -tax, -total

        invoice_day = date.today() - timedelta(days=rnd.randint(0, 45))

        def f(value: str, low: float, high: float) -> OcrField:
            return OcrField(value=value, confidence=round(rnd.uniform(low, high), 2))

        fields = {
            "invoice_type": f(invoice_type, 0.90, 0.99),
            "invoice_code": f(invoice_code or "", 0.88, 0.99),
            "invoice_no": f(invoice_no, 0.90, 0.99),
            "invoice_date": f(invoice_day.isoformat(), 0.88, 0.99),
            "buyer_name": f("示例科技有限公司", 0.86, 0.99),
            "buyer_tax_no": f("91330100MA2XXXXXXX", 0.85, 0.98),
            "seller_name": f(rnd.choice(self.SELLERS), 0.85, 0.99),
            "amount_excl_tax": f(f"{amount_excl:.2f}", 0.83, 0.99),
            "tax_amount": f(f"{tax:.2f}", 0.83, 0.99),
            "total_amount": f(f"{total:.2f}", 0.83, 0.99),
            "item_name": f(rnd.choice(self.ITEMS), 0.80, 0.98),
        }
        overall = round(sum(x.confidence for x in fields.values()) / len(fields), 2)
        text = "\n".join(f"{k}: {v.value}" for k, v in fields.items())
        return OcrResult(fields=fields, confidence=overall, provider=self.name, raw_text=text)


def _decode_image(image_bytes: bytes) -> Any:
    """发票影像 bytes → BGR ndarray（cv2 由 paddleocr 的传递依赖提供，惰性导入）。

    解码失败属于「整张图都用不了」，与「单个字段没命中」不同，这里明确抛错，
    会被 service 层记成 ocr_task.FAILED 并把原因回传前端。
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "缺少图像解码依赖（opencv-python / numpy）。请执行："
            ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("发票影像解码失败：文件可能已损坏，或不是有效的 JPG/PNG")
    return image


def _rec_from_page(page: Any) -> tuple[Any, Any]:
    """取 3.x 结果对象里的 rec_texts / rec_scores；取不到返回 (None, None)。"""
    getter = getattr(page, "get", None)
    if not callable(getter):
        return None, None
    try:
        return getter("rec_texts"), getter("rec_scores")
    except Exception:
        return None, None


def _flatten_ocr_lines(raw: Any) -> list[TextLine]:
    """把 PaddleOCR 的返回展开成 [(text, confidence)]。

    - 2.x：``[[ [box, (text, score)], ... ]]``（每个元素是一张图的结果）
    - 3.x：``[ <dict-like: rec_texts/rec_scores> ]``
    """
    lines: list[TextLine] = []
    for page in raw or []:
        texts, scores = _rec_from_page(page)
        if texts is not None:
            scores = scores if scores is not None else []
            for index, text in enumerate(texts):
                score = float(scores[index]) if index < len(scores) else 0.0
                lines.append(TextLine(text=str(text), confidence=score))
            continue

        for item in page or []:
            try:
                text, score = item[1][0], item[1][1]
            except (TypeError, IndexError, KeyError):
                continue
            lines.append(TextLine(text=str(text), confidence=float(score)))
    return lines


class PaddleOcrProvider:
    """本地 PaddleOCR provider（需求 2.1）：CPU 推理，影像与结果都不出本机。

    - 引擎懒加载 + 进程内单例：首次识别时初始化，之后复用；
    - 识别调用加锁串行化：PaddleOCR 预测器在多线程下不稳定，而 FastAPI 的同步
      端点跑在线程池里，不加锁会有并发隐患。将来按需求 5.1 改成 Worker 进程池后，
      这里的锁可以去掉。
    """

    name = "paddle"

    def __init__(self) -> None:
        # 构造函数必须保持轻量：_REGISTRY 在 import 时就会实例化本类
        self._engine: Any = None
        self._engine_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    # ---------------------------------------------------------------- 引擎
    def _build_engine(self) -> Any:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "未安装 PaddleOCR。请在 backend 目录执行："
                ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"
                '（或 pip install "paddleocr>=2.7,<3.0" "paddlepaddle>=2.6,<3.0"）'
            ) from exc

        base: dict[str, Any] = {
            "use_angle_cls": True,
            "lang": settings.PADDLE_OCR_LANG,
            "show_log": settings.PADDLE_OCR_SHOW_LOG,
        }
        if settings.PADDLE_OCR_USE_GPU:
            base["use_gpu"] = True
        # 离线部署：填了模型目录就不联网下载
        for key, value in (
            ("det_model_dir", settings.PADDLE_OCR_DET_MODEL_DIR),
            ("rec_model_dir", settings.PADDLE_OCR_REC_MODEL_DIR),
            ("cls_model_dir", settings.PADDLE_OCR_CLS_MODEL_DIR),
        ):
            if value:
                base[key] = value

        # 万一装成了 3.x：参数名变了（use_angle_cls → use_textline_orientation），给一次机会
        drop_for_v3 = ("use_angle_cls", "use_gpu", "show_log")
        fallback = {k: v for k, v in base.items() if k not in drop_for_v3}
        fallback["use_textline_orientation"] = True
        if settings.PADDLE_OCR_USE_GPU:
            fallback["device"] = "gpu"

        last_error: Exception | None = None
        for kwargs in (base, fallback):
            try:
                return PaddleOCR(**kwargs)
            except (TypeError, ValueError) as exc:
                # 参数不被接受：换下一组参数再试
                last_error = exc
            except Exception as exc:  # 模型下载失败 / 目录不存在 / 权重损坏等
                raise RuntimeError(
                    "PaddleOCR 初始化失败：常见原因是模型未下载或网络不可达。"
                    "请先联网触发一次模型下载，或把已有模型目录填入 "
                    "PADDLE_OCR_DET_MODEL_DIR / PADDLE_OCR_REC_MODEL_DIR / PADDLE_OCR_CLS_MODEL_DIR。"
                    f"原始错误：{exc}"
                ) from exc

        raise RuntimeError(
            "PaddleOCR 初始化失败：初始化参数不被接受，通常是把 3.x 装上了。"
            '请改装 2.x：pip install "paddleocr>=2.7,<3.0" "paddlepaddle>=2.6,<3.0"。'
            f"原始错误：{last_error}"
        )

    def _get_engine(self) -> Any:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = self._build_engine()
        return self._engine

    # ---------------------------------------------------------------- 识别
    def _infer(self, engine: Any, image: Any) -> Any:
        """调用引擎。2.x 用 ``.ocr(image, cls=True)``；3.x 退化为 ``.predict(image)``。"""
        ocr = getattr(engine, "ocr", None)
        if callable(ocr):
            try:
                return ocr(image, cls=True)
            except TypeError:
                return ocr(image)
        return engine.predict(image)

    def recognize(self, image_bytes: bytes, hint: str = "") -> OcrResult:
        """识别一张发票影像。hint 保留以维持 provider 接口，当前未使用。"""
        engine = self._get_engine()
        image = _decode_image(image_bytes)

        with self._infer_lock:
            raw = self._infer(engine, image)

        lines = _flatten_ocr_lines(raw)
        hits = extract_invoice_fields(lines)

        # 边界转换：字段没命中时提取层给的是 None，但 service 层会跳过「缺失的键」，
        # 而 invoice_no 是非空列 —— 所以这里统一落成空串（0 置信度），
        # 让发票仍能入库并标记「待人工确认」，而不是 INSERT NULL 报 500。
        fields = {
            name: OcrField(
                value=hit.value if hit.value is not None else "",
                confidence=float(hit.confidence) if hit.confidence is not None else 0.0,
            )
            for name, hit in hits.items()
        }

        # 整体置信度：只在「OCR 本该产出」的字段上取平均（缺失按 0 计，缺失越多越低，
        # service 层据此置 need_confirm=1）。两类字段要从分母里剔除，否则会被冤枉：
        #   - invoice_type：由票面关键词/号码位数推导，本来就没有 OCR 分数；
        #   - 数电票的 invoice_code：它天生没有发票代码，不该因「本来不存在」拉低置信度。
        is_esign = hits["invoice_type"].value == INVOICE_TYPE_ESIGN
        applicable = [
            name
            for name in FIELD_NAMES
            if name != "invoice_type" and not (name == "invoice_code" and is_esign)
        ]
        found = [hits[name].confidence for name in applicable if hits[name].confidence is not None]
        overall = round(sum(found) / len(applicable), 2) if found else 0.0

        return OcrResult(
            fields=fields,
            confidence=overall,
            provider=self.name,
            raw_text="\n".join(line.text for line in lines),
        )


_REGISTRY: dict[str, OcrProvider] = {
    "mock": MockOcrProvider(),
    "paddle": PaddleOcrProvider(),
}


def get_provider(name: str) -> OcrProvider:
    provider = _REGISTRY.get(name)
    if provider is None:
        raise ValueError(f"未知 OCR provider：{name}（可选 {'/'.join(_REGISTRY)}）")
    return provider
