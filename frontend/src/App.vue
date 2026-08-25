<template>
  <!-- 按需引入后不再有 app.use(ElementPlus, { locale })，组件库的中文文案（表格空态、
       日期选择器、MessageBox 的确认/取消按钮等）改由 ConfigProvider 提供。
       ConfigProvider 会把 locale 写入 Element Plus 的模块级全局配置，
       所以命令式调用的 ElMessage / ElMessageBox 同样能取到中文。 -->
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>

<script setup>
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

<style>
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: #f5f7fa;
  color: #303133;
}

/* el-main 自带 20px 内边距，而每个业务页最外层还有 .page 的留白 —— 两层叠加会显得很空。
   统一由 .page 控制留白，这里把 el-main 的内边距去掉（7 个业务页都用了 .page）。

   前面加 body 是为了**提高特异性**：按需引入 Element Plus 时，它的组件 CSS 在 App.vue
   之后注入，同特异性的规则会被它盖掉（那样这条就白写了）。加一层后不再依赖注入顺序。 */
body .el-main { padding: 0; }

.page { padding: 18px 22px; }

/* 标题加一条左侧色条：比纯文字更容易定位当前页 */
.page-title {
  position: relative;
  font-size: 18px; font-weight: 600; margin: 0 0 16px;
  padding-left: 11px; line-height: 1.4;
}
.page-title::before {
  content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 17px; border-radius: 2px;
  background: var(--el-color-primary, #409eff);
}

/* 卡片加细边框 + 极浅阴影，在浅灰底上更容易区分层次 */
.page-card {
  background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px;
  border: 1px solid #ebeef5; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

/* 表头加一点底色，长表格扫读时不容易串行。
   同样加 body 提升特异性，否则会被 Element Plus 自己的表头底色覆盖。 */
body .el-table th.el-table__cell {
  background: #fafbfc; color: #606266; font-weight: 600;
}

.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
.pager { margin-top: 14px; justify-content: flex-end; }
.muted { color: #909399; font-size: 12px; }

/* 登录 / 注册 / 忘记密码 / 重置密码 共用的居中卡片式布局
   （取值与 Login.vue 原有的 scoped 样式一致，保证四个页面观感统一） */
.auth-wrap {
  min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #1f2d3d, #3a5169); padding: 24px 0;
}
/* 认证卡片在深色渐变上：去掉 el-card 的浅色描边、换成投影更有浮起感。
   用 .auth-wrap .auth-card 两层选择器，确保能盖住 Element Plus 的 .el-card 边框。 */
.auth-wrap .auth-card {
  width: 400px; padding: 8px 12px;
  border: none; box-shadow: 0 8px 28px rgba(0, 0, 0, 0.22);
}
.auth-title { margin: 0 0 6px; font-size: 19px; }
.auth-sub { margin: 0 0 18px; }
.auth-submit { width: 100%; margin-top: 6px; }
.auth-links { margin-top: 14px; display: flex; justify-content: space-between; }
</style>
