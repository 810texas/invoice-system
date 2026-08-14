import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // 第三个参数传 '' 表示不做 VITE_ 前缀过滤，这样 VITE_PROXY_TARGET 也能读到
  const env = loadEnv(mode, process.cwd(), '')
  const proxy = {
    '/api': {
      // 开发期代理，规避 HTTPOnly Cookie 跨端口问题；地址可用 .env.local 覆盖
      target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
      changeOrigin: true,
    },
  }

  return {
    plugins: [
      vue(),
      // Element Plus 按需引入：模板里用到的组件、以及 v-loading 指令都由插件自动 import 并带上
      // 对应样式，取代原先 main.js 里的全量 app.use(ElementPlus) + 全量 index.css。
      // 命令式调用的 ElMessage / ElMessageBox 不在模板里，插件管不到，样式在 main.js 显式引入。
      Components({ resolvers: [ElementPlusResolver()], dts: false }),
    ],
    server: { port: 5173, proxy },
    // vite preview 用的是 preview.proxy（不继承 server.proxy）。
    // 不配的话 `npm run preview` 下所有 /api 请求都会 404，页面能开但接口不通。
    preview: { port: 4173, proxy },
  }
})
