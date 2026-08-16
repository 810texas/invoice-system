"""账号生命周期与三层权限验收脚本。

覆盖四块：
  1. 自助注册（含**不能自选角色/部门**这类安全断言）
  2. 忘记密码（令牌单次、过期、不泄露账号是否存在、重置后旧登录态失效）
  3. 公开接口限流
  4. 三层权限（菜单 / 操作 / 数据范围）

用法（在 backend 目录下，先启动 uvicorn）：
    .venv\\Scripts\\python.exe scripts\\check_auth.py
    .venv\\Scripts\\python.exe scripts\\check_auth.py --base http://127.0.0.1:8010/api/v1

⚠️ 建议用独立库（DB_NAME=invoice_system_e2e）跑：本脚本会注册若干测试账号且不做删除。
⚠️ 限流那一段会**故意打满配额**，所以它放在最后；跑完后同一分钟内再跑本脚本可能直接 429。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _accounts as accounts  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from scripts.e2e_check import Client  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
SUFFIX = datetime.now().strftime("%H%M%S")


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def register(client: Client, username: str, password: str, extra: dict | None = None,
             name: str = "测试用户", email: str | None = None):
    payload = {"username": username, "name": name, "password": password}
    if email is not None:
        payload["email"] = email
    if extra:
        payload.update(extra)  # 故意夹带 role_id / dept_id，验证服务端是否会忽略
    return client.post_json("/auth/register", payload)


# ---------------------------------------------------------------- 1. 注册
def case_register() -> None:
    section("1. 自助注册")

    weak_cases = [
        ("短密码", "Ab1"),
        ("纯数字", "123456789"),
        ("纯字母", "abcdefghi"),
        ("常见弱口令", "password123"),
        ("与用户名相同", None),  # 密码 = 用户名，见下
    ]
    for idx, (label, pwd) in enumerate(weak_cases):
        c = Client()
        # 用户名确定性生成（不要用 hash()，它受 PYTHONHASHSEED 影响，会让用例不可复现）
        uname = f"user{SUFFIX}" if label == "与用户名相同" else f"weak{SUFFIX}{idx}"
        status, body = register(c, uname, pwd or uname)
        check(f"弱密码被拒（{label}）", status == 400, str(body.get("detail"))[:50])

    c = Client()
    status, body = register(c, f"badmail{SUFFIX}", accounts.DEMO_PASSWORD,
                            email="not-an-email")
    check("非法邮箱被拒", status == 400, str(body.get("detail"))[:50])

    # 正常注册：**夹带 role_id / dept_id**，验证服务端忽略客户端传值
    uname = f"newbie{SUFFIX}"
    c = Client()
    status, body = register(
        c, uname, "Newbie@2026", name="新注册员工",
        email=f"{uname}@example.com",
        extra={"role_id": 2, "dept_id": 1, "role": "FINANCE", "status": "LOCKED"},
    )
    ok = status == 200
    check("正常注册成功（注册即登录）", ok, f"status={status} {str(body.get('detail') or '')[:60]}")
    if not ok:
        return

    user = body.get("user") or {}
    check("★注册者被强制为员工角色（请求里带的 role_id/role 被忽略）",
          user.get("role") == "EMPLOYEE", f"role={user.get('role')}")
    check("★注册者被强制进「待分配」部门（请求里带的 dept_id 被忽略）",
          user.get("dept") == "待分配", f"dept={user.get('dept')}")
    check("注册返回数据范围 = OWN",
          user.get("data_scope") == "OWN", f"scope={user.get('data_scope')}")
    check("注册直接下发登录态（有 csrf_token）", bool(body.get("csrf_token")))
    check("注册返回的权限码只有员工那几项",
          set(user.get("permissions") or []) ==
          {"invoice:upload", "invoice:edit", "reimb:submit", "reimb:view_own"},
          str(user.get("permissions")))

    status, me = c.get("/auth/me")
    check("注册后可直接访问 /auth/me", status == 200 and me.get("username") == uname)

    # 重复用户名
    c2 = Client()
    status, body = register(c2, uname, "Another@2026")
    check("重复用户名被拒", status == 400, str(body.get("detail"))[:50])

    # 重复邮箱
    c3 = Client()
    status, body = register(c3, f"{uname}b", "Another@2026", email=f"{uname}@example.com")
    check("重复邮箱被拒", status == 400, str(body.get("detail"))[:50])

    # 「待分配」部门的上限不能是 0：否则预审的 `if monthly_limit:` 会当成没设上限
    c4 = Client()
    uname4 = f"budget{SUFFIX}"
    status, body = register(c4, uname4, "Budget@2026", name="预算测试")
    if status == 200:
        status, draft = c4.post_json("/reimbursements", {"reason": "验证待分配部门预算生效"})
        rid = draft.get("id")
        status, up = c4.upload([("big.png", __import__("scripts.e2e_check", fromlist=["x"]).make_png(seed=77), "image/png")],
                               {"reimbursement_id": str(rid)})
        # 手工把金额改大，触发「待分配」的单笔上限（100 元）
        items = up.get("items") or []
        if items and items[0].get("status") == "DONE":
            inv_id = items[0]["invoice"]["id"]
            # 三个金额一起改，保证勾稽一致（8000 + 888 = 8888），
            # 否则会先被「金额勾稽不符」拦下，测不到预算规则
            c4.request("PATCH", f"/invoices/{inv_id}",
                       json.dumps({"amount_excl_tax": 8000.0, "tax_amount": 888.0,
                                   "total_amount": 8888.0}).encode(), "application/json")
            status, res = c4.post_json(f"/reimbursements/{rid}/submit", {})
            reasons = (res.get("detail") or {}).get("reasons") if isinstance(res.get("detail"), dict) else []
            check("★「待分配」部门的小额上限真的生效（证明上限没写成 0）",
                  status == 409 and any("单笔上限" in r for r in (reasons or [])),
                  (reasons or [str(status)])[0][:60])
        else:
            check("★「待分配」部门的小额上限真的生效（证明上限没写成 0）", False, "上传未成功")


# ------------------------------------------------------------ 2. 忘记密码
def case_forgot() -> None:
    section("2. 忘记密码")
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.rbac import PasswordResetToken
    from app.models.user import User

    # 用演示账号走完整流程
    c = Client()
    status, exist = c.post_json("/auth/forgot-password", {"account": accounts.EMPLOYEE})
    check("存在的账号：提交重置申请返回 200", status == 200, f"status={status}")

    c2 = Client()
    status2, missing = c2.post_json(
        "/auth/forgot-password", {"account": f"no-such-user-{SUFFIX}"}
    )
    check("不存在的账号：同样返回 200", status2 == 200, f"status={status2}")
    check("★两种情况的提示文案完全一致（不泄露账号是否存在）",
          exist.get("message") == missing.get("message"),
          f"{str(exist.get('message'))[:30]} vs {str(missing.get('message'))[:30]}")

    # 令牌只存哈希
    db = SessionLocal()
    try:
        user = db.execute(
            select(User).where(User.username == accounts.EMPLOYEE)
        ).scalar_one()
        rows = db.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id)
            .order_by(PasswordResetToken.id.desc())
        ).scalars().all()
        check("重置申请已入库", len(rows) >= 1, f"{len(rows)} 条")
        check("★库里只有哈希、没有明文令牌",
              all(len(r.token_hash) == 64 for r in rows),
              f"长度={[len(r.token_hash) for r in rows]}")

        # 手动造一个令牌，避免依赖 SMTP/控制台
        from app.core.security import new_reset_token, token_fingerprint

        raw = new_reset_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_fingerprint(raw),
                expires_at=datetime.now() + timedelta(minutes=30),
                delivery="MANUAL",
            )
        )
        db.commit()

        # 过期令牌
        expired = new_reset_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_fingerprint(expired),
                expires_at=datetime.now() - timedelta(minutes=1),
                delivery="MANUAL",
            )
        )
        db.commit()
    finally:
        db.close()

    c3 = Client()
    status, body = c3.post_json("/auth/reset-password", {"token": "not-a-real-token",
                                                         "new_password": "Reset@2026"})
    check("伪造令牌被拒", status == 400, str(body.get("detail"))[:50])

    c4 = Client()
    status, body = c4.post_json("/auth/reset-password", {"token": expired,
                                                         "new_password": "Reset@2026"})
    check("过期令牌被拒", status == 400, str(body.get("detail"))[:50])

    c5 = Client()
    status, body = c5.post_json("/auth/reset-password", {"token": raw, "new_password": "weak"})
    check("重置时弱密码被拒（与注册同一口径）", status == 400, str(body.get("detail"))[:50])

    c6 = Client()
    status, body = c6.post_json("/auth/reset-password", {"token": raw, "new_password": "Reset@2026"})
    check("用令牌重置成功", status == 200, str(body.get("detail") or "")[:50])

    # 重置后：新密码可登录、旧令牌不可复用
    c7 = Client()
    status, body = c7.post_json("/auth/login",
                                {"username": accounts.EMPLOYEE, "password": "Reset@2026"})
    check("重置后可用新密码登录", status == 200, f"status={status}")

    c8 = Client()
    status, body = c8.post_json("/auth/reset-password", {"token": raw, "new_password": "Again@2026"})
    check("★同一令牌不能重复使用（单次有效）", status == 400, str(body.get("detail"))[:50])

    # 恢复演示账号密码，避免影响其它脚本
    db = SessionLocal()
    try:
        from app.core.security import hash_password

        user = db.execute(
            select(User).where(User.username == accounts.EMPLOYEE)
        ).scalar_one()
        user.password_hash = hash_password(accounts.DEMO_PASSWORD)
        user.token_version += 1
        db.commit()
    finally:
        db.close()
    c9 = Client()
    status, _ = c9.post_json("/auth/login",
                             {"username": accounts.EMPLOYEE, "password": accounts.DEMO_PASSWORD})
    check("演示账号密码已复原", status == 200, f"status={status}")


# -------------------------------------------------- 3. 管理员侧与三层权限
def case_admin_and_permissions() -> None:
    section("3. 管理员视角与三层权限")
    a = Client()
    status, _ = a.post_json("/auth/login",
                            {"username": accounts.ADMIN, "password": accounts.DEMO_PASSWORD})
    check("管理员登录成功", status == 200, f"status={status}")

    status, reqs = a.get("/admin/password-reset-requests")
    check("管理员可查看待处理的重置申请", status == 200 and isinstance(reqs, list),
          f"{len(reqs) if isinstance(reqs, list) else '?'} 条")
    if isinstance(reqs, list) and reqs:
        check("申请里带用户与时间信息",
              all("username" in r and "created_at" in r for r in reqs))

    # 管理员建号也走同一条密码强度口径
    status, roles = a.get("/admin/roles")
    status, depts = a.get("/admin/departments")
    emp_role = next(r for r in roles if r["code"] == "EMPLOYEE")
    tech = next(d for d in depts if d["name"] == accounts.DEPT_TECH)

    status, body = a.post_json("/admin/users", {
        "username": f"weakcreate{SUFFIX}", "name": "弱密码建号", "password": "123456",
        "role_id": emp_role["id"], "dept_id": tech["id"],
    })
    check("管理员建号：弱密码同样被拒（口径统一）", status == 400, str(body.get("detail"))[:50])

    status, body = a.post_json("/admin/users", {
        "username": f"mailcreate{SUFFIX}", "name": "重复邮箱", "password": "Create@2026",
        "email": f"newbie{SUFFIX}@example.com",
        "role_id": emp_role["id"], "dept_id": tech["id"],
    })
    check("管理员建号：重复邮箱被拒", status == 400, str(body.get("detail"))[:50])

    status, body = a.post_json("/admin/users", {
        "username": f"finance{SUFFIX}", "name": "新财务", "password": "Finance@2026",
        "email": f"finance{SUFFIX}@example.com",
        "role_id": next(r for r in roles if r["code"] == "FINANCE")["id"],
        "dept_id": next(d for d in depts if d["name"] == accounts.DEPT_FINANCE)["id"],
    })
    check("管理员可以创建财务账号（自助注册做不到）", status == 200, f"status={status}")

    # ---- 角色 → 默认部门 ----
    # 部门只影响「申请人所在部门的预算」与数据范围；财务不提交报销单、数据范围本就是 ALL，
    # 所以部门对财务纯属展示信息 —— 不该让管理员每次手选，后端应兜成财务部。
    status, roles2 = a.get("/admin/roles")
    fin_row = next(r for r in roles2 if r["code"] == "FINANCE")
    emp_row = next(r for r in roles2 if r["code"] == "EMPLOYEE")
    check("★/admin/roles 下发角色的默认部门（财务 → 财务部）",
          fin_row.get("default_dept_name") == "财务部",
          str(fin_row.get("default_dept_name")))
    check("员工没有默认部门（部门要按实际组织分配，不能瞎给）",
          not emp_row.get("default_dept_name"), str(emp_row.get("default_dept_name")))

    status, nodept = a.post_json("/admin/users", {
        "username": f"nodept{SUFFIX}", "name": "无部门财务", "password": "Nodept@2026",
        "role_id": fin_row["id"],
    })
    check("不传部门也能创建财务账号", status == 200,
          f"status={status} {str(nodept.get('detail') or '')[:40]}")
    check("★财务账号自动落到财务部", nodept.get("dept") == "财务部", str(nodept.get("dept")))

    # 「把已有用户改成财务」这条路径也要兜默认部门（管理员最常见的操作）
    status, emp_user = a.post_json("/admin/users", {
        "username": f"promote{SUFFIX}", "name": "待提权员工", "password": "Promote@2026",
        "role_id": emp_row["id"],
    })
    check("员工账号可以不选部门创建", status == 200, f"status={status}")
    check("员工账号部门留空（没有默认部门）", not emp_user.get("dept"), str(emp_user.get("dept")))

    status, promoted = a.put_json(f"/admin/users/{emp_user['id']}", {
        "username": f"promote{SUFFIX}", "name": "待提权员工",
        "role_id": fin_row["id"], "status": "NORMAL",
    })
    check("★把已有员工的角色改成财务成功", status == 200, f"status={status}")
    check("★改角色到财务后自动分配财务部", promoted.get("dept") == "财务部",
          str(promoted.get("dept")))

    # ---- 三层权限 ----
    emp = Client()
    status, body = emp.post_json("/auth/login",
                                 {"username": accounts.EMPLOYEE,
                                  "password": accounts.DEMO_PASSWORD})
    me = body.get("user") or {}
    check("员工 /auth/me 下发 permissions", bool(me.get("permissions")), str(me.get("permissions")))
    check("员工数据范围为 OWN", me.get("data_scope") == "OWN", str(me.get("data_scope")))
    check("员工数据范围有中文说明", me.get("data_scope_text") == "仅本人",
          str(me.get("data_scope_text")))
    check("员工菜单只有员工那两项",
          sorted(m["title"] for m in me.get("menus") or []) == ["我的报销单", "新建报销单"],
          str([m["title"] for m in me.get("menus") or []]))

    fin = Client()
    status, body = fin.post_json("/auth/login",
                                 {"username": accounts.FINANCE,
                                  "password": accounts.DEMO_PASSWORD})
    fme = body.get("user") or {}
    check("财务数据范围为 ALL", fme.get("data_scope") == "ALL", str(fme.get("data_scope")))
    check("★管理员不再持有 AI 助手权限（原先是个进不去界面的死权限）",
          "agent:advice" not in (a.get("/auth/me")[1].get("user") or {}).get("permissions", []),
          str((a.get("/auth/me")[1].get("user") or {}).get("permissions")))
    check("★管理员不再持有业务审核权限 reimb:review",
          "reimb:review" not in (a.get("/auth/me")[1].get("user") or {}).get("permissions", []))

    # 操作权限：员工不能调财务/AI 接口
    for path, label in (("/review/todos", "待办审核"),
                        ("/agent/status", "AI 助手"),
                        ("/review/logs/query", "审核轨迹"),
                        ("/admin/users", "用户管理")):
        status, _ = emp.get(path)
        check(f"员工访问「{label}」被拒 403", status == 403, f"status={status}")

    status, _ = fin.get("/admin/users")
    check("财务访问「用户管理」被拒 403", status == 403, f"status={status}")

    # ---- 部门维护归财务（管理员只读）----
    # 预算上限本来就是个「钱」的事，归财务；但管理员的「用户管理」要按部门给用户归属，
    # 所以读必须留给管理员（DEPT_VIEW），建/改只给财务（DEPT_MANAGE）。
    status, depts = fin.get("/admin/departments")
    check("财务可读部门列表（含预算上限）",
          status == 200 and all("monthly_limit" in d for d in depts),
          f"{len(depts) if isinstance(depts, list) else '?'} 个")
    status, made = fin.post_json(
        "/admin/departments",
        {"name": f"财务新建部{SUFFIX}", "single_limit": 2000, "monthly_limit": 20000},
    )
    check("★财务可新建部门", status == 200, f"status={status} {str(made.get('detail') or '')[:40]}")
    if status == 200:
        status, _ = fin.put_json(
            f"/admin/departments/{made['id']}",
            {"name": f"财务新建部{SUFFIX}", "single_limit": 3000, "monthly_limit": 30000},
        )
        check("★财务可修改部门预算上限", status == 200, f"status={status}")

    status, admin_depts = a.get("/admin/departments")
    check("管理员仍可读部门列表（用户管理的部门下拉框要用）",
          status == 200 and len(admin_depts) > 0, f"status={status}")
    status, body = a.post_json(
        "/admin/departments",
        {"name": f"越权部门{SUFFIX}", "single_limit": 100, "monthly_limit": 100},
    )
    check("★管理员不能新建部门 403（已移交财务）", status == 403,
          f"status={status} {str(body.get('detail') or '')[:30]}")
    if isinstance(depts, list) and depts:
        status, body = a.put_json(
            f"/admin/departments/{depts[0]['id']}",
            {"name": depts[0]["name"], "single_limit": depts[0]["single_limit"],
             "monthly_limit": depts[0]["monthly_limit"]},
        )
        check("★管理员不能改部门预算上限 403", status == 403, f"status={status}")

    status, _ = emp.get("/admin/departments")
    check("员工读部门列表 403", status == 403, f"status={status}")

    # 数据范围：/users 只有 ALL 范围能看到全部
    status, mine = emp.get("/users")
    check("员工 /users 只返回自己（OWN 范围）",
          status == 200 and len(mine) == 1 and mine[0]["username"] == accounts.EMPLOYEE,
          f"{len(mine) if isinstance(mine, list) else '?'} 条")
    status, allu = fin.get("/users")
    check("财务 /users 返回全部（ALL 范围）",
          status == 200 and len(allu) > 1, f"{len(allu) if isinstance(allu, list) else '?'} 条")

    # 跨部门员工也看不到别人的单据
    other = Client()
    status, _ = other.post_json("/auth/login",
                                {"username": accounts.EMPLOYEE_OTHER_DEPT,
                                 "password": accounts.DEMO_PASSWORD})
    check("跨部门员工登录成功", status == 200, f"status={status}")
    status, todo = emp.post_json("/reimbursements", {"reason": "跨部门隔离验证"})
    rid = todo.get("id")
    status, _ = other.get(f"/reimbursements/{rid}")
    check("★跨部门员工访问他人单据 403（OWN 范围）", status == 403, f"status={status}")


# ---------------------------------------------------------- 5. 自助修改密码
def case_change_password() -> None:
    section("5. 登录后自助修改密码")
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models.user import User

    NEW_PWD = "Changed@2026"

    # 本机会话（用员工：改完密码还要发一个写请求，财务没有 reimb:submit 权限）
    me_client = Client()
    status, _ = me_client.post_json("/auth/login",
                                    {"username": accounts.EMPLOYEE,
                                     "password": accounts.DEMO_PASSWORD})
    check("员工登录成功", status == 200, f"status={status}")
    old_csrf = me_client.cookie("csrf_token")
    # 另一个「设备」的会话：改密码后它应该被踢下线
    other_device = Client()
    status, _ = other_device.post_json("/auth/login",
                                       {"username": accounts.EMPLOYEE,
                                        "password": accounts.DEMO_PASSWORD})
    status, _ = other_device.get("/auth/me")
    check("第二个设备的会话有效（改密前）", status == 200, f"status={status}")

    status, body = me_client.post_json("/auth/change-password",
                                       {"old_password": "WrongOld1", "new_password": NEW_PWD})
    check("原密码错误被拒", status == 400, str(body.get("detail"))[:40])

    status, body = me_client.post_json("/auth/change-password",
                                       {"old_password": accounts.DEMO_PASSWORD,
                                        "new_password": "123456"})
    check("新密码不满足强度被拒（与注册同一口径）", status == 400, str(body.get("detail"))[:40])

    status, body = me_client.post_json(
        "/auth/change-password",
        {"old_password": accounts.DEMO_PASSWORD, "new_password": accounts.DEMO_PASSWORD},
    )
    check("新密码与原密码相同被拒", status == 400, str(body.get("detail"))[:40])

    status, body = me_client.post_json("/auth/change-password",
                                       {"old_password": accounts.DEMO_PASSWORD,
                                        "new_password": NEW_PWD})
    check("修改密码成功", status == 200, str(body.get("message"))[:40])

    # 改密码会**轮换 CSRF Token**。前端曾经把 token 缓存进模块变量并优先用缓存，
    # 于是改完密码后下一个写请求固定 403（要刷新页面才恢复）——这里把服务端契约钉住：
    # 新 token 必须可用、旧 token 必须失效，否则前端「以 Cookie 为准」的修法就失去依据。
    new_csrf = me_client.cookie("csrf_token")
    check("★改密码后 CSRF Token 已轮换（新旧不同）",
          bool(new_csrf) and new_csrf != old_csrf)
    check("★响应体带回新 csrf_token，供前端同步",
          bool(body.get("csrf_token")) and body.get("csrf_token") == new_csrf)

    status, draft = me_client.post_json("/reimbursements", {"reason": "改密码后的写操作"})
    check("★用轮换后的 token 发写请求成功（客户端不再 403）",
          status == 200, f"status={status} {str(draft.get('detail') or '')[:40]}")

    status, _ = me_client.get("/auth/me")
    check("★本机会话仍然有效（不会把自己踢下线）", status == 200, f"status={status}")
    status, _ = other_device.get("/auth/me")
    check("★另一个设备的会话已失效（token_version 生效）", status in (401, 403),
          f"status={status}")

    c = Client()
    status, _ = c.post_json("/auth/login",
                            {"username": accounts.EMPLOYEE, "password": accounts.DEMO_PASSWORD})
    check("旧密码不再能登录", status == 401, f"status={status}")
    c2 = Client()
    status, _ = c2.post_json("/auth/login",
                             {"username": accounts.EMPLOYEE, "password": NEW_PWD})
    check("新密码可以登录", status == 200, f"status={status}")

    # 复原演示账号密码，避免影响其它脚本
    db = SessionLocal()
    try:
        user = db.execute(
            select(User).where(User.username == accounts.EMPLOYEE)
        ).scalar_one()
        user.password_hash = hash_password(accounts.DEMO_PASSWORD)
        user.token_version += 1
        db.commit()
    finally:
        db.close()
    c3 = Client()
    status, _ = c3.post_json("/auth/login",
                             {"username": accounts.EMPLOYEE,
                              "password": accounts.DEMO_PASSWORD})
    check("演示账号密码已复原", status == 200, f"status={status}")


# ---------------------------------------------------------- 6. 退出登录
def case_logout() -> None:
    """退出登录必须是**服务端失效**，不能只靠前端删 Cookie。

    这条以前没有任何验收覆盖，而它恰恰是「怎么从自动登录状态里出来」的答案：
    退出后 access/refresh 都应立即失效，且不能再续期。
    """
    section("6. 退出登录（会话在服务端彻底失效）")
    c = Client()
    status, _ = c.post_json("/auth/login",
                            {"username": accounts.EMPLOYEE,
                             "password": accounts.DEMO_PASSWORD})
    check("登录成功", status == 200, f"status={status}")
    status, _ = c.get("/auth/me")
    check("登录后 /auth/me 可用", status == 200, f"status={status}")

    status, _ = c.post_json("/auth/logout", {})
    check("退出登录返回 200", status == 200, f"status={status}")
    status, _ = c.get("/auth/me")
    check("★退出后 /auth/me 返回 401", status == 401, f"status={status}")
    status, _ = c.post_json("/auth/refresh", {})
    check("★退出后不能用旧 Refresh 续期（服务端已撤销）",
          status in (401, 403), f"status={status}")

    # 重新登录应恢复正常，别把账号搞坏
    c2 = Client()
    status, _ = c2.post_json("/auth/login",
                             {"username": accounts.EMPLOYEE,
                              "password": accounts.DEMO_PASSWORD})
    check("退出后仍可重新登录", status == 200, f"status={status}")


# ---------------------------------------------------------------- 7. 限流
def case_ratelimit() -> None:
    section("7. 公开接口限流（本段会故意打满配额，故放在最后）")
    # 用同一个账号连打，触发「按账号」的收紧限制（默认 3 次/分钟）。
    # 用不存在的账号名，避免给真实账号产生一堆待处理申请。
    probe = f"rate-limit-probe-{SUFFIX}"
    c = Client()
    saw_429 = False
    status = 0
    detail = ""
    for i in range(8):
        status, body = c.post_json("/auth/forgot-password", {"account": probe})
        detail = str(body.get("detail") or body.get("message") or "")[:50]
        if status == 429:
            saw_429 = True
            check("同一账号重复提交触发 429 限流", True, f"第 {i + 1} 次被拦：{detail}")
            break
        time.sleep(0.05)
    if not saw_429:
        check("同一账号重复提交触发 429 限流", False,
              f"8 次都返回 {status}；请确认 FORGOT_ACCOUNT_RATE_LIMIT_PER_MINUTE 未被配成 0")


def main() -> int:
    args = sys.argv[1:]
    if "--base" in args:
        import scripts.e2e_check as e2e

        e2e.BASE = args[args.index("--base") + 1]

    import scripts.e2e_check as e2e

    print(f"目标: {e2e.BASE}")
    print("提示：建议对独立库 invoice_system_e2e 运行（本脚本会注册测试账号且不清理）")

    case_register()
    case_forgot()
    case_admin_and_permissions()
    case_change_password()
    case_logout()
    case_ratelimit()

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
