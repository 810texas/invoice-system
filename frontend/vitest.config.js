import { defineConfig } from 'vitest/config'

// 单独一份配置：单元测试只跑纯逻辑（枚举与请求层），不需要 Vue / Element Plus 的构建插件
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
  },
})
