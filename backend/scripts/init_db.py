"""初始化数据库：建表 + 种子数据 + 生成 DDL。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts\\init_db.py          # 建表 + 引导一个管理员（真实场景）
    .venv\\Scripts\\python.exe scripts\\init_db.py --demo   # 额外灌入演示/验收账号
    .venv\\Scripts\\python.exe scripts\\init_db.py --ddl    # 仅打印 DDL，不连库改结构
    .venv\\Scripts\\python.exe scripts\\init_db.py --drop   # 先删表再重建（危险）

**默认不灌演示账号**：真实使用场景是「管理员引导 → 财务由管理员创建 → 员工自助注册」。
只有需要跑验收脚本或在本地演示时，才加 `--demo` 灌入 `scripts/_accounts.py` 里那几个账号。
"""
from __future__ import annotations

import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

import _accounts as accounts  # noqa: E402  scripts 目录自身在 sys.path 上
from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.permissions import (  # noqa: E402
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_FINANCE,
)
from app.core.security import hash_password, password_policy_error  # noqa: E402
import app.models  # noqa: E402,F401  导入即注册全部表
from app.models.rbac import Menu, Role  # noqa: E402
from app.models.user import Department, User  # noqa: E402

settings = get_settings()

# 菜单种子：按角色可见性（需求 3.3 第 3 项）
MENUS = [
    ("我的报销单", "/my-reimbursements", "Document", 10, [ROLE_EMPLOYEE]),
    ("新建报销单", "/new-reimbursement", "Plus", 20, [ROLE_EMPLOYEE]),
    ("待办审核", "/review/todos", "List", 30, [ROLE_FINANCE]),
    ("审核轨迹", "/review/logs", "Clock", 40, [ROLE_FINANCE, ROLE_ADMIN]),
    ("用户管理", "/admin/users", "User", 50, [ROLE_ADMIN]),
    # 部门与预算上限归**财务**维护：预算限额是财务的决定，不是系统管理员的。
    # 管理员的「用户管理」仍然能读到部门列表（DEPT_VIEW），但没有改的权限。
    ("部门维护", "/admin/departments", "OfficeBuilding", 60, [ROLE_FINANCE]),
    ("基础数据", "/admin/base", "Setting", 70, [ROLE_ADMIN]),
]

ROLES = [
    (ROLE_EMPLOYEE, "普通员工"),
    (ROLE_FINANCE, "财务人员"),
    (ROLE_ADMIN, "管理员"),
]

DEPARTMENTS = [
    (accounts.DEPT_TECH, 5000.00, 50000.00),
    (accounts.DEPT_MARKET, 8000.00, 80000.00),
    (accounts.DEPT_FINANCE, 3000.00, 30000.00),
    (accounts.DEPT_HR, 3000.00, 20000.00),
    # 自助注册用户的默认归属。上限刻意很小，但**不能为 0** ——
    # 预审规则写的是 `if monthly_limit:`，0 会被当成「没设上限」从而完全不检查预算。
    (
        settings.DEFAULT_DEPT_NAME,
        settings.DEFAULT_DEPT_SINGLE_LIMIT,
        settings.DEFAULT_DEPT_MONTHLY_LIMIT,
    ),
]

# 演示/验收账号（仅 --demo 时创建）。用户名与密码统一来自 scripts/_accounts.py
DEMO_USERS = [
    (accounts.EMPLOYEE, "张伟", ROLE_EMPLOYEE, accounts.DEPT_TECH),
    (accounts.EMPLOYEE2, "刘洋", ROLE_EMPLOYEE, accounts.DEPT_TECH),
    (accounts.EMPLOYEE_OTHER_DEPT, "陈静", ROLE_EMPLOYEE, accounts.DEPT_MARKET),
    (accounts.FINANCE, "王芳", ROLE_FINANCE, accounts.DEPT_FINANCE),
    (accounts.ADMIN, "赵敏", ROLE_ADMIN, accounts.DEPT_FINANCE),
]


def print_ddl() -> None:
    for table in Base.metadata.sorted_tables:
        print(str(CreateTable(table).compile(dialect=engine.dialect)).strip() + ";\n")


