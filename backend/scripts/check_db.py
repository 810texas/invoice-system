"""库结构自检：把**模型定义的**表与列和库中实际结构逐一比对。

为什么重写：原先只比对一份硬编码的 11 张表清单，既不查缺列（如 `user.email`）、
也不查缺表（如 `password_reset_token`），缺了照样打印「模型与库结构一致：OK」——
在做结构升级的场景下，这种**假 OK 比报错更危险**（应用起来才发现 500）。
现在直接以 `Base.metadata` 为唯一基准。

用法：
    .venv\\Scripts\\python.exe scripts\\check_db.py
发现缺表/缺列时退出码为 1，并提示用 `scripts/init_db.py` 补齐。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 中文 Windows 控制台默认 GBK，下面要打印 ✓ / ✗，不切编码会直接
# UnicodeEncodeError 崩掉（与其它 scripts/check_*.py 保持同一做法）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # 老版本 Python 或非标准输出流
    pass

from sqlalchemy import inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, engine  # noqa: E402
import app.models  # noqa: E402,F401

settings = get_settings()


def main() -> int:
    print(f"[check] 目标库 {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    expected = {t.name for t in Base.metadata.sorted_tables}

    print(f"[check] 模型中定义 {len(expected)} 张表，库中实际 {len(existing)} 张")
    print(f"[check] 库中已有表：{', '.join(sorted(existing)) or '无'}")

    missing_tables = expected - existing
    extra_tables = existing - expected

    # 逐表比对列（只对两边都有的表比；缺的表下面单独报）
    missing_columns: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        lack = [c.name for c in table.columns if c.name not in have]
        if lack:
            missing_columns[table.name] = lack
        else:
            print(f"[check] {table.name}: {len(have)} 列 ✓")

    if extra_tables:
        # 不算失败：同一个库里可能还有别的工具建的表
        print(f"[check] 提示：库中多出模型未定义的表（不影响使用）：{', '.join(sorted(extra_tables))}")

    if missing_tables or missing_columns:
        if missing_tables:
            print(f"[check] ✗ 缺表：{', '.join(sorted(missing_tables))}")
        for name, cols in sorted(missing_columns.items()):
            print(f"[check] ✗ 缺列：{name}.{', '.join(cols)}")
        print("[check] 修复：运行 scripts\\init_db.py（会补建缺失的表与列，不动已有数据）")
        print("[check] 模型与库结构一致：NO")
        return 1

    # 唯一索引直接决定查重等业务约束，逐表确认模型声明的唯一索引都存在。
    # 说明：invoice 的查重不是直接唯一 `dedup_key`，而是「占用式」——
    # 用唯一列 `occupy_key`（仅在被占用时有值，释放后置 NULL，MySQL 允许多个 NULL）。
    # 所以这里做**通用**校验，而不是硬编码某个索引名。
    missing_unique: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {i["name"] for i in inspector.get_indexes(table.name)}
        have |= {c["name"] for c in inspector.get_unique_constraints(table.name) if c.get("name")}
        for idx in table.indexes:
            if idx.unique and idx.name and idx.name not in have:
                missing_unique.append(f"{table.name}.{idx.name}")

    print(f"[check] invoice 唯一索引：{[i['name'] for i in inspector.get_indexes('invoice') if i.get('unique')]}")

    if missing_unique:
        print(f"[check] ✗ 缺唯一索引：{', '.join(missing_unique)}")
        print("[check] 修复：运行 scripts\\init_db.py（会补建缺失的索引）")
        print("[check] 模型与库结构一致：NO")
        return 1

    print("[check] 模型与库结构一致：OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
