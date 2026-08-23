import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const routes = [
  // 公开页面：登录 / 注册 / 忘记密码 / 重置密码
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  { path: '/register', name: 'register', component: () => import('../views/Register.vue'), meta: { public: true } },
  {
    path: '/forgot-password',
    name: 'forgot-password',
    component: () => import('../views/ForgotPassword.vue'),
    meta: { public: true },
  },
  {
    // 邮件里的重置链接会带 ?token=xxx
    path: '/reset-password',
    name: 'reset-password',
    component: () => import('../views/ResetPassword.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('../layout/AppLayout.vue'),
    children: [
      { path: '', redirect: '/my-reimbursements' },
      {
        path: 'my-reimbursements',
        name: 'my-reimbursements',
        component: () => import('../views/MyReimbursements.vue'),
        // 守卫用**权限码**而不是角色名：加角色时不用改这里
        meta: { perms: ['reimb:view_own'] },
      },
      {
        path: 'new-reimbursement',
        name: 'new-reimbursement',
        component: () => import('../views/NewReimbursement.vue'),
        meta: { perms: ['reimb:submit'] },
      },
      {
        path: 'review/todos',
        name: 'review-todos',
        component: () => import('../views/ReviewTodos.vue'),
        meta: { perms: ['reimb:review'] },
      },
      {
        path: 'review/logs',
        name: 'review-logs',
        component: () => import('../views/ReviewLogs.vue'),
        meta: { perms: ['review:log'] },
      },
      {
        path: 'admin/users',
        name: 'admin-users',
        component: () => import('../views/AdminUsers.vue'),
        meta: { perms: ['admin:user'] },
      },
      {
        path: 'admin/departments',
        name: 'admin-departments',
        component: () => import('../views/AdminDepartments.vue'),
        // 部门与预算上限由**财务**维护（原先是管理员）
        meta: { perms: ['dept:manage'] },
      },
      {
        path: 'admin/base',
        name: 'admin-base',
        component: () => import('../views/AdminBase.vue'),
        meta: { perms: ['admin:base'] },
      },
    ],
  },
  // 兜底：未知路径不要留白屏（已登录时回自己的首页，未登录时去登录页）
  { path: '/:pathMatch(.*)*', name: 'not-found', redirect: '/login' },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.loaded) await auth.fetchMe()

  if (to.meta.public) {
    // 已登录的人不该再看到登录/注册页
    return auth.isLogin && to.name !== 'reset-password' ? auth.homePath() : true
  }
  if (!auth.isLogin) return { name: 'login', query: { redirect: to.fullPath } }

  const need = to.meta.perms
  if (need && !need.some((p) => auth.can(p))) {
    // 权限不足时回落到该用户自己的首页（而不是白屏或报错）
    return auth.homePath()
  }
  return true
})

export default router
