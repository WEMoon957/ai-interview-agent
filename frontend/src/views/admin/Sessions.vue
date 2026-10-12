<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>面试会话</span>
          <el-button type="primary" @click="openCreate">创建面试</el-button>
        </div>
      </template>

      <el-table :data="sessions" v-loading="loading" stripe>
        <el-table-column label="候选人" min-width="120">
          <template #default="{ row }">
            <span style="font-weight: 500">{{ row.candidate_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="岗位" min-width="140">
          <template #default="{ row }">{{ jobTitle(row.template_id) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="答题入口" min-width="220">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="openInterview(row)">打开面试页</el-button>
            <el-button size="small" @click="copyLink(row)">复制链接</el-button>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="viewReport(row)">查看报告</el-button>
            <el-button size="small" type="danger" text @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建面试弹窗 -->
    <el-dialog v-model="dialogVisible" title="创建面试会话" width="600px">
      <el-form label-width="100px">
        <el-form-item label="面试模板" required>
          <el-select v-model="form.template_id" placeholder="选择模板" style="width: 100%">
            <el-option
              v-for="t in templates"
              :key="t.id"
              :label="`${jobTitle(t.job_id)}（${t.questions.length} 题）`"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="候选人姓名" required>
          <el-input v-model="form.name" placeholder="候选人姓名" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="选填" />
        </el-form-item>
        <el-form-item label="电话">
          <el-input v-model="form.phone" placeholder="选填" />
        </el-form-item>
        <el-form-item label="简历文件">
          <el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="onFileChange"
            :on-remove="() => (form.file = null)"
          >
            <el-button>选择文件（PDF/DOCX/TXT）</el-button>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">创建</el-button>
      </template>
    </el-dialog>

    <!-- 创建成功弹窗 -->
    <el-dialog v-model="resultVisible" title="面试已创建" width="560px">
      <div class="result">
        <p>候选人答题链接：</p>
        <el-input v-model="interviewLink" readonly>
          <template #append>
            <el-button @click="copyResultLink">复制</el-button>
          </template>
        </el-input>
        <el-button type="primary" style="margin-top: 16px" @click="openInterviewResult">
          打开面试页
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listJobs, listTemplates, listSessions, createSession, deleteSession } from '../../api'

const router = useRouter()
const jobs = ref([])
const templates = ref([])
const sessions = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const resultVisible = ref(false)
const saving = ref(false)
const interviewLink = ref('')
const currentToken = ref('')
const form = ref({ template_id: '', name: '', email: '', phone: '', file: null })

function candidateName(id) {
  return '-'
}
function jobTitle(templateId) {
  const tpl = templates.value.find((t) => t.id === templateId)
  if (!tpl) return '-'
  return jobs.value.find((j) => j.id === tpl.job_id)?.title || '-'
}

function statusType(s) {
  return { pending: 'info', running: 'warning', finished: 'success' }[s] || 'info'
}

function statusText(s) {
  return { pending: '待面试', running: '面试中', finished: '已完成' }[s] || s
}

async function load() {
  loading.value = true
  try {
    ;[jobs.value, templates.value, sessions.value] = await Promise.all([
      listJobs(),
      listTemplates(),
      listSessions(),
    ])
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { template_id: '', name: '', email: '', phone: '', file: null }
  dialogVisible.value = true
}

function onFileChange(file) {
  form.value.file = file.raw
}

async function handleSave() {
  if (!form.value.template_id) {
    ElMessage.warning('请选择模板')
    return
  }
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写候选人姓名')
    return
  }
  saving.value = true
  try {
    const fd = new FormData()
    fd.append('template_id', form.value.template_id)
    fd.append('name', form.value.name.trim())
    fd.append('email', form.value.email)
    fd.append('phone', form.value.phone)
    if (form.value.file) fd.append('file', form.value.file)

    const data = await createSession(fd)
    currentToken.value = data.token
    interviewLink.value = `${window.location.origin}/i/${data.token}`
    dialogVisible.value = false
    resultVisible.value = true
    load()
  } finally {
    saving.value = false
  }
}

function copyText(text) {
  // clipboard API 在非 HTTPS/非 localhost 环境不可用，做降级处理
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).then(() => true).catch(() => false)
  }
  // 降级：老式 execCommand
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
    return Promise.resolve(true)
  } catch (e) {
    return Promise.resolve(false)
  }
}

function copyLink(row) {
  const link = `${window.location.origin}/i/${row.token}`
  copyText(link).then((ok) => {
    if (ok) {
      ElMessage.success('链接已复制')
    } else {
      ElMessage.warning('复制失败，请手动复制：' + link)
    }
  })
}

function openInterview(row) {
  router.push(`/i/${row.token}`)
}

function copyResultLink() {
  copyText(interviewLink.value).then((ok) => {
    if (ok) {
      ElMessage.success('链接已复制')
    } else {
      ElMessage.warning('复制失败，请手动复制：' + interviewLink.value)
    }
  })
}

function openInterviewResult() {
  router.push(`/i/${currentToken.value}`)
}

function viewReport(row) {
  router.push({ path: '/console/reports', query: { session_id: row.id } })
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除会话「${row.candidate_name || '该候选人'}」将同时删除其评分、报告和简历数据，确定要删除吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteSession(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    // 已提示
  }
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.result p {
  margin-bottom: 8px;
  color: #4b5563;
}
</style>
