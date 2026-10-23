# 智能发票报销审核系统

以 **OCR 自动识别**替代手工录入、以 **规则化预审**统一审核口径的报销审核系统，构建「机器预审 + 人工终审」的人机协同流程。

**核心目标**

1. **自动填单** —— 批量上传发票影像 → OCR 提取字段 → 自动填入报销单，人工只做确认与修正
2. **规则拦截** —— 提交时硬拦截重复报销与金额不符，预警抬头错误与预算超标
3. **全程留痕** —— 字段修改痕迹与审核决策轨迹全程可追溯

**技术栈**：Vue 3 + Element Plus ｜ FastAPI + SQLAlchemy 2.0 ｜ MySQL 8.0（14 张表）｜ OCR：`mock` / `paddle`（本地 PaddleOCR）｜ LLM：`mock` / `deepseek` / `qwen` / `glm` / `openai`

---

## 目录结构

```
.
├─ backend/                 # FastAPI 后端
│  ├─ app/
│  │  ├─ api/v1/            # 路由：auth / upload / invoices / reimb / review / agent / admin / meta
│  │  ├─ core/              # 配置、数据库、权限（三层）、JWT+CSRF、限流
│  │  ├─ models/            # ORM 模型（14 张表）
│  │  └─ services/          # 业务：预审规则、报销单、OCR、LLM、Agent、PDF 拆页、邮件
│  ├─ scripts/              # 初始化与自检脚本（见下文）
│  ├─ .env.example          # 配置模板（复制为 .env 后填写）
│  └─ requirements.txt
├─ frontend/                # Vue 3 前端
│  ├─ src/{api,components,composables,layout,router,stores,utils,views}
│  └─ nginx.conf.example    # 生产部署示例（SPA fallback + /api 反代）
└─ README.md
```

---

## 环境要求

| 需要什么 | 版本 / 怎么查 |
| :--- | :--- |
| MySQL | 8.0，且服务正在运行 |
| Python | 3.10+（`python --version`） |
| Node.js | 18+（`node -v`） |

---

## 快速开始

### 1. 建库

```sql
CREATE DATABASE IF NOT EXISTS invoice_system
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. 配置

```powershell
cd backend
if (-not (Test-Path .env)) { copy .env.example .env }
```

打开 `backend\.env`，至少改这一行（`.env` 已在 `.gitignore` 中，不会被提交）：

```ini
DB_PASSWORD=你的MySQL密码
```

其余可选项：`OCR_PROVIDER=mock|paddle`、`LLM_PROVIDER=mock|deepseek|qwen|glm|openai`、`JWT_SECRET`（正式部署务必换成 ≥32 位随机串）。
**改完 `.env` 必须重启后端**才生效（配置只在启动时读取一次）。

### 3. 装后端依赖 + 建表

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # 含 PaddleOCR，约 250–350MB
.venv\Scripts\python.exe scripts\init_db.py                   # 建表 + 引导一个管理员
.venv\Scripts\python.exe scripts\init_db.py --demo            # 可选：额外创建 5 个演示账号
.venv\Scripts\python.exe scripts\check_db.py                  # 期望输出「模型与库结构一致：OK」
```

`init_db.py` **默认不灌演示账号**，只引导**一个管理员**：用户名取 `.env` 的 `BOOTSTRAP_ADMIN_USERNAME`（默认 `admin`），
密码取 `BOOTSTRAP_ADMIN_PASSWORD`；**密码留空则随机生成 14 位一次性密码并打印到控制台（只显示这一次）**。

### 4. 装前端依赖并启动

```powershell
cd frontend
npm install
```

### 5. 启动（两个终端，都别关）

```powershell
# 终端 A —— 后端
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 终端 B —— 前端
cd frontend
npx vite --port 5173 --strictPort
```

浏览器访问 **<http://localhost:5173>**（必须用 `localhost`，Vite 只监听它，用 `127.0.0.1` 会连不上）。

- 接口文档（Swagger）：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health> → `{"status":"ok"}`

---

## 账号与权限

**账号体系**：`init_db.py` 默认只引导一个管理员，之后——

1. 管理员登录 → 在「用户管理」创建**财务**账号
2. 员工在登录页点「注册新账号」**自助注册**（强制为普通员工、强制进入「待分配」部门，再由管理员调岗）
3. 「忘记密码？」配了 SMTP 就发邮件重置链接（30 分钟内有效、只能用一次）；**默认没配**，会落一条「待处理重置申请」，管理员在「用户管理」页顶部一键重置。两种情况页面文案一致，不泄露账号是否存在。

**演示账号**（统一密码 `Demo@123456`，需先跑 `init_db.py --demo`，仅本地演示用）

