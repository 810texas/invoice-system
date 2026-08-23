import axios from 'axios'

// 接口前缀：默认走 vite proxy（/api → 127.0.0.1:8000），可用 VITE_API_BASE 覆盖（见 .env.example）
const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

const http = axios.create({ baseURL: API_BASE, timeout: 60000, withCredentials: true })

// 刷新令牌专用实例：不能复用 http，否则刷新返回的 401 会被同一个拦截器再次拦截（递归）
const authHttp = axios.create({ baseURL: API_BASE, timeout: 30000, withCredentials: true })

export function readCookie(name) {
  const hit = document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))
  return hit ? decodeURIComponent(hit.split('=').slice(1).join('=')) : ''
}

/**
 * CSRF 双提交 Token 的写入。
 *
 * ⚠️ 这里**刻意不缓存到模块变量**。原先的做法是「缓存优先，Cookie 兜底」
 * （`csrfToken || readCookie(...)`），于是只要服务端轮换了 CSRF Cookie 而前端没显式同步，
 * 下一个写请求就会 403。而服务端轮换的地方有三处：登录、刷新令牌、**改密码** ——
 * 改密码那处就真的踩到了（改完密码后任何保存/审核都 403，刷新页面才恢复）。
 *
 * CSRF Cookie 本身是可被 JS 读取的（刻意非 HttpOnly，双提交模式的前提），
 * 它才是权威来源；每次写请求读一下即可，开销可以忽略。
 */
export function setCsrfToken(token) {
  if (token) {
    document.cookie = `csrf_token=${encodeURIComponent(token)}; path=/`
  }
}

/**
 * 写请求要带的 CSRF 头值。**每次现取**，也便于单测钉住「不存在缓存」这一约定。
 */
export function currentCsrfToken() {
  return readCookie('csrf_token')
}

/**
 * 会话续期（单飞）：并发的多个 401 只会触发一次刷新请求。
 * 后端 /auth/refresh 用 HttpOnly 的 refresh cookie（7 天）换新的 access token，
 * 同时下发新的 CSRF Token（后端 auth.py 的 /auth/refresh）。
 */
let refreshPromise = null

function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = authHttp
      .post('/auth/refresh', {}, { headers: currentCsrfToken() ? { 'X-CSRF-Token': currentCsrfToken() } : {} })
      .then((resp) => {
        setCsrfToken(resp.data?.csrf_token)
        return true
      })
      .catch(() => false)
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

/** 续期失败：回登录页并记住原地址（已在登录页则什么都不做，避免刷新循环）。 */
function redirectToLogin() {
  if (window.location.pathname === '/login') return
  const { pathname, search, hash } = window.location
  window.location.assign(`/login?redirect=${encodeURIComponent(`${pathname}${search}${hash}`)}`)
}

/** Pydantic v2 的默认校验报错是英文，这里把本 API 可能出现的几种译成中文（未命中则原样返回）。 */
export function translateValidationMsg(msg) {
  const text = String(msg || '')
  let hit = text.match(/String should have at least (\d+) character/i)
  if (hit) return `至少需要 ${hit[1]} 个字符`
  hit = text.match(/String should have at most (\d+) character/i)
  if (hit) return `最多 ${hit[1]} 个字符`
  if (/^Field required$/i.test(text)) return '该字段必填'
  if (/valid integer/i.test(text)) return '必须是整数'
  if (/valid (number|float)/i.test(text)) return '必须是数字'
  if (/valid string/i.test(text)) return '必须是字符串'
  if (/valid (list|array)/i.test(text)) return '必须是数组'
  if (/none is not an allowed value/i.test(text)) return '不能为空'
  return text
}

/** 把后端的各种错误形态统一成「一句中文 + reasons 明细列表」。 */
export function buildError(error) {
  const status = error.response?.status
  const detail = error.response?.data?.detail
  let message = '请求失败'
  let reasons = []

  if (typeof detail === 'string') {
    message = detail
  } else if (Array.isArray(detail)) {
    // FastAPI 422：detail 是 [{ loc: ['body','field'], msg, type }, ...]，逐字段拼成可读文案
    reasons = detail.map((item) => {
      const loc = Array.isArray(item?.loc)
        ? item.loc.filter((p) => p !== 'body' && p !== 'query')
        : []
      const msg = translateValidationMsg(item?.msg) || '参数不合法'
      return loc.length ? `${loc.join('.')}：${msg}` : msg
    })
    message = reasons.length ? `参数校验失败 —— ${reasons.join('；')}` : '参数校验失败'
  } else if (detail && typeof detail === 'object') {
    message = detail.message || '请求失败'
    reasons = detail.reasons || []
  } else if (error.code === 'ECONNABORTED' || /timeout/i.test(error.message || '')) {
    // 超时：服务端可能已经处理完（上传接口是逐文件落库），提示用户刷新核对而不是直接重试
    message = '请求超时：服务端可能已处理部分内容，请刷新页面确认后再操作'
  } else if (error.message) {
    message = error.message
  }

  return Object.assign(new Error(message), { reasons, status })
}

http.interceptors.request.use((config) => {
  const method = (config.method || 'get').toUpperCase()
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    // 每次从 Cookie 取，保证与服务端刚轮换过的值一致（见 setCsrfToken 的说明）
    config.headers['X-CSRF-Token'] = currentCsrfToken()
  }
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  async (error) => {
    const status = error.response?.status
    const config = error.config || {}
    const url = config.url || ''
    // 登录与刷新本身的失败不做续期，否则错误密码/失效刷新令牌会绕圈
    const isAuthEntry = url.includes('/auth/login') || url.includes('/auth/refresh')

    if (status === 401 && !config._retried && !isAuthEntry) {
      config._retried = true
      if (await refreshSession()) {
        // 续期成功：带上新的 CSRF Token 重放原请求（请求拦截器会重新注入请求头）
        return http.request(config)
      }
      redirectToLogin()
    }

    return Promise.reject(buildError(error))
  },
)

