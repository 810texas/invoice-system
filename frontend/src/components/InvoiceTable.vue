<template>
  <el-table :data="invoices" size="small" border :empty-text="emptyText">
    <el-table-column label="影像" width="70" align="center">
      <template #default="{ row }">
        <el-image
          v-if="row.attachment_id"
          :src="api.imageUrl(row.attachment_id, { thumb: THUMB_EDGE })"
          :preview-src-list="[api.imageUrl(row.attachment_id)]"
          fit="cover"
          style="width: 44px; height: 44px"
          preview-teleported
          loading="lazy"
        />
      </template>
    </el-table-column>

    <el-table-column label="类型" prop="invoice_type" width="80" />
    <el-table-column v-if="detailed" label="发票代码" width="110">
      <template #default="{ row }">{{ row.invoice_code || '—（数电票）' }}</template>
    </el-table-column>
    <el-table-column label="发票号码" prop="invoice_no" width="125" />
    <el-table-column v-if="detailed" label="开票日期" prop="invoice_date" width="110" />
    <el-table-column label="销售方" prop="seller_name" min-width="150" show-overflow-tooltip />

    <el-table-column v-if="detailed" label="不含税" width="100" align="right">
      <template #default="{ row }">{{ money(row.amount_excl_tax) }}</template>
    </el-table-column>
    <el-table-column v-if="detailed" label="税额" width="90" align="right">
      <template #default="{ row }">{{ money(row.tax_amount) }}</template>
    </el-table-column>
    <el-table-column label="价税合计" width="105" align="right">
      <template #default="{ row }">{{ money(row.total_amount) }}</template>
    </el-table-column>

    <el-table-column label="费用类别" width="90">
      <template #default="{ row }">{{ row.category || '未填' }}</template>
    </el-table-column>
    <el-table-column label="置信度" width="100" align="center">
      <template #default="{ row }">
        <el-tag :type="lowConfidence(row) ? 'warning' : 'success'" size="small">
          {{ row.ocr_confidence }}
        </el-tag>
        <div v-if="row.need_confirm" class="muted">待人工确认</div>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup>
import { api, THUMB_EDGE } from '../api'
import { useMetaStore } from '../stores/meta'
import { money } from '../utils/format'

// detailed=true 展示完整字段（财务审核用）；false 是员工自查的紧凑视图
defineProps({
  invoices: { type: Array, default: () => [] },
  detailed: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无发票' },
})

const meta = useMetaStore()

// 阈值来自后端 /meta，与预审打标用的是同一个配置项，避免两边漂移
function lowConfidence(row) {
  return Number(row.ocr_confidence || 0) < meta.ocr_confidence_threshold
}
</script>

<style scoped>
.muted { color: #909399; font-size: 12px; }
</style>