def migrate_schema() -> list[str]:
    """轻量迁移：给**已存在**的表补上缺失的列与索引。

    为什么需要：`Base.metadata.create_all()` 只会创建缺失的**表**，
    不会给已有表加列。所以老库直接跑新代码会 500（例如 user 表少了 email 列）。

    本项目没有 Alembic，这里只处理最常见的两种情况 ——
    新增列、新增索引/唯一索引。**不做**改类型、删列、改约束；
    那类变更请用 mysqldump 备份 + init_db.py --drop 重建。

    返回做了哪些改动的说明，便于打印与排查。
    """
    from sqlalchemy import inspect as sa_inspect, text
    from sqlalchemy.schema import CreateColumn, CreateIndex

    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    changes: list[str] = []

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # 新表交给 create_all
            have_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have_cols:
                    continue
                ddl = str(CreateColumn(column).compile(dialect=engine.dialect))
                conn.execute(text(f"ALTER TABLE `{table.name}` ADD COLUMN {ddl}"))
                changes.append(f"{table.name}.{column.name}")

            have_idx = {i["name"] for i in inspector.get_indexes(table.name)}
            for index in table.indexes:
                if index.name in have_idx:
                    continue
                conn.execute(CreateIndex(index))
                changes.append(f"{table.name} 索引 {index.name}")

    return changes


def _seed_roles_and_menus(db, notes: list[str]) -> dict[str, Role]:
    """角色、菜单，以及「哪些角色能看到哪些菜单」的种子。

    这里是**双向同步**：既补上缺失的菜单与关联，也移除不该有的关联。
    为什么必须能移除：菜单可见性是种子数据（「基础数据」页只读），而归属本身会变 ——
    例如「部门维护」从管理员移给财务。只加不减的话，老库里那条旧关联会一直留着，
    管理员那边照样看得到，「移动」就等于没做。
    """
    role_map: dict[str, Role] = {}
    for code, name in ROLES:
        role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
        if role is None:
            role = Role(code=code, name=name)
            db.add(role)
            db.flush()
        role_map[code] = role

    for title, path, icon, sort, role_codes in MENUS:
        menu = db.execute(select(Menu).where(Menu.path == path)).scalar_one_or_none()
        if menu is None:
            menu = Menu(title=title, path=path, icon=icon, sort=sort)
            db.add(menu)
            db.flush()
        else:
            # 标题/图标/排序也按定义校准，避免改了种子但老库不生效
            menu.title, menu.icon, menu.sort = title, icon, sort

        want = {role_map[code] for code in role_codes}
        removed = [r.name for r in menu.roles if r not in want]
        added = [r.name for r in want if r not in menu.roles]
        for role in list(menu.roles):
            if role not in want:
                menu.roles.remove(role)
        for role in want:
            if role not in menu.roles:
                menu.roles.append(role)
        if removed or added:
            detail = []
            if added:
                detail.append("加入 " + "、".join(added))
            if removed:
                detail.append("移除 " + "、".join(removed))
            notes.append(f"[seed] 菜单归属调整：「{title}」 → {'；'.join(detail)}")

    return role_map


def _seed_departments(db) -> dict[str, Department]:
    dept_map: dict[str, Department] = {}
    for name, single, monthly in DEPARTMENTS:
        dept = db.execute(
            select(Department).where(Department.name == name)
        ).scalar_one_or_none()
        if dept is None:
            dept = Department(name=name, single_limit=single, monthly_limit=monthly)
            db.add(dept)
            db.flush()
        dept_map[name] = dept
    return dept_map


def _random_password() -> str:
    """生成一个一定满足强度口径的随机初始密码。"""
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(14))
        if password_policy_error(pwd) is None:
            return pwd


