/**
 * 请求层的错误归一化测试。
 *
 * 用例里的载荷都是从真实后端抓下来的（见「P0 修复记录」），
 * 不是臆造的形态 —— 尤其是 422 的 detail 是数组这一点。
 */
import { beforeAll, describe, expect, it, vi } from 'vitest'

let buildError
let translateValidationMsg
let setCsrfToken
let currentCsrfToken

beforeAll(async () => {
  // api/index.js 在模块加载时会读 cookie，Node 环境先补一个最小 document
  vi.stubGlobal('document', { cookie: '' })
  const mod = await import('./index')
  buildError = mod.buildError
  translateValidationMsg = mod.translateValidationMsg
  setCsrfToken = mod.setCsrfToken
  currentCsrfToken = mod.currentCsrfToken
})

/**
 * CSRF 双提交的回归防线。
 *
 * 曾经的写法是把 token 缓存进模块变量并「缓存优先、Cookie 兜底」，于是服务端一旦轮换
 * CSRF Cookie（登录、刷新令牌、**改密码**都会轮换）而前端没显式同步，下一个写请求就 403。
 * 改密码那处真的踩到了：改完密码后任何保存/审核都失败，刷新页面才恢复。
 */
describe('CSRF Token 始终以 Cookie 为准', () => {
  it('服务端轮换 Cookie 后，取到的立刻是新值（不被任何缓存顶回去）', () => {
    document.cookie = 'csrf_token=OLD'
    expect(currentCsrfToken()).toBe('OLD')

    // 服务端轮换，在 JS 视角就是 Cookie 变了
    document.cookie = 'csrf_token=ROTATED'
    expect(currentCsrfToken()).toBe('ROTATED')
  })

  it('setCsrfToken 写入后立即生效（登录/续期用它同步）', () => {
    document.cookie = ''
    setCsrfToken('FROM_LOGIN')
    expect(currentCsrfToken()).toBe('FROM_LOGIN')
  })

  it('无 Cookie 时返回空串，便于调用方决定不带该请求头', () => {
    document.cookie = ''
    expect(currentCsrfToken()).toBe('')
  })
})

describe('translateValidationMsg', () => {
  it('把 Pydantic v2 的英文报错译成中文', () => {
    expect(translateValidationMsg('String should have at least 1 character')).toBe(
      '至少需要 1 个字符',
    )
    expect(translateValidationMsg('String should have at most 50 characters')).toBe(
      '最多 50 个字符',
    )
    expect(translateValidationMsg('Field required')).toBe('该字段必填')
    expect(translateValidationMsg('Input should be a valid number')).toBe('必须是数字')
    expect(translateValidationMsg('Input should be a valid integer')).toBe('必须是整数')
  })

  it('未命中翻译表时原样返回，不吞掉信息', () => {
    expect(translateValidationMsg('Something else entirely')).toBe('Something else entirely')
    expect(translateValidationMsg('')).toBe('')
  })
})

describe('buildError：FastAPI 422（detail 是数组）', () => {
  const r422 = {
    response: {
      status: 422,
      data: {
        detail: [
          {
            loc: ['body', 'username'],
            msg: 'String should have at least 1 character',
            type: 'string_too_short',
          },
          {
            loc: ['body', 'name'],
            msg: 'String should have at least 1 character',
            type: 'string_too_short',
          },
        ],
      },
    },
    message: 'Request failed with status code 422',
  }

  it('逐字段拼出可读文案，而不是笼统的「请求失败」', () => {
    const e = buildError(r422)
    expect(e.message).toBe(
      '参数校验失败 —— username：至少需要 1 个字符；name：至少需要 1 个字符',
    )
    expect(e.reasons).toHaveLength(2)
    expect(e.status).toBe(422)
  })

  it('过滤掉 loc 里的 body/query 前缀', () => {
    const e = buildError({
      response: {
        status: 422,
        data: { detail: [{ loc: ['query', 'page'], msg: 'Field required' }] },
      },
    })
    expect(e.message).toBe('参数校验失败 —— page：该字段必填')
  })
})

describe('buildError：硬拦截 409（detail 是对象）', () => {
  it('保留 message 与 reasons 列表', () => {
    const e = buildError({
      response: {
        status: 409,
        data: {
          detail: {
            message: '提交被拦截',
            reasons: ['发票 12345 已在单据 BX1 中报销，存在重复'],
          },
        },
      },
    })
    expect(e.message).toBe('提交被拦截')
    expect(e.reasons).toEqual(['发票 12345 已在单据 BX1 中报销，存在重复'])
    expect(e.status).toBe(409)
  })
})

describe('buildError：其它形态', () => {
  it('字符串 detail（401 / 423 等）', () => {
    const e = buildError({
      response: { status: 401, data: { detail: '登录状态已失效，请重新登录' } },
    })
    expect(e.message).toBe('登录状态已失效，请重新登录')
    expect(e.status).toBe(401)
  })

  it('超时给出「服务端可能已处理」的提示（上传是逐文件落库）', () => {
    const e = buildError({ code: 'ECONNABORTED', message: 'timeout of 60000ms exceeded' })
    expect(e.message).toContain('请求超时')
    expect(e.status).toBeUndefined()
  })

  it('网络错误回退到原始 message', () => {
    const e = buildError({ message: 'Network Error' })
    expect(e.message).toBe('Network Error')
    expect(e.reasons).toEqual([])
  })
})
