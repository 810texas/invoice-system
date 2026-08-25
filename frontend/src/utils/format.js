// 状态与标签映射：报销单状态机、风险等级、发票占用状态
// 状态枚举的**权威定义在后端**（`app/models/document.py` 的 `REIMB_STATUS`，
// 状态机流转也在后端）。这里是前端的**展示表**：筛选下拉的顺序 + 中文标签 + 标签颜色，
// 这三样后端给不了，所以前端必须自己有一份。新增状态时两处都要改。
export const STATUSES = ['DRAFT', 'PENDING', 'APPROVED', 'REJECTED']

export const STATUS_TEXT = {
  DRAFT: '草稿',
  PENDING: '待审核',
  APPROVED: '已通过',
  REJECTED: '已驳回',
}

export const STATUS_TYPE = {
  DRAFT: 'info',
  PENDING: 'warning',
  APPROVED: 'success',
  REJECTED: 'danger',
}

// 风险等级：LOW 无预警 / MID 有预警（reimbursement.risk_level）
export const RISK_TEXT = {
  LOW: '低',
  MID: '中',
}

export const RISK_LONG_TEXT = {
  LOW: '低（无预警）',
  MID: '中（有预警）',
}

export function riskText(value) {
  return RISK_TEXT[value] || value || '—'
}

export function riskLongText(value) {
  return RISK_LONG_TEXT[value] || value || '—'
}

export function riskType(value) {
  return value === 'MID' ? 'warning' : 'success'
}

export const ACTION_TEXT = {
  SUBMIT: '提交',
  APPROVE: '通过',
  REJECT: '驳回',
  RESUBMIT_DRAFT: '驳回后重新编辑',
}

export const ACTION_TYPE = {
  SUBMIT: 'info',
  APPROVE: 'success',
  REJECT: 'danger',
  RESUBMIT_DRAFT: 'warning',
}

export function actionType(value) {
  return ACTION_TYPE[value] || 'info'
}

export function actionText(value) {
  return ACTION_TEXT[value] || value
}

// AI 审核助手的结论与置信度（对应后端 services/llm/provider.py 的 CONCLUSIONS / CONFIDENCES）
export const CONCLUSION_TEXT = {
  APPROVE: '建议通过',
  REJECT: '建议驳回',
  NEED_MORE_INFO: '建议人工确认',
}

export const CONCLUSION_TYPE = {
  APPROVE: 'success',
  REJECT: 'danger',
  NEED_MORE_INFO: 'warning',
}

export const CONFIDENCE_TEXT = {
  high: '高',
  mid: '中',
  low: '低',
}

export function conclusionText(value) {
  return CONCLUSION_TEXT[value] || value || '—'
}

export function conclusionType(value) {
  return CONCLUSION_TYPE[value] || 'info'
}

export function confidenceText(value) {
  return CONFIDENCE_TEXT[value] || value || '—'
}

export function statusText(value) {
  return STATUS_TEXT[value] || value
}

export function statusType(value) {
  return STATUS_TYPE[value] || 'info'
}

export function money(value) {
  const num = Number(value || 0)
  return num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function timeText(value) {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 19)
}

/** 转义 HTML：预审原因里含发票号码/购销方等来自 OCR 与用户输入的文本。 */
export function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
  return String(text ?? '').replace(/[&<>"']/g, (c) => map[c])
}

/**
 * 把「硬拦截原因」列表拼成多行 HTML。
 * Element Plus 的 ElMessageBox 默认按文本节点渲染，`\n` 会被 HTML 折叠成空格，
 * 所以这里必须转义后拼 `<br>`，并配合 `dangerouslyUseHTMLString: true` 使用。
 */
export function reasonsHtml(list) {
  return (list || []).map((item) => `· ${escapeHtml(item)}`).join('<br>')
}
