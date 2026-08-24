<template>
  <div class="auth-wrap">
    <el-card class="auth-card">
      <h2 class="auth-title">忘记密码</h2>
      <p class="muted auth-sub">
        填写用户名或邮箱，我们会把重置方式发给你。
      </p>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="用户名或邮箱" prop="account">
          <el-input v-model="form.account" autocomplete="username"
                    placeholder="例如 zhangwei 或 zhangwei@example.com"
                    @keyup.enter="onSubmit" />
        </el-form-item>
        <el-button type="primary" class="auth-submit" :loading="loading" @click="onSubmit">
          提交重置申请
        </el-button>
      </el-form>

      <!-- 后端不论账号是否存在都返回同一句话（避免这个接口变成账号枚举器），
           所以这里也只展示后端给的文案，不做任何"账号不存在"之类的判断 -->
      <el-alert v-if="message" :title="message" type="success" :closable="false" show-icon
                style="margin-top: 14px" />
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon
                style="margin-top: 14px" />

      <div class="auth-links">
        <el-button link type="primary" @click="$router.push('/login')">返回登录</el-button>
        <el-button link @click="$router.push('/register')">还没有账号？去注册</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'

import { api } from '../api'

const formRef = ref(null)
const loading = ref(false)
const message = ref('')
const error = ref('')
const form = reactive({ account: '' })

const rules = {
  account: [{ required: true, message: '请输入用户名或邮箱', trigger: 'blur' }],
}

async function onSubmit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  message.value = ''
  error.value = ''
  try {
    const res = await api.forgotPassword(form.account)
    message.value = res.message
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>
