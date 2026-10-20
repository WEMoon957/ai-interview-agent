<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>账号管理</span>
          <el-button type="primary" @click="openCreate">新建账号</el-button>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        style="margin-bottom: 14px"
        title="系统不开放自助注册：账号由管理员在这里创建"
        description="给 HR / 店长建号后，把初始密码单独发给本人（建议微信/短信，不要发群里），提醒首次登录后自行改密。"
        show-icon
      />

      <el-table :data="users" v-loading="loading" stripe>
        <el-table-column prop="username" label="用户名" min-width="120" />
        <el-table-column prop="display_name" label="姓名" min-width="100">
          <template #default="{ row }">{{ row.display_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag :type="roleTag(row.role).type" size="small" effect="plain">
              {{ roleTag(row.role).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="150">
          <template #default="{ row }">{{ row.email || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
              {{ row.is_active ? '启用' : '停用' }}
            </el-tag>
            <el-tag v-if="isLocked(row)" type="warning" size="small" style="margin-left: 4px">
              锁定中
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后登录" width="160">
          <template #default="{ row }">
            <span v-if="row.last_login_at">{{ fmt(row.last_login_at) }}</span>
            <span v-else class="muted">从未登录</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="300">
          <template #default="{ row }">
            <el-button
              v-if="!row.is_active"
              size="small"
              type="success"
              text
              @click="onEnable(row)"
            >
              启用
            </el-button>
            <el-button v-if="isLocked(row)" size="small" type="warning" text @click="onUnlock(row)">
              解锁
            </el-button>
            <el-button size="small" type="primary" text @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.is_active" size="small" type="primary" text @click="openReset(row)">
              重置密码
            </el-button>
            <el-button
              v-if="row.is_active"
              size="small"
              type="danger"
              text
              @click="onDisable(row)"
            >
              停用
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有账号。点右上角「新建账号」为 HR 或店长开通登录账号">
            <el-button type="primary" @click="openCreate">新建账号</el-button>
          </el-empty>
        </template>
      </el-table>
    </el-card>

    <!-- 新建账号 -->
    <el-dialog
      v-model="createVisible"
      :title="editingId ? '编辑账号' : '新建账号'"
      width="min(480px, 94vw)"
      @closed="resetCreateForm"
    >
      <el-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-position="top"
        @submit.prevent="submitCreate"
      >
        <el-form-item label="登录账号" prop="username">
          <el-input
            v-model="createForm.username"
            :disabled="!!editingId"
            placeholder="字母/数字/._-，至少 3 位"
          />
          <el-text v-if="editingId" size="small" type="info">
            登录账号创建后不可修改；如需更换，请新建账号并停用旧账号
          </el-text>
        </el-form-item>
        <el-form-item label="姓名（展示用）" prop="display_name">
          <el-input v-model="createForm.display_name" placeholder="如：张三" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-radio-group v-model="createForm.role">
            <el-radio value="hr">HR（全部业务功能）</el-radio>
            <el-radio value="store_manager">店长（仅线下复试）</el-radio>
            <el-radio value="boss">老板（终试决策 + 录用审批）</el-radio>
            <el-radio value="admin">管理员（含账号管理）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="邮箱（选填）" prop="email">
          <el-input v-model="createForm.email" placeholder="用于标识，暂不做验证" />
        </el-form-item>
        <el-form-item v-if="!editingId" label="初始密码" prop="password">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            placeholder="至少 10 位，含大小写/数字/符号中至少三类"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">
          {{ editingId ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 重置密码：结果只展示一次，需要管理员当场转交 -->
    <el-dialog v-model="resetVisible" title="重置密码" width="480px" @closed="resetPwdResult">
      <div v-if="!resetResult">
        <p style="margin-top: 0">
          为 <b>{{ resetTarget?.username }}</b> 设置新密码。重置后该账号的<b>所有登录会话立即失效</b>
          （包括可能被他人持有的），需要重新登录。
        </p>
        <el-form
          ref="resetFormRef"
          :model="resetForm"
          :rules="resetRules"
          label-position="top"
          @submit.prevent="submitReset"
        >
          <el-form-item label="新密码" prop="new_password">
            <el-input
              v-model="resetForm.new_password"
              type="password"
              show-password
              placeholder="至少 10 位，含大小写/数字/符号中至少三类"
            />
          </el-form-item>
        </el-form>
      </div>
      <div v-else>
        <el-result icon="success" title="密码已重置" :sub-title="`新密码：${resetResult}`" />
        <el-alert
          type="warning"
          :closable="false"
          title="此密码只显示这一次，请立即复制并单独发给本人"
          show-icon
        />
      </div>
      <template #footer>
        <el-button v-if="!resetResult" @click="resetVisible = false">取消</el-button>
        <el-button v-if="!resetResult" type="primary" :loading="submitting" @click="submitReset">
          重置
        </el-button>
        <el-button v-if="resetResult" type="primary" @click="copyResult">复制密码</el-button>
        <el-button v-if="resetResult" @click="resetVisible = false">我已转交，关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  createAdminUser,
  disableAdminUser,
  enableAdminUser,
  listAdminUsers,
  resetAdminUserPassword,
  unlockAdminUser,
  updateAdminUser,
} from '../../api'

const users = ref([])
const loading = ref(false)
const submitting = ref(false)

// ── 角色展示 ──────────────────────────────────────────────
const ROLE_MAP = {
  admin: { label: '管理员', type: 'warning' },
  hr: { label: 'HR', type: 'info' },
  store_manager: { label: '店长', type: 'success' },
  boss: { label: '老板', type: 'danger' },
}
const roleTag = (role) => ROLE_MAP[role] || { label: role, type: 'info' }

/** 是否处于登录失败锁定窗口（locked_until 非空且晚于当前时间）。 */
function isLocked(row) {
  if (!row.locked_until) return false
  return new Date(row.locked_until).getTime() > Date.now()
}

/** ISO 时间转本地展示。 */
function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('zh-CN') : '-'
}

async function load() {
  loading.value = true
  try {
    users.value = await listAdminUsers()
  } finally {
    loading.value = false
  }
}

// ── 新建账号 ──────────────────────────────────────────────
const createVisible = ref(false)
// 正在编辑的账号 id；为空串表示「新建」
const editingId = ref('')
const createFormRef = ref(null)
const createForm = reactive({
  username: '',
  display_name: '',
  role: 'hr',
  email: '',
  password: '',
})

const createRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 64, message: '3~64 个字符', trigger: 'blur' },
  ],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 10, message: '密码至少 10 位', trigger: 'blur' },
  ],
}

