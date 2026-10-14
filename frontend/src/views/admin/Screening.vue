<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>简历初筛</span>
          <div class="header-actions">
            <el-select
              v-model="jobId"
              placeholder="选择岗位"
              style="width: 220px"
              filterable
              @change="loadScreenings"
            >
              <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
            </el-select>
            <el-tag v-if="isDirect" type="warning" effect="dark">免初筛 · 直通面试</el-tag>
            <el-button v-if="!isDirect" :loading="notifying" @click="pushToHr">
              重新推送榜单
            </el-button>
            <el-button type="primary" @click="openUpload">
              {{ isDirect ? '上传简历并生成面试链接' : '上传岗位文件夹' }}
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="isDirect"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        title="该岗位已设为「免初筛」：上传简历后不做 AI 打分，直接为每位候选人生成 AI 面试链接。"
      />

      <el-alert
        v-if="!isDirect"
        type="success"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        title="初筛完成后会自动推送给 HR：达到门槛（默认 ≥65）的候选人会生成 AI 面试链接，随卡片一起发出，并同步创建飞书待办 —— 无需手动点击。"
      />

      <!-- 上传结果小结 -->
      <el-alert
        v-if="uploadResult"
        :title="uploadResultTitle"
        :type="uploadResult.failed ? 'warning' : pushFailed ? 'error' : 'success'"
        :closable="true"
        style="margin-bottom: 12px"
        @close="uploadResult = null"
      />
      <el-alert
        v-if="pushFailed"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
        :title="`HR 飞书推送失败：${uploadResult?.notify?.reason || '未知原因'}`"
        description="业务数据已正常入库，不影响使用。常见原因：服务器未把推送改为 api 传输、或 PUBLIC_BASE_URL 仍是本机地址。"
      />

      <el-table
        ref="tableRef"
        :data="screenings"
        v-loading="loading"
        stripe
        row-key="id"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" reserve-selection />
        <el-table-column label="#" width="60">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="候选人" min-width="120">
          <template #default="{ row }">
            <span style="font-weight: 500">{{ row.candidate_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="file_name" label="简历文件" min-width="180" show-overflow-tooltip />
        <el-table-column label="初筛分" width="110" sortable :sort-by="(r) => r.total_score">
          <template #default="{ row }">
            <span v-if="row.total_score != null" class="score" :class="scoreClass(row.total_score)">
              {{ row.total_score.toFixed(1) }}
            </span>
            <el-tag v-else type="warning" size="small" effect="plain">免初筛</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="建议" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.recommendation" :type="verdictType(row.recommendation)" size="small">
              {{ row.recommendation }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openDetail(row)">详情</el-button>
            <el-button size="small" text type="primary" @click="copyLink(row)">答题链接</el-button>
            <el-button size="small" text type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无初筛数据，请选择岗位并上传简历文件夹" />
        </template>
      </el-table>

      <div class="footer-bar">
        <span class="tip">
          <template v-if="isDirect">免初筛岗位：上传即自动生成答题链接，可点「答题链接」复制</template>
          <template v-else>已选 {{ selected.length }} 位候选人</template>
        </span>
        <el-button
          v-if="!isDirect"
          type="primary"
          :disabled="!selected.length"
          @click="inviteSelected"
        >
          批量发起 AI 面试
        </el-button>
      </div>
    </el-card>

    <!-- 上传文件夹弹窗 -->
    <el-dialog
      v-model="uploadVisible"
      :title="isDirect ? '上传简历文件夹（免初筛 · 直接发面试链接）' : '上传岗位文件夹（批量简历）'"
      width="620px"
    >
      <el-form label-width="100px">
        <el-form-item label="目标岗位" required>
          <el-select v-model="jobId" placeholder="选择岗位" style="width: 100%" filterable>
            <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="简历文件夹">
          <el-upload
            ref="uploadRef"
            directory
            multiple
            :auto-upload="false"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
            :file-list="fileList"
          >
            <el-button>选择文件夹</el-button>
            <template #tip>
              <div class="tip">
                支持 PDF / DOCX / TXT；文件名建议为「姓名.pdf」
                <template v-if="isDirect">，AI 将直接为每人生成面试链接</template>
                <template v-else>，AI 将按岗位 JD 逐份打分</template>
              </div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="screening" @click="submitUpload">
          {{ screening ? '处理中…' : isDirect ? '上传并生成面试链接' : '开始 AI 初筛' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="简历初筛详情" size="520px">
      <template v-if="detail">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="候选人">{{ detail.candidate_name }}</el-descriptions-item>
          <el-descriptions-item label="简历">{{ detail.file_name }}</el-descriptions-item>
          <el-descriptions-item label="初筛总分">
            <span class="score" :class="scoreClass(detail.total_score)">
              {{ detail.total_score?.toFixed(1) }}
            </span>
            <el-tag :type="verdictType(detail.recommendation)" size="small" style="margin-left: 8px">
              {{ detail.recommendation }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">维度得分</el-divider>
        <el-table :data="detail.dim_scores" size="small" stripe>
          <el-table-column prop="name" label="维度" width="110" />
          <el-table-column prop="score" label="得分" width="80" />
          <el-table-column prop="max_score" label="满分" width="80" />
          <el-table-column prop="weighted_score" label="加权贡献" />
        </el-table>

        <el-divider content-position="left">综合评价</el-divider>
        <p class="summary">{{ detail.summary || '—' }}</p>

        <template v-if="detail.highlights?.length">
          <el-divider content-position="left">亮点</el-divider>
          <ul><li v-for="(h, i) in detail.highlights" :key="i">{{ h }}</li></ul>
        </template>
        <template v-if="detail.risks?.length">
          <el-divider content-position="left">风险 / 待确认</el-divider>
          <ul><li v-for="(r, i) in detail.risks" :key="i">{{ r }}</li></ul>
        </template>
      </template>
    </el-drawer>

    <!-- 面试链接弹窗 -->
    <el-dialog v-model="linkVisible" title="AI 面试答题链接（可直接转发给候选人）" width="640px">
      <el-table :data="invitedItems" size="small" stripe max-height="360">
        <el-table-column prop="candidate_name" label="候选人" width="120" />
        <el-table-column label="答题链接" min-width="320">
          <template #default="{ row }">
            <el-input :model-value="linkOf(row)" readonly size="small" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" @click="copyText(linkOf(row))">复制</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="linkVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listJobs,
  listScreenings,
  uploadResumes,
  inviteScreenings,
  deleteScreening,
  notifyScreenings,
} from '../../api'

const jobs = ref([])
const jobId = ref('')
const screenings = ref([])
const loading = ref(false)
const uploadVisible = ref(false)
const screening = ref(false)
const notifying = ref(false)
const fileList = ref([])
const uploadRef = ref(null)
const tableRef = ref(null)
const selected = ref([])
const uploadResult = ref(null)
const detailVisible = ref(false)
const detail = ref(null)
const linkVisible = ref(false)
const invitedItems = ref([])

// 当前岗位是否「免初筛」（上传即发面试链接）
const currentJob = computed(() => jobs.value.find((j) => j.id === jobId.value))
const isDirect = computed(() => !!currentJob.value?.skip_screening)

// 自动推送是否失败（用于显著提示，避免"以为没推"）
const pushFailed = computed(() => {
  const n = uploadResult.value?.notify
  if (!n) return false
  return n.status === 'failed' || n.task?.status === 'failed'
})

const uploadResultTitle = computed(() => {
  if (!uploadResult.value) return ''
  const r = uploadResult.value
  const head = `本次上传 ${r.total} 份，成功 ${r.succeeded} 份，失败 ${r.failed} 份`
  const suffix = r.mode === 'direct_invite' ? '（已生成面试链接）' : notifySuffix(r.notify)
  return head + suffix
})

/** 推送 / 待办结果后缀文案 */
function notifySuffix(notify) {
  if (!notify) return ''
  const parts = []
  if (notify.status === 'sent') {
    parts.push(`已推送 HR 飞书（推荐 ${notify.recommended} 人）`)
  } else if (notify.status === 'failed') {
    parts.push('⚠️ HR 飞书推送失败')
  }
  if (notify.task?.status === 'created') {
    parts.push(`已建飞书待办 ${notify.task.assignees?.length || 0} 条`)
  } else if (notify.task?.status === 'failed') {
    parts.push('⚠️ 飞书待办创建失败')
  }
  return parts.length ? `；${parts.join('；')}` : ''
}

async function pushToHr() {
  if (!jobId.value) {
    ElMessage.warning('请先选择岗位')
    return
  }
  notifying.value = true
  try {
    const res = await notifyScreenings({ job_id: jobId.value })
    if (res.status === 'sent') {
      const taskPart =
        res.task?.status === 'created'
          ? `；已建飞书待办 ${res.task.assignees?.length || 0} 条`
          : res.task?.status === 'failed'
            ? '；⚠️ 飞书待办创建失败'
            : ''
      ElMessage.success(`已推送 HR 飞书（推荐 ${res.recommended} 人）${taskPart}`)
    } else {
      ElMessage.warning(res.reason || '未推送')
    }
  } finally {
    notifying.value = false
  }
}

function scoreClass(score) {
  if (score >= 80) return 's-high'
  if (score >= 65) return 's-mid'
  if (score >= 50) return 's-low'
  return 's-bad'
}

function verdictType(v) {
  return { 强烈推荐: 'success', 推荐: 'success', 待定: 'warning', 不推荐: 'danger' }[v] || 'info'
}

function statusType(s) {
  return { screened: 'info', imported: 'info', interview_sent: 'success', rejected: 'danger' }[s] || 'info'
}

function statusText(s) {
  return {
    screened: '已初筛',
    imported: '已导入',
    interview_sent: '已发面试',
    rejected: '已淘汰',
  }[s] || s
}

async function loadJobs() {
  jobs.value = await listJobs()
  if (!jobId.value && jobs.value.length) jobId.value = jobs.value[0].id
}

async function loadScreenings() {
  loading.value = true
  try {
    screenings.value = await listScreenings(jobId.value ? { job_id: jobId.value } : {})
  } finally {
    loading.value = false
  }
}

function openUpload() {
  fileList.value = []
  uploadVisible.value = true
}

function onFileChange(file, files) {
  fileList.value = files
}

function onFileRemove(file, files) {
  fileList.value = files
}

async function submitUpload() {
  if (!jobId.value) {
    ElMessage.warning('请选择岗位')
    return
  }
  if (!fileList.value.length) {
    ElMessage.warning('请选择包含简历的文件夹')
    return
  }
  screening.value = true
  try {
    const fd = new FormData()
    fd.append('job_id', jobId.value)
    // 取文件夹名（webkitRelativePath 形如 "张三简历/张三.pdf"）
    const folderName = fileList.value[0]?.raw?.webkitRelativePath?.split('/')?.[0] || ''
    fd.append('folder_name', folderName)
    // 免初筛岗位：直通模式下不做 AI 打分，直接生成面试链接
    fd.append('direct_invite', isDirect.value ? 'true' : 'false')
    fd.append('screen', 'true')
    fileList.value.forEach((f) => fd.append('files', f.raw))

    const res = await uploadResumes(fd)
    uploadResult.value = res
    uploadVisible.value = false
    await loadScreenings()

    if (res.mode === 'direct_invite') {
      // 直通模式：直接把生成的答题链接弹出来，方便 HR 转发
      invitedItems.value = res.items.filter((it) => it.interview_path)
      ElMessage.success(`已上传 ${res.succeeded} 份并生成面试链接`)
      if (invitedItems.value.length) linkVisible.value = true
    } else {
      // 自动推送失败要显著提示，避免"以为系统没推"
      const n = res.notify || {}
      if (n.status === 'failed' || n.task?.status === 'failed') {
        ElMessage.warning(`初筛完成 ${res.succeeded} 份，但自动推送未成功，详见页面提示`)
      } else if (n.warning) {
        // 基址仍为本机地址时给出显式告警（卡片里的链接别人打不开）
        ElMessage.warning(n.warning)
      } else {
        ElMessage.success(`初筛完成：成功 ${res.succeeded} 份${notifySuffix(n)}`)
      }
      if (res.note) ElMessage.warning(res.note)
    }
  } finally {
    screening.value = false
  }
}

function onSelectionChange(rows) {
  selected.value = rows
}

async function openDetail(row) {
  detail.value = row
  detailVisible.value = true
}

function linkOf(row) {
  return row.interview_path ? `${window.location.origin}${row.interview_path}` : '尚未生成链接'
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(
      () => ElMessage.success('已复制'),
      () => ElMessage.warning('复制失败：' + text)
    )
    return
  }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败：' + text)
  }
}

function copyLink(row) {
  if (!row.interview_path) {
    ElMessage.warning('该候选人尚未发起面试，请先批量发起 AI 面试')
    return
  }
  copyText(linkOf(row))
}

async function inviteSelected() {
  const ids = selected.value.map((r) => r.id)
  if (!ids.length) return
  const res = await inviteScreenings({ screening_ids: ids })
  invitedItems.value = res.items
  linkVisible.value = true
  tableRef.value?.clearSelection()
  await loadScreenings()
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.candidate_name}」的初筛记录将同时删除其候选人/简历数据，确定吗？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  await deleteScreening(row.id)
  ElMessage.success('已删除')
  await loadScreenings()
}

onMounted(async () => {
  await loadJobs()
  await loadScreenings()
})
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-actions {
  display: flex;
  gap: 10px;
}
.footer-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  margin-top: 14px;
}
.tip {
  color: #909399;
  font-size: 13px;
}
.score {
  font-weight: 600;
}
.s-high {
  color: #16a34a;
}
.s-mid {
  color: #6366f1;
}
.s-low {
  color: #d97706;
}
.s-bad {
  color: #dc2626;
}
.muted {
  color: #909399;
}
.summary {
  color: #4b5563;
  line-height: 1.7;
}
</style>
