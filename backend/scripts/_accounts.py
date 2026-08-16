"""验收/演示账号的**唯一来源**。

为什么单独一个文件：这些用户名和密码原先硬编码在 5 个验收脚本 + 种子里，
改一次要满仓库找、极易漏。现在种子与脚本都从这里取，改一处即可。

⚠️ 这些账号**只在 `init_db.py --demo` 时创建**。
默认种子只引导一个管理员，不灌任何演示账号 —— 真实使用场景下员工是自助注册的。
"""
from __future__ import annotations

# 演示账号统一初始密码。必须满足密码强度口径（≥8 位且同时含字母与数字），
# 否则和管理员建号/自助重置走的是两套标准。
DEMO_PASSWORD = "Demo@123456"

# 员工：同部门两人，用于「同部门也不能互相看单据/影像」的越权测试
EMPLOYEE = "zhangwei"            # 张伟 · 技术研发部
EMPLOYEE2 = "liuyang"            # 刘洋 · 技术研发部（与张伟同部门）
# 跨部门员工：验证数据隔离（员工数据范围是 OWN，跨部门自然也不可见）
EMPLOYEE_OTHER_DEPT = "chenjing"  # 陈静 · 市场销售部
FINANCE = "wangfang"             # 王芳 · 财务部（审核、驳回、轨迹）
ADMIN = "zhaomin"                # 赵敏 · 财务部（用户/部门/基础数据）

ALL_DEMO_ACCOUNTS = [EMPLOYEE, EMPLOYEE2, EMPLOYEE_OTHER_DEPT, FINANCE, ADMIN]

# 部门名（种子与脚本共用，避免脚本里写错部门名）
DEPT_TECH = "技术研发部"
DEPT_MARKET = "市场销售部"
DEPT_FINANCE = "财务部"
DEPT_HR = "人力资源部"
