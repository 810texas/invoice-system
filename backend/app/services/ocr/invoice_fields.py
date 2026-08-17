"""发票字段提取层：把 PaddleOCR 的「文本行」转成 10 个结构化字段。

设计要点
--------
1. **纯标准库**（只用 ``re`` / ``datetime``）：不依赖 paddleocr / numpy / cv2，
   因此可以完全离线单测（见 ``scripts/check_ocr_fields.py``），也方便后续换 OCR 引擎时复用。
2. **关键词定位 + 正则** 两步走：先用关键词把票面切成「购买方区 / 销售方区」，
   再在区块内套正则，避免「名称」这类重复标签互相串味。
3. **命中不了就返回 None，绝不抛异常**：字段缺失由上层（provider）兜底成空串，
   这样上传链路只会得到一张「待人工确认」的发票，而不是 500。
4. 关键词与正则全部是模块级常量，**改这里就能调识别口径**，不必动引擎代码。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Sequence

# 提取层输出的字段名，必须与 app.services.ocr.service.FIELD_MAP 一致
# （scripts/check_ocr_fields.py 会断言两者相等，防止两边漂移）
FIELD_NAMES: tuple[str, ...] = (
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
)

AMOUNT_FIELDS: tuple[str, ...] = ("amount_excl_tax", "tax_amount", "total_amount")

# 与 models/document.py 的 INVOICE_TYPES 保持一致
INVOICE_TYPE_BLUE = "蓝字"
INVOICE_TYPE_RED = "红字"
INVOICE_TYPE_ESIGN = "数电票"


@dataclass(frozen=True)
class TextLine:
    """一行 OCR 文本：文本 + PaddleOCR 给出的置信度。"""

    text: str
    confidence: float = 0.0


@dataclass(frozen=True)
class Extracted:
    """单个字段的提取结果；没命中时 value / confidence 均为 None。"""

    value: str | None = None
    confidence: float | None = None
    source: str = ""  # 命中的原始文本行，排查用


# --------------------------------------------------------------------------
# 关键词表（调口径改这里）
# --------------------------------------------------------------------------

# 发票类型：按优先级从上到下判定
TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (INVOICE_TYPE_RED, ("红字", "红字发票", "负数发票")),
    (INVOICE_TYPE_ESIGN, ("数电票", "全电发票", "电子发票(数电票)")),
    (
        INVOICE_TYPE_BLUE,
        ("增值税专用发票", "专用发票", "增值税普通发票", "电子普通发票", "普通发票", "电子发票"),
    ),
)

# 区块分隔词：用于把「购买方信息」和「销售方信息」两段分开
BUYER_SECTION_KEYWORDS: tuple[str, ...] = ("购买方", "购方", "买方")
SELLER_SECTION_KEYWORDS: tuple[str, ...] = ("销售方", "销方", "卖方")

NAME_LABELS: tuple[str, ...] = ("名称", "名 称")
TAX_NO_LABELS: tuple[str, ...] = (
    "纳税人识别号",
    "统一社会信用代码",
    "纳税人识别号码",
    "税号",
)

EXCL_TAX_LABELS: tuple[str, ...] = (
    "不含税金额",
    "金额合计",
    "合计金额",
    "不含税价",
    "金额",
)
TAX_AMOUNT_LABELS: tuple[str, ...] = ("税额合计", "增值税额", "税额")
TOTAL_LABELS: tuple[str, ...] = ("价税合计", "小写")

# 公司名兜底：命中这些后缀却没有「名称」标签时也认
COMPANY_SUFFIXES: tuple[str, ...] = (
    "有限公司",
    "有限责任公司",
    "股份有限公司",
    "公司",
    "厂",
    "店",
    "中心",
    "事务所",
    "银行",
    "酒店",
    "宾馆",
    "超市",
)

_NUM_TOKEN = r"([-+]?[¥￥]?\s*[-+]?\d[\d,]*(?:\.\d{1,2})?)"


def _label_regex(labels: Sequence[str], *, gap: int = 24) -> re.Pattern[str]:
    """生成「标签 + 最多 gap 个非数字字符 + 数字」的正则。

    间隔字符里 **排除数字、货币符号与正负号**，否则 ``价税合计(小写)-¥212.00``
    的前导负号会被间隔吃掉，金额符号就丢了。
    """
    alt = "|".join(re.escape(x) for x in labels)
    return re.compile(r"(?:%s)[^0-9¥￥+\-]{0,%d}%s" % (alt, gap, _NUM_TOKEN))


INVOICE_CODE_RE = re.compile(r"发票代码[^0-9]{0,8}(\d{10,12})")
INVOICE_NO_RE = re.compile(r"发票号码[^0-9]{0,8}(\d{8,20})")
DATE_RE = re.compile(r"(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?")
TAX_NO_VALUE_RE = re.compile(r"([0-9A-Z]{15,20})")

EXCL_TAX_RE = _label_regex(EXCL_TAX_LABELS)
TAX_AMOUNT_RE = _label_regex(TAX_AMOUNT_LABELS)
TOTAL_RE = _label_regex(TOTAL_LABELS)

# 金额字段的互斥词：避免把「价税合计」的金额当成「不含税金额」
EXCL_TAX_FORBID: tuple[str, ...] = ("价税合计", "大写")
TAX_AMOUNT_FORBID: tuple[str, ...] = ("价税合计", "税率", "不含税")
# 价税合计行常把大写、小写印在同一行（大写是中文数字，正则不会命中），因此这里不排除「大写」
TOTAL_FORBID: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# 归一化小工具
# --------------------------------------------------------------------------

def _to_halfwidth(text: str) -> str:
    """全角 ASCII（含全角数字/字母/冒号/括号）转半角。中文字符不受影响。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _compact(text: str) -> str:
    """去空白 + 全角转半角，用于关键词与正则匹配。"""
    return re.sub(r"\s+", "", _to_halfwidth(text))