| 用户名 | 姓名 | 角色 | 部门（单笔/月度上限） | 用途 |
| :--- | :--- | :--- | :--- | :--- |
| `zhangwei` | 张伟 | 普通员工 | 技术研发部（5000 / 50000） | 主流程：提单 → 被驳回 → 改后重提 |
| `liuyang` | 刘洋 | 普通员工 | 技术研发部 | 与张伟同部门，验证「看不到别人的单据/影像」 |
| `chenjing` | 陈静 | 普通员工 | 市场销售部（8000 / 80000） | 跨部门，验证数据隔离 |
| `wangfang` | 王芳 | 财务人员 | 财务部（3000 / 30000） | 待办审核、审核轨迹、部门维护、AI 审核助手 |
| `zhaomin` | 赵敏 | 管理员 | 财务部 | 审核轨迹、用户管理、基础数据 |

**三层权限**

| 层 | 落在哪里 | 回答的问题 |
| :--- | :--- | :--- |
| 菜单权限 | `role_menu` 表 | 能看到哪些页面 |
| 操作权限 | 权限码常量（`/auth/me` 下发给前端） | 能调哪些接口 / 点哪些按钮 |
| 数据权限 | 员工 `OWN` / 财务 `ALL` / 管理员 `ALL` | 能看到**谁**的数据 |

- **管理员不参与业务审核**：没有 `reimb:review`，也就没有「待办审核」菜单和 AI 助手入口
- **部门与预算上限归财务**（`dept:manage`）；管理员只有 `dept:view`，能读不能改
- 连续 5 次密码错误锁定 15 分钟，管理员可解锁
- 写操作（含 `logout`、`refresh`）均校验 CSRF 双提交 Token；仅登录/注册/忘记密码/重置这四个无登录态的公开接口豁免（改用限流防刷）

---

## 主流程演示（约 5 分钟）

**第 1 幕 · 员工提单**（`zhangwei`）

1. 「新建报销单」→ 填事由 → **创建草稿**
2. **选择图片并自动识别**，选 1 张 JPG/PNG 或一份 PDF（PDF 自动拆页，每页一行发票）
3. 核对识别结果（10 个字段 + 置信度，低置信度标「待人工确认」）→ **选费用类别**（必填）→ 保存修改
4. 「预审检查」看拦截/预警（只读，不改状态）→ **提交报销单**
   - 顺利则状态变「待审核」；若弹「提交被拦截」，说明命中了硬拦截规则

**第 2 幕 · 财务审核**（`wangfang`）

1. 「待办审核」→ 按状态/申请人筛选、按时间/金额/风险排序
2. 点 **审核** 打开抽屉：单据信息、发票影像（可放大）、OCR 字段与置信度、预审预警、审核轨迹时间轴、**AI 审核助手**
3. 写意见 → **通过**（终态）或 **驳回**（必须填原因，原因对申请人可见）
4. 「部门维护」可改各部门的单笔 / 月度预算上限——这两个值就是预算拦截的依据

**第 3 幕 · 员工回看**（`zhangwei`）

- 「我的报销单」看状态与驳回原因；被驳回可 **重新提交**（版本号 +1，历史轨迹保留）

**第 4 幕 · 管理员**（`zhaomin`）

- 「用户管理」：新建 / 编辑 / 重置密码 / 解锁，顶部处理「待处理重置申请」
- 「基础数据」：查看角色权限码与菜单（本期权限矩阵固定，不做可视化配置）

> 想看硬拦截长什么样：用同一张图再提一单（mock 引擎会给出相同发票号）→ 409 提示「发票 xxx 已在单据 BX… 中报销」；
> 或在某行把三个金额改成不一致（如 100 + 6 ≠ 999）→ 提交被拦截并给出算式。

---

## 可选：接真实 OCR / 真实大模型

**真实 OCR**（本地推理，影像不出本机）

```ini
# backend/.env
OCR_PROVIDER=paddle
```

重启后端；首次识别会自动下载三个中文模型（约 17MB）到 `~/.paddleocr`，之后完全离线。实测热态约 0.5 秒/张，冷启动约 3.7 秒。

**真实大模型**（财务审核 AI 助手，只出**建议**，不参与硬拦截）

```ini
# backend/.env
LLM_PROVIDER=deepseek        # 或 qwen / glm / openai
LLM_API_KEY=sk-xxxx
LLM_MASK_PII=true            # 销售方名称脱敏后再外发
```

未填 Key 时会**优雅降级**为规则摘要并在界面明确标注，不会报错、不影响审核。

---

## 自检与验收脚本

都在 `backend/scripts/`，在 `backend` 目录下用 `.venv\Scripts\python.exe scripts\xxx.py` 运行：

