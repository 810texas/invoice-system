<template>
  <div class="page">
    <h3 class="page-title">{{ rid ? '编辑报销单' : '新建报销单' }}</h3>

    <div class="page-card">
      <el-form label-width="90px">
        <el-form-item label="报销事由">
          <el-input v-model="reason" type="textarea" :rows="2" maxlength="200" show-word-limit
                    placeholder="例如：8 月客户拜访差旅费用报销" />
        </el-form-item>
      </el-form>
      <div class="toolbar">
        <el-button type="primary" :loading="creating" @click="ensureDraft">
          {{ rid ? '保存事由' : '创建草稿' }}
        </el-button>
        <span v-if="rid" class="muted">当前报销单：{{ rid }}</span>
      </div>
    </div>

    <div class="page-card">
      <h4>① 批量上传发票影像（JPG/PNG/PDF，单个 ≤10MB，单次 ≤5 个文件；PDF 会自动拆页）</h4>
      <el-upload
        :auto-upload="false"
        :show-file-list="false"
        multiple
        accept="image/jpeg,image/png,application/pdf,.pdf"
        :on-change="onFileChange"
      >
        <el-button type="primary" :disabled="!rid" :loading="uploading">选择图片并自动识别</el-button>
      </el-upload>
      <span v-if="!rid" class="muted">请先创建草稿</span>
      <div v-if="failures.length" class="failures">
        <el-alert
          v-for="(f, i) in failures"
          :key="i"
          :title="`${f.origin || '文件'}：${f.error}`"
          type="error"
          :closable="false"
          show-icon
        />
      </div>
    </div>

    <div class="page-card">
      <h4>② 核对识别结果（可修改，费用类别必填）</h4>
      <el-table :data="invoices" v-loading="loading" empty-text="暂无发票，请先上传影像" border>
        <el-table-column label="影像" width="70" align="center">
          <template #default="{ row }">
            <el-image
              v-if="row.attachment_id"
              :src="api.imageUrl(row.attachment_id, { thumb: THUMB_EDGE })"
              :preview-src-list="[api.imageUrl(row.attachment_id)]"
              fit="cover"
              style="width: 42px; height: 42px"
              preview-teleported
              loading="lazy"
            />
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-select v-model="row.invoice_type" size="small" @change="markDirty(row)">
              <el-option v-for="t in meta.invoice_types" :key="t" :label="t" :value="t" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="发票代码" width="120">
          <template #default="{ row }">
            <el-input v-model="row.invoice_code" size="small" placeholder="数电票可空" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="发票号码" width="130">
          <template #default="{ row }">
            <el-input v-model="row.invoice_no" size="small" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="开票日期" width="150">
          <template #default="{ row }">
            <el-date-picker v-model="row.invoice_date" type="date" size="small" value-format="YYYY-MM-DD"
                            style="width: 100%" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="购买方" min-width="150">
          <template #default="{ row }">
            <el-input v-model="row.buyer_name" size="small" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="销售方" min-width="150">
          <template #default="{ row }">
            <el-input v-model="row.seller_name" size="small" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="不含税" width="110">
          <template #default="{ row }">
            <el-input-number v-model="row.amount_excl_tax" :controls="false" :precision="2" size="small"
                             style="width: 100%" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="税额" width="100">
          <template #default="{ row }">
            <el-input-number v-model="row.tax_amount" :controls="false" :precision="2" size="small"
                             style="width: 100%" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="价税合计" width="110">
          <template #default="{ row }">
            <el-input-number v-model="row.total_amount" :controls="false" :precision="2" size="small"
                             style="width: 100%" @change="markDirty(row)" />
          </template>
        </el-table-column>
        <el-table-column label="费用类别" width="120">
          <template #default="{ row }">
            <el-select v-model="row.category" size="small" placeholder="必填" @change="markDirty(row)">
              <el-option v-for="c in meta.expense_categories" :key="c" :label="c" :value="c" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="识别状态" width="130">
          <template #default="{ row }">
            <el-tag :type="row.ocr_confidence < meta.ocr_confidence_threshold ? 'warning' : 'success'" size="small">
              置信度 {{ row.ocr_confidence }}
            </el-tag>
            <div v-if="row.need_confirm" class="muted">待人工确认</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="!dirtyIds.has(row.id)" @click="saveRow(row)">保存修改</el-button>
            <el-button link type="danger" @click="removeRow(row)">移除</el-button>
            <el-button link type="info" @click="showChanges(row)">留痕</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="summary">
        <span>发票张数：<b>{{ invoices.length }}</b></span>
        <span>合计金额：<b>￥{{ money(total) }}</b></span>
      </div>
    </div>

    <div class="page-card">
      <h4>③ 提交预审</h4>
      <div class="toolbar">
        <el-button @click="preview">预审检查</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">提交报销单</el-button>
      </div>
      <div v-if="result">
        <el-alert v-if="result.blocked" type="error" :closable="false" show-icon title="存在硬拦截项，已在服务端阻止提交" />
        <el-alert v-else type="success" :closable="false" show-icon title="预审通过，可以提交" />
        <ul v-if="result.hard_blocks?.length" class="reason-list">
          <li v-for="(r, i) in result.hard_blocks" :key="i" class="hard">硬拦截：{{ r }}</li>
        </ul>
        <ul v-if="result.warnings?.length" class="reason-list">
          <li v-for="(r, i) in result.warnings" :key="i" class="warn">预警：{{ r }}</li>
        </ul>
      </div>
    </div>

    <el-drawer v-model="changeDrawer" title="字段修改痕迹" size="560px">
      <el-table :data="changes" size="small" border empty-text="暂无修改记录">
        <el-table-column prop="field_name" label="字段" width="130" />
        <el-table-column prop="old_value" label="原值" min-width="110" />
        <el-table-column prop="new_value" label="新值" min-width="110" />
        <el-table-column prop="operator" label="修改人" width="90" />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ timeText(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, THUMB_EDGE } from '../api'
