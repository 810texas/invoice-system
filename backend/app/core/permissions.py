"""权限模型：**三层** —— 菜单权限 / 操作权限 / 数据权限。

| 层 | 落在哪里 | 回答的问题 |
| :--- | :--- | :--- |
| **菜单权限** | `role` → `menu` → `role_menu` 表（种子见 `scripts/init_db.py` 的 `MENUS`） | 能看到哪些页面 |
| **操作权限** | 本文件的权限码常量 + `ROLE_PERMISSIONS` | 能调哪些接口 / 点哪些按钮 |
| **数据权限** | 本文件的 `ROLE_DATA_SCOPE` | 能看到**谁**的数据 |

需求 4.1 明确「三角色边界固定，本期不做可配置权限矩阵」——权限矩阵入库是需求第七章的二期项，
所以前两层仍用常量表达；但第三层从「散落的角色名判断」改成了**声明式数据范围**：

原先 `is_finance_like()` 被 `api/v1/meta.py`、`api/v1/upload.py`、`services/reimbursement.py`
各判一次，加第四个角色就要改三处、还容易漏。现在只改 `ROLE_DATA_SCOPE` 一行，
统一入口是 `services/reimbursement.py` 的 `visible_applicant_ids()` / `can_access_owner()`。
"""
from fastapi import HTTPException, status

ROLE_EMPLOYEE = "EMPLOYEE"
ROLE_FINANCE = "FINANCE"
ROLE_ADMIN = "ADMIN"

# 权限码
INVOICE_UPLOAD = "invoice:upload"        # 员工：上传发票
INVOICE_EDIT = "invoice:edit"            # 员工：修正识别字段
REIMB_SUBMIT = "reimb:submit"            # 员工：提交报销单
REIMB_VIEW_OWN = "reimb:view_own"        # 员工：查看本人单据
REIMB_REVIEW = "reimb:review"            # 财务：通过 / 驳回
REIMB_VIEW_ALL = "reimb:view_all"        # 财务：跨用户查询
REVIEW_LOG_VIEW = "review:log"           # 审核轨迹查询
AGENT_ADVICE = "agent:advice"            # 财务：调 AI 审核助手出建议
ADMIN_USER_MANAGE = "admin:user"         # 管理员：用户管理
ADMIN_BASE_DATA = "admin:base"           # 管理员：基础数据
# 部门（含单笔/月度预算上限）的维护归**财务**：预算限额本来就是财务的决定，不是系统管理员的决定。
# 为什么拆成两个权限码而不是一个：管理员的「用户管理」要用部门列表填下拉框，
# 所以「读」必须留给管理员，而「建/改」要真正交给财务 ——
# 合成一个码的话，要么管理员建号时选不了部门，要么财务并没有真正拿到这个板块。
DEPT_VIEW = "dept:view"                  # 财务 + 管理员：查看部门与预算上限
DEPT_MANAGE = "dept:manage"              # 财务：新建 / 修改部门与预算上限

ROLE_PERMISSIONS: dict[str, set[str]] = {
    ROLE_EMPLOYEE: {
        INVOICE_UPLOAD,
        INVOICE_EDIT,
        REIMB_SUBMIT,
        REIMB_VIEW_OWN,
    },
    ROLE_FINANCE: {
        REIMB_REVIEW,
        REIMB_VIEW_ALL,
        REVIEW_LOG_VIEW,
        AGENT_ADVICE,
        # 部门与预算上限由财务维护（这才是「钱」的事）
        DEPT_VIEW,
        DEPT_MANAGE,
    },
    # 管理员只做系统管理，**不参与业务审核**（沿用原设计意图）。
    # 特别说明：这里**不给** AGENT_ADVICE。AI 助手挂在「待办审核」抽屉里，
    # 而该菜单与 /review/todos 都只开放给财务 —— 给管理员就是一个永远用不到的死权限
    # （之前确实是这样：持有 agent:advice 却没有任何入口）。
    # 同理不给 REIMB_VIEW_ALL：管理员不做跨用户业务查询，审核轨迹走 REVIEW_LOG_VIEW。
    ROLE_ADMIN: {
        ADMIN_USER_MANAGE,
        ADMIN_BASE_DATA,
        REVIEW_LOG_VIEW,
        # 只读部门：管理员的「用户管理」要按部门给用户归属，下拉框的数据来自这个接口。
        # 但**不给 DEPT_MANAGE** —— 预算上限的调整权已交给财务。
        DEPT_VIEW,
    },
}

# ---------------- 第三层：数据权限 ----------------
DATA_SCOPE_OWN = "OWN"    # 只看自己的数据
DATA_SCOPE_DEPT = "DEPT"  # 看本部门所有成员的（当前三角色未使用，为将来「部门主管」预留）
DATA_SCOPE_ALL = "ALL"    # 看全部

ROLE_DATA_SCOPE: dict[str, str] = {
    ROLE_EMPLOYEE: DATA_SCOPE_OWN,
    ROLE_FINANCE: DATA_SCOPE_ALL,
    ROLE_ADMIN: DATA_SCOPE_ALL,
}

# 数据范围的说明文案，用于 /auth/me 让前端也能解释自己能看到什么
DATA_SCOPE_TEXT = {
    DATA_SCOPE_OWN: "仅本人",
    DATA_SCOPE_DEPT: "本部门",
    DATA_SCOPE_ALL: "全部",
}


def has_permission(role_code: str, permission: str) -> bool:
    """操作权限校验：三角色边界固定，管理员不隐式继承财务的业务权限。"""
    return permission in ROLE_PERMISSIONS.get(role_code, set())


def require_permission(role_code: str, permission: str) -> None:
    """后端权限码校验，失败抛 403（需求 5.2 前后端双重校验）。"""
    if not has_permission(role_code, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权执行该操作"
        )


def permissions_of(role_code: str) -> list[str]:
    """该角色的权限码列表（/auth/me 下发给前端做权限驱动路由与按钮级控制）。"""
    return sorted(ROLE_PERMISSIONS.get(role_code, set()))


def data_scope_of(role_code: str) -> str:
    """该角色的数据范围。未知角色一律按最小权限处理（只看自己）。"""
    return ROLE_DATA_SCOPE.get(role_code, DATA_SCOPE_OWN)


def can_view_all_data(role_code: str) -> bool:
    return data_scope_of(role_code) == DATA_SCOPE_ALL


def can_view_dept_data(role_code: str) -> bool:
    return data_scope_of(role_code) in (DATA_SCOPE_DEPT, DATA_SCOPE_ALL)
