import { describe, expect, it } from 'vitest'

import {
  ACTION_TEXT,
  ACTION_TYPE,
  actionText,
  actionType,
  conclusionText,
  conclusionType,
  confidenceText,
  escapeHtml,
  money,
  reasonsHtml,
  riskLongText,
  riskText,
  riskType,
  STATUSES,
  statusText,
  statusType,
  timeText,
} from './format'

describe('状态枚举', () => {
  it('STATUSES 覆盖状态机四种状态', () => {
    expect(STATUSES).toEqual(['DRAFT', 'PENDING', 'APPROVED', 'REJECTED'])
  })

  it('状态文案与标签类型齐全，未知值有兜底', () => {
    expect(statusText('PENDING')).toBe('待审核')
    expect(statusType('REJECTED')).toBe('danger')
    expect(statusText('SOMETHING')).toBe('SOMETHING')
    expect(statusType('SOMETHING')).toBe('info')
  })
})

describe('风险等级', () => {
  it('LOW/MID 分别映射到低/中', () => {
    expect(riskText('LOW')).toBe('低')
    expect(riskText('MID')).toBe('中')
    expect(riskLongText('MID')).toBe('中（有预警）')
  })

  it('MID 是唯一需要 warning 样式的等级', () => {
    expect(riskType('MID')).toBe('warning')
    expect(riskType('LOW')).toBe('success')
    expect(riskType(undefined)).toBe('success')
  })
})

describe('审核动作', () => {
  it('四种动作的文案与标签类型', () => {
    expect(Object.keys(ACTION_TEXT)).toHaveLength(4)
    // 两个映射表必须覆盖同一组动作，避免新增动作时漏改其中一个
    expect(Object.keys(ACTION_TYPE).sort()).toEqual(Object.keys(ACTION_TEXT).sort())
    expect(actionText('APPROVE')).toBe('通过')
    expect(actionType('REJECT')).toBe('danger')
    expect(actionType('RESUBMIT_DRAFT')).toBe('warning')
    expect(actionType('UNKNOWN')).toBe('info')
  })
})

describe('AI 助手结论映射', () => {
  it('三种结论都有中文文案与标签类型', () => {
    expect(conclusionText('APPROVE')).toBe('建议通过')
    expect(conclusionText('REJECT')).toBe('建议驳回')
    expect(conclusionText('NEED_MORE_INFO')).toBe('建议人工确认')
    expect(conclusionType('REJECT')).toBe('danger')
    expect(conclusionType('NEED_MORE_INFO')).toBe('warning')
  })

  it('未知/空值有兜底，不会渲染成 undefined', () => {
    expect(conclusionText(undefined)).toBe('—')
    expect(conclusionType(undefined)).toBe('info')
    expect(confidenceText(undefined)).toBe('—')
  })

  it('置信度映射为高/中/低', () => {
    expect(confidenceText('high')).toBe('高')
    expect(confidenceText('mid')).toBe('中')
    expect(confidenceText('low')).toBe('低')
  })
})

describe('money', () => {
  it('千分位 + 两位小数', () => {
    expect(money(1234.5)).toBe('1,234.50')
    expect(money(0)).toBe('0.00')
    expect(money(null)).toBe('0.00')
    expect(money('888.88')).toBe('888.88')
  })
})

describe('timeText', () => {
  it('把 ISO 串截成可读时间，空值给破折号', () => {
    expect(timeText('2026-10-06T08:30:00')).toBe('2026-10-06 08:30:00')
    expect(timeText('')).toBe('—')
    expect(timeText(null)).toBe('—')
  })
})

describe('escapeHtml', () => {
  it('转义会破坏 HTML 结构的字符', () => {
    expect(escapeHtml('<script>alert(1)</script>')).toBe(
      '&lt;script&gt;alert(1)&lt;/script&gt;',
    )
    expect(escapeHtml('a & b "c" \'d\'')).toBe('a &amp; b &quot;c&quot; &#39;d&#39;')
  })

  it('非字符串输入安全处理', () => {
    expect(escapeHtml(null)).toBe('')
    expect(escapeHtml(undefined)).toBe('')
    expect(escapeHtml(123)).toBe('123')
  })
})

describe('reasonsHtml', () => {
  it('多条原因用 <br> 连接（ElMessageBox 的 \\n 会被 HTML 折叠成空格）', () => {
    expect(reasonsHtml(['原因一', '原因二'])).toBe('· 原因一<br>· 原因二')
  })

  it('对 OCR/用户来源的文本做转义，避免弹窗被注入', () => {
    expect(reasonsHtml(['发票 <img src=x onerror=alert(1)> 重复'])).toBe(
      '· 发票 &lt;img src=x onerror=alert(1)&gt; 重复',
    )
  })

  it('空输入返回空串', () => {
    expect(reasonsHtml([])).toBe('')
    expect(reasonsHtml(null)).toBe('')
    expect(reasonsHtml(undefined)).toBe('')
  })
})
