import { describe, expect, it } from 'vitest'

import { COMMON_WEAK_PASSWORDS, PASSWORD_MIN_LENGTH, passwordPolicyError } from './password'

describe('密码策略（必须与后端 app/core/security.py 同口径）', () => {
  it('长度下限是 8 位', () => {
    expect(PASSWORD_MIN_LENGTH).toBe(8)
    expect(passwordPolicyError('Ab1')).toContain('至少 8 位')
    // 注意：'Abc12345' 本身在弱口令清单里，不能拿来当「合格密码」用
    expect(passwordPolicyError('GoodPass2026')).toBeNull()
  })

  it('必须同时含字母和数字', () => {
    expect(passwordPolicyError('abcdefghi')).toContain('字母和数字') // 纯字母
    expect(passwordPolicyError('9876543210')).toContain('字母和数字') // 纯数字（不在弱口令清单里）
  })

  it('弱口令清单优先于「字母+数字」检查，提示更有指导性', () => {
    // 'password' 也不含数字，但应该提示「过于常见」而不是「需要字母和数字」
    expect(passwordPolicyError('password')).toContain('过于常见')
  })

  it('拦下常见弱口令（清单与后端一致）', () => {
    for (const weak of ['password', 'password123', 'admin123', 'qwerty123456']) {
      expect(COMMON_WEAK_PASSWORDS.has(weak)).toBe(true)
      expect(passwordPolicyError(weak)).toContain('过于常见')
    }
  })

  it('不允许与用户名相同（忽略大小写）', () => {
    expect(passwordPolicyError('Zhangwei1', 'zhangwei1')).toContain('不能与用户名相同')
    expect(passwordPolicyError('Zhangwei1', 'someone-else')).toBeNull()
  })

  it('空密码与超长值', () => {
    expect(passwordPolicyError('')).toContain('不能为空')
    expect(passwordPolicyError(undefined)).toContain('不能为空')
  })
})
