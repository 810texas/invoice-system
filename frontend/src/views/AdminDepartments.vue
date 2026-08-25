<template>
  <div class="page">
    <h3 class="page-title">部门维护</h3>

    <div class="page-card">
      <div class="toolbar">
        <el-button type="primary" @click="openCreate">新建部门</el-button>
        <el-button @click="load">刷新</el-button>
        <span class="muted">本页由<b>财务</b>维护（管理员只读）：单笔上限 / 月度累计上限是提交预审的预算拦截依据（累计达 80% 预警、超 100% 拦截）。</span>
      </div>

      <el-table :data="rows" v-loading="loading" empty-text="暂无部门">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" label="部门名称" width="180" />
        <el-table-column label="单笔报销上限（元）" width="190" align="right">
          <template #default="{ row }">{{ money(row.single_limit) }}</template>
        </el-table-column>
        <el-table-column label="月度累计上限（元）" width="200" align="right">
          <template #default="{ row }">{{ money(row.monthly_limit) }}</template>
        </el-table-column>
        <el-table-column label="操作" min-width="160">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" :title="form.id ? '编辑部门' : '新建部门'" width="440px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="130px">
        <el-form-item label="部门名称" prop="name"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="单笔报销上限" prop="single_limit">
          <el-input-number v-model="form.single_limit" :min="0" :precision="2" :step="500" style="width: 100%" />
        </el-form-item>
        <el-form-item label="月度累计上限" prop="monthly_limit">
          <el-input-number v-model="form.monthly_limit" :min="0" :precision="2" :step="1000" style="width: 100%" />
        </el-form-item>
        <!-- 只提示、不拦截：单笔高于月度是合法配置（月度规则优先生效、单笔规则不起作用），
             但通常是把两个值填反了，所以给个提醒。 -->
        <el-alert
          v-if="singleOverMonthly"
          type="info"
          :closable="false"
          show-icon
          title="单笔上限高于月度上限：月度累计上限会优先生效，单笔上限实际不会触发。"
        />
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
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useAsyncData } from '../composables/useAsyncData'
import { money } from '../utils/format'

const rows = ref([])
const saving = ref(false)
const dialog = ref(false)
const formRef = ref(null)
const form = reactive({ id: null, name: '', single_limit: 0, monthly_limit: 0 })

const { loading, run: load } = useAsyncData(async () => {
  rows.value = await api.adminDepartments()
})

/** 单笔高于月度是合法但通常是填反了：只提示，不阻断保存（后端也不校验这条）。 */
const singleOverMonthly = computed(
  () => Number(form.single_limit) > Number(form.monthly_limit),
)

const rules = {
  name: [{ required: true, message: '请输入部门名称', trigger: 'blur' }],
  single_limit: [
    { type: 'number', min: 0, message: '预算上限不能为负数', trigger: 'change' },
  ],
  monthly_limit: [
    { type: 'number', min: 0, message: '预算上限不能为负数', trigger: 'change' },
  ],
}

function showDialog() {
  dialog.value = true
  nextTick(() => formRef.value?.clearValidate())
}

function openCreate() {
  Object.assign(form, { id: null, name: '', single_limit: 0, monthly_limit: 0 })
  showDialog()
}

function openEdit(row) {
  Object.assign(form, { id: row.id, name: row.name, single_limit: row.single_limit, monthly_limit: row.monthly_limit })
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
    const payload = { name: form.name, single_limit: form.single_limit, monthly_limit: form.monthly_limit }
    if (form.id) await api.updateDepartment(form.id, payload)
    else await api.createDepartment(payload)
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