import { useMetaStore } from '../stores/meta'
import { money, reasonsHtml, timeText } from '../utils/format'

const route = useRoute()
const router = useRouter()

const rid = ref(route.query.id ? Number(route.query.id) : null)
const reason = ref('')
const invoices = ref([])
// 费用类别 / 发票类型 / 置信度阈值统一来自 meta store（整个会话只拉一次）
const meta = useMetaStore()
const failures = ref([])
const result = ref(null)
const changes = ref([])
const changeDrawer = ref(false)
const dirtyIds = ref(new Set())
const creating = ref(false)
const uploading = ref(false)
const submitting = ref(false)
const loading = ref(false)

// 多选文件时 el-upload 会逐个触发 on-change，这里聚合为一次批量上传
let pendingFiles = []
let uploadTimer = null
// 与后端 upload.py 的 MAX_BATCH 保持一致：超过就分批，而不是丢弃
const MAX_BATCH = 5

const total = computed(() => invoices.value.reduce((sum, i) => sum + Number(i.total_amount || 0), 0))

async function loadDetail() {
  if (!rid.value) return
  loading.value = true
  try {
    const detail = await api.reimbursementDetail(rid.value)
    reason.value = detail.reason
    invoices.value = detail.invoices
    dirtyIds.value = new Set()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

async function ensureDraft() {
  creating.value = true
  try {
    if (rid.value) {
      await api.updateReason(rid.value, reason.value)
      ElMessage.success('报销事由已保存')
    } else {
      const draft = await api.createReimbursement({ reason: reason.value })
      rid.value = draft.id
      ElMessage.success(`草稿已创建：${draft.code}`)
    }
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    creating.value = false
  }
}

async function onFileChange(file) {
  if (!rid.value) {
    ElMessage.warning('请先创建草稿')
    return
  }
  pendingFiles.push(file.raw)
  clearTimeout(uploadTimer)
  uploadTimer = setTimeout(flushUpload, 120)
}

/** 以服务端为准重建发票列表（不碰 reason，避免覆盖用户未保存的事由编辑）。 */
async function refreshInvoices() {
  if (!rid.value) return
  try {
    const detail = await api.reimbursementDetail(rid.value)
    invoices.value = detail.invoices
    dirtyIds.value = new Set()
  } catch {
    // 拉取失败时保留当前列表，至少不丢已识别的结果
  }
}

async function flushUpload() {
  const files = pendingFiles.slice()
  pendingFiles = []
  if (!files.length) return

  // 超过单次上限就分批串行上传（旧实现是 slice(0,5) 后直接清空，第 6 个起被静默丢弃）
  const batches = []
  for (let i = 0; i < files.length; i += MAX_BATCH) {
    batches.push(files.slice(i, i + MAX_BATCH))
  }

  uploading.value = true
  failures.value = []
  const elapsed = []
  let ok = 0
  let hadError = false

  try {
    for (let i = 0; i < batches.length; i += 1) {
      try {
        const res = await api.uploadInvoices(rid.value, batches[i])
        res.items.forEach((item) => {
          if (item.status === 'DONE') {
            invoices.value.push({ ...item.invoice, category: item.invoice.category || '' })
            ok += 1
            if (item.elapsed_ms) elapsed.push(item.elapsed_ms)
          } else {
            failures.value.push({
              origin: item.origin_name || '未知文件',
              error: item.error || '识别失败',
            })
          }
        })
      } catch (e) {
        hadError = true
        failures.value.push({
          origin: batches.length > 1 ? `第 ${i + 1} 批（共 ${batches.length} 批）` : '上传失败',
          error: e.message,
        })
      }
    }

    if (ok) {
      const avg = elapsed.length
        ? Math.round(elapsed.reduce((a, b) => a + b, 0) / elapsed.length)
        : 0
      ElMessage.success(`识别完成 ${ok} 张，平均耗时 ${avg}ms/张`)
      result.value = null
    }
    if (batches.length > 1) {
      ElMessage.info(`共 ${files.length} 个文件，已自动分 ${batches.length} 批上传（单次上限 ${MAX_BATCH} 张）`)
    }
  } finally {
    uploading.value = false
    // 超时/异常时服务端可能已经落库（上传接口逐文件提交），必须以服务端为准重建列表，
    // 否则用户按"没传上去"重试会造成重复报销
    if (hadError || failures.value.length) await refreshInvoices()
  }
}

function markDirty(row) {
  dirtyIds.value = new Set([...dirtyIds.value, row.id])
}

async function saveRow(row) {
  try {
    const payload = {
      invoice_type: row.invoice_type,
      invoice_code: row.invoice_code,
      invoice_no: row.invoice_no,
      invoice_date: row.invoice_date,
      buyer_name: row.buyer_name,
      seller_name: row.seller_name,
      amount_excl_tax: row.amount_excl_tax,
      tax_amount: row.tax_amount,
      total_amount: row.total_amount,
    }
    if (row.category) payload.category = row.category
    const res = await api.patchInvoice(row.id, payload)
    Object.assign(row, res.invoice, { category: res.invoice.category || '' })
    dirtyIds.value = new Set([...dirtyIds.value].filter((id) => id !== row.id))
    ElMessage.success(res.changed.length ? `已保存，留痕字段：${res.changed.join('、')}` : '没有字段变化')
    result.value = null
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function removeRow(row) {
  try {
    await api.removeInvoice(rid.value, row.id)
    invoices.value = invoices.value.filter((i) => i.id !== row.id)
    ElMessage.success('已从本单移除')
    result.value = null
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function showChanges(row) {
  try {
    changes.value = await api.invoiceChanges(row.id)
    changeDrawer.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function preview() {
  if (!rid.value) return ElMessage.warning('请先创建草稿并上传发票')
  try {
    result.value = await api.auditPreview(rid.value)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function submit() {
  if (!rid.value) return ElMessage.warning('请先创建草稿并上传发票')
  submitting.value = true
  try {
    const res = await api.submit(rid.value)
    result.value = res.preaudit
    ElMessage.success(
      res.preaudit.risk_level === 'MID' ? '已提交（存在预警，等待财务审核）' : '已提交，等待财务审核',
    )
    router.push('/my-reimbursements')
  } catch (e) {
    if (e.reasons?.length || e.status === 409) {
      result.value = { blocked: true, hard_blocks: e.reasons || [], warnings: [] }
      await ElMessageBox.alert(
        reasonsHtml(e.reasons?.length ? e.reasons : [e.message]),
        '提交被拦截',
        { type: 'error', confirmButtonText: '返回修改', dangerouslyUseHTMLString: true },
      )
    } else {
      ElMessage.error(e.message)
    }
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  // 两个请求互不依赖，并发发出，避免原先的串行两次往返
  await Promise.all([meta.load(), loadDetail()])
})

// 卸载时清掉待触发的聚合定时器，避免离开页面后仍发出一次上传请求
onUnmounted(() => clearTimeout(uploadTimer))
</script>

<style scoped>
h4 { margin: 0 0 12px; font-size: 14px; }
.summary { margin-top: 14px; display: flex; gap: 28px; font-size: 14px; }
.failures { margin-top: 12px; display: grid; gap: 6px; }
.reason-list { margin: 10px 0 0; padding-left: 18px; line-height: 1.9; }
.reason-list .hard { color: #f56c6c; }
.reason-list .warn { color: #e6a23c; }
</style>
