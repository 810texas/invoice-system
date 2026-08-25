import { ref } from 'vue'

import { ElMessage } from 'element-plus'

/**
 * 列表加载的通用包装：统一 loading 开关与错误提示。
 *
 * 把 8 个页面里重复的这段模板收敛到一处：
 *   loading.value = true
 *   try { ... } catch (e) { ElMessage.error(e.message) } finally { loading.value = false }
 *
 * 用法：
 *   const { loading, run: load } = useAsyncData(async () => { rows.value = await api.x() })
 *   onMounted(load)
 *
 * `showError: false` 用于调用方想自己处理错误的场景（如区分 409 与普通失败）。
 */
export function useAsyncData(task, { showError = true } = {}) {
  const loading = ref(false)

  async function run(...args) {
    loading.value = true
    try {
      return await task(...args)
    } catch (e) {
      if (showError) ElMessage.error(e.message)
      return undefined
    } finally {
      loading.value = false
    }
  }

  return { loading, run }
}
