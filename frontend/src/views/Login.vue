<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="auth-title">智能发票报销审核系统</h2>
      <el-form :model="form" @submit.prevent="onSubmit" label-position="top">
        <el-form-item label="用户名">
          <!-- autocomplete 交给浏览器/密码管理器：不加的话它会把账号密码填错字段、
               也不提供「生成强密码」。注册页用 new-password 就是为了这个。 -->
          <el-input v-model="form.username" autocomplete="username"
                    placeholder="请输入用户名" @keyup.enter="onSubmit" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password
                    autocomplete="current-password" placeholder="请输入密码"
                    @keyup.enter="onSubmit" />
        </el-form-item>
        <el-button type="primary" :loading="loading" class="auth-submit" @click="onSubmit">
          登录
        </el-button>
      </el-form>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon
                style="margin-top: 14px" />

      <div class="auth-links">
        <el-button link type="primary" @click="$router.push('/register')">注册新账号</el-button>
        <el-button link @click="$router.push('/forgot-password')">忘记密码？</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const form = reactive({ username: '', password: '' })
const loading = ref(false)
const error = ref('')

async function onSubmit() {
  if (!form.username || !form.password) {
    error.value = '请输入用户名与密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await auth.login(form.username, form.password)
    // 只接受站内路径：避免把 ?redirect= 里的任意值直接交给路由
    const target = route.query.redirect
    const redirect = typeof target === 'string' && target.startsWith('/') ? target : null
    // 首页按**权限**推导，不再硬编码角色名
    router.push(redirect || auth.homePath())
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>
