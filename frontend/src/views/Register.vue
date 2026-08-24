<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="auth-title">注册账号</h2>
      <p class="muted auth-sub">
        注册后角色为「普通员工」，部门先进入「待分配」，由管理员调整到实际部门。
      </p>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" autocomplete="username"
                    placeholder="3–20 位字母、数字或下划线" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" autocomplete="name" placeholder="请输入真实姓名" />
        </el-form-item>
        <el-form-item label="邮箱（用于找回密码）" prop="email">
          <el-input v-model="form.email" autocomplete="email"
                    placeholder="选填；填写后可用邮箱重置密码" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <!-- new-password：这样浏览器才会提供「生成强密码」而不是回填旧密码 -->
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password"
                    :placeholder="`至少 ${PASSWORD_MIN_LENGTH} 位，需含字母和数字`" />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password"
                    placeholder="请再输入一次" @keyup.enter="onSubmit" />
        </el-form-item>

        <el-button type="primary" class="auth-submit" :loading="loading" @click="onSubmit">
          注册并登录
        </el-button>
      </el-form>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon
                style="margin-top: 14px" />

      <div class="auth-links">
        <el-button link type="primary" @click="$router.push('/login')">已有账号，去登录</el-button>
        <el-button link @click="$router.push('/forgot-password')">忘记密码？</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import { PASSWORD_MIN_LENGTH, passwordRules } from '../utils/password'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref(null)
const loading = ref(false)
const error = ref('')

const form = reactive({ username: '', name: '', email: '', password: '', confirm: '' })

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度 3–20 位', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_]+$/, message: '只能用字母、数字和下划线', trigger: 'blur' },
  ],
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  email: [
    {
      // 选填；填了才校验格式（与后端 is_valid_email 同口径）
      validator: (_rule, value, callback) => {
        if (!value) return callback()
        return /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(value)
          ? callback()
          : callback(new Error('邮箱格式不正确'))
      },
      trigger: 'blur',
    },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    ...passwordRules(() => form.username),
  ],
  confirm: [
    {
      validator: (_rule, value, callback) => {
        if (!value) return callback(new Error('请再输入一次密码'))
        if (value !== form.password) return callback(new Error('两次输入的密码不一致'))
        return callback()
      },
      trigger: 'blur',
    },
  ],
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch {
    return // 校验失败由表单项就地提示
  }
  loading.value = true
  error.value = ''
  try {
    await auth.register({
      username: form.username,
      name: form.name,
      email: form.email || null,
      password: form.password,
    })
    // 注册即登录，直接进该角色的首页
    router.push(auth.homePath())
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>
