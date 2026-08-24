<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="auth-title">设置新密码</h2>

      <el-alert v-if="!token" type="error" :closable="false" show-icon
                title="链接里缺少重置令牌"
                description="请从邮件里的链接进入本页，或重新提交一次「忘记密码」。" />

      <template v-else>
        <p class="muted auth-sub">设置后该账号在其他设备上的登录会立即失效，需要重新登录。</p>

        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
          <el-form-item label="新密码" prop="password">
            <el-input v-model="form.password" type="password" show-password autocomplete="new-password"
                      :placeholder="`至少 ${PASSWORD_MIN_LENGTH} 位，需含字母和数字`" />
          </el-form-item>
          <el-form-item label="确认新密码" prop="confirm">
            <el-input v-model="form.confirm" type="password" show-password autocomplete="new-password"
                      placeholder="请再输入一次" @keyup.enter="onSubmit" />
          </el-form-item>
          <el-button type="primary" class="auth-submit" :loading="loading" @click="onSubmit">
            确认重置
          </el-button>
        </el-form>
      </template>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon
                style="margin-top: 14px" />
      <el-alert v-if="done" type="success" :closable="false" show-icon
                title="密码已重置，正在跳转登录页…" style="margin-top: 14px" />

      <div class="auth-links">
        <el-button link type="primary" @click="$router.push('/login')">返回登录</el-button>
        <el-button link @click="$router.push('/forgot-password')">重新申请重置</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api'
import { PASSWORD_MIN_LENGTH, passwordRules } from '../utils/password'

const route = useRoute()
const router = useRouter()
const formRef = ref(null)
const loading = ref(false)
const error = ref('')
const done = ref(false)
// 邮件里的链接形如 /reset-password?token=xxx
const token = ref(String(route.query.token || ''))

const form = reactive({ password: '', confirm: '' })

const rules = {
  password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    ...passwordRules(),
  ],
  confirm: [
    {
      validator: (_rule, value, callback) => {
        if (!value) return callback(new Error('请再输入一次新密码'))
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
    return
  }
  loading.value = true
  error.value = ''
  try {
    await api.resetPassword(token.value, form.password)
    done.value = true
    setTimeout(() => router.push('/login'), 1200)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>
