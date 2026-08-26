# 前端（Vue 3 + Element Plus + Vite）

智能发票报销审核系统的前端。完整的项目说明见仓库根目录的 [`项目总览.md`](../项目总览.md)，
需求规格见 [`提示词.md`](../提示词.md)。

## 命令

```bash
npm install
npx vite --port 5173 --strictPort   # 开发服务器（浏览器访问 http://localhost:5173）
npm run build                       # 生产构建 → dist/
npm run preview                     # 预览构建产物（已配 /api 代理，见 vite.config.js）
npm run check:sfc                   # 逐组件编译校验（不依赖浏览器）
npm run lint                        # ESLint
npm run test                        # Vitest 单元测试
```

## 目录

```
src/
├─ api/index.js          # 统一请求层：CSRF 注入、401 单飞续期、错误文案归一化、缩略图 URL
├─ components/           # 跨页面复用组件（发票表格、审核轨迹时间轴）
├─ composables/          # 组合式函数（列表加载等）
├─ router/index.js       # 路由与**权限码**守卫（按 /auth/me 下发的 permissions 判断，不硬编码角色名）
├─ stores/auth.js        # 登录态、菜单、权限码与数据范围
├─ stores/meta.js        # /meta 缓存（费用类别、发票类型、OCR 置信度阈值）
├─ utils/password.js     # 密码强度（与后端 password_policy_error 同口径）
├─ utils/format.js       # 状态/风险/动作/结论枚举与格式化（前端展示表，权威定义在后端）
├─ layout/AppLayout.vue  # 侧边菜单 + 顶栏（含修改密码）
└─ views/                # 11 个页面（认证 4：登录/注册/忘记密码/重置密码；员工 2；财务 2；管理员 3）
```

## 环境变量

复制 `.env.example` 为 `.env.local` 后按需修改（模板入库、实际配置不入库）：

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `VITE_API_BASE` | `/api/v1` | 接口前缀。默认走代理（同源），一般不用改 |
| `VITE_PROXY_TARGET` | `http://127.0.0.1:8000` | 开发/预览代理的后端地址 |

## 生产部署

`npm run build` 产出 `dist/`，用 nginx 托管并把 `/api` 反代到后端即可
（SPA 需要回退到 `index.html`）。完整示例见 [`nginx.conf.example`](nginx.conf.example)。

要点：**让前端与接口同源**，这样 HttpOnly Cookie + CSRF 双提交 + `SameSite=Lax` 都按预期工作，
前端无需配置 CORS 或 `VITE_API_BASE`。

## Element Plus 按需引入

`vite.config.js` 里用 `unplugin-vue-components` 的 `ElementPlusResolver` 做按需引入：
模板中用到的组件与 `v-loading` 指令会被自动 import 并只打包对应样式。
**注意**：`ElMessage` / `ElMessageBox` 是命令式调用（不是模板标签），插件无法识别，
它们的样式在 `src/main.js` 里显式引入 —— 新增其它命令式组件（如 `ElNotification`）时同理。

组件库的中文文案由 `App.vue` 的 `<el-config-provider :locale="zhCn">` 提供。