def normalize_amount(raw: str | None) -> str | None:
    """金额格式化：去掉 ¥ / ￥ / 空格 / 千分位，全角转半角，输出两位小数字符串。

    ``"¥ 1,234.5"`` → ``"1234.50"``；无法解析时返回 None。
    """
    if not raw:
        return None
    s = _compact(raw).lstrip("+")
    for symbol in ("¥", "￥", ",", "元"):
        s = s.replace(symbol, "")
    s = s.replace("。", ".")
    match = re.fullmatch(r"(-?)(\d+)(?:\.(\d{1,2}))?", s)
    if not match:
        return None
    sign = "-" if match.group(1) else ""
    decimals = (match.group(3) or "0").ljust(2, "0")
    return f"{sign}{match.group(2)}.{decimals}"


def normalize_date(raw: str | None) -> str | None:
    """日期归一化为 ISO ``YYYY-MM-DD``（``service._coerce`` 只认这个格式）。"""
    if not raw:
        return None
    match = DATE_RE.search(_compact(raw))
    if not match:
        return None
    year, month, day = (int(x) for x in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 逐字段提取
# --------------------------------------------------------------------------

def _iter_matches(line: TextLine) -> str:
    return _compact(line.text)


def _scan(
    lines: Sequence[TextLine],
    pattern: re.Pattern[str],
    *,
    forbid: Sequence[str] = (),
    start: int = 0,
    end: int | None = None,
    group: int = 1,
) -> Extracted:
    """在指定行范围内找第一个命中：返回匹配文本 + 该行置信度。

    ``group=0`` 用于取整个匹配（例如日期，DATE_RE 的 group(1) 只是年份）。
    """
    for line in lines[start:end]:
        text = _iter_matches(line)
        if not text:
            continue
        if any(word in text for word in forbid):
            continue
        match = pattern.search(text)
        if match:
            return Extracted(value=match.group(group), confidence=line.confidence, source=line.text)
    return Extracted()


def _looks_like_company(text: str) -> bool:
    return any(suffix in text for suffix in COMPANY_SUFFIXES)


def _zone_bounds(lines: Sequence[TextLine]) -> tuple[int, int]:
    """返回 (购买方区结束点, 销售方区起点)——都用**第一个销售方关键词**做分界。

    为什么不靠购买方关键词定位：数电票里「购」常被 OCR 拆成单字、或被排到下方
    （实测出现过「购」「买方信：」等噪声），拿它当起点会把区块切错。销售方关键词
    命中更稳定，所以只认它：它之前全算购买方区，之后全算销售方区。
    """
    for idx, line in enumerate(lines):
        text = _iter_matches(line)
        if any(k in text for k in SELLER_SECTION_KEYWORDS):
            return idx, idx
    return len(lines), len(lines)


# 名称候选里要排除的噪声行（开户行、账号等也算“公司”，但显然不是购销方名称）
NAME_NOISE: tuple[str, ...] = (
    "开户银行",
    "开户行",
    "银行账号",
    "账号",
    "地址",
    "电话",
    "纳税人识别号",
    "统一社会信用代码",
    "税号",
)


def _name_from_line(line: TextLine) -> Extracted | None:
    """从一行里抽公司名：优先「名称」标签，其次整行含公司后缀；噪声行直接放弃。"""
    text = _iter_matches(line)
    if not text or any(word in text for word in NAME_NOISE):
        return None

    for label in NAME_LABELS:
        idx = text.find(_compact(label))
        if idx < 0:
            continue
        value = _clean_party_name(text[idx + len(_compact(label)):])
        if value:
            return Extracted(value=value, confidence=line.confidence, source=line.text)

    if "名称" not in text and _looks_like_company(text):
        value = _clean_party_name(text)
        if value:
            return Extracted(value=value, confidence=line.confidence, source=line.text)
    return None


def _tax_no_from_line(line: TextLine) -> Extracted | None:
    """从一行里抽税号：带标签优先，其次整行就是 15–20 位大写字母数字。"""
    text = _iter_matches(line)
    if not text:
        return None
    if any(label in text for label in TAX_NO_LABELS):
        match = TAX_NO_VALUE_RE.search(text)
        if match:
            return Extracted(value=match.group(1), confidence=line.confidence, source=line.text)
        return None
    if re.fullmatch(r"[0-9A-Z]{15,20}", text) and re.search(r"\d", text):
        return Extracted(value=text, confidence=line.confidence, source=line.text)
    return None


def _first_in(lines: Sequence[TextLine], start: int, end: int, extractor) -> Extracted:
    for line in lines[start:end]:
        hit = extractor(line)
        if hit is not None:
            return hit
    return Extracted()


def _collect(lines: Sequence[TextLine], extractor) -> list[Extracted]:
    """按出现顺序收集所有候选（数电票是左右两栏，靠顺序取第一个/第二个最稳）。"""
    hits: list[Extracted] = []
    for line in lines:
        hit = extractor(line)
        if hit is not None:
            hits.append(hit)
    return hits


def _clean_party_name(text: str) -> str | None:
    """去掉冒号、标签残留与括号内容，返回公司名；空则 None。"""
    value = text.strip(":：;；")
    value = re.sub(r"^(名称|名\s*称)", "", value)
    value = value.strip(":：;；")
    value = re.sub(r"[(（].*?[)）]", "", value).strip()
    if not value or len(value) < 2:
        return None
    return value


def _detect_invoice_type(full_text: str) -> str | None:
    for invoice_type, keywords in TYPE_KEYWORDS:
        if any(_compact(k) in full_text for k in keywords):
            return invoice_type
    return None


def _fallback_invoice_no(lines: Sequence[TextLine]) -> Extracted:
    """没有「发票号码」标签时：找独立的 8 位或 20 位数字串（传统票 8 位 / 数电票 20 位）。"""
    for line in lines:
        text = _iter_matches(line)
        if not text:
            continue
        for candidate in re.findall(r"\d{8}|\d{20}", text):
            if re.fullmatch(r"0+", candidate):
                continue
            return Extracted(value=candidate, confidence=line.confidence, source=line.text)
    return Extracted()


# ---- 表格版式的金额兜底（数电票把「金额 / 税率 / 税额」拆成多行，标签不在同一行）----

RATE_RE = re.compile(r"^\(?\d{1,2}(?:\.\d+)?%\)?$")
AMOUNT_ONLY_RE = re.compile(r"^[¥￥]?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$")


def _amount_only_lines(lines: Sequence[TextLine]) -> list[tuple[int, Extracted]]:
    """纯金额行（整行只有一个数字，可能带 ¥）——表格里的金额单元格就长这样。"""
    hits: list[tuple[int, Extracted]] = []
    for idx, line in enumerate(lines):
        text = _iter_matches(line)
        if text and AMOUNT_ONLY_RE.match(text) and normalize_amount(text) is not None:
            hits.append((idx, Extracted(value=text, confidence=line.confidence, source=line.text)))
    return hits


def _table_amount_pair(
    lines: Sequence[TextLine], total: str | None
) -> tuple[Extracted, Extracted]:
    """从表格行里推断 (不含税金额, 税额)。

    两个信号，按可靠性排序：
    A. 「价税合计」行**上面**最近的两个纯金额（通常就是合计行的 不含税 / 税额）；
    B. 「税率」行（1% / 13% 这种）前后的纯金额（单行明细的场景）。

    ⚠️ 有价税合计时**必须**满足「不含税 + 税额 = 价税合计」才采用——
    金额宁可留空让人工补，也绝不能悄悄填错。
    """
    amounts = _amount_only_lines(lines)
    if not amounts:
        return Extracted(), Extracted()

    candidates: list[tuple[Extracted, Extracted]] = []

    # 信号 A：价税合计行上方的两个纯金额
    for idx, line in enumerate(lines):
        text = _iter_matches(line)
        if any(_compact(label) in text for label in TOTAL_LABELS):
            above = [hit for i, hit in amounts if i < idx]
            if len(above) >= 2:
                candidates.append((above[-2], above[-1]))
            break

    # 信号 B：税率行前后各取一个纯金额
    for idx, line in enumerate(lines):
        if RATE_RE.match(_iter_matches(line)):
            before = [hit for i, hit in amounts if i < idx]
            after = [hit for i, hit in amounts if i > idx]
            if before and after:
                candidates.append((before[-1], after[0]))

    for excl, tax in candidates:
        left = float(normalize_amount(excl.value) or 0)
        right = float(normalize_amount(tax.value) or 0)
        if total is not None:
            if abs(left + right - float(total)) > 0.02:
                continue  # 与价税合计对不上，丢弃这个候选
        return excl, tax

    return Extracted(), Extracted()


def extract_invoice_fields(lines: Sequence[TextLine]) -> dict[str, Extracted]:
    """把 OCR 文本行转成 10 个字段；未命中一律返回 value=None。"""
    lines = list(lines)
    result: dict[str, Extracted] = {name: Extracted() for name in FIELD_NAMES}
    full_text = _compact("".join(line.text for line in lines))

    buyer_end, seller_start = _zone_bounds(lines)

    invoice_code = _scan(lines, INVOICE_CODE_RE)
    invoice_no = _scan(lines, INVOICE_NO_RE)
    if invoice_no.value is None:
        invoice_no = _fallback_invoice_no(lines)

    # 发票类型：关键词判定 → 用「有没有发票代码」互校 → 再用号码位数兜底
    invoice_type = _detect_invoice_type(full_text)
    if invoice_code.value and invoice_type == INVOICE_TYPE_ESIGN:
        invoice_type = INVOICE_TYPE_BLUE
    if invoice_type != INVOICE_TYPE_RED and re.fullmatch(r"\d{20}", invoice_no.value or ""):
        # 20 位号码是真·数电票的硬特征（它的票头常写「电子发票(普通发票)」，光看关键词认不出来）
        invoice_type = INVOICE_TYPE_ESIGN

    date_hit = _scan(lines, DATE_RE, group=0)
    invoice_date = Extracted(
        value=normalize_date(date_hit.value),
        confidence=date_hit.confidence if date_hit.value else None,
        source=date_hit.source,
    )

    # 购销方：先按区块取；区块取不到（数电票左右分栏、OCR 行序会乱）就按出现顺序兜底
    names = _collect(lines, _name_from_line)
    tax_nos = _collect(lines, _tax_no_from_line)

    buyer_name = _first_in(lines, 0, buyer_end, _name_from_line)
    if buyer_name.value is None and names:
        buyer_name = names[0]

    seller_name = _first_in(lines, seller_start, len(lines), _name_from_line)
    if seller_name.value is None and len(names) > 1:
        seller_name = names[1]

    buyer_tax_no = _first_in(lines, 0, buyer_end, _tax_no_from_line)
    if buyer_tax_no.value is None and tax_nos:
        buyer_tax_no = tax_nos[0]

    total_hit = _scan(lines, TOTAL_RE, forbid=TOTAL_FORBID)
    total_amount = normalize_amount(total_hit.value)

    excl_hit = _scan(lines, EXCL_TAX_RE, forbid=EXCL_TAX_FORBID)
    tax_hit = _scan(lines, TAX_AMOUNT_RE, forbid=TAX_AMOUNT_FORBID)

    # 标签法没命中（数电票的表格行）→ 走表格兜底，并用「不含税 + 税额 = 价税合计」校验
    if excl_hit.value is None or tax_hit.value is None:
        excl_table, tax_table = _table_amount_pair(lines, total_amount)
        if excl_hit.value is None and excl_table.value is not None:
            excl_hit = excl_table
        if tax_hit.value is None and tax_table.value is not None:
            tax_hit = tax_table

    result["invoice_type"] = Extracted(value=invoice_type, confidence=None, source="")
    result["invoice_code"] = Extracted(
        value=invoice_code.value, confidence=invoice_code.confidence, source=invoice_code.source
    )
    result["invoice_no"] = Extracted(
        value=invoice_no.value, confidence=invoice_no.confidence, source=invoice_no.source
    )
    result["invoice_date"] = invoice_date
    result["buyer_name"] = buyer_name
    result["buyer_tax_no"] = buyer_tax_no
    result["seller_name"] = seller_name
    result["amount_excl_tax"] = Extracted(
        value=normalize_amount(excl_hit.value),
        confidence=excl_hit.confidence if excl_hit.value else None,
        source=excl_hit.source,
    )
    result["tax_amount"] = Extracted(
        value=normalize_amount(tax_hit.value),
        confidence=tax_hit.confidence if tax_hit.value else None,
        source=tax_hit.source,
    )
    result["total_amount"] = Extracted(
        value=total_amount,
        confidence=total_hit.confidence if total_hit.value else None,
        source=total_hit.source,
    )
    return result
