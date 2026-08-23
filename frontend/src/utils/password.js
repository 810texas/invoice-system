/**
 * 前端密码策略 —— 必须与后端 `app/core/security.py` 的 `password_policy_error()` 保持一致。
 *
 * 为什么在前端再写一遍：后端校验是权威（绕过前端也拦得住），前端这份只是为了**即时提示**、
 * 不用等一次请求往返才知道密码不合格。两边口径要一起改，
 * 所以两处的弱口令清单与长度阈值都写了指向对方的注释。
 */
export const PASSWORD_MIN_LENGTH = 8

// 与后端 COMMON_WEAK_PASSWORDS 保持同一份清单
export const COMMON_WEAK_PASSWORDS = new Set([
  '12345678', '123456789', '1234567890', 'password', 'password1', 'password123',
  'passw0rd', 'qwerty123', 'qwerty123456', 'abc12345', 'abcd1234', 'a1234567',
  'admin123', 'admin1234', 'iloveyou', '11111111', '88888888', '1qaz2wsx',
])

/** 返回中文错误原因；符合要求返回 null。 */
export function passwordPolicyError(password, username = '') {
  if (!password) return '密码不能为空'
  if (password.length < PASSWORD_MIN_LENGTH) return `密码至少 ${PASSWORD_MIN_LENGTH} 位`
  // 弱口令清单放在「字母+数字」之前：`password` 虽然也不含数字，
  // 但「过于常见」比「需要字母和数字」更有指导意义（与后端顺序一致）
  if (COMMON_WEAK_PASSWORDS.has(password.toLowerCase())) return '该密码过于常见，请更换'
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) return '密码需同时包含字母和数字'
  if (username && password.toLowerCase() === username.toLowerCase()) {
    return '密码不能与用户名相同'
  }
  return null
}

/** 生成可直接给 el-form 用的密码校验规则（confirm 字段另配）。 */
export function passwordRules(getUsername = () => '') {
  return [
    {
      validator: (_rule, value, callback) => {
        const error = passwordPolicyError(value, getUsername())
        return error ? callback(new Error(error)) : callback()
      },
      trigger: 'blur',
    },
  ]
}
