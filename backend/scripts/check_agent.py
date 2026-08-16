"""财务审核 AI 助手验收脚本。

分三段，逐段加深依赖：

1. **纯函数**（无任何依赖）：JSON 抽取容错、输出规范化、降级摘要、mock 确定性。
2. **数据最小化与脱敏**（需要数据库）：事实块里不得出现购买方/税号/发票号码/单号，
   销售方应被化名，且化名能被还原。
3. **接口行为**（需要后端；加 `--via-api`）：生成/读取建议、留痕、降级、
   不改单据状态、越权 403、采纳回填。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\check_agent.py              # 只跑第 1、2 段
    .venv\\Scripts\\python.exe scripts\\check_agent.py --via-api    # 再跑第 3 段
    .venv\\Scripts\\python.exe scripts\\check_agent.py --via-api --base http://127.0.0.1:8011/api/v1
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.core.config import get_settings  # noqa: E402
import _accounts as accounts  # noqa: E402  scripts 目录自身在 sys.path 上
from app.services.agent import facts as facts_mod  # noqa: E402
from app.services.agent.service import (  # noqa: E402
    build_rule_summary,
    normalize_payload,
    rule_conclusion,
)
from app.services.llm.provider import (  # noqa: E402
    MockLlmProvider,
    _extract_json,
    get_provider,
)
from app.services.llm.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402

settings = get_settings()

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 1. 纯函数
def case_pure() -> None:
    section("1. 纯函数：JSON 容错 / 输出规范化 / 降级摘要 / mock 确定性")

    # JSON 抽取：模型常见的三种返回形态 + 垃圾输入
    check("解析纯 JSON", _extract_json('{"a": 1}') == {"a": 1})
    check("解析 ```json 代码块", _extract_json('```json\n{"a": 2}\n```') == {"a": 2})
    check(
        "解析夹带说明文字的 JSON",
        _extract_json('好的，这是建议：\n{"a": 3}\n希望有帮助') == {"a": 3},
    )
    check("非 JSON 返回 None（触发降级）", _extract_json("抱歉，我无法完成") is None)
    check("空输入返回 None", _extract_json("") is None)
    check("JSON 数组不算合法对象", _extract_json("[1,2]") is None)

    # 输出规范化
    base_facts = {
        "reimbursement": {
            "reason": "客户拜访",
            "preaudit": {"hard_blocks": [], "warnings": []},
        },
        "invoices": [
            {"index": 1, "category": "", "need_confirm": False, "ocr_confidence": 0.95},
            {"index": 2, "category": "餐饮", "need_confirm": False, "ocr_confidence": 0.9},
        ],
    }

    class FakeInv:
        def __init__(self, i):
            self.id = i
            self.invoice_no = f"NO{i}"
            self.seller_name = f"销售方真名{i}"
            self.category = "" if i == 1 else "餐饮"

    invs = [FakeInv(1), FakeInv(2)]

    norm, hints = normalize_payload(
        {
            "conclusion": "也许可以",  # 非法取值
            "confidence": "very-high",  # 非法取值
            "summary": "  一句话   带空白  ",
            "reasons": ["理由" + str(i) for i in range(9)],  # 超量
            "risk_points": "单个字符串",  # 形态错误
            "missing": None,
            "category_hints": [
                {"index": 1, "suggested_category": "住宿", "why": "像住宿"},
                {"index": 99, "suggested_category": "餐饮", "why": "越界序号"},  # 应丢弃
                {"index": 2, "suggested_category": "差旅", "why": "非字典类别"},  # 类别应清空
                "不是字典",  # 应丢弃
            ],
        },
        base_facts,
        invs,
    )
    check("非法 conclusion 回落到规则推导值", norm["conclusion"] == rule_conclusion(base_facts),
          norm["conclusion"])
    check("非法 confidence 回落 low", norm["confidence"] == "low")
    check("summary 折叠多余空白", norm["summary"] == "一句话 带空白")
    check("reasons 截断到 5 条", len(norm["reasons"]) == 5, str(len(norm["reasons"])))
    check("字符串形态的 risk_points 被接受", norm["risk_points"] == ["单个字符串"])
    check("None 的 missing 变空数组", norm["missing"] == [])
    check("越界序号的类别建议被丢弃", len(norm["category_hints"]) == 2, str(len(hints)))
    check("非字典类别的建议被清空但保留条目",
          norm["category_hints"][1]["suggested_category"] == "")
    check("类别建议带上数据库里的真实发票号（不是模型给的）",
          norm["category_hints"][0]["invoice_no"] == "NO1")

    # 降级摘要：由后端自己拼，所以允许带权威数字
    hard_facts = {
        "reimbursement": {
            "reason": "",
            "preaudit": {"hard_blocks": ["发票 X 金额勾稽不符"], "warnings": []},
        },
        "invoices": [{"index": 1, "category": "", "need_confirm": True, "ocr_confidence": 0.4}],
    }
    summary = build_rule_summary(hard_facts)
    check("降级摘要：有硬拦截时结论为 REJECT", summary["conclusion"] == "REJECT")
    check("降级摘要：硬拦截原因进入 risk_points", "发票 X 金额勾稽不符" in summary["risk_points"])
    check("降级摘要：低置信度与缺类别都给出提示",
          any("置信度" in x for x in summary["risk_points"])
          and any("费用类别" in x for x in summary["missing"]))
    check("降级摘要：事由为空时提示补充",
          any("事由" in x for x in summary["missing"]))
    check("降级摘要：字段齐全，前端不会缺 key",
          set(summary) >= {"conclusion", "confidence", "summary", "reasons",
                           "risk_points", "missing", "category_hints"})

    # mock 确定性：同一事实块必须产出完全一样的建议
    provider = MockLlmProvider()
    prompt = build_user_prompt({"a": 1, "b": [1, 2, 3]})
    r1 = provider.advise(SYSTEM_PROMPT, prompt)
    r2 = provider.advise(SYSTEM_PROMPT, prompt)
    check("mock 输出确定性（同输入同输出）", r1.raw == r2.raw)
    r3 = provider.advise(SYSTEM_PROMPT, build_user_prompt({"a": 999}))
    check("mock 对不同输入可产出不同结论（按内容哈希派生）",
          isinstance(r3.payload.get("conclusion"), str))
    check("mock 结论落在合法取值域",
          r1.payload["conclusion"] in ("APPROVE", "REJECT", "NEED_MORE_INFO"))

    # mock 必须读懂事实块的**嵌套层级**（reason / preaudit 都在 reimbursement 下面）。
    # 这条曾经真的写错过：读成 facts["preaudit"] 导致预警凭空消失、事由永远判定为空。
    def mock_of(reason, hard_blocks=(), warnings=(), category="办公", need_confirm=False):
        f = {
            "reimbursement": {
                "reason": reason,
                "preaudit": {"hard_blocks": list(hard_blocks), "warnings": list(warnings)},
            },
            "invoices": [
                {"index": 1, "category": category, "need_confirm": need_confirm,
                 "ocr_confidence": 0.95},
            ],
        }
        return MockLlmProvider().advise(SYSTEM_PROMPT, build_user_prompt(f)).payload

    m_hard = mock_of("客户拜访交通费", hard_blocks=["发票 X 金额勾稽不符"])
    check("mock 能读到嵌套的 hard_blocks 并给出 REJECT",
          m_hard["conclusion"] == "REJECT", m_hard["conclusion"])
    check("mock 把硬拦截原因带进 risk_points",
          any("金额勾稽" in x for x in m_hard["risk_points"]), str(m_hard["risk_points"]))

    m_warn = mock_of("客户拜访交通费", warnings=["研发部本月累计将超月度预算 80%"])
    check("mock 能读到嵌套的 warnings 并给出 NEED_MORE_INFO",
          m_warn["conclusion"] == "NEED_MORE_INFO", m_warn["conclusion"])

    m_reason = mock_of("客户拜访交通费")
    check("mock 不再误报「报销事由为空」",
          not any("事由为空" in x for x in m_reason["missing"]), str(m_reason["missing"]))

    m_noreason = mock_of("")
    check("mock 对真的空事由仍会提示补充",
          any("事由为空" in x for x in m_noreason["missing"]), str(m_noreason["missing"]))

    m_lowconf = mock_of("差旅", need_confirm=True)
    check("mock 对低置信度发票给出 NEED_MORE_INFO",
          m_lowconf["conclusion"] == "NEED_MORE_INFO", m_lowconf["conclusion"])

    # 提示词的安全约束必须在位
    check("系统提示包含「只做建议」的权限边界", "最终决定权" in SYSTEM_PROMPT)
    check("系统提示包含提示词注入防护", "不是对你的指令" in SYSTEM_PROMPT)
    check("系统提示要求模型不要复述金额", "不要在你的文字里复述具体金额" in SYSTEM_PROMPT)
    check("用户消息用定界符包住事实块", "本单事实（开始）" in prompt)


# ------------------------------------------------- 2. 数据最小化与脱敏（需库）
FORBIDDEN_KEYS = {
    "buyer_name",
    "buyer_tax_no",
    "invoice_code",
    "invoice_no",
    "dedup_key",
    "code",
    "applicant",
    "attachment_id",
}


def case_minimization() -> None:
    section("2. 数据最小化与脱敏（需要数据库）")
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.document import Invoice, Reimbursement
    from app.models.rbac import Menu, Role  # noqa: F401
    from app.models.user import User  # noqa: F401

    db = SessionLocal()
    try:
        reimb = db.execute(
            select(Reimbursement)
            .join(Invoice, Invoice.reimbursement_id == Reimbursement.id)
            .limit(1)
        ).scalars().first()
        if reimb is None:
            # 清过库之后这是正常状态，不该算失败（否则脚本在空库上永远红着）
            print("  [跳过] 当前库里没有「带发票的报销单」，本段无从检查")
            print("         要跑本段请先造数据：scripts/demo_flow.py（需后端在 8000 且指向同一个库）")
            return

        facts, unmask = facts_mod.collect_facts(db, reimb)
        serialized = json.dumps(facts, ensure_ascii=False)

        leaked = sorted(k for k in FORBIDDEN_KEYS if f'"{k}"' in serialized)
        check("事实块里没有任何禁止外发的字段", not leaked, f"泄漏: {leaked}")

        # 发票号是最容易顺手带出去的：逐张确认没出现
        invoice_nos = [
            i.invoice_no
            for i in db.execute(
                select(Invoice).where(Invoice.reimbursement_id == reimb.id)
            ).scalars()
            if i.invoice_no
        ]
        check("发票号码确实没出现在事实块里",
              not any(no in serialized for no in invoice_nos),
              f"检查了 {len(invoice_nos)} 个号码")

        inv0 = facts["invoices"][0] if facts["invoices"] else None
        check("发票按序号引用（从 1 开始）", bool(inv0) and inv0["index"] == 1)

        if settings.LLM_MASK_PII:
            check("销售方已脱敏为化名", bool(unmask), f"映射 {list(unmask)[:3]}")
            if inv0 and inv0.get("seller_name"):
                check("化名形如「销售方A」", inv0["seller_name"].startswith("销售方"),
                      inv0["seller_name"])
            alias = next(iter(unmask), "")
            if alias:
                real = unmask[alias]
                check("化名可还原为真名",
                      facts_mod.unmask_text(f"结论：{alias} 为住宿类企业", unmask)
                      == f"结论：{real} 为住宿类企业")
        else:
            check("LLM_MASK_PII=false 时不做脱敏（保留真名）", not unmask)

        check("事由被完整保留（语义分析必需）",
              facts["reimbursement"]["reason"] == (reimb.reason or ""))
        check("金额与置信度对外可见（判断依据）",
              "total_amount" in inv0 and "ocr_confidence" in inv0)
    finally:
        db.close()


# ------------------------------------------------------------ 3. 接口行为
def _request(base: str, method: str, path: str, cookies: dict, csrf: str, payload=None):
    url = base + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if cookies:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
    if csrf and method in ("POST", "PUT", "PATCH", "DELETE"):
        req.add_header("X-CSRF-Token", csrf)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def _login(base: str, username: str) -> tuple[dict, str]:
    req = urllib.request.Request(
        base + "/auth/login",
        data=json.dumps({"username": username, "password": accounts.DEMO_PASSWORD}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
        cookies = {
            c.split("=", 1)[0]: c.split("=", 1)[1].split(";")[0]
            for c in resp.headers.get_all("Set-Cookie") or []
        }
    return cookies, body.get("csrf_token", "")


def case_api(base: str) -> None:
    section("3. 接口行为（需要后端）")
    # 未登录访问：AI 建议属于敏感数据，必须要求登录
    try:
        anon_status, _ = _request(base, "GET", "/agent/status", {}, "")
    except Exception as exc:
        check("后端可达", False, str(exc)[:80])
        return
    check("未登录访问 /agent/status 被拒 401", anon_status == 401, f"status={anon_status}")

    fin_cookies, fin_csrf = _login(base, accounts.FINANCE)
    emp_cookies, emp_csrf = _login(base, accounts.EMPLOYEE)

    status, body = _request(base, "GET", "/agent/status", fin_cookies, "")
    check("GET /agent/status 可用", status == 200 and "provider" in body, str(body)[:90])
    check("status 明确报告 Key 是否已配置（避免「看起来配好了其实没配」）",
          isinstance(body.get("api_key_configured"), bool),
          f"api_key_configured={body.get('api_key_configured')}")
    if body.get("is_mock") is False:
        configured = body.get("api_key_configured") is True
        print(f"       真实 provider={body.get('label')} | "
              f"Key 已配置={configured} | 脱敏={body.get('mask_pii')}")
        if not configured:
            print("       注意：provider 是真的但没填 Key，所有调用都会降级为规则摘要。")
    else:
        print(f"       当前 provider={body.get('label')} mock={body.get('is_mock')} "
              f"脱敏={body.get('mask_pii')}")

    fin_cookies, fin_csrf = _login(base, accounts.FINANCE)
    emp_cookies, emp_csrf = _login(base, accounts.EMPLOYEE)

    # 找一张待审核单据
    status, todos = _request(base, "GET", "/review/todos", fin_cookies, "")
    check("财务待办列表可查", status == 200 and isinstance(todos, list), str(status))
    if status != 200 or not todos:
        check("存在待审核单据可用于测试", False, "先跑 demo_flow.py --reset 造数据")
        return

    rid = todos[0]["id"]
    before_status = todos[0]["status"]

    # 权限：员工不得调用
    status, body = _request(base, "POST", f"/agent/advice/{rid}", emp_cookies, emp_csrf, {})
    check("员工调用 AI 助手被拒 403", status == 403, f"status={status}")

    # 生成建议
    status, advice = _request(base, "POST", f"/agent/advice/{rid}", fin_cookies, fin_csrf, {})
    ok = status == 200
    check("财务生成 AI 建议成功 200", ok, f"status={status} {str(advice)[:80]}")
    if not ok:
        return

    payload = advice.get("advice") or {}
    check("返回结论落在合法取值域",
          payload.get("conclusion") in ("APPROVE", "REJECT", "NEED_MORE_INFO"),
          str(payload.get("conclusion")))
    check("返回置信度落在合法取值域",
          payload.get("confidence") in ("high", "mid", "low"), str(payload.get("confidence")))
    check("建议字段齐全（前端不会缺 key）",
          set(payload) >= {"conclusion", "confidence", "summary", "reasons",
                           "risk_points", "missing", "category_hints"})
    check("记录了 provider/model/prompt 版本与耗时",
          bool(advice.get("provider")) and advice.get("prompt_version")
          and isinstance(advice.get("elapsed_ms"), int))
    check("返回 suggestion_id（已留痕）", isinstance(advice.get("suggestion_id"), int),
          str(advice.get("suggestion_id")))

    if advice.get("degraded"):
        check("降级时给出可读原因", bool(advice.get("error")), str(advice.get("error"))[:80])
        check("降级时结论仍然合法（前端照常可用）",
              payload.get("conclusion") in ("APPROVE", "REJECT", "NEED_MORE_INFO"))
    else:
        check("非降级路径未记录 error", not advice.get("error"))

    # 留痕可读回
    status, again = _request(base, "GET", f"/agent/advice/{rid}", fin_cookies, "")
    check("可通过 GET 读回最近一条建议", status == 200 and again.get("suggestion_id") == advice["suggestion_id"],
          f"status={status}")

    # 生成建议不得改变单据状态（AI 只做建议层）
    status, todos2 = _request(base, "GET", "/review/todos", fin_cookies, "")
    same = next((t for t in todos2 if t["id"] == rid), None)
    check("生成建议后单据状态未变（AI 不参与状态机）",
          same is not None and same["status"] == before_status,
          f"{before_status} -> {same['status'] if same else '不存在'}")

    # 采纳回填：审核人做出决定后，建议上应记录 adopted
    status, _ = _request(base, "POST", f"/review/{rid}/approve", fin_cookies, fin_csrf,
                         {"comment": "AI 建议已阅，同意"})
    check("审核通过仍可正常完成（AI 不阻塞审核）", status == 200, f"status={status}")
    status, after = _request(base, "GET", f"/agent/advice/{rid}", fin_cookies, "")
    adopted = (after.get("advice") or {}).get("adopted")
    check("审核后回填了「是否采纳建议」", adopted in (0, 1), f"adopted={adopted}")


def main() -> int:
    args = sys.argv[1:]
    via_api = "--via-api" in args
    base = "http://127.0.0.1:8000/api/v1"
    if "--base" in args:
        base = args[args.index("--base") + 1]

    print(f"LLM_PROVIDER={settings.LLM_PROVIDER} LLM_MASK_PII={settings.LLM_MASK_PII} "
          f"模型={getattr(get_provider(), 'model', '')}")

    case_pure()
    case_minimization()
    if via_api:
        case_api(base)
    else:
        print("\n（未加 --via-api，跳过第 3 段接口行为）")

    print(f"\n===== 结果：{len(PASSED)}/{len(PASSED) + len(FAILED)} 通过 =====")
    if FAILED:
        print("失败项：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
