import { createApp } from 'vue'
import { createPinia } from 'pinia'

// Element Plus 按需引入（由 vite.config.js 的 unplugin-vue-components 处理）：
// 模板里用到的组件与 v-loading 指令会被自动 import，并只打包用到的样式。
// 例外：ElMessage / ElMessageBox 是命令式调用（不是模板标签），插件无法识别，
// 所以这里显式引入它们的样式（每个文件都是自包含的，无额外依赖）。
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import 'element-plus/theme-chalk/el-overlay.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
