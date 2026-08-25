import { defineStore } from 'pinia'

import { api, setCsrfToken } from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    loaded: false,
  }),
  getters: {
    isLogin: (state) => !!state.user,
    role: (state) => state.user?.role || '',
    menus: (state) => state.user?.menus || [],
    // 三层权限里的第二层：操作权限码由后端下发（/auth/me），前端不再硬编码角色名
    permissions: (state) => state.user?.permissions || [],
    dataScope: (state) => state.user?.data_scope || '',
    dataScopeText: (state) => state.user?.data_scope_text || '',
  },
  actions: {
    /** 是否拥有某个操作权限码，例如 can('reimb:review') */
    can(permission) {
      return this.permissions.includes(permission)
    },
    /** 该角色登录后的默认首页（按权限推导，避免在多个文件里重复角色映射） */
    homePath() {
      if (this.can('admin:user')) return '/admin/users'
      if (this.can('reimb:review')) return '/review/todos'
      return '/my-reimbursements'
    },
    _apply(data) {
      setCsrfToken(data.csrf_token)
      this.user = data.user
      this.loaded = true
      return data.user
    },
    async login(username, password) {
      return this._apply(await api.login({ username, password }))
    },
    async register(payload) {
      // 注册即登录：后端返回结构与登录一致，所以这里能复用同一套处理
      return this._apply(await api.register(payload))
    },
    async fetchMe() {
      try {
        this.user = await api.me()
        setCsrfToken()
      } catch {
        this.user = null
      } finally {
        this.loaded = true
      }
      return this.user
    },
    async logout() {
      try {
        await api.logout()
      } finally {
        this.user = null
        setCsrfToken('')
      }
    },
  },
})