| 脚本 | 覆盖什么 | 依赖 |
| :--- | :--- | :--- |
| `check_db.py` | 模型定义的表/列/唯一索引与库中实际结构逐一比对 | MySQL |
| `init_db.py` | 建表 + 种子（`--demo` 灌演示账号，`--drop` 重建，`--ddl` 只打印 DDL） | MySQL |
| `demo_flow.py --reset` | 15 幕演示主流程（上传→预审→提交→驳回→重提→通过），打印 `32/32 步符合预期` | 后端已启动 + mock OCR |
| `e2e_check.py` | 端到端验收：登录/越权/上传校验/OCR 填单/留痕/查重/勾稽/预算/审核/状态机 | 后端已启动 + **干净库** + mock OCR |
| `check_auth.py` | 认证与权限专项：注册、锁定、改密踢下线、三层权限、CSRF | 后端已启动 |
| `check_ocr_fields.py` | 字段提取层（纯标准库，不需 PaddleOCR），50 条断言 | 无 |
| `check_paddle_provider.py` | 真机 PaddleOCR 冒烟（脚本自己合成一张发票图） | 已装 PaddleOCR |
| `check_pdf_pages.py` | PDF 拆页 | 已装 pypdfium2 |
| `check_agent.py` | AI 建议链路：事实块、提示词、输出校验、降级、留痕与采纳 | 后端已启动 |

> `e2e_check.py` **非幂等**（固定发票号），必须在清库后的库上跑，建议用独立库：`DB_NAME=invoice_system_e2e`。
> 参考：项目最近一次全量自检的结果是 e2e 43/43、demo 32/32、字段提取 50/50、PDF 拆页 14/14（mock 引擎）。前端单测 `npm test` 32 个用例通过，`npm run lint`、`npm run build`、`npm run check:sfc` 均无问题。

---

## 常见问题

| 现象 | 原因 / 怎么办 |
| :--- | :--- |
| 打开 5173 白屏或连不上 | 前端没起；或地址写成了 `127.0.0.1:5173` → 改用 `http://localhost:5173` |
| 页面能开，一操作就报错 / 502 | 后端没起或不在 8000 → 先访问 `/api/v1/health` 自检 |
| 启动后端报数据库连接失败 | `.env` 的 `DB_PASSWORD` / `DB_USER` / `DB_NAME` 不对，或 MySQL 没启动 |
| 报 `UnicodeEncodeError: 'latin-1' codec ...` | `.env` 的 `DB_PASSWORD` 还是模板占位文字，改成真实密码后重启后端 |
| 登录或任何操作都 500 | 多数同上；**改完 `.env` 必须重启后端** |
| 列表里没数据 | 正常，库里只有引导管理员。跑 `demo_flow.py --reset` 造演示数据 |
| 报 `Table 'invoice_system.user' doesn't exist` | 还没建表 → `scripts/init_db.py` |
| 「账号已锁定」 | 连续输错 5 次，等 15 分钟；或管理员在「用户管理」点解锁 |
| 上传被拒 | 仅 JPG/PNG/PDF、单个 ≤10MB、单次 ≤5 个；扩展名与内容不符会被 magic bytes 校验拦下 |
| 一份 PDF 出现好几行 | 正常：每页算一张发票，来源列写着「xxx.pdf 第 N 页」 |
| 端口被占用 | 后端换 `--port 8001`（同时改 `frontend/vite.config.js` 的代理目标），前端换 `--port 5174` |
| 想清空重来 | `scripts/init_db.py --drop --demo`（**会删除全部单据与留痕**） |

---

## 已知限制

- **OCR 同步执行**：上传接口内同步识别，多页 PDF 会长时间占用请求；生产应改为 Worker 进程池消费 `ocr_task` 表
- **限流只覆盖公开接口**（注册 / 忘记密码 / 重置），且是单进程内存实现；多实例部署需换 Redis
- **费用类别目前由前端在提交前强制**，后端预审未把它列为硬拦截项（用接口直接提交可绕过）
- **`need_confirm` 只置不清**：低置信度标记不会因为人工修改而消除，也没有「已确认」这一状态
- **公司主体（`COMPANY_NAME` / `COMPANY_TAX_NO`）与部门预算为占位值**，需按真实业务写入后再打开抬头核验（`PREAUDIT_BUYER_CHECK`）
- **没有后端自动化测试**：`backend/scripts/` 下的自检脚本需要起服务 + MySQL；前端有 32 个 vitest 用例
- **管理员数据范围为 `ALL`**，因此能查看发票影像，但没有任何审核类权限码
- `.env`、`backend/storage/`（上传的发票影像）、`node_modules/`、`.venv/` 均不入库
