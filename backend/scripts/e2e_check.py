"""端到端验证脚本：只用标准库（urllib + http.cookiejar + 内存 PNG/JPG 生成）。

覆盖需求第六章验收标准：
1 登录与锁定  2 越权防护  3 上传校验  4 OCR 填单  5 修改留痕
6 重复报销拦截 7 金额勾稽  8 预算控制  9 审核流程  10 状态机

用法（在 backend 目录下，先启动 uvicorn）：
    .venv\\Scripts\\python.exe scripts\\e2e_check.py

⚠️ 必须用 mock 引擎跑：本脚本上传的是「合成噪声图」，只有 mock（按图片哈希派生）能稳定产出字段；
   后端跑 paddle 真实引擎时会读不出内容、断言连锁失败。脚本会在开头检查并提醒。
   真实引擎的效果请用 scripts/check_paddle_provider.py（真机冒烟）或界面实测。
   另：本脚本非幂等（固定发票号），请在清库后的库上执行，建议用独立库（DB_NAME=invoice_system_e2e）。
"""
from __future__ import annotations

import json
import os
import struct
import sys
import urllib.error
import urllib.request
import uuid
import zlib
from http.cookiejar import CookieJar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _accounts as accounts  # noqa: E402  scripts 目录自身在 sys.path 上

# 中文 Windows 控制台默认 GBK，统一按 UTF-8 输出，避免个别字符打断打印
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000/api/v1")
PASSED: list[str] = []
FAILED: list[str] = []


# ---------------- 测试用图标生成（免第三方依赖） ----------------
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(seed: int = 1, size: int = 24) -> bytes:
    """生成真实合法的 PNG：每个 seed 像素不同 → OCR mock 结果稳定且互不相同。"""
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0
        for x in range(size):
            raw += bytes(
                [
                    (x * 7 + seed * 13) % 256,
                    (y * 11 + seed * 29) % 256,
                    (x * y + seed * 53) % 256,
                ]
            )
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def make_jpg(seed: int = 1) -> bytes:
    """构造通过 magic bytes 校验的最小 JPEG（仅用于上传链路验证）。"""
    payload = bytes((i * 31 + seed * 7) % 256 for i in range(256))
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + payload + b"\xff\xd9"


def make_fake_jpg() -> bytes:
    """扩展名是 jpg、内容是文本 → 应被 magic bytes 拦截。"""
    return "这不是一张图片，只是伪装成 jpg 的文本".encode("utf-8")


