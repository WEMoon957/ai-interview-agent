<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>妙记授权</span>
          <el-space>
            <el-button :loading="loading" @click="load">刷新</el-button>
            <el-button type="primary" :loading="starting" @click="start">
              生成授权链接
            </el-button>
          </el-space>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
        title="应用身份只能读「已公开」的妙记；授权后系统以本人身份读取，私有妙记也能自动转写"
        description="把授权链接发给对应的人（店长 / HR），对方在飞书里打开并确认即可。一次授权约 30 天有效，过期后重新生成。"
      />

      <el-alert
        v-if="session"
        :type="sessionAlertType"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
      >
        <template #title>
          <span v-if="session.status === 'pending'">
            等待对方在飞书中确认授权…（本页会自动刷新结果）
          </span>
          <span v-else-if="session.status === 'success'">
            授权成功{{ session.name ? `：${session.name}` : '' }}
          </span>
          <span v-else-if="session.status === 'denied'">对方拒绝了授权</span>
          <span v-else-if="session.status === 'expired'">授权码已过期，请重新生成</span>
          <span v-else>授权未完成</span>
        </template>

        <div v-if="session.status === 'pending'" class="auth-box">
          <div class="auth-line">
            <span class="auth-label">授权链接</span>
            <el-link
              type="primary"
              :href="session.verification_uri_complete"
              target="_blank"
              rel="noopener"
            >
              {{ session.verification_uri_complete || session.verification_uri }}
            </el-link>
          </div>
          <div v-if="session.user_code" class="auth-line">
            <span class="auth-label">验证码</span>
            <b class="auth-code">{{ session.user_code }}</b>
            <span class="muted">（在飞书授权页核对）</span>
          </div>
          <el-button size="small" style="margin-top: 8px" @click="copyLink">复制链接</el-button>
        </div>
      </el-alert>

      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="name" label="姓名" width="120">
          <template #default="{ row }">{{ row.name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="open_id" label="open_id" min-width="230" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status).type" size="small">
              {{ statusTag(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Access 到期" width="170">
          <template #default="{ row }">{{ fmt(row.expires_at) }}</template>
        </el-table-column>
        <el-table-column label="Refresh 到期" width="170">
          <template #default="{ row }">{{ fmt(row.refresh_expires_at) }}</template>
        </el-table-column>
        <el-table-column prop="authorized_by" label="授权操作人" width="120">
          <template #default="{ row }">{{ row.authorized_by || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button
              v-if="row.status !== 'revoked'"
              size="small"
              type="danger"
              text
              @click="revoke(row)"
            >
              撤销
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            description="还没有授权。点右上角「生成授权链接」，发给需要拉取妙记的人（店长 / HR）"
          >
            <el-button type="primary" :loading="starting" @click="start">生成授权链接</el-button>
          </el-empty>
        </template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  listMinutesAuth,
  pollMinutesAuth,
  revokeMinutesAuth,
  startMinutesAuth,
} from '../../api'

const rows = ref([])
const loading = ref(false)
const starting = ref(false)
const session = ref(null)
let timer = null

const sessionAlertType = computed(() => {
  if (!session.value) return 'info'
  return session.value.status === 'success'
    ? 'success'
    : session.value.status === 'pending'
      ? 'info'
      : 'warning'
})

function clearTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function load() {
  loading.value = true
  try {
    rows.value = await listMinutesAuth()
  } finally {
    loading.value = false
  }
}

async function start() {
  starting.value = true
  try {
    const data = await startMinutesAuth()
    session.value = { ...data, status: 'pending', name: '' }
    ElMessage.success('授权链接已生成，请发给对方')
    clearTimer()
    const interval = Math.max(3, Number(data.interval) || 5)
    timer = setInterval(poll, interval * 1000)
  } finally {
    starting.value = false
  }
}

async function poll() {
  const current = session.value
  if (!current?.session_id || current.status !== 'pending') return
  try {
    const res = await pollMinutesAuth(current.session_id)
    if (res.status === 'pending') return
    current.status = res.status
    if (res.status === 'success') {
      current.name = res.authorization?.name || ''
      clearTimer()
      ElMessage.success(`授权成功${current.name ? `：${current.name}` : ''}`)
      await load()
      setTimeout(() => {
        if (session.value === current) session.value = null
      }, 6000)
    } else {
      clearTimer()
      if (res.message) ElMessage.warning(res.message)
    }
  } catch (err) {
    clearTimer()
    current.status = 'error'
  }
}

async function copyLink() {
  const text = session.value?.verification_uri_complete || session.value?.verification_uri
  if (!text) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      // http 非安全上下文下 clipboard API 不可用，退回 textarea 方案
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
    }
    ElMessage.success('链接已复制')
  } catch (err) {
    ElMessage.warning('复制失败，请手动选中链接复制')
  }
}

async function revoke(row) {
  await ElMessageBox.confirm(
    `确定撤销「${row.name || row.open_id}」的妙记授权？撤销后其私有妙记将无法自动转写。`,
    '撤销授权',
    { type: 'warning' },
  )
  await revokeMinutesAuth(row.open_id)
  ElMessage.success('已撤销')
  await load()
}

function fmt(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function statusTag(status) {
  const map = {
    active: { type: 'success', label: '有效' },
    expired: { type: 'warning', label: '已过期' },
    revoked: { type: 'info', label: '已撤销' },
  }
  return map[status] || { type: 'info', label: status || '-' }
}

onMounted(load)
onBeforeUnmount(clearTimer)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.auth-box {
  margin-top: 8px;
  line-height: 1.9;
}

.auth-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.auth-label {
  color: var(--el-text-color-secondary);
  min-width: 64px;
}

.auth-code {
  font-size: 16px;
  letter-spacing: 1px;
}

.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
