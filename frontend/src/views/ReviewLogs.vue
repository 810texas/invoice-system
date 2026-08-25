<template>
  <div class="page">
    <h3 class="page-title">审核轨迹</h3>

    <div class="page-card">
      <div class="toolbar">
        <el-input v-model="filters.applicant" placeholder="申请人姓名" clearable style="width: 170px" />
        <el-select v-model="filters.status" placeholder="单据状态" clearable style="width: 160px">
          <el-option v-for="s in statuses" :key="s" :label="statusText(s)" :value="s" />
        </el-select>
        <el-date-picker
          v-model="range"
          type="datetimerange"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 380px"
        />
        <el-button type="primary" @click="load">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <el-table :data="rows" v-loading="loading" empty-text="暂无轨迹记录">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ timeText(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="code" label="报销单号" width="200" />
        <el-table-column prop="applicant" label="申请人" width="100" />
        <el-table-column label="动作" width="140">
          <template #default="{ row }">
            <el-tag size="small" :type="actionType(row.action)">
              {{ actionText(row.action) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="100" />
        <el-table-column label="单据状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="70" align="center" />
        <el-table-column prop="reason" label="理由 / 意见" min-width="220" show-overflow-tooltip />
      </el-table>
      <div class="muted">最多展示最近 200 条记录；轨迹不可修改、不可删除。</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { useAsyncData } from '../composables/useAsyncData'
import { actionText, actionType, STATUSES, statusText, statusType, timeText } from '../utils/format'

const rows = ref([])
const range = ref(null)
const filters = reactive({ applicant: '', status: '' })
const statuses = STATUSES

const { loading, run: load } = useAsyncData(async () => {
  rows.value = await api.logs({
    applicant: filters.applicant || undefined,
    status: filters.status || undefined,
    start: range.value?.[0] || undefined,
    end: range.value?.[1] || undefined,
  })
})

function reset() {
  filters.applicant = ''
  filters.status = ''
  range.value = null
  load()
}

onMounted(load)
</script>

<style scoped>
.muted { margin-top: 10px; }
</style>
