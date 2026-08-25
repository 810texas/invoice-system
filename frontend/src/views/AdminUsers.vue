<template>
  <div class="page">
    <h3 class="page-title">用户管理</h3>

    <div class="page-card">
      <div class="toolbar">
        <el-button type="primary" @click="openCreate">新建用户</el-button>
        <el-button @click="load">刷新</el-button>
        <span class="muted">密码使用 BCrypt 散列存储；重置密码会使该用户已登录的凭证立即失效。</span>
      </div>

      <!-- 忘记密码在「没配 SMTP」时会落成待处理申请；配了 SMTP 则直接发邮件，这里就是空的 -->
      <el-alert v-if="resetRequests.length" type="warning" :closable="false" show-icon
                class="reset-alert">
        <template #title>
          有 {{ resetRequests.length }} 条「忘记密码」申请待处理
        </template>
        <div v-for="r in resetRequests" :key="r.request_id" class="reset-item">
          {{ r.name }}（{{ r.username }}<template v-if="r.dept"> · {{ r.dept }}</template>）
          于 {{ timeText(r.created_at) }} 提交
          <el-button link type="primary" @click="resetPwd({ id: r.user_id, name: r.name })">
            为该用户重置密码
          </el-button>
        </div>
      </el-alert>

      <el-table :data="rows" v-loading="loading" empty-text="暂无用户">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="username" label="登录账号" width="130" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="email" label="邮箱" min-width="180">
          <template #default="{ row }">
            <span v-if="row.email">{{ row.email }}</span>
            <span v-else class="muted">未填（无法自助找回密码）</span>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="110">
          <template #default="{ row }">{{ roleName(row.role) }}</template>
        </el-table-column>
        <el-table-column prop="dept" label="部门" width="120" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'NORMAL' ? 'success' : 'danger'" size="small">
              {{ row.status === 'NORMAL' ? '正常' : '锁定/停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="锁定截止" width="170">
          <template #default="{ row }">{{ timeText(row.locked_until) }}</template>
        </el-table-column>
        <el-table-column label="操作" min-width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" @click="resetPwd(row)">重置密码</el-button>
            <el-button link type="success" @click="unlock(row)">解锁</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑用户' : '新建用户'" width="460px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="登录账号" prop="username">
          <el-input v-model="form.username" :disabled="!!form.id" />
        </el-form-item>
        <el-form-item label="姓名" prop="name"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="选填；填了才能自助找回密码" />
        </el-form-item>
        <el-form-item v-if="!form.id" label="初始密码" prop="password">
          <el-input v-model="form.password" type="password" show-password
                    :placeholder="`至少 ${PASSWORD_MIN_LENGTH} 位，需含字母和数字`" />
        </el-form-item>
        <el-form-item label="角色" prop="role_id">
          <el-select v-model="form.role_id" style="width: 100%" @change="onRoleChange">
            <el-option v-for="r in roles" :key="r.id" :label="r.name" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="form.dept_id" clearable style="width: 100%">
            <el-option v-for="d in depts" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
          <!-- 顺手解释一下「财务为什么还要部门」：部门只影响申请人所在部门的预算与数据范围 -->
          <div v-if="roleDeptHint" class="muted" style="line-height: 1.5">{{ roleDeptHint }}</div>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="NORMAL">正常</el-radio>
            <el-radio value="LOCKED">停用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAsyncData } from '../composables/useAsyncData'
import { timeText } from '../utils/format'
import { PASSWORD_MIN_LENGTH, passwordPolicyError, passwordRules } from '../utils/password'

const rows = ref([])
const roles = ref([])
const depts = ref([])
const resetRequests = ref([])
const saving = ref(false)
const dialog = ref(false)
const formRef = ref(null)
const form = reactive({ id: null, username: '', name: '', email: '', password: '', role_id: null, dept_id: null, status: 'NORMAL' })

const { loading, run: load } = useAsyncData(async () => {
  const [users, roleList, deptList, requests] = await Promise.all([
    api.users(),
    api.roles(),
    api.adminDepartments(),
    // 「忘记密码申请」只是顶部的提示条：它取不到（比如后端版本较旧）不该把
    // 用户列表一起带崩 —— 所以单独吞掉错误，返回空数组。
    api.passwordResetRequests().catch(() => []),
  ])
  rows.value = users
  roles.value = roleList
  depts.value = deptList
  resetRequests.value = requests
})

// 校验规则与后端对齐：admin.py 的 create_user 要求账号/姓名非空、
// 邮箱格式正确且唯一、初始密码满足统一的强度口径（≥8 位且含字母和数字）
const rules = {
  username: [{ required: true, message: '请输入登录账号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  role_id: [{ required: true, message: '请选择角色', trigger: 'change' }],
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
    {
      // 编辑态不展示密码框（改密码走「重置密码」），这里直接放行
      validator: (_rule, value, callback) => {
        if (form.id) return callback()
        if (!value) return callback(new Error('请设置初始密码'))
        return callback() // 具体强度由下面 passwordRules 统一校验
      },
      trigger: 'blur',
    },
    ...passwordRules(() => form.username),
  ],
}

function showDialog() {
  dialog.value = true
  // 清掉上一次打开时残留的校验红字
  nextTick(() => formRef.value?.clearValidate())
}

function roleName(code) {
  return roles.value.find((r) => r.code === code)?.name || code
}

/**
 * 角色改成「财务人员」时自动带出财务部。
 *
 * 为什么财务本来就不需要部门：部门只影响「**申请人**所在部门的预算上限」与数据范围，
 * 而财务既没有 reimb:submit（不提交报销单）、数据范围又是 ALL —— 部门对财务纯属展示信息。
 * 所以不该让管理员每次手选。后端也会在部门为空时兜同样的默认值，这里只是把它显示出来。
 *
 * 只在管理员**主动切换**角色时触发（el-select 的 change 事件），
 * 打开编辑框时不会触发，避免把该用户已存的部门悄悄改掉。
 */
function onRoleChange(roleId) {
  const role = roles.value.find((r) => r.id === roleId)
  if (!role?.default_dept_name) return
  const dept = depts.value.find((d) => d.name === role.default_dept_name)
  if (dept) form.dept_id = dept.id
}

const roleDeptHint = computed(() => {
  const role = roles.value.find((r) => r.id === form.role_id)
  if (!role?.default_dept_name) return ''
  return `${role.name}不提交报销单、数据范围为全部，所属部门仅用于展示；已默认「${role.default_dept_name}」，可手动改。`
})

function openCreate() {
  Object.assign(form, { id: null, username: '', name: '', email: '', password: '', role_id: roles.value[0]?.id, dept_id: depts.value[0]?.id, status: 'NORMAL' })
  showDialog()
}

function openEdit(row) {
  Object.assign(form, {
    id: row.id, username: row.username, name: row.name, email: row.email || '', password: '',
    role_id: row.role_id, dept_id: row.dept_id, status: row.status === 'NORMAL' ? 'NORMAL' : 'LOCKED',
  })
  showDialog()
}

async function save() {
  try {
    await formRef.value.validate()
  } catch {
    return // 校验未通过，错误提示由 el-form-item 就地展示
  }
  saving.value = true
  try {
    if (form.id) {
      await api.updateUser(form.id, {
        username: form.username, name: form.name, email: form.email || null,
        role_id: form.role_id, dept_id: form.dept_id, status: form.status,
      })
    } else {
      await api.createUser({
        username: form.username, name: form.name, email: form.email || null,
        password: form.password, role_id: form.role_id, dept_id: form.dept_id,
        status: 'NORMAL',
      })
    }
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function resetPwd(row) {
  try {
    const { value } = await ElMessageBox.prompt(
      `为「${row.name}」设置新密码（至少 ${PASSWORD_MIN_LENGTH} 位，需含字母和数字）`,
      '重置密码',
      {
        inputType: 'password',
        inputValidator: (v) => passwordPolicyError(v) || true,
      },
    )
    await api.adminResetPassword(row.id, value)
    ElMessage.success('已重置；该用户需用新密码重新登录')
    await load() // 该用户的重置申请会一起作废，刷新一下列表
  } catch (e) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

async function unlock(row) {
  try {
    await api.unlockUser(row.id)
    ElMessage.success('已解锁并恢复为正常状态')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.reset-alert { margin-bottom: 14px; }
.reset-item { margin-top: 4px; font-size: 13px; line-height: 1.9; }
</style>