/** 列表接口：响应体是数组，总数在 X-Total-Count 响应头里（后端为兼容旧调用方没有改成对象）。 */
function listWithTotal(request) {
  return request.then((resp) => ({
    items: resp.data,
    total: Number(
      resp.headers['x-total-count'] ?? (Array.isArray(resp.data) ? resp.data.length : 0),
    ),
  }))
}

// 列表/审核页的缩略图边长（px）：后端按此降采样并缓存 JPEG，点击放大仍取原图
export const THUMB_EDGE = 300

export const api = {
  // 鉴权
  login: (payload) => http.post('/auth/login', payload).then((r) => r.data),
  register: (payload) => http.post('/auth/register', payload).then((r) => r.data),
  forgotPassword: (account) => http.post('/auth/forgot-password', { account }).then((r) => r.data),
  // 自助重置：凭邮件/管理员给出的重置令牌设置新密码
  resetPassword: (token, newPassword) =>
    http.post('/auth/reset-password', { token, new_password: newPassword }).then((r) => r.data),
  // 已登录用户自助改密码（引导管理员的一次性初始密码只能从这里改）
  changePassword: (oldPassword, newPassword) =>
    http
      .post('/auth/change-password', { old_password: oldPassword, new_password: newPassword })
      .then((r) => r.data),
  logout: () => http.post('/auth/logout', {}).then((r) => r.data),
  refresh: () => http.post('/auth/refresh', {}).then((r) => r.data),
  me: () => http.get('/auth/me').then((r) => r.data),

  // 元数据
  meta: () => http.get('/meta').then((r) => r.data),

  // 员工
  createReimbursement: (payload) => http.post('/reimbursements', payload).then((r) => r.data),
  myReimbursements: (params) => listWithTotal(http.get('/reimbursements/mine', { params })),
  reimbursementDetail: (id) => http.get(`/reimbursements/${id}`).then((r) => r.data),
  updateReason: (id, reason) => http.put(`/reimbursements/${id}`, { reason }).then((r) => r.data),
  removeInvoice: (rid, iid) => http.delete(`/reimbursements/${rid}/invoices/${iid}`).then((r) => r.data),
  uploadInvoices: (rid, files) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    if (rid) form.append('reimbursement_id', String(rid))
    return http.post('/invoices/upload', form).then((r) => r.data)
  },
  patchInvoice: (id, payload) => http.patch(`/invoices/${id}`, payload).then((r) => r.data),
  invoiceChanges: (id) => http.get(`/invoices/${id}/changes`).then((r) => r.data),
  auditPreview: (rid) =>
    http.get('/reimbursements/audit', { params: { reimbursement_id: rid } }).then((r) => r.data),
  submit: (id) => http.post(`/reimbursements/${id}/submit`, {}).then((r) => r.data),
  resubmit: (id) => http.post(`/reimbursements/${id}/resubmit`, {}).then((r) => r.data),
  // 发票影像：thumb 传边长则返回降采样 JPEG（列表页缩略图），不传则返回原图（点开放大用）
  imageUrl: (attachmentId, { thumb } = {}) =>
    `${API_BASE}/attachments/${attachmentId}/raw${thumb ? `?thumb=${thumb}` : ''}`,

  // 财务
  todos: (params) => listWithTotal(http.get('/review/todos', { params })),
  reviewDetail: (id) => http.get(`/review/${id}`).then((r) => r.data),
  approve: (id, comment) => http.post(`/review/${id}/approve`, { comment }).then((r) => r.data),
  reject: (id, comment) => http.post(`/review/${id}/reject`, { comment }).then((r) => r.data),
  logs: (params) => http.get('/review/logs/query', { params }).then((r) => r.data),

  // AI 审核助手：POST 会真的调一次大模型并留痕，GET 只读最近一次建议（未生成过返回 404）
  agentStatus: () => http.get('/agent/status').then((r) => r.data),
  agentAdvice: (id) => http.get(`/agent/advice/${id}`).then((r) => r.data),
  generateAdvice: (id) => http.post(`/agent/advice/${id}`, {}).then((r) => r.data),

  // 管理员
  users: () => http.get('/admin/users').then((r) => r.data),
  createUser: (payload) => http.post('/admin/users', payload).then((r) => r.data),
  updateUser: (id, payload) => http.put(`/admin/users/${id}`, payload).then((r) => r.data),
  // 管理员重置某用户的密码（走后端 /admin/users/{id}/reset-password）
  adminResetPassword: (id, newPassword) =>
    http.post(`/admin/users/${id}/reset-password`, { new_password: newPassword }).then((r) => r.data),
  unlockUser: (id) => http.post(`/admin/users/${id}/unlock`, {}).then((r) => r.data),
  passwordResetRequests: () => http.get('/admin/password-reset-requests').then((r) => r.data),
  adminDepartments: () => http.get('/admin/departments').then((r) => r.data),
  createDepartment: (payload) => http.post('/admin/departments', payload).then((r) => r.data),
  updateDepartment: (id, payload) => http.put(`/admin/departments/${id}`, payload).then((r) => r.data),
  roles: () => http.get('/admin/roles').then((r) => r.data),
  menus: () => http.get('/admin/menus').then((r) => r.data),
}

export default http
