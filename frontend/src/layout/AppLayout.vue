<template>
  <el-container class="shell">
    <el-aside width="212px" class="aside">
      <div class="brand">智能发票报销审核</div>
      <el-menu :default-active="route.path" router class="menu">
        <el-menu-item v-for="m in auth.menus" :key="m.path" :index="m.path">
          <span>{{ m.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="workbench">{{ roleLabel }}</span>
        <div class="right">
          <span class="user">{{ auth.user?.name }}（{{ auth.user?.dept || '未分配部门' }}）</span>
          <el-button link @click="openPwd">修改密码</el-button>
          <el-button link type="primary" @click="onLogout">退出登录</el-button>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>

    <!-- 自助改密码：引导管理员拿到的是一次性随机密码，只能从这里改 -->
    <el-dialog v-model="pwdDialog" title="修改密码" width="420px">
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px"
                title="修改后其他设备上的登录会立即失效，需要重新登录。" />
      <el-form ref="pwdFormRef" :model="pwdForm" :rules="pwdRules" label-position="top">
        <el-form-item label="原密码" prop="old_password">
          <el-input v-model="pwdForm.old_password" type="password" show-password
                    autocomplete="current-password" />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="pwdForm.new_password" type="password" show-password
                    autocomplete="new-password"
                    :placeholder="`至少 ${PASSWORD_MIN_LENGTH} 位，需含字母和数字`" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input v-model="pwdForm.confirm" type="password" show-password
                    autocomplete="new-password" @keyup.enter="submitPwd" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="submitPwd">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import { useMetaStore } from '../stores/meta'
import { PASSWORD_MIN_LENGTH, passwordRules } from '../utils/password'

const auth = useAuthStore()
const meta = useMetaStore()
const route = useRoute()
const router = useRouter()

// 基础数据整个会话只取一次（费用类别、状态字典、OCR 置信度阈值）
onMounted(() => {
  meta.load().catch(() => {})
})

// 按**权限**推导工作台名称，与路由守卫、auth.homePath() 用同一套判断。
// 原先这里硬编码 { EMPLOYEE / FINANCE / ADMIN } 角色名，是权限驱动改造漏掉的一处 ——
// 加第四个角色（例如部门主管）时不必再回来改这里。
const roleLabel = computed(() => {
  if (auth.can('admin:user')) return '系统管理'
  if (auth.can('reimb:review')) return '财务审核工作台'
  return '员工工作台'
})

async function onLogout() {
  await auth.logout()
  router.push('/login')
}

// ---- 修改密码 ----
const pwdDialog = ref(false)
const pwdSaving = ref(false)
const pwdFormRef = ref(null)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })

const pwdRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    ...passwordRules(() => auth.user?.username || ''),
  ],
  confirm: [
    {
      validator: (_rule, value, callback) => {
        if (!value) return callback(new Error('请再输入一次新密码'))
        if (value !== pwdForm.new_password) return callback(new Error('两次输入的密码不一致'))
        return callback()
      },
      trigger: 'blur',
    },
  ],
}

function openPwd() {
  Object.assign(pwdForm, { old_password: '', new_password: '', confirm: '' })
  pwdDialog.value = true
  nextTick(() => pwdFormRef.value?.clearValidate())
}

async function submitPwd() {
  try {
    await pwdFormRef.value.validate()
  } catch {
    return
  }
  pwdSaving.value = true
  try {
    await api.changePassword(pwdForm.old_password, pwdForm.new_password)
    pwdDialog.value = false
    ElMessage.success('密码已修改，其他设备需重新登录')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    pwdSaving.value = false
  }
}
</script>

<style scoped>
.shell { height: 100vh; }

/* 侧栏：右缘加一道浅阴影，让导航与内容区有层次（原来是两块纯色直接相邻）。
   position: relative 是必需的 —— 静态定位下 z-index 不生效，阴影会被右侧白底盖住。 */
.aside {
  background: #1f2d3d; color: #fff;
  position: relative; z-index: 2;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.14);
}
.brand {
  padding: 20px 18px 18px; font-size: 15px; font-weight: 600; color: #fff;
  letter-spacing: 1px; border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.menu { border-right: none; background: transparent; padding: 8px 0; }
/* 菜单项改成圆角「胶囊」并留出左右间距，比整条铺满的方块更轻 */
.menu :deep(.el-menu-item) {
  height: 44px; line-height: 44px; margin: 2px 10px; border-radius: 6px;
  color: #c0c4cc; position: relative;
}
.menu :deep(.el-menu-item:hover) { color: #fff; background: rgba(255, 255, 255, 0.06); }
.menu :deep(.el-menu-item.is-active) { color: #fff; background: #2b3a4d; font-weight: 600; }
/* 选中项左侧色条：一眼能看出当前在哪个页面 */
.menu :deep(.el-menu-item.is-active)::before {
  content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 18px; border-radius: 0 3px 3px 0;
  background: var(--el-color-primary, #409eff);
}

/* 头部：硬边框改浅阴影；工作台名称从「灰色小字」提到正文级（它比右下角的按钮更该被看到）。
   同样需要 position: relative 才能让阴影压在下方内容之上。 */
.header {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid #ebeef5;
  position: relative; z-index: 1;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}
.workbench { font-size: 14px; font-weight: 600; color: #303133; }
.right { display: flex; align-items: center; gap: 14px; }
.user { font-size: 13px; color: #606266; }
</style>
