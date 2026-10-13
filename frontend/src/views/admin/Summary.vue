<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>招聘数据汇总（初筛 → AI 面试 → 线下复试）</span>
          <div class="header-actions">
            <el-select v-model="jobId" placeholder="全部岗位" clearable style="width: 180px" @change="reload">
              <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
            </el-select>
            <el-radio-group v-model="source" @change="reload">
              <el-radio-button label="live">实时汇总</el-radio-button>
              <el-radio-button label="snapshot">已落库快照</el-radio-button>
            </el-radio-group>
            <el-button @click="exportCsv">导出 CSV</el-button>
            <el-button :loading="notifying" @click="pushToHr">推送复核给 HR</el-button>
            <el-button type="primary" :loading="rebuilding" @click="doRebuild">生成 / 更新汇总落库</el-button>
          </div>
        </div>
      </template>

      <!-- 统计卡片 -->
      <div class="stats">
        <div class="stat"><div class="num">{{ rows.length }}</div><div class="label">候选人数</div></div>
        <div class="stat"><div class="num">{{ completeCount }}</div><div class="label">全链路完成</div></div>
        <div class="stat"><div class="num">{{ avgFinal }}</div><div class="label">平均最终分</div></div>
        <div class="stat"><div class="num">{{ topCount }}</div><div class="label">推荐及以上</div></div>
      </div>

      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column label="#" width="60">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="候选人" min-width="110">
          <template #default="{ row }">{{ row.candidate_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="岗位" min-width="120">
          <template #default="{ row }">{{ row.job_title || jobTitle(row.job_id) }}</template>
        </el-table-column>
        <el-table-column label="初筛分" width="90">
          <template #default="{ row }">{{ num(row.screening_score) }}</template>
        </el-table-column>
        <el-table-column label="AI面试分" width="100">
          <template #default="{ row }">{{ num(row.ai_interview_score) }}</template>
        </el-table-column>
        <el-table-column label="HR/店长打分" width="110">
          <template #default="{ row }">{{ num(row.offline_score) }}</template>
        </el-table-column>
        <el-table-column label="最终加权分" width="110" sortable :sort-by="(r) => r.final_score">
          <template #default="{ row }">
            <span v-if="row.final_score != null" class="score" :class="scoreClass(row.final_score)">
              {{ row.final_score.toFixed(1) }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="阶段" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.stage_label || stageText(row.stage) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.final_verdict" :type="verdictType(row.final_verdict)" size="small">
              {{ row.final_verdict }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="全链路" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_complete ? 'success' : 'info'" size="small">
              {{ row.is_complete ? '已完成' : '进行中' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="汇总说明" min-width="260" show-overflow-tooltip />
        <template #empty>
          <el-empty description="暂无汇总数据；可先上传简历、跑 AI 面试与线下复试后再汇总" />
        </template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listJobs, getSummary, rebuildSummary, listSummarySnapshots, notifySummary } from '../../api'

const jobs = ref([])
const jobId = ref('')
const source = ref('live')
const rows = ref([])
const loading = ref(false)
const rebuilding = ref(false)
const notifying = ref(false)

const completeCount = computed(() => rows.value.filter((r) => r.is_complete).length)
const topCount = computed(
  () => rows.value.filter((r) => ['强烈推荐', '推荐'].includes(r.final_verdict)).length
)
const avgFinal = computed(() => {
  const vals = rows.value.map((r) => r.final_score).filter((v) => v != null)
  if (!vals.length) return '—'
  return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1)
})

function num(v) {
  return v == null ? '—' : Number(v).toFixed(1)
}
function scoreClass(s) {
  if (s >= 80) return 's-high'
  if (s >= 65) return 's-mid'
  if (s >= 50) return 's-low'
  return 's-bad'
}
function verdictType(v) {
  return { 强烈推荐: 'success', 推荐: 'success', 待定: 'warning', 不推荐: 'danger' }[v] || 'info'
}
function stageText(s) {
  return {
    screened: '已初筛',
    imported: '已导入待面试',
    ai_interview: 'AI面试中',
    ai_done: 'AI面试完成',
    offline_scheduled: '已约线下复试',
    offline_done: '线下复试完成',
  }[s] || s
}
function jobTitle(id) {
  return jobs.value.find((j) => j.id === id)?.title || '-'
}

async function reload() {
  loading.value = true
  try {
    const params = jobId.value ? { job_id: jobId.value } : {}
    rows.value =
      source.value === 'live'
        ? await getSummary(params)
        : await listSummarySnapshots(params)
  } finally {
    loading.value = false
  }
}

async function doRebuild() {
  rebuilding.value = true
  try {
    const params = jobId.value ? { job_id: jobId.value } : {}
    const res = await rebuildSummary(params)
    const sent = (res.notify || []).filter((n) => n.status === 'sent')
    const failed = (res.notify || []).filter((n) => n.status === 'failed')
    let msg = `汇总已落库，共更新 ${res.saved} 条`
    if (sent.length) msg += `；已推送 HR 飞书（${sent.length} 个岗位）`
    if (failed.length) msg += `；⚠️ ${failed.length} 个岗位推送失败`
    ElMessage.success(msg)
    source.value = 'snapshot'
    await reload()
  } finally {
    rebuilding.value = false
  }
}

async function pushToHr() {
  notifying.value = true
  try {
    const params = jobId.value ? { job_id: jobId.value } : {}
    const res = await notifySummary(params)
    ElMessage.success(`已推送 HR 飞书：${res.jobs} 个岗位`)
  } finally {
    notifying.value = false
  }
}

function exportCsv() {
  if (!rows.value.length) {
    ElMessage.warning('暂无可导出的数据')
    return
  }
  const headers = ['候选人', '岗位', '初筛分', 'AI面试分', '线下复试分', '最终加权分', '阶段', '结论', '全链路']
  const lines = [headers.join(',')]
  rows.value.forEach((r) => {
    const cells = [
      r.candidate_name,
      r.job_title || jobTitle(r.job_id),
      r.screening_score,
      r.ai_interview_score,
      r.offline_score,
      r.final_score,
      r.stage_label || stageText(r.stage),
      r.final_verdict,
      r.is_complete ? '已完成' : '进行中',
    ].map((v) => `"${v == null ? '' : String(v).replace(/"/g, '""')}"`)
    lines.push(cells.join(','))
  })
  // 加 BOM 保证 Excel 正确识别 UTF-8
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `招聘汇总_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  jobs.value = await listJobs()
  await reload()
})
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}
.header-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}
.stats {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.stat {
  flex: 1;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 14px 18px;
  text-align: center;
}
.stat .num {
  font-size: 26px;
  font-weight: 700;
  color: #6366f1;
}
.stat .label {
  color: #6b7280;
  font-size: 13px;
  margin-top: 4px;
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
</style>
