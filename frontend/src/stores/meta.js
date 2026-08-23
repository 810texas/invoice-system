import { defineStore } from 'pinia'

import { api } from '../api'

/**
 * 基础数据与元信息（GET /meta）缓存。
 *
 * 解决两个问题：
 * 1. 「待人工确认」的置信度阈值原先在前端三处硬编码 0.85，后端却可以改
 *    `OCR_CONFIDENCE_THRESHOLD`，两边会漂移；现在统一以后端返回值为准。
 * 2. 费用类别 / 发票类型只取一次，不必每个页面各拉一遍。
 *
 * 注意这里**只放真正被页面用到的字段**。原先还存了 `statuses` 与 `can_review`：
 * 前者三个筛选下拉用的其实是 `utils/format.js` 的展示表（带中文标签与颜色），
 * 后者是权限判断、已由 `/auth/me` 的 `permissions` 承担 —— 两个都是没人读的死状态，已删除。
 */
export const useMetaStore = defineStore('meta', {
  state: () => ({
    expense_categories: [],
    invoice_types: [],
    // 兜底值：与后端默认一致，load() 之后以后端为准
    ocr_confidence_threshold: 0.85,
    loaded: false,
  }),
  actions: {
    async load() {
      if (this.loaded) return this
      Object.assign(this, await api.meta())
      this.loaded = true
      return this
    },
  },
})
