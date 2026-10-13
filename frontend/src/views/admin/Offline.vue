<template>
  <div>
    <!-- 待 HR / 店长处理：AI 面试已完成、尚未打分的人 -->
    <el-card shadow="never" class="pending-card">
      <template #header>
        <div class="card-header">
          <span>
            待 HR / 店长处理
            <el-tag v-if="pending.length" type="danger" size="small" effect="dark" style="margin-left: 6px">
              {{ pending.length }}
            </el-tag>
          </span>
          <span class="tip">AI 面试已出结果、尚未录入 HR/店长打分的候选人</span>
        </div>
      </template>

      <el-table :data="pending" v-loading="pendingLoading" stripe size="small">
        <el-table-column label="候选人" min-width="110">
          <template #default="{ row }">{{ row.candidate_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="岗位" min-width="130">
          <template #default="{ row }">{{ row.job_title || jobTitle(row.job_id) }}</template>
        </el-table-column>
        <el-table-column label="AI 面试分" width="110">
          <template #default="{ row }">
            <span class="score" :class="scoreClass(row.ai_interview_score)">
              {{ row.ai_interview_score.toFixed(1) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="AI 面试结论" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.ai_interview_verdict" :type="verdictType(row.ai_interview_verdict)" size="small">
              {{ row.ai_interview_verdict }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="openScheduleFor(row)">约线下面</el-button>
            <el-button size="small" @click="quickScore(row)">直接打分</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无待处理候选人（AI 面试完成、并录入 HR/店长打分后会移出此列表）" :image-size="60" />
        </template>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>线下复试（HR / 店长约面与打分）</span>
          <div class="header-actions">
            <el-select v-model="jobId" placeholder="全部岗位" clearable style="width: 180px" @change="load">
              <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
            </el-select>
            <el-select v-model="status" placeholder="全部状态" clearable style="width: 150px" @change="load">
              <el-option label="已约面" value="scheduled" />
              <el-option label="已完成" value="done" />
              <el-option label="爽约" value="no_show" />
              <el-option label="已取消" value="cancelled" />
            </el-select>
            <el-button type="primary" @click="openCreate">新增约面</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column label="候选人" min-width="110">
          <template #default="{ row }">{{ row.candidate_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="岗位" min-width="130">
          <template #default="{ row }">{{ row.job_title || '-' }}</template>
        </el-table-column>
        <el-table-column label="约面时间" min-width="160">
          <template #default="{ row }">{{ fmt(row.scheduled_at) }}</template>
        </el-table-column>
        <el-table-column prop="location" label="地点" min-width="120" show-overflow-tooltip />
        <el-table-column label="面试官" min-width="130">
          <template #default="{ row }">
            {{ row.interviewer || '-' }}
            <el-tag v-if="row.interviewer_role" size="small" effect="plain" style="margin-left: 4px">
              {{ roleText(row.interviewer_role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="HR/店长打分" width="110">
          <template #default="{ row }">
            <span v-if="row.score != null" class="score" :class="scoreClass(row.score)">
              {{ row.score.toFixed(1) }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.decision" :type="decisionType(row.decision)" size="small">
              {{ decisionText(row.decision) }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openScore(row)">
              {{ row.score != null ? '修改打分' : '打分' }}
            </el-button>
            <el-button size="small" text @click="openEdit(row)">改期</el-button>
            <el-button size="small" text type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无线下复试安排" />
        </template>
      </el-table>
    </el-card>

    <!-- 新增约面 -->
    <el-dialog v-model="createVisible" title="新增线下复试安排" width="600px">
      <el-form label-width="100px">
        <el-form-item label="岗位" required>
          <el-select v-model="form.jobId" placeholder="选择岗位" style="width: 100%" filterable @change="loadCandidates">
            <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="候选人" required>
          <el-select v-model="form.screeningId" placeholder="选择候选人（来自简历初筛）" style="width: 100%" filterable :loading="candLoading">
            <el-option
              v-for="c in candidates"
              :key="c.id"
              :label="candidateLabel(c)"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="约面时间">
          <el-date-picker v-model="form.scheduledAt" type="datetime" placeholder="选择时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="form.location" placeholder="如：XX 门店 / 总部会议室" />
        </el-form-item>
        <el-form-item label="面试官">
          <el-input v-model="form.interviewer" placeholder="面试官姓名" />
        </el-form-item>
        <el-form-item label="面试官角色">
          <el-radio-group v-model="form.interviewerRole">
            <el-radio label="hr">HR</el-radio>
            <el-radio label="store_manager">店长</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 改期 / 改状态 -->
    <el-dialog v-model="editVisible" title="修改线下复试安排" width="560px">
      <el-form label-width="100px">
        <el-form-item label="约面时间">
          <el-date-picker v-model="editForm.scheduledAt" type="datetime" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="editForm.location" />
        </el-form-item>
        <el-form-item label="面试官">
          <el-input v-model="editForm.interviewer" />
        </el-form-item>
        <el-form-item label="面试官角色">
          <el-radio-group v-model="editForm.interviewerRole">
            <el-radio label="hr">HR</el-radio>
            <el-radio label="store_manager">店长</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option label="已约面" value="scheduled" />
            <el-option label="已完成" value="done" />
            <el-option label="爽约" value="no_show" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 打分 -->
    <el-dialog v-model="scoreVisible" title="线下复试打分" width="620px">
      <el-form label-width="100px">
        <el-form-item label="候选人">
          <span>{{ scoreTarget?.candidate_name }}</span>
        </el-form-item>
        <el-form-item label="总分" required>
          <el-input-number v-model="scoreForm.score" :min="0" :max="100" :precision="1" />
          <span class="muted" style="margin-left: 8px">百分制</span>
        </el-form-item>
        <el-form-item label="维度得分">
          <div class="dim-wrap">
            <div v-for="(d, i) in scoreForm.dimScores" :key="i" class="dim-row">
              <el-input v-model="d.name" placeholder="维度名" style="width: 150px" />
              <el-input-number v-model="d.score" :min="0" :max="d.max_score || 10" :precision="1" />
              <span class="muted">/ {{ d.max_score || 10 }}</span>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="面试评语">
          <el-input v-model="scoreForm.comment" type="textarea" :rows="3" placeholder="填写线下复试评价" />
        </el-form-item>
        <el-form-item label="录用结论">
          <el-radio-group v-model="scoreForm.decision">
            <el-radio label="pass">通过</el-radio>
            <el-radio label="hold">待定</el-radio>
            <el-radio label="reject">淘汰</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="scoreVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitScore">提交打分</el-button>
      </template>
    </el-dialog>

    <!-- 待办：从 AI 面试结果直接约线下面试 -->
    <el-dialog v-model="scheduleVisible" title="约线下复试" width="560px">
      <el-form label-width="100px">
        <el-form-item label="候选人">
          <span>{{ pendingTarget?.candidate_name }}（AI 面试 {{ pendingTarget?.ai_interview_score }} 分）</span>
        </el-form-item>
        <el-form-item label="岗位">
          <span>{{ pendingTarget?.job_title || jobTitle(pendingTarget?.job_id) }}</span>
        </el-form-item>
        <el-form-item label="约面时间" required>
          <el-date-picker v-model="pendingForm.scheduledAt" type="datetime" placeholder="选择时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="pendingForm.location" placeholder="如：XX 门店 / 总部会议室" />
        </el-form-item>
        <el-form-item label="面试官">
          <el-input v-model="pendingForm.interviewer" placeholder="面试官姓名" />
        </el-form-item>
        <el-form-item label="面试官角色">
          <el-radio-group v-model="pendingForm.interviewerRole">
            <el-radio label="hr">HR</el-radio>
            <el-radio label="store_manager">店长</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="scheduleVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitScheduleFor">创建约面</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listJobs,
  listScreenings,
  listOfflineInterviews,
  createOfflineInterview,
  updateOfflineInterview,
  scoreOfflineInterview,
  deleteOfflineInterview,
  getSummary,
} from '../../api'

const jobs = ref([])
const jobId = ref('')
const status = ref('')
const rows = ref([])
const loading = ref(false)
const saving = ref(false)

// 待 HR / 店长处理（AI 面试已出结果、尚未打分）
const pending = ref([])
const pendingLoading = ref(false)
const scheduleVisible = ref(false)
const pendingTarget = ref(null)
const pendingForm = ref({ scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' })

const createVisible = ref(false)
const editVisible = ref(false)
const scoreVisible = ref(false)
const candidates = ref([])
const candLoading = ref(false)

const form = ref({ jobId: '', screeningId: '', scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' })
const editForm = ref({ id: '', scheduledAt: null, location: '', interviewer: '', interviewerRole: '', status: 'scheduled' })
const scoreTarget = ref(null)
const scoreForm = ref({ score: 70, dimScores: [], comment: '', decision: 'pass' })

const DEFAULT_DIMS = [
  { key: 'service', name: '服务意识', score: 7, max_score: 10 },
  { key: 'stability', name: '稳定性', score: 7, max_score: 10 },
  { key: 'skill', name: '岗位技能', score: 7, max_score: 10 },
]

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('zh-CN') : '-'
}
function jobTitle(id) {
  return jobs.value.find((j) => j.id === id)?.title || '-'
}
function verdictType(v) {
  return { 强烈推荐: 'success', 推荐: 'success', 待定: 'warning', 不推荐: 'danger' }[v] || 'info'
}
function candidateLabel(c) {
  // 免初筛岗位无初筛分，标注出来避免误显示 "null 分"
  return c.total_score != null
    ? `${c.candidate_name}（初筛 ${c.total_score.toFixed(1)} 分）`
    : `${c.candidate_name}（免初筛）`
}
function roleText(r) {
  return { hr: 'HR', store_manager: '店长' }[r] || r
}
function statusText(s) {
  return { scheduled: '已约面', done: '已完成', no_show: '爽约', cancelled: '已取消' }[s] || s
}
function statusType(s) {
  return { scheduled: 'warning', done: 'success', no_show: 'danger', cancelled: 'info' }[s] || 'info'
}
function decisionText(d) {
  return { pass: '通过', hold: '待定', reject: '淘汰' }[d] || d
}
function decisionType(d) {
  return { pass: 'success', hold: 'warning', reject: 'danger' }[d] || 'info'
}
function scoreClass(s) {
  if (s >= 80) return 's-high'
  if (s >= 65) return 's-mid'
  return 's-low'
}

async function load() {
  loading.value = true
  try {
    // 状态筛选只作用于下方列表；上方待办列表只看「AI 面试完成且未打分」
    const listParams = {}
    if (jobId.value) listParams.job_id = jobId.value
    if (status.value) listParams.status = status.value
    rows.value = await listOfflineInterviews(listParams)
  } finally {
    loading.value = false
  }
  await loadPending()
}

async function loadPending() {
  pendingLoading.value = true
  try {
    const params = jobId.value ? { job_id: jobId.value } : {}
    const summary = await getSummary(params)
    // AI 面试已出结果、且尚未录入 HR/店长打分
    pending.value = summary.filter((r) => r.ai_interview_score != null && r.offline_id == null)
  } finally {
    pendingLoading.value = false
  }
}

/** 从待办直接约线下面试（无需先手工选候选人） */
function openScheduleFor(row) {
  pendingTarget.value = row
  pendingForm.value = { scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' }
  scheduleVisible.value = true
}

async function submitScheduleFor() {
  saving.value = true
  try {
    await createOfflineInterview({
      session_id: pendingTarget.value.session_id,
      job_id: pendingTarget.value.job_id,
      scheduled_at: pendingForm.value.scheduledAt ? pendingForm.value.scheduledAt.toISOString() : null,
      location: pendingForm.value.location,
      interviewer: pendingForm.value.interviewer,
      interviewer_role: pendingForm.value.interviewerRole,
    })
    ElMessage.success('已创建约面')
    scheduleVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

/** 直接打分：不必先约面，先建一条记录再打开打分弹窗 */
async function quickScore(row) {
  const created = await createOfflineInterview({
    session_id: row.session_id,
    job_id: row.job_id,
  })
  openScore(created)
}

function openCreate() {
  form.value = { jobId: jobId.value || '', screeningId: '', scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' }
  candidates.value = []
  if (form.value.jobId) loadCandidates()
  createVisible.value = true
}

async function loadCandidates() {
  if (!form.value.jobId) {
    candidates.value = []
    return
  }
  candLoading.value = true
  try {
    candidates.value = await listScreenings({ job_id: form.value.jobId })
  } finally {
    candLoading.value = false
  }
}

async function submitCreate() {
  if (!form.value.screeningId) {
    ElMessage.warning('请选择候选人')
    return
  }
  saving.value = true
  try {
    await createOfflineInterview({
      screening_id: form.value.screeningId,
      scheduled_at: form.value.scheduledAt ? form.value.scheduledAt.toISOString() : null,
      location: form.value.location,
      interviewer: form.value.interviewer,
      interviewer_role: form.value.interviewerRole,
    })
    ElMessage.success('已创建约面')
    createVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openEdit(row) {
  editForm.value = {
    id: row.id,
    scheduledAt: row.scheduled_at ? new Date(row.scheduled_at) : null,
    location: row.location || '',
    interviewer: row.interviewer || '',
    interviewerRole: row.interviewer_role || 'hr',
    status: row.status,
  }
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    await updateOfflineInterview(editForm.value.id, {
      scheduled_at: editForm.value.scheduledAt ? editForm.value.scheduledAt.toISOString() : null,
      location: editForm.value.location,
      interviewer: editForm.value.interviewer,
      interviewer_role: editForm.value.interviewerRole,
      status: editForm.value.status,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openScore(row) {
  scoreTarget.value = row
  scoreForm.value = {
    score: row.score != null ? row.score : 70,
    dimScores: row.dim_scores?.length
      ? JSON.parse(JSON.stringify(row.dim_scores))
      : JSON.parse(JSON.stringify(DEFAULT_DIMS)),
    comment: row.comment || '',
    decision: row.decision || 'pass',
  }
  scoreVisible.value = true
}

async function submitScore() {
  saving.value = true
  try {
    await scoreOfflineInterview(scoreTarget.value.id, {
      score: scoreForm.value.score,
      dim_scores: scoreForm.value.dimScores.map((d) => ({
        key: d.key || d.name,
        name: d.name,
        score: d.score,
        max_score: d.max_score || 10,
      })),
      comment: scoreForm.value.comment,
      decision: scoreForm.value.decision,
    })
    ElMessage.success('打分已提交')
    scoreVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`删除「${row.candidate_name}」的线下复试记录？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await deleteOfflineInterview(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(async () => {
  jobs.value = await listJobs()
  await load()
})
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.pending-card {
  margin-bottom: 16px;
}
.tip {
  color: #909399;
  font-size: 13px;
}
.header-actions {
  display: flex;
  gap: 10px;
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
.muted {
  color: #909399;
}
.dim-wrap {
  width: 100%;
}
.dim-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
</style>