def _bootstrap_admin(db, role_map, dept_map, created_note: list[str]) -> None:
    """引导第一个管理员。

    系统必须有一个种子管理员，否则没人能创建财务账号（自助注册只能注册出员工）。
    """
    username = settings.BOOTSTRAP_ADMIN_USERNAME
    exists = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if exists is not None:
        # 幂等：已存在就不动它的密码（避免每次跑 init_db 都把管理员密码改掉）
        created_note.append(f"[seed] 引导管理员已存在：{username}（密码未改动）")
        return

    generated = False
    password = settings.BOOTSTRAP_ADMIN_PASSWORD
    if password:
        err = password_policy_error(password, username)
        if err:
            raise SystemExit(
                f"[seed] BOOTSTRAP_ADMIN_PASSWORD 不满足密码强度：{err}\n"
                f"       请改成至少 {settings.PASSWORD_MIN_LENGTH} 位、同时含字母与数字。"
            )
    else:
        password = _random_password()
        generated = True

    db.add(
        User(
            username=username,
            name=settings.BOOTSTRAP_ADMIN_NAME,
            password_hash=hash_password(password),
            role_id=role_map[ROLE_ADMIN].id,
            dept_id=dept_map[accounts.DEPT_FINANCE].id,
            status="NORMAL",
        )
    )
    db.commit()
    created_note.append(f"[seed] 已创建引导管理员：{username}")
    if generated:
        created_note.append(
            f"[seed] 初始密码（**只显示这一次**，登录后请立即修改）：{password}"
        )
    else:
        created_note.append("[seed] 初始密码取自 .env 的 BOOTSTRAP_ADMIN_PASSWORD")


def _seed_demo_users(db, role_map, dept_map) -> None:
    for username, name, role_code, dept_name in DEMO_USERS:
        exists = db.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(
            User(
                username=username,
                name=name,
                email=f"{username}@example.com",  # 演示账号给个邮箱，便于试「忘记密码」发信
                password_hash=hash_password(accounts.DEMO_PASSWORD),
                role_id=role_map[role_code].id,
                dept_id=dept_map[dept_name].id,
                status="NORMAL",
            )
        )
    db.commit()


def seed(with_demo: bool) -> None:
    db = SessionLocal()
    notes: list[str] = []
    try:
        role_map = _seed_roles_and_menus(db, notes)
        dept_map = _seed_departments(db)
        db.commit()

        if with_demo:
            _seed_demo_users(db, role_map, dept_map)
            notes.append(
                "[seed] 已灌入演示账号（--demo）："
                + "、".join(f"{u[0]}({u[1]})" for u in DEMO_USERS)
            )
            notes.append(f"[seed] 演示账号统一密码：{accounts.DEMO_PASSWORD}")
        else:
            _bootstrap_admin(db, role_map, dept_map, notes)

        notes.insert(0, "[seed] 角色 / 菜单 / 部门 就绪")
    finally:
        db.close()

    for line in notes:
        print(line)

    if not with_demo:
        print()
        print("[seed] 接下来：")
        print("  1) 用上面的引导管理员登录「用户管理」，创建财务账号（角色选「财务人员」）")
        print("  2) 员工在登录页点「注册」自助注册，注册后角色固定为员工、部门为「待分配」")
        print("  3) 管理员在「用户管理」里把新员工调到实际部门（部门决定预算上限）")
        print("  说明：默认不灌演示账号；需要跑验收脚本或本地演示时用 --demo")


def main() -> None:
    args = set(sys.argv[1:])
    print(f"[db] {settings.DB_USER}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")

    if "--ddl" in args:
        print_ddl()
        return

    if "--drop" in args:
        Base.metadata.drop_all(bind=engine)
        print("[db] 已删除全部表")

    Base.metadata.create_all(bind=engine)
    print(f"[db] 建表完成，共 {len(Base.metadata.sorted_tables)} 张表：")
    for table in Base.metadata.sorted_tables:
        print(f"  - {table.name}")

    # 给已存在的表补列/补索引（create_all 只管建新表，不会改老表）
    changes = migrate_schema()
    if changes:
        print(f"[db] 已补齐缺失的列/索引（{len(changes)} 项）：")
        for item in changes:
            print(f"  - {item}")
    else:
        print("[db] 已有表的列与索引无需补齐")

    seed(with_demo="--demo" in args)


if __name__ == "__main__":
    main()
