<template>
  <div class="page">
    <h3 class="page-title">我的报销单</h3>

    <div class="page-card">
      <div class="toolbar">
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 160px">
          <el-option v-for="s in statuses" :key="s" :label="statusText(s)" :value="s" />
        </el-select>
        <el-button type="primary" @click="$router.push('/new-reimbursement')">新建报销单</el-button>
        <el-button @click="load">刷新</el-button>
      </div>

      <el-table :data="rows" v-loading="loading" empty-text="暂无报销单，点击「新建报销单」开始">
        <el-table-column prop="code" label="报销单号" width="200" />
        <el-table-column prop="reason" label="报销事由" min-width="160" show-overflow-tooltip />
        <el-table-column label="金额（元）" width="130" align="right">
          <template #default="{ row }">{{ money(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="invoice_count" label="发票张数" width="90" align="center" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险" width="80">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" size="small">{{ riskText(row.risk_level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="版本" prop="version" width="70" align="center" />
        <el-table-column label="提交时间" width="170">
          <template #default="{ row }">{{ timeText(row.submitted_at) }}</template>
        </el-table-column>
        <el-table-column label="驳回原因" min-width="180">
          <template #default="{ row }">
            <span v-if="row.status === 'REJECTED'" class="reject">{{ row.review_comment || '—' }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button v-if="row.status === 'DRAFT'" link type="primary" @click="edit(row)">继续填单</el-button>
            <el-button
              v-if="row.status === 'REJECTED'"
              link
              type="warning"
              :loading="resubmittingId === row.id"
              @click="doResubmit(row)"
            >重新提交</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        class="pager"
        @current-change="load"
        @size-change="onSizeChange"
      />
    </div>

    <el-drawer v-model="drawer" title="报销单详情" size="720px">
      <div v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="报销单号">{{ detail.code }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(detail.status)" size="small">{{ statusText(detail.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="报销事由" :span="2">{{ detail.reason || '—' }}</el-descriptions-item>
          <el-descriptions-item label="总金额">￥{{ money(detail.total_amount) }}</el-descriptions-item>
          <el-descriptions-item label="发票张数">{{ detail.invoice_count }}</el-descriptions-item>
          <el-descriptions-item label="风险等级">
            {{ riskLongText(detail.risk_level) }}
          </el-descriptions-item>
          <el-descriptions-item label="版本号">{{ detail.version }}</el-descriptions-item>
          <el-descriptions-item label="风险明细" :span="2">{{ detail.risk_detail || '无' }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.review_comment" label="驳回原因" :span="2">
            <span class="reject">{{ detail.review_comment }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4>发票明细</h4>
        <InvoiceTable :invoices="detail.invoices" empty-text="本单暂无发票" />

        <h4>审核轨迹</h4>
        <ReviewTimeline :logs="detail.logs" show-result />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import InvoiceTable from '../components/InvoiceTable.vue'
import ReviewTimeline from '../components/ReviewTimeline.vue'
import { money, reasonsHtml, riskLongText, riskText, riskType, STATUSES, statusText, statusType, timeText } from '../utils/format'

const router = useRouter()
const rows = ref([])
const loading = ref(false)
const statusFilter = ref('')
const statuses = STATUSES
const resubmittingId = ref(null)

// 分页（服务端）：状态筛选也交给服务端，否则筛选只作用于当前页会漏数据
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const drawer = ref(false)
const detail = ref(null)

async function load() {
  loading.value = true
  try {
    const res = await api.myReimbursements({
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function onSizeChange() {
  page.value = 1
  load()
}

// 换筛选条件回到第 1 页，避免停在越界页码上
watch(statusFilter, () => {
  page.value = 1
  load()
})

async function openDetail(row) {
  try {
    detail.value = await api.reimbursementDetail(row.id)
    drawer.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function edit(row) {
  router.push({ name: 'new-reimbursement', query: { id: row.id } })
}

async function doResubmit(row) {
  try {
    await ElMessageBox.confirm(
      `确认重新提交「${row.code}」？版本号将 +1，历史审核轨迹保留。`,
      '重新提交',
      { type: 'warning' },
    )
  } catch {
    return
  }
  resubmittingId.value = row.id
  try {
    const res = await api.resubmit(row.id)
    ElMessage.success(`已重新提交，当前版本 v${res.reimbursement.version}`)
  } catch (e) {
    // 后端在预审之前就已把单据改成 DRAFT 且版本 +1，被拦截时服务端状态已经变了，
    // 所以除了拦截原因，还要明确告诉用户去「继续填单」修正后重提。
    await ElMessageBox.alert(
      `${reasonsHtml(e.reasons?.length ? e.reasons : [e.message])}<br><br>`
        + '单据已回到「草稿」，请点「继续填单」修正后重新提交。',
      '提交被拦截',
      { type: 'error', dangerouslyUseHTMLString: true },
    )
  } finally {
    resubmittingId.value = null
    // 无论成功或被拦截都以服务端状态为准刷新，避免列表与服务端不一致
    await load()
  }
}

onMounted(load)
</script>

<style scoped>
.reject { color: #f56c6c; }
h4 { margin: 20px 0 10px; font-size: 14px; }
</style>