function resetCreateForm() {
  editingId.value = ''
  createForm.username = ''
  createForm.display_name = ''
  createForm.role = 'hr'
  createForm.email = ''
  createForm.password = ''
  createFormRef.value?.clearValidate()
}

function openCreate() {
  editingId.value = ''
  createVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  createForm.username = row.username
  createForm.display_name = row.display_name || ''
  createForm.role = row.role
  createForm.email = row.email || ''
  createForm.password = ''
  createVisible.value = true
}

async function submitCreate() {
  const valid = await createFormRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    if (editingId.value) {
      // 编辑：仅提交显示名 / 角色 / 邮箱（登录账号与密码不在此处修改）
      await updateAdminUser(editingId.value, {
        role: createForm.role,
        email: createForm.email.trim() || null,
        display_name: createForm.display_name.trim(),
      })
      createVisible.value = false
      ElMessage.success('账号已更新')
    } else {
      await createAdminUser({
        username: createForm.username.trim(),
        password: createForm.password,
        role: createForm.role,
        email: createForm.email.trim() || null,
        display_name: createForm.display_name.trim(),
      })
      createVisible.value = false
      ElMessage.success('账号已创建，请把初始密码单独发给本人')
    }
    await load()
  } catch (err) {
    // 错误提示由请求拦截器统一弹出（重名 409 / 弱密码 422 等）
  } finally {
    submitting.value = false
  }
}

// ── 重置密码 ──────────────────────────────────────────────
const resetVisible = ref(false)
const resetFormRef = ref(null)
const resetTarget = ref(null)
const resetForm = reactive({ new_password: '' })
// 重置成功后只在弹窗里展示一次新密码（后端只存哈希，谁都查不到明文）
const resetResult = ref('')

const resetRules = {
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 10, message: '密码至少 10 位', trigger: 'blur' },
  ],
}

function openReset(row) {
  resetTarget.value = row
  resetForm.new_password = ''
  resetResult.value = ''
  resetVisible.value = true
}

function resetPwdResult() {
  resetResult.value = ''
}

async function submitReset() {
  const valid = await resetFormRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    await resetAdminUserPassword(resetTarget.value.id, {
      new_password: resetForm.new_password,
    })
    resetResult.value = resetForm.new_password
    await load()
  } catch (err) {
    // 错误提示由请求拦截器统一弹出
  } finally {
    submitting.value = false
  }
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(resetResult.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

// ── 启用 / 停用 / 解锁 ────────────────────────────────────
async function onEnable(row) {
  await enableAdminUser(row.id)
  ElMessage.success(`已启用 ${row.username}`)
  await load()
}

async function onDisable(row) {
  try {
    await ElMessageBox.confirm(
      `停用「${row.username}」？该账号将被立即踢下线且无法再登录。`,
      '停用确认',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  const res = await disableAdminUser(row.id)
  ElMessage.success(`已停用${res?.revokedSessions ? `，下线会话 ${res.revokedSessions} 处` : ''}`)
  await load()
}

async function onUnlock(row) {
  await unlockAdminUser(row.id)
  ElMessage.success(`已解除 ${row.username} 的登录锁定`)
  await load()
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.muted {
  color: #909399;
}
</style>
