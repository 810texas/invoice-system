"""一键走完全流程的演示脚本（给汇报/自测用）。

按真实业务顺序逐步执行，并打印每一步的服务端返回，便于现场展示与核对：

  1 员工登录            2 创建报销单草稿      3 上传 2 张发票并 OCR 自动填单
  4 查看聚合结果        5 人工修正字段+留痕   6 提交前预审预览
  7 提交报销单          8 重复报销硬拦截      9 财务驳回（必填原因）
 10 申请人查看驳回原因  11 重新提交（版本+1） 12 财务通过
 13 审核轨迹            14 越权防护         15 管理员基础数据

用法（在 backend 目录下，需先启动 uvicorn）：
    .venv\\Scripts\\python.exe scripts\\demo_flow.py
    .venv\\Scripts\\python.exe scripts\\demo_flow.py --reset   # 先清库重建再演示
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 中文 Windows 控制台默认 GBK，统一按 UTF-8 输出，避免个别字符打断打印
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _accounts as accounts  # noqa: E402  scripts 目录自身在 sys.path 上
from scripts.e2e_check import Client, login, make_png  # noqa: E402

SUFFIX = uuid.uuid4().hex[:4].upper()
OK = 0
NG = 0


def title(step: str, text: str) -> None:
    print(f"\n{'=' * 72}\n【{step}】{text}\n{'=' * 72}")


def show(data, limit: int = 900) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    print(text if len(text) <= limit else text[:limit] + "…")


def expect(cond: bool, desc: str, extra: str = "") -> None:
    global OK, NG
    if cond:
        OK += 1
        print(f"  [OK]   {desc}{(' — ' + extra) if extra else ''}")
    else:
        NG += 1
        print(f"  [BAD]  {desc}{(' — ' + extra) if extra else ''}")


def main() -> int:
    if "--reset" in sys.argv:
        from scripts.init_db import main as init_main

        print(">>> 先执行清库重建 …")
        # 必须带上 --demo：默认种子只引导一个管理员，本脚本要用的是 _accounts.py 里那套演示账号，
        # 只跑 --drop 会把它们洗掉，脚本紧接着就会登录失败。
        sys.argv = [sys.argv[0], "--drop", "--demo"]
        init_main()
        time.sleep(1)

    # 连通性自检
    try:
        status, _ = Client().get("/health")
        if status != 200:
            raise RuntimeError(f"health 返回 {status}")
    except Exception as exc:
        print(f"后端不可达（{exc}）。请先在 backend 目录启动：")
        print("  .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000")
        return 2

    # ---------------------------------------------------------------- 1
    title("1/15", f"员工登录（{accounts.EMPLOYEE} / {accounts.DEMO_PASSWORD}）")
    emp = Client()
    status, body = login(emp, accounts.EMPLOYEE)
    show({"status": status, "user": body.get("user")})
    expect(status == 200, "登录成功", f"角色={body.get('user', {}).get('role')}")
    expect(bool(body.get("user", {}).get("menus")), "后端按其角色返回可见菜单",
           str([m["title"] for m in body.get("user", {}).get("menus", [])]))

    # ---------------------------------------------------------------- 2
    title("2/15", "创建报销单草稿（先存草稿再提交）")
    status, draft = emp.post_json("/reimbursements", {"reason": "8 月华东区客户拜访差旅报销"})
    rid = draft["id"]
    show(draft)
    expect(status == 200 and draft["status"] == "DRAFT", "草稿创建成功", f"单号 {draft.get('code')}")

    # ---------------------------------------------------------------- 3
    title("3/15", "批量上传 2 张 JPG/PNG 发票 → OCR 自动填单")
    status, up = emp.upload(
        [("fapiao_A.png", make_png(seed=101), "image/png"),
         ("fapiao_B.png", make_png(seed=202), "image/png")],
        {"reimbursement_id": str(rid)},
    )
    items = up.get("items", [])
    show([{"file": i + 1, "status": it["status"], "elapsed_ms": it["elapsed_ms"],
           "invoice": {k: it["invoice"][k] for k in
                       ("invoice_type", "invoice_no", "buyer_name", "total_amount",
                        "ocr_confidence", "need_confirm", "category")}}
          for i, it in enumerate(items)])
    expect(status == 200 and len(items) == 2 and all(i["status"] == "DONE" for i in items),
           "2 张全部识别完成")
    expect(all(i["elapsed_ms"] < 3000 for i in items), "单张识别耗时 ≤3 秒",
           f"{[i['elapsed_ms'] for i in items]} ms")

    invoices = [i["invoice"] for i in items]
    first = invoices[0]

    # 让演示可重复：把识别到的号码改成带本次随机后缀的唯一号码，
    # 避免与上一次演示残留的发票撞查重键（真实使用时这一步就是人工核对修正）。
    status, fixed = emp.patch_json(f"/invoices/{first['id']}", {
        "invoice_type": "蓝字",
        "invoice_code": f"04400190{SUFFIX}",
        "invoice_no": f"8{SUFFIX}0001",
        "amount_excl_tax": 838.57, "tax_amount": 50.31, "total_amount": 888.88,
        "category": "交通", "buyer_name": "示例科技有限公司",
    })
    show({"unique_fix": fixed.get("changed"), "invoice": fixed.get("invoice")})
    expect(status == 200, "发票号码已修正为本次唯一号", f"{fixed.get('invoice', {}).get('invoice_code')}-{fixed.get('invoice', {}).get('invoice_no')}")
    first = fixed["invoice"]
    invoices[0] = first

    status, fixed2 = emp.patch_json(f"/invoices/{invoices[1]['id']}", {
        "invoice_type": "蓝字",
        "invoice_code": f"04400191{SUFFIX}",
        "invoice_no": f"8{SUFFIX}0002",
        "amount_excl_tax": 542.38, "tax_amount": 30.70, "total_amount": 573.08,
        "category": "餐饮", "buyer_name": "示例科技有限公司",
    })
    invoices[1] = fixed2["invoice"]

    # ---------------------------------------------------------------- 4
    title("4/15", "查看草稿聚合结果（自动回写金额与张数）")
    status, detail = emp.get(f"/reimbursements/{rid}")
    show({"code": detail["code"], "invoice_count": detail["invoice_count"],
          "total_amount": detail["total_amount"], "status": detail["status"],
          "invoices": [{"id": i["id"], "invoice_no": i["invoice_no"],
                        "total_amount": i["total_amount"], "category": i["category"],
                        "need_confirm": i["need_confirm"]} for i in detail["invoices"]]})
    expect(detail["invoice_count"] == 2, "发票张数已聚合", str(detail["invoice_count"]))
    expect(detail["total_amount"] > 0, "总金额已聚合", str(detail["total_amount"]))
    pending = [i["id"] for i in detail["invoices"] if i["need_confirm"]]
    print(f"  说明：待人工确认（置信度 <0.85）的发票 {pending or '无'}；本演示会统一修正金额与费用类别")

    # ---------------------------------------------------------------- 5
    title("5/15", "人工修正字段 + 查看留痕（原值/新值/修改人/时间）")
    inv_id = first["id"]
    status, patched = emp.patch_json(f"/invoices/{inv_id}", {
        "amount_excl_tax": 800.00, "tax_amount": 48.00, "total_amount": 848.00,
        "category": "交通",
    })
    show({"changed": patched.get("changed"), "invoice": patched.get("invoice")})
    expect(status == 200 and patched.get("changed"), "修正成功", f"变更字段 {patched.get('changed')}")

    status, history = emp.get(f"/invoices/{inv_id}/changes")
    show(history[:4])
    expect(status == 200 and len(history) > 0, "留痕已生成", f"{len(history)} 条")

    status, detail = emp.get(f"/reimbursements/{rid}")
    missing = [i["invoice_no"] for i in detail["invoices"] if not i["category"]]
    expect(not missing, "全部发票均已选费用类别", f"未填 {missing or '无'}")

    # ---------------------------------------------------------------- 6
    title("6/15", "提交前预审预览（只读，不改状态）")
    status, preview = emp.get(f"/reimbursements/audit?reimbursement_id={rid}")
    show(preview)
    expect(status == 200, "预审预览可用",
           f"blocked={preview.get('blocked')} risk={preview.get('risk_level')}")

    # ---------------------------------------------------------------- 7
    title("7/15", "提交报销单 → 待审核")
    status, sub = emp.post_json(f"/reimbursements/{rid}/submit", {})
    show(sub)
    expect(status == 200 and sub["reimbursement"]["status"] == "PENDING",
           "提交成功，状态 PENDING", f"risk={sub.get('preaudit', {}).get('risk_level')}")

    # ---------------------------------------------------------------- 8
    title("8/15", "重复报销硬拦截：拿同一张发票再建一单提交")
    status, draft2 = emp.post_json("/reimbursements", {"reason": "重复报销验证单"})
    rid2 = draft2["id"]
    status, up2 = emp.upload([("fapiao_A_copy.png", make_png(seed=101), "image/png")],
                             {"reimbursement_id": str(rid2)})
    copy_inv = up2["items"][0]["invoice"]
    print(f"  复制件识别结果：{copy_inv['invoice_type']} {copy_inv['invoice_code']}-{copy_inv['invoice_no']}"
          f"（与首单发票共 {len([i for i in emp.get('/reimbursements/' + str(rid))[1]['invoices']])} 张）")

    # 改写为与已占用发票完全相同的「代码 + 号码」；数电票无代码，统一补成带代码的形式
    status, copied = emp.patch_json(f"/invoices/{copy_inv['id']}", {
        "invoice_type": "蓝字",
        "invoice_code": first["invoice_code"],
        "invoice_no": first["invoice_no"],
        "amount_excl_tax": 838.57, "tax_amount": 50.31, "total_amount": 888.88,
        "category": "交通", "buyer_name": "示例科技有限公司",
    })
    expect(status == 200 and "invoice_no" in (copied.get("changed") or []),
           "复制件已改写为与已报销发票同号",
           f"{copied.get('invoice', {}).get('invoice_code')}-{copied.get('invoice', {}).get('invoice_no')}")

    status, blocked = emp.post_json(f"/reimbursements/{rid2}/submit", {})
    show(blocked)
    reasons = (blocked.get("detail") or {}).get("reasons", []) if isinstance(blocked.get("detail"), dict) else []
    print(f"  [debug] http={status} 本单号={draft2['code']} 比对发票={first['invoice_code']}-{first['invoice_no']}")
    expect(status == 409 and reasons, "被硬拦截（HTTP 409）并给出原因", (reasons or [""])[0][:70])
    expect(f"发票 {first['invoice_no']}" in (reasons[0] if reasons else ""),
           "拦截原因精确指向该发票号", (reasons or [""])[0][:40])

    # ---------------------------------------------------------------- 9
    title("9/15", "财务登录：驳回必填原因")
    fin = Client()
    status, fbody = login(fin, accounts.FINANCE)
    show({"status": status, "user": fbody.get("user")})
    expect(status == 200 and fbody["user"]["role"] == "FINANCE", "财务登录成功")

    status, bad = fin.post_json(f"/review/{rid}/reject", {"comment": ""})
    expect(status == 400, "驳回不填原因被拒（400）", str(bad.get("detail"))[:40])

    comment = "发票抬头与公司主体不一致，请补充说明后重新提交"
    status, rej = fin.post_json(f"/review/{rid}/reject", {"comment": comment})
    show(rej)
    expect(status == 200 and rej["status"] == "REJECTED", "驳回成功，状态 REJECTED")

    # ---------------------------------------------------------------- 10
    title("10/15", "申请人查看驳回原因")
    status, mine = emp.get("/reimbursements/mine")
    row = next((r for r in mine if r["id"] == rid), {})
    show([r for r in mine if r["id"] == rid])
    expect(row.get("review_comment") == comment, "驳回原因对申请人可见", row.get("review_comment", "")[:40])

    # ---------------------------------------------------------------- 11
    title("11/15", "驳回后重新提交（版本号 +1）")
    old_version = row.get("version", 1)
    status, resub = emp.post_json(f"/reimbursements/{rid}/resubmit", {})
    show(resub)
    expect(status == 200 and resub["reimbursement"]["version"] == old_version + 1,
           "重提成功且版本号 +1", f"v{old_version} → v{resub.get('reimbursement', {}).get('version')}")

    # ---------------------------------------------------------------- 12
    title("12/15", "财务通过 → 终态")
    status, appr = fin.post_json(f"/review/{rid}/approve", {"comment": "核对无误，同意报销"})
    show(appr)
    expect(status == 200 and appr["status"] == "APPROVED", "通过成功，状态 APPROVED")

    status, attempt = emp.patch_json(f"/invoices/{inv_id}", {"total_amount": 1.0})
    expect(status == 400, "APPROVED 后不可再修改发票", str(attempt.get("detail"))[:40])

    # ---------------------------------------------------------------- 13
    title("13/15", "审核轨迹（提交→驳回→重提→提交→通过 全程保留）")
    status, logs = emp.get(f"/reimbursements/{rid}")
    action_text = {"SUBMIT": "提交", "APPROVE": "通过", "REJECT": "驳回",
                   "RESUBMIT_DRAFT": "驳回后重新编辑"}
    chain = [f"{action_text.get(lg['action'], lg['action'])}/{lg['status'] if 'status' in lg else lg['result']}"
             for lg in logs["logs"]]
    show(logs["logs"])
    expect(len(logs["logs"]) >= 5, "轨迹条数完整", " → ".join(chain))

    status, queried = fin.get("/review/logs/query")
    expect(status == 200 and len(queried) > 0, "审核轨迹查询可用", f"{len(queried)} 条")

    # ---------------------------------------------------------------- 14
    title("14/15", "越权防护：员工 B 访问员工 A 的单据")
    emp2 = Client()
    login(emp2, accounts.EMPLOYEE2)
    status, denied = emp2.get(f"/reimbursements/{rid}")
    show(denied)
    expect(status == 403, "返回 403 且无数据泄露", str(denied.get("detail"))[:40])

    status, _ = emp2.request("GET", f"/attachments/{first['attachment_id']}/raw")
    expect(status == 403, "他人发票影像同样 403", f"status={status}")

    # ---------------------------------------------------------------- 15
    title("15/15", "管理员：用户 / 基础数据；财务：部门维护")
    adm = Client()
    login(adm, accounts.ADMIN)
    status, users = adm.get("/admin/users")
    expect(status == 200, "用户列表可查", f"{len(users)} 人")
    # 部门维护已归财务；管理员仍可只读（「用户管理」里给用户选部门要用这份列表）
    status, depts = fin.get("/admin/departments")
    show(depts)
    expect(status == 200, "财务可查部门与单笔/月度上限")
    status, roles = adm.get("/admin/roles")
    expect(status == 200, "角色权限码可见", str([r["code"] for r in roles]))
    status, _ = fin.get("/admin/users")
    expect(status == 403, "财务访问「用户管理」仍被拒 403", f"status={status}")

    # ---------------------------------------------------------------- 收尾
    # 再留一张「待审核」单据，使库里同时存在 已通过 / 待审核 / 草稿 三种状态，便于演示
    title("收尾", "再补一张待审核单据，凑齐三种状态便于演示")
    status, draft3 = emp.post_json("/reimbursements", {"reason": "市场推广物料采购报销（演示用待审核单）"})
    rid3 = draft3["id"]
    status, up3 = emp.upload([("fapiao_C.png", make_png(seed=303), "image/png")],
                             {"reimbursement_id": str(rid3)})
    inv3 = up3["items"][0]["invoice"]
    status, fixed3 = emp.patch_json(f"/invoices/{inv3['id']}", {
        "invoice_type": "蓝字",
        "invoice_code": f"04400192{SUFFIX}",
        "invoice_no": f"8{SUFFIX}0003",
        "amount_excl_tax": 1200.00, "tax_amount": 72.00, "total_amount": 1272.00,
        "category": "办公", "buyer_name": "示例科技有限公司",
    })
    status, sub3 = emp.post_json(f"/reimbursements/{rid3}/submit", {})
    expect(status == 200 and sub3["reimbursement"]["status"] == "PENDING",
           "待审核单据已生成", sub3.get("reimbursement", {}).get("code", ""))

    status, mine = emp.get("/reimbursements/mine")
    states = sorted({r["status"] for r in mine})
    print(f"  当前库内单据状态：{states}")

    # ---------------------------------------------------------------- 汇总
    total = OK + NG
    print(f"\n{'=' * 72}")
    verdict = "全部符合预期" if NG == 0 else f"{NG} 项异常，请检查上面的 [BAD] 行"
    print(f"演示流程结束：{OK}/{total} 步符合预期 —— {verdict}")
    print(f"本次演示单号：{draft['code']}（已通过）、{draft2['code']}（草稿·重复拦截）、{draft3['code']}（待审核）")
    print(f"演示数据已落库，可切到前端用 {accounts.EMPLOYEE} / {accounts.FINANCE} 账号查看")
    print("=" * 72)
    return 0 if NG == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