# ---------------- 极简 HTTP 客户端 ----------------
class Client:
    def __init__(self) -> None:
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def cookie(self, name: str) -> str | None:
        for c in self.jar:
            if c.name == name:
                return c.value
        return None

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str | None = None,
        csrf: bool = True,
    ) -> tuple[int, dict]:
        req = urllib.request.Request(BASE + path, data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        if body is not None and csrf:
            token = self.cookie("csrf_token")
            if token:
                req.add_header("X-CSRF-Token", token)
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        try:
            return status, json.loads(raw.decode("utf-8"))
        except Exception:
            return status, {"raw": raw[:200].decode("utf-8", "ignore")}

    def get(self, path: str) -> tuple[int, dict]:
        return self.request("GET", path)

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        return self.request(
            "POST", path, json.dumps(payload).encode(), "application/json"
        )

    def put_json(self, path: str, payload: dict) -> tuple[int, dict]:
        return self.request(
            "PUT", path, json.dumps(payload).encode(), "application/json"
        )

    def patch_json(self, path: str, payload: dict) -> tuple[int, dict]:
        return self.request(
            "PATCH", path, json.dumps(payload).encode(), "application/json"
        )

    def delete(self, path: str) -> tuple[int, dict]:
        return self.request("DELETE", path)

    def upload(self, files: list[tuple[str, bytes, str]], fields: dict | None = None):
        boundary = f"----dsh{uuid.uuid4().hex}"
        buf = bytearray()
        for key, value in (fields or {}).items():
            buf += f"--{boundary}\r\n".encode()
            buf += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
            buf += f"{value}\r\n".encode()
        for name, data, mime in files:
            buf += f"--{boundary}\r\n".encode()
            buf += (
                f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'
            ).encode()
            buf += f"Content-Type: {mime}\r\n\r\n".encode()
            buf += data + b"\r\n"
        buf += f"--{boundary}--\r\n".encode()
        return self.request(
            "POST",
            "/invoices/upload",
            bytes(buf),
            f"multipart/form-data; boundary={boundary}",
        )


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    (PASSED if ok else FAILED).append(name)
    print(f"[{mark}] {name}{(' — ' + detail) if detail else ''}")


def login(client: Client, username: str, password: str = accounts.DEMO_PASSWORD):
    return client.post_json("/auth/login", {"username": username, "password": password})


# ---------------- 用例 ----------------
def case_auth() -> None:
    print("\n=== 1. 登录 / 越权 / 上传校验 ===")
    c = Client()
    status, body = login(c, accounts.EMPLOYEE)
    check("正常登录并返回角色与菜单", status == 200 and body["user"]["role"] == "EMPLOYEE",
          f"role={body.get('user', {}).get('role')}")
    check("CSRF Token 已下发", bool(c.cookie("csrf_token")))

    # CSRF 双提交：未登录无 Token 应 401；已登录但缺 X-CSRF-Token 应 403
    status, body = Client().request(
        "POST", "/reimbursements", b'{"reason":"x"}', "application/json", csrf=False
    )
    check("未登录写操作被拒 401", status == 401, f"status={status}")

    raw = Client()
    login(raw, accounts.EMPLOYEE)
    status, body = raw.request(
        "POST", "/reimbursements", b'{"reason":"x"}', "application/json", csrf=False
    )
    check("已登录缺 CSRF Token 被拒 403", status == 403, f"status={status}")

    # 越权：员工访问他人单据
    other = Client()
    login(other, accounts.EMPLOYEE2)
    status, mine = other.post_json("/reimbursements", {"reason": "李四的单据"})
    other_id = mine.get("id")
    status, body = c.get(f"/reimbursements/{other_id}")
    check("员工访问他人单据返回 403", status == 403, f"status={status}")

    # 上传校验
    status, body = c.upload([("fake.jpg", make_fake_jpg(), "image/jpeg")])
    check("伪装扩展名文件被拒", status == 400, str(body.get("detail"))[:60])

    status, body = c.upload([("big.jpg", b"\xff\xd8\xff" + b"0" * (11 * 1024 * 1024), "image/jpeg")])
    check("超过 10MB 被拒", status == 400, str(body.get("detail"))[:60])

    # 现在支持 JPG/PNG/PDF；PDF 的支持情况由 scripts/check_pdf_pages.py 专门验证
    status, body = c.upload([("a.docx", b"PK\x03\x04 not an invoice", "application/octet-stream")])
    check("非 JPG/PNG/PDF 被拒", status == 400, str(body.get("detail"))[:60])

    # 影像访问边界：上传者本人与财务可见，他人员工 403
    status, mine = c.post_json("/reimbursements", {"reason": "影像权限验证"})
    status, up = c.upload([("own.png", make_png(seed=21), "image/png")], {"reimbursement_id": str(mine["id"])})
    attachment_id = up["items"][0]["attachment_id"]
    other2 = Client()
    login(other2, accounts.EMPLOYEE2)
    status, _ = other2.request("GET", f"/attachments/{attachment_id}/raw")
    check("他人员工访问发票影像 403", status == 403, f"status={status}")
    fin = Client()
    login(fin, accounts.FINANCE)
    status, _ = fin.request("GET", f"/attachments/{attachment_id}/raw")
    check("财务可查看发票影像", status == 200, f"status={status}")


def case_lock() -> None:
    print("\n=== 2. 密码连续错误锁定 ===")
    c = Client()
    last = None
    for i in range(5):
        last = login(c, accounts.EMPLOYEE, "wrong-password")
    status, body = last
    check("连续 5 次错误密码后锁定（423）", status == 423, str(body.get("detail"))[:60])
    status, body = login(Client(), accounts.EMPLOYEE)
    check("锁定期间正确密码仍被拒", status == 423, str(body.get("detail"))[:60])
    # 解锁（管理员）
    admin = Client()
    login(admin, accounts.ADMIN)
    status, users = admin.get("/admin/users")
    target = next(u for u in users if u["username"] == accounts.EMPLOYEE)
    status, body = admin.post_json(f"/admin/users/{target['id']}/unlock", {})
    check("管理员解锁成功", status == 200)
    status, body = login(Client(), accounts.EMPLOYEE)
    check("解锁后可正常登录", status == 200)


def case_flow() -> int:
    print("\n=== 3. 上传 → OCR 填单 → 修正留痕 → 提交 → 审核 ===")
    c = Client()
    status, body = login(c, accounts.EMPLOYEE)
    if status != 200:
        raise RuntimeError(f"{accounts.EMPLOYEE} 登录失败（前置用例可能污染状态）：{body}")
    status, reimb = c.post_json("/reimbursements", {"reason": "8 月客户拜访差旅报销"})
    rid = reimb["id"]

    status, up = c.upload(
        [("fapiao1.png", make_png(seed=7), "image/png"), ("fapiao2.png", make_png(seed=9), "image/png")],
        {"reimbursement_id": str(rid)},
    )
    ok = status == 200 and all(i["status"] == "DONE" for i in up["items"])
    first = up["items"][0]["invoice"]
    check("批量上传 2 张并全部识别完成", ok, f"elapsed={[i.get('elapsed_ms') for i in up['items']]}ms")
    check("OCR 返回字段与置信度", bool(first["invoice_no"]) and first["ocr_confidence"] > 0,
          f"发票号={first['invoice_no']} 置信度={first['ocr_confidence']}")
    check("识别耗时 ≤3 秒", all(i["elapsed_ms"] < 3000 for i in up["items"]))

    status, detail = c.get(f"/reimbursements/{rid}")
    check("聚合金额与发票张数已回写", detail["invoice_count"] == 2 and detail["total_amount"] > 0,
          f"张数={detail['invoice_count']} 金额={detail['total_amount']}")

    # 修改留痕（三个金额字段需一致修改，否则会命中金额勾稽硬拦截）
    inv = detail["invoices"][0]
    status, body = c.patch_json(
        f"/invoices/{inv['id']}",
        {"amount_excl_tax": 838.57, "tax_amount": 50.31, "total_amount": 888.88, "category": "交通"},
    )
    check("人工修正金额与费用类别成功", status == 200, f"changed={body.get('changed')}")
    status, history = c.get(f"/invoices/{inv['id']}/changes")
    hit = [h for h in history if h["field_name"] == "total_amount"]
    check(
        "留痕含原值/新值/修改人",
        bool(hit) and hit[0]["old_value"] != hit[0]["new_value"] and hit[0]["operator"],
        f"{hit[0]['old_value']} → {hit[0]['new_value']} by {hit[0]['operator']}" if hit else "无记录",
    )

    # 预审预览 + 提交
    status, preview = c.get(f"/reimbursements/audit?reimbursement_id={rid}")
    check("提交前预审预览可用", status == 200, str(preview)[:80])
    status, body = c.post_json(f"/reimbursements/{rid}/submit", {})
    check("提交成功进入 PENDING", status == 200 and body["reimbursement"]["status"] == "PENDING",
          f"status={body.get('reimbursement', {}).get('status')}")

    # 财务审核：驳回必填原因
    f = Client()
    login(f, accounts.FINANCE)
    status, body = f.post_json(f"/review/{rid}/reject", {"comment": ""})
    check("驳回不填原因被拒 400", status == 400, str(body.get("detail"))[:40])
    status, body = f.post_json(f"/review/{rid}/reject", {"comment": "发票抬头与公司主体不一致，请补充说明"})
    check("财务驳回成功", status == 200 and body["status"] == "REJECTED")

    status, mine = c.get("/reimbursements/mine")
    row = next(r for r in mine if r["id"] == rid)
    check("申请人可见驳回原因", "抬头" in (row["review_comment"] or ""), row["review_comment"][:30])

    # 重新提交：版本号 +1
    status, body = c.post_json(f"/reimbursements/{rid}/resubmit", {})
    check("驳回后重新提交成功且版本号 +1",
          status == 200 and body["reimbursement"]["version"] == row["version"] + 1,
          f"version {row['version']} → {body.get('reimbursement', {}).get('version')}")
    status, body = f.post_json(f"/review/{rid}/approve", {"comment": "核对无误"})
    check("财务通过进入 APPROVED", status == 200 and body["status"] == "APPROVED")

    status, detail = c.get(f"/reimbursements/{rid}")
    actions = [lg["action"] for lg in detail["logs"]]
    check("审核轨迹保留完整链路",
          actions[:4] == ["SUBMIT", "REJECT", "RESUBMIT_DRAFT", "SUBMIT"] and "APPROVE" in actions,
          " → ".join(actions))
    status, body = c.patch_json(f"/invoices/{inv['id']}", {"total_amount": 1.0})
    check("APPROVED 后不可修改发票", status == 400, str(body.get("detail"))[:40])
    return rid


def _make_invoice(
    no: str, amount: float, rid: int, code: str = "044001900111", dedup_key: str | None = None
):
    """直接造一张未占用发票，用于隔离验证单条预审规则。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.document import Invoice

    db = SessionLocal()
    try:
        attachment_id = db.execute(select(Invoice.attachment_id)).scalars().first()
        inv = Invoice(
            attachment_id=attachment_id,
            reimbursement_id=rid,
            invoice_type="蓝字",
            invoice_code=code,
            invoice_no=no,
            buyer_name="示例科技有限公司",
            buyer_tax_no="91330100MA2XXXXXXX",
            amount_excl_tax=amount,
            tax_amount=0.0,
            total_amount=amount,
            ocr_confidence=0.95,
            occupy_state="FREE",
        )
        inv.dedup_key = dedup_key or f"{code}-{no}"
        db.add(inv)
        db.commit()
        return inv.id
    finally:
        db.close()


def _new_draft(client: Client, reason: str) -> int:
    status, reimb = client.post_json("/reimbursements", {"reason": reason})
    return reimb["id"]


def case_rules(approved_rid: int) -> None:
    print("\n=== 4. 预审规则：重复报销 / 金额勾稽 / 预算分级 ===")
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.document import Invoice

    c = Client()
    login(c, accounts.EMPLOYEE)

    # --- 规则 1：重复报销（同一发票号已在已通过单据中） ---
    rid = _new_draft(c, "重复报销验证单")
    db = SessionLocal()
    try:
        src = db.execute(
            select(Invoice).where(Invoice.reimbursement_id == approved_rid)
        ).scalars().first()
        dup_no, dup_code, dup_key = src.invoice_no, src.invoice_code or "", src.dedup_key
    finally:
        db.close()
    _make_invoice(dup_no, 106.0, rid, dup_code, dedup_key=dup_key)
    status, body = c.post_json(f"/reimbursements/{rid}/submit", {})
    reasons = (body.get("detail") or {}).get("reasons") if isinstance(body.get("detail"), dict) else []
    check("重复报销被硬拦截 409 且提示原单据号",
          status == 409 and any("重复" in r for r in (reasons or [])),
          (reasons or [""])[0][:70])

    # --- 规则 1b：红字（负数）发票单独放行，不被查重拦截 ---
    rid_red = _new_draft(c, "红字发票放行验证单")
    _make_invoice("RED0001", -200.0, rid_red, "044001900222")
    db = SessionLocal()
    try:
        from app.models.document import Invoice as Inv

        red = db.execute(select(Inv).where(Inv.invoice_no == "RED0001")).scalars().one()
        red.invoice_type = "红字"
        red.dedup_key = "044001900222-RED0001"
        db.commit()
    finally:
        db.close()
    status, body = c.post_json(f"/reimbursements/{rid_red}/submit", {})
    check("红字发票单独放行（不触发查重）",
          status == 200 or (isinstance(body.get("detail"), dict) and
                            not any("重复" in r for r in body["detail"].get("reasons", []))),
          f"status={status}")

    # --- 规则 2：金额勾稽（不含税 + 税额 ≠ 价税合计） ---
    rid2 = _new_draft(c, "金额勾稽验证单")
    bad_id = _make_invoice("BAD0001", 100.0, rid2, "044001900333")
    db = SessionLocal()
    try:
        bad = db.get(Invoice, bad_id)
        bad.tax_amount = 6.0
        bad.total_amount = 999.0  # 100 + 6 ≠ 999
        db.commit()
    finally:
        db.close()
    status, body = c.post_json(f"/reimbursements/{rid2}/submit", {})
    reasons = (body.get("detail") or {}).get("reasons") if isinstance(body.get("detail"), dict) else []
    check("金额勾稽不符被硬拦截 409",
          status == 409 and any("勾稽" in r for r in (reasons or [])),
          (reasons or [""])[0][:70])

    # --- 规则 3：部门预算（技术研发部单笔 5000 / 月度 50000） ---
    rid3 = _new_draft(c, "单笔上限验证单")
    _make_invoice("LIM0001", 6000.0, rid3, "044001900444")
    status, body = c.post_json(f"/reimbursements/{rid3}/submit", {})
    reasons = (body.get("detail") or {}).get("reasons") if isinstance(body.get("detail"), dict) else []
    check("超过部门单笔上限被硬拦截",
          status == 409 and any("单笔上限" in r for r in (reasons or [])),
          (reasons or [""])[0][:70])

    # 月度分级用独立部门，避免与单笔上限规则相互干扰。
    # 通过管理员接口调整用户部门归属（与真实流程一致），避免会话内关系缓存过期。
    a = Client()
    login(a, accounts.ADMIN)
    # 部门与预算上限由**财务**维护（管理员只剩只读），所以后面建部门要用财务账号
    fin_mgr = Client()
    login(fin_mgr, accounts.FINANCE)
    employee_row = next(
        u for u in a.get("/admin/users")[1] if u["username"] == accounts.EMPLOYEE
    )
    role_id = employee_row["role_id"]

    def move_employee(dept_id: int, dept_name: str) -> None:
        status, body = a.put_json(
            f"/admin/users/{employee_row['id']}",
            {"username": accounts.EMPLOYEE, "name": employee_row["name"],
             "role_id": role_id, "dept_id": dept_id, "status": "NORMAL"},
        )
        if status != 200:
            raise RuntimeError(f"调整部门失败（{dept_name}）：{body}")

    # 月度累计 ≥80%（预警放行）：单笔上限放宽，月度上限 50000
    status, dept_warn = fin_mgr.post_json(
        "/admin/departments",
        {"name": "预算预警部", "single_limit": 50000, "monthly_limit": 50000},
    )
    move_employee(dept_warn["id"], "预算预警部")
    rid4 = _new_draft(c, "月度预警验证单")
    _make_invoice("WARN001", 42000.0, rid4, "044001900555")
    status, body = c.post_json(f"/reimbursements/{rid4}/submit", {})
    pre = body.get("preaudit") or {}
    check("月度预算达 80% 时预警但可提交",
          status == 200 and pre.get("risk_level") == "MID"
          and any("80%" in w for w in pre.get("warnings", [])),
          f"risk={pre.get('risk_level')} warns={pre.get('warnings')}")

    # 月度累计 >100%（硬拦截）：新部门月度上限 1000
    status, dept_block = fin_mgr.post_json(
        "/admin/departments",
        {"name": "预算拦截部", "single_limit": 100000, "monthly_limit": 1000},
    )
    move_employee(dept_block["id"], "预算拦截部")
    rid5 = _new_draft(c, "月度累计验证单")
    _make_invoice("MON0001", 2000.0, rid5, "044001900666")
    status, body = c.post_json(f"/reimbursements/{rid5}/submit", {})
    reasons = (body.get("detail") or {}).get("reasons") if isinstance(body.get("detail"), dict) else []
    check("月度累计超 100% 被硬拦截",
          status == 409 and any("月度上限" in r for r in (reasons or [])),
          (reasons or [""])[0][:70])

    # 复原部门归属，避免影响后续用例
    move_employee(1, accounts.DEPT_TECH)



def case_admin() -> None:
    print("\n=== 5. 管理员：用户 / 部门 / 基础数据 ===")
    a = Client()
    login(a, accounts.ADMIN)
    status, users = a.get("/admin/users")
    check("用户列表可查", status == 200 and len(users) >= 4, f"{len(users)} 人")
    status, depts = a.get("/admin/departments")
    check("部门含单笔/月度上限", status == 200 and all("monthly_limit" in d for d in depts),
          f"{[d['name'] for d in depts]}")
    status, roles = a.get("/admin/roles")
    check("角色权限码可见", status == 200 and all(r["permissions"] for r in roles),
          f"{[r['code'] for r in roles]}")
    status, menus = a.get("/admin/menus")
    check("菜单种子数据可查", status == 200 and len(menus) >= 5, f"{len(menus)} 条")

    status, body = a.post_json(
        "/admin/users",
        {"username": "tester01", "name": "临时测试", "password": accounts.DEMO_PASSWORD,
         "role_id": roles[0]["id"] if roles else 1, "dept_id": depts[0]["id"] if depts else None},
    )
    check("新建用户成功", status == 200, f"id={body.get('id')}")
    if status == 200:
        status, body = a.post_json(f"/admin/users/{body['id']}/reset-password",
                                   {"new_password": "newpass123"})
        check("重置密码成功", status == 200)

    # 权限边界：财务不能进管理员接口
    f = Client()
    login(f, accounts.FINANCE)
    status, body = f.get("/admin/users")
    check("财务访问管理员接口 403", status == 403, f"status={status}")

    # 审核轨迹查询
    status, logs = f.get("/review/logs/query")
    check("审核轨迹查询可用", status == 200 and len(logs) > 0, f"{len(logs)} 条")


def main() -> int:
    try:
        status, body = Client().get("/health")
        if status != 200:
            raise RuntimeError("health 不通过")
    except Exception as exc:
        print(f"后端未启动或不可达：{exc}\n请先在 backend 目录启动 uvicorn app.main:app --port 8000")
        return 2

    # 前置检查：本脚本用「合成噪声图」做端到端验收，只有 mock 引擎能从中稳定产出字段。
    # 若后端跑的是 paddle（真实引擎），它会读不出内容 → 发票号为空、断言连锁失败，
    # 报错还很难看懂。这里直接拦下来并给出做法。
    from app.core.config import get_settings

    provider = get_settings().OCR_PROVIDER
    if provider != "mock":
        print(
            f"当前 OCR_PROVIDER={provider}（后端与脚本读的是同一份 .env）。\n"
            "本脚本上传的是合成噪声图，真实引擎识别不出字段，会导致大量断言失败。\n"
            "请改用 mock 跑验收（后端也要用 mock 启动）：\n"
            "  $env:OCR_PROVIDER='mock'   # PowerShell（先设变量，再启后端与脚本）\n"
            "真实引擎的效果请用 scripts/check_paddle_provider.py 或界面实测。"
        )
        return 2

    case_auth()
    case_lock()
    approved_rid = case_flow()
    case_rules(approved_rid)
    case_admin()

    total = len(PASSED) + len(FAILED)
    print(f"\n===== 结果：{len(PASSED)}/{total} 通过 =====")
    if FAILED:
        print("失败用例：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
