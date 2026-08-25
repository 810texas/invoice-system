<template>
  <div class="page">
    <h3 class="page-title">基础数据</h3>

    <div class="page-card">
      <h4>角色与权限码</h4>
      <el-table :data="roles" v-loading="loading" border size="small">
        <el-table-column prop="code" label="角色码" width="120" />
        <el-table-column prop="name" label="角色名" width="110" />
        <el-table-column label="权限码" min-width="320">
          <template #default="{ row }">
            <el-tag v-for="p in row.permissions" :key="p" size="small" class="tag">{{ p }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="可见菜单" min-width="200">
          <template #default="{ row }">
            <div v-for="m in row.menus" :key="m.path" class="muted">{{ m.title }}（{{ m.path }}）</div>
          </template>
        </el-table-column>
      </el-table>
      <div class="muted">本期权限矩阵固定为三角色常量，不做可视化配置（见需求第七章二期规划）。</div>
    </div>

    <div class="page-card">
      <h4>菜单种子数据</h4>
      <el-table :data="menus" v-loading="loading" border size="small">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="title" label="菜单名" width="140" />
        <el-table-column prop="path" label="路由" width="200" />
        <el-table-column prop="icon" label="图标" width="120" />
        <el-table-column prop="sort" label="排序" width="80" align="center" />
        <el-table-column label="可见角色" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="r in row.roles" :key="r" size="small" class="tag">{{ r }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { useAsyncData } from '../composables/useAsyncData'

const roles = ref([])
const menus = ref([])

const { loading, run: load } = useAsyncData(async () => {
  const [roleList, menuList] = await Promise.all([api.roles(), api.menus()])
  roles.value = roleList
  menus.value = menuList
})

onMounted(load)
</script>

<style scoped>
h4 { margin: 0 0 12px; font-size: 14px; }
.tag { margin: 2px 4px 2px 0; }
.muted { margin-top: 10px; }
</style>
