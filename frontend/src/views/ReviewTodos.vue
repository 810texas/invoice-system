<template>
  <div class="page">
    <h3 class="page-title">待办审核</h3>

    <div class="page-card">
      <div class="toolbar">
        <el-select v-model="filters.status" placeholder="状态（默认待审核）" clearable style="width: 170px">
          <el-option v-for="s in statuses" :key="s" :label="statusText(s)" :value="s" />
        </el-select>
        <el-input v-model="filters.applicant" placeholder="申请人姓名/账号" clearable style="width: 180px" />
        <el-select v-model="filters.order" style="width: 170px">
          <el-option label="按提交时间" value="submitted_at" />
          <el-option label="按金额从高到低" value="amount" />
          <el-option label="按风险等级" value="risk" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
      </div>

      <el-table :data="rows" v-loading="loading" empty-text="暂无待办单据">
        <el-table-column prop="code" label="报销单号" width="200" />
        <el-table-column prop="applicant" label="申请人" width="90" />
        <el-table-column prop="dept" label="部门" width="100" />
        <el-table-column prop="reason" label="报销事由" min-width="160" show-overflow-tooltip />
        <el-table-column label="金额（元）" width="120" align="right">
          <template #default="{ row }">{{ money(row.total_amount) }}</template>
        </el-table-column>
        <el-table-column prop="invoice_count" label="发票张数" width="90" align="center" />
        <el-table-column label="风险" width="90">
          <template #default="{ row }">
            <el-tag :type="riskType(row.risk_level)" size="small">{{ riskText(row.risk_level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="提交时间" width="165">
          <template #default="{ row }">{{ timeText(row.submitted_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="open(row)">审核</el-button>
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

    <el-drawer v-model="drawer" title="单据审核" size="860px">
      <div v-if="detail" v-loading="detailLoading">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="报销单号">{{ detail.code }}</el-descriptions-item>
          <el-descriptions-item label="申请人">{{ detail.applicant.name }}（{{ detail.applicant.dept || '—' }}）</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(detail.status)" size="small">{{ statusText(detail.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="报销事由" :span="3">{{ detail.reason || '—' }}</el-descriptions-item>
          <el-descriptions-item label="总金额">￥{{ money(detail.total_amount) }}</el-descriptions-item>
          <el-descriptions-item label="发票张数">{{ detail.invoice_count }}</el-descriptions-item>
          <el-descriptions-item label="版本号">v{{ detail.version }}</el-descriptions-item>
          <el-descriptions-item label="预审预警" :span="3">
            <span :class="detail.risk_level === 'MID' ? 'warn' : 'muted'">
              {{ detail.risk_detail || '无预警' }}
            </span>
          </el-descriptions-item>
        </el-descriptions>

        <h4>发票明细与 OCR 结果</h4>
        <InvoiceTable :invoices="detail.invoices" detailed empty-text="本单暂无发票" />

        <h4>
          AI 审核助手
          <span class="muted">
            （{{ aiStatus.is_mock ? 'mock 模式：确定性输出，仅用于联调/验收' : aiStatus.label }}）
          </span>
          <el-tag v-if="!aiStatus.is_mock && !aiStatus.api_key_configured" type="danger" size="small">
            未配置 LLM_API_KEY
          </el-tag>
        </h4>
        <div class="ai-box">
          <!-- 尚未分析：不自动调用（每次打开抽屉都调模型会白花钱），要点一下 -->
          <div v-if="!ai" class="ai-empty">
            <span class="muted">尚未分析本单。</span>
            <el-button size="small" type="primary" :loading="aiLoading" @click="runAdvice">
              生成建议
            </el-button>
            <span class="muted">AI 只给参考意见，不会改变单据状态。</span>
          </div>
          <el-alert
            v-if="!aiStatus.is_mock && !aiStatus.api_key_configured"
            type="error"
            :closable="false"
            show-icon
            title="后端还没有读到 LLM_API_KEY"
            description="请在 backend/.env 里填 LLM_API_KEY=你的Key，保存后重启后端（配置只在启动时读一次）。否则点「生成建议」只会得到规则摘要。"
          />

          <div v-else v-loading="aiLoading">
            <el-alert
              v-if="ai.degraded"
              type="warning"
              :closable="false"
              show-icon
              title="AI 分析不可用，以下为规则摘要"
              :description="ai.error"
            />

            <div class="ai-head">
              <el-tag :type="conclusionType(ai.advice.conclusion)" size="small">
                {{ conclusionText(ai.advice.conclusion) }}
              </el-tag>
              <span class="muted">置信度 {{ confidenceText(ai.advice.confidence) }}</span>
              <span class="muted">· {{ ai.provider }}{{ ai.model ? `:${ai.model}` : '' }}</span>
              <span class="muted">· {{ ai.elapsed_ms }}ms</span>
              <span v-if="ai.advice.adopted === 1" class="muted">· 审核结论与建议一致</span>
              <span v-else-if="ai.advice.adopted === 0" class="muted">· 审核结论与建议不一致</span>
            </div>

            <p class="ai-summary">{{ ai.advice.summary }}</p>

            <ul v-if="ai.advice.reasons?.length" class="ai-list">
              <li v-for="(r, i) in ai.advice.reasons" :key="`r${i}`">{{ r }}</li>
            </ul>
            <div v-if="ai.advice.risk_points?.length" class="ai-group">
              <b>需留意</b>
              <ul class="ai-list risk">
                <li v-for="(r, i) in ai.advice.risk_points" :key="`k${i}`">{{ r }}</li>
              </ul>
            </div>
            <div v-if="ai.advice.missing?.length" class="ai-group">
              <b>建议补充</b>
              <ul class="ai-list">
                <li v-for="(r, i) in ai.advice.missing" :key="`m${i}`">{{ r }}</li>
              </ul>
            </div>

            <div v-if="ai.advice.category_hints?.length" class="ai-group">
              <b>费用类别建议</b>
              <div v-for="h in ai.advice.category_hints" :key="h.invoice_id" class="ai-hint">
                第 {{ h.index }} 张（{{ h.invoice_no || '无号码' }} · {{ h.seller_name || '未知销售方' }}）：
                当前「{{ h.current_category || '未填' }}」
                <template v-if="h.suggested_category">
                  → 建议「{{ h.suggested_category }}」<span class="muted">（{{ h.why }}）</span>
                </template>
                <template v-else>
                  <span class="muted">（{{ h.why || '无法判断' }}）</span>
                </template>
              </div>
              <div class="muted">
                单据已提交，发票字段不可再改；如需修正请驳回并让申请人重新编辑。
              </div>
            </div>

            <div class="toolbar ai-actions">
              <el-button size="small" :loading="aiLoading" @click="runAdvice">重新分析</el-button>
              <el-button size="small" @click="applyAdviceToComment">填入审核意见</el-button>
            </div>
            <div class="muted">
              AI 生成，仅供参考，最终决定以审核人判断为准。金额、发票号以本页展示的为准。
            </div>
          </div>
        </div>

        <h4>审核轨迹</h4>
        <ReviewTimeline :logs="detail.logs" />

        <div v-if="detail.status === 'PENDING'" class="decision">
          <el-input v-model="comment" type="textarea" :rows="3" maxlength="200" show-word-limit
                    placeholder="驳回必须填写原因，例如：发票抬头与公司主体不一致，请补充说明" />
          <div class="toolbar decision-actions">
            <el-button type="success" :loading="acting" @click="decide('approve')">通过</el-button>
            <el-button type="danger" :loading="acting" @click="decide('reject')">驳回</el-button>
            <span class="muted">通过意见可选；驳回必填原因，原因对申请人可见</span>
          </div>
        </div>
        <el-alert v-else type="info" :closable="false" show-icon
                  :title="`当前状态为 ${statusText(detail.status)}，不可再次审核`" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import InvoiceTable from '../components/InvoiceTable.vue'
import ReviewTimeline from '../components/ReviewTimeline.vue'
import {
  conclusionText,
  conclusionType,
  confidenceText,
  money,
  riskText,
  riskType,
  STATUSES,
  statusText,
  statusType,
  timeText,
} from '../utils/format'

const rows = ref([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const acting = ref(false)
const comment = ref('')
const filters = reactive({ status: '', applicant: '', order: 'submitted_at' })
const statuses = STATUSES

// AI 审核助手：ai 为 null 表示本单还没有建议
const ai = ref(null)
const aiLoading = ref(false)
const aiStatus = reactive({ label: '未知', is_mock: true, api_key_configured: false })

// 分页（服务端）：筛选与排序都由后端处理，换条件时回到第 1 页
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

async function load() {
  loading.value = true
  try {
    const res = await api.todos({
      status: filters.status || undefined,
      applicant: filters.applicant || undefined,
      order: filters.order,
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

/** 「查询」按钮：筛选条件变了，必须从第 1 页看起 */
function search() {
  page.value = 1
  load()
}

function onSizeChange() {
  page.value = 1
  load()
}

/** 读取已存在的建议（不发模型请求）；没有就保持 null */
async function loadExistingAdvice(rid) {
  try {
    ai.value = await api.agentAdvice(rid)
  } catch (e) {
    if (e.status !== 404) ElMessage.error(e.message)
    ai.value = null
  }
}

/** 主动生成建议：会真的调用一次大模型并留痕 */
async function runAdvice() {
  if (!detail.value) return
  aiLoading.value = true
  try {
    ai.value = await api.generateAdvice(detail.value.id)
    if (ai.value.degraded) ElMessage.warning('AI 不可用，已改为展示规则摘要')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    aiLoading.value = false
  }
}

/** 把建议要点填进审核意见框，方便财务在此基础上修改 */
function applyAdviceToComment() {
  if (!ai.value) return
  const a = ai.value.advice || {}
  const lines = [`【AI 参考】${conclusionText(a.conclusion)}：${a.summary}`]
  if (a.risk_points?.length) lines.push(`需留意：${a.risk_points.join('；')}`)
  if (a.missing?.length) lines.push(`建议补充：${a.missing.join('；')}`)
  const text = lines.join('\n')
  comment.value = comment.value.trim() ? `${comment.value.trim()}\n${text}` : text
  ElMessage.success('已填入审核意见，可继续编辑')
}

async function open(row) {
  drawer.value = true
  detailLoading.value = true
  comment.value = ''
  ai.value = null // 换单据时先清掉上一单的建议，避免串台
  try {
    detail.value = await api.reviewDetail(row.id)
    // 只读回已存在的建议，不主动触发模型调用（避免每次打开抽屉都花钱）
    await loadExistingAdvice(row.id)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    detailLoading.value = false
  }
}

async function decide(action) {
  if (action === 'reject' && !comment.value.trim()) {
    ElMessage.warning('驳回必须填写原因')
    return
  }
  const label = action === 'approve' ? '通过' : '驳回'
  try {
    await ElMessageBox.confirm(`确认${label}单据「${detail.value.code}」？`, `${label}确认`, {
      type: action === 'approve' ? 'success' : 'warning',
    })
  } catch {
    return
  }
  acting.value = true
  try {
    if (action === 'approve') await api.approve(detail.value.id, comment.value)
    else await api.reject(detail.value.id, comment.value)
    ElMessage.success(`已${label}`)
    drawer.value = false
    ai.value = null // 审核结论已变，下次打开重新读建议（含是否采纳）
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    acting.value = false
  }
}

onMounted(async () => {
  await load()
  try {
    Object.assign(aiStatus, await api.agentStatus())
  } catch {
    // 助手状态拿不到不影响审核主流程
  }
})
</script>

<style scoped>
h4 { margin: 20px 0 10px; font-size: 14px; }
.warn { color: #e6a23c; }
.decision { margin-top: 18px; }
.decision-actions { margin-top: 12px; }

.ai-box {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px 14px;
  background: #fafcff;
}
.ai-empty { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ai-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 10px 0 6px; }
.ai-summary { margin: 6px 0 8px; font-size: 14px; }
.ai-group { margin-top: 8px; }
.ai-list { margin: 4px 0 0; padding-left: 18px; line-height: 1.8; }
.ai-list.risk { color: #e6a23c; }
.ai-hint { font-size: 13px; line-height: 1.9; }
.ai-actions { margin-top: 10px; }
</style>
