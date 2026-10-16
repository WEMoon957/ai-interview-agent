<template>
  <el-container class="admin-layout">
    <el-aside width="220px" class="aside">
      <div class="logo">笨二娘隆江猪脚饭</div>
      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item v-if="canSee('dashboard')" index="/console/dashboard">
          <el-icon><Odometer /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('jobs')" index="/console/jobs">
          <el-icon><Briefcase /></el-icon>
          <span>岗位管理</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('company')" index="/console/company">
          <el-icon><Reading /></el-icon>
          <span>公司介绍</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('templates')" index="/console/templates">
          <el-icon><Document /></el-icon>
          <span>面试模板</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('screening')" index="/console/screening">
          <el-icon><Files /></el-icon>
          <span>简历初筛</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('sessions')" index="/console/sessions">
          <el-icon><User /></el-icon>
          <span>AI 面试记录</span>
        </el-menu-item>
        <el-menu-item index="/console/offline">
          <el-icon><OfficeBuilding /></el-icon>
          <span>线下复试</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('reports')" index="/console/reports">
          <el-icon><DataAnalysis /></el-icon>
          <span>评分报告</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('summary')" index="/console/summary">
          <el-icon><PieChart /></el-icon>
          <span>数据汇总</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('offers')" index="/console/offers">
          <el-icon><Ticket /></el-icon>
          <span>录用 / 入职</span>
        </el-menu-item>
        <el-menu-item v-if="canSee('accounts')" index="/console/accounts">
          <el-icon><Setting /></el-icon>
          <span>账号管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <span class="page-title">{{ $route.meta.title }}</span>

        <el-dropdown class="user-dropdown" @command="onUserCommand">
          <span class="user-trigger">
            <el-icon><UserFilled /></el-icon>
            <span>{{ auth.displayName }}</span>
            <el-tag v-if="auth.isAdmin" size="small" type="warning" effect="plain">管理员</el-tag>
            <el-tag v-else-if="auth.user?.role === 'store_manager'" size="small" type="success" effect="plain">店长</el-tag>
            <el-tag v-else size="small" type="info" effect="plain">HR</el-tag>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="password">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 修改密码：改密后后端会撤销除当前会话外的其他登录会话 -->
    <el-dialog v-model="pwdVisible" title="修改密码" width="420px" @closed="resetPwdForm">
      <el-form
        ref="pwdFormRef"
        :model="pwdForm"
        :rules="pwdRules"
        label-position="top"
        @submit.prevent="submitPwd"
      >
        <el-form-item label="当前密码" prop="current_password">
          <el-input v-model="pwdForm.current_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="pwdForm.new_password"
            type="password"
            show-password
            placeholder="至少 10 位，含大小写/数字/符号中至少三类"
          />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input v-model="pwdForm.confirm_password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdSubmitting" @click="submitPwd">确定</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  Briefcase,
  DataAnalysis,
  Document,
  Files,
  Odometer,
  OfficeBuilding,
  PieChart,
  Reading,
  Setting,
  Ticket,
  User,
  UserFilled,
} from '@element-plus/icons-vue'

import { authChangePassword } from '../../api'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const activeMenu = computed(() => route.path)

// 菜单可见性：按路由 meta.roles 判定（店长只见「线下复试」）。
// 与 router 守卫同一份配置，避免两处维护；真正的拦截仍在后端。
function canSee(name) {
  const matched = router.getRoutes().find((r) => r.name === name)
  const roles = matched?.meta?.roles
  if (!roles) return true
  return roles.includes(auth.user?.role)
}

// ── 修改密码 ──────────────────────────────────────────────
const pwdVisible = ref(false)
const pwdSubmitting = ref(false)
const pwdFormRef = ref(null)
const pwdForm = reactive({ current_password: '', new_password: '', confirm_password: '' })

const pwdRules = {
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 10, message: '新密码至少 10 位', trigger: 'blur' },
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) =>
        value === pwdForm.new_password ? callback() : callback(new Error('两次输入的密码不一致')),
      trigger: 'blur',
    },
  ],
}

function resetPwdForm() {
  pwdForm.current_password = ''
  pwdForm.new_password = ''
  pwdForm.confirm_password = ''
  pwdFormRef.value?.clearValidate()
}

async function submitPwd() {
  const valid = await pwdFormRef.value?.validate().catch(() => false)
  if (!valid) return

  pwdSubmitting.value = true
  try {
    const res = await authChangePassword({
      current_password: pwdForm.current_password,
      new_password: pwdForm.new_password,
    })
    pwdVisible.value = false
    ElMessage.success(
      res?.revokedSessions
        ? `密码已修改，已下线其他设备的登录 ${res.revokedSessions} 处`
        : '密码已修改',
    )
  } catch (err) {
    // 错误提示已由请求拦截器统一弹出
  } finally {
    pwdSubmitting.value = false
  }
}

// ── 用户菜单 ──────────────────────────────────────────────
async function onUserCommand(command) {
  if (command === 'password') {
    pwdVisible.value = true
    return
  }

  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定退出登录？', '提示', { type: 'warning' })
    } catch (err) {
      return // 用户取消
    }
    await auth.logout()
    ElMessage.success('已退出登录')
    router.replace({ name: 'login' })
  }
}
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
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.user-dropdown {
  cursor: pointer;
}

.user-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #374151;
  outline: none;
}

.user-trigger:hover {
  color: #6366f1;
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
