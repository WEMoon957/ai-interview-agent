<template>
  <el-container class="admin-layout">
    <el-aside width="220px" class="aside">
      <div class="logo">笨二娘隆江猪脚饭</div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item index="/console/dashboard">
          <el-icon><Odometer /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item index="/console/jobs">
          <el-icon><Briefcase /></el-icon>
          <span>岗位管理</span>
        </el-menu-item>
        <el-menu-item index="/console/templates">
          <el-icon><Document /></el-icon>
          <span>面试模板</span>
        </el-menu-item>
        <el-menu-item index="/console/screening">
          <el-icon><Files /></el-icon>
          <span>简历初筛</span>
        </el-menu-item>
        <el-menu-item index="/console/sessions">
          <el-icon><User /></el-icon>
          <span>面试会话</span>
        </el-menu-item>
        <el-menu-item index="/console/offline">
          <el-icon><OfficeBuilding /></el-icon>
          <span>线下复试</span>
        </el-menu-item>
        <el-menu-item index="/console/reports">
          <el-icon><DataAnalysis /></el-icon>
          <span>评分报告</span>
        </el-menu-item>
        <el-menu-item index="/console/summary">
          <el-icon><PieChart /></el-icon>
          <span>数据汇总</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <span class="page-title">{{ $route.meta.title }}</span>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Odometer, Briefcase, Document, Files, User, OfficeBuilding, DataAnalysis, PieChart } from '@element-plus/icons-vue'

const route = useRoute()
const activeMenu = computed(() => route.path)
</script>

<style scoped>
.admin-layout {
  height: 100vh;
}

.aside {
  background: #1f2937;
  color: #fff;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 1px;
}

.menu {
  border-right: none;
  background: transparent;
}

.menu :deep(.el-menu-item) {
  color: #cbd5e1;
}

.menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: #6366f1;
}

.menu :deep(.el-menu-item:hover) {
  background: #374151;
}

.header {
  display: flex;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.main {
  background: #f5f7fa;
}
</style>
