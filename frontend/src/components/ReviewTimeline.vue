<template>
  <div>
    <el-timeline v-if="logs.length">
      <el-timeline-item
        v-for="log in logs"
        :key="log.id"
        :timestamp="timeText(log.created_at)"
        placement="top"
      >
        <b>{{ actionText(log.action) }}</b>
        <span class="muted"> · {{ log.operator }}</span>
        <span v-if="showResult && log.result" class="muted"> · 结果 {{ statusText(log.result) }}</span>
        <div v-if="log.reason" class="muted">{{ log.reason }}</div>
      </el-timeline-item>
    </el-timeline>
    <div v-else class="muted">暂无审核轨迹</div>
  </div>
</template>

<script setup>
import { actionText, statusText, timeText } from '../utils/format'

defineProps({
  logs: { type: Array, default: () => [] },
  // 员工端额外显示该动作之后的结果状态
  showResult: { type: Boolean, default: false },
})
</script>

<style scoped>
.muted { color: #909399; font-size: 12px; }
</style>
