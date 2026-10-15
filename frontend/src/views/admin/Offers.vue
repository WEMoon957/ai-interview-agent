<template>
  <div>
    <!-- 使用指引：让不熟悉流程的 HR 一眼看懂该干什么 -->
    <el-alert type="info" :closable="false" show-icon class="guide">
      <template #title>
        <div class="guide-text">
          <b>操作流程</b>：在「数据汇总」页选好达标的候选人 → 点「发起 Offer」填写门店与报到日期 →
          点「提交审批」（审批人会在飞书上收到卡片）→ 审批通过后<b>复制链接发给候选人</b> →
          候选人接受后，回到本页点「办理入职」标记报到情况。
        </div>
      </template>
    </el-alert>

    <!-- 统计卡片 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>Offer / 入职 概览</span>
          <div class="header-actions">
            <el-select v-model="statsMonth" placeholder="全部时间" clearable style="width: 150px" @change="loadStats">
              <el-option v-for="m in monthOptions" :key="m.value" :label="m.label" :value="m.value" />
            </el-select>
            <el-button type="primary" @click="openCreate()">发起 Offer</el-button>
            <el-button @click="reload">刷新</el-button>
          </div>
        </div>
      </template>

      <div class="stats">
        <div class="stat">
          <div class="num">{{ stats.pending_approval || 0 }}</div>
          <div class="label">待审批</div>
        </div>
        <div class="stat">
          <div class="num">{{ stats.sent || 0 }}</div>
          <div class="label">待候选人确认</div>
        </div>
        <div class="stat">
          <div class="num">{{ rateText(stats.accept_rate) }}</div>
          <div class="label">Offer 接受率</div>
        </div>
        <div class="stat">
          <div class="num">{{ stats.onboarded || 0 }}</div>
          <div class="label">已入职</div>
        </div>
        <div class="stat">
          <div class="num warn">{{ stats.no_show || 0 }}</div>
          <div class="label">未报到</div>
        </div>
      </div>

      <div class="funnel">
        <span class="funnel-title">转化漏斗</span>
        <span class="funnel-item">汇总完成 <b>{{ stats.funnel?.summaries_complete ?? 0 }}</b></span>
        <span class="arrow">→</span>
        <span class="funnel-item">已发 Offer <b>{{ stats.funnel?.offers_sent ?? 0 }}</b></span>
        <span class="arrow">→</span>
        <span class="funnel-item">已接受 <b>{{ stats.funnel?.offers_accepted ?? 0 }}</b></span>
        <span class="arrow">→</span>
        <span class="funnel-item">已入职 <b>{{ stats.funnel?.onboarded ?? 0 }}</b></span>
      </div>
    </el-card>

    <!-- 列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>Offer 列表</span>
          <div class="header-actions">
            <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 140px" @change="reload">
              <el-option v-for="(label, key) in STATUS_LABELS" :key="key" :label="label" :value="key" />
            </el-select>
            <el-select v-model="filters.source" placeholder="全部来源" clearable style="width: 130px" @change="reload">
              <el-option label="线上投递" value="online" />
              <el-option label="到店直招" value="walk_in" />
            </el-select>
            <el-input v-model="filters.keyword" placeholder="候选人姓名" clearable style="width: 140px" @keyup.enter="reload" />
            <el-input v-model="filters.store" placeholder="报到门店" clearable style="width: 140px" @keyup.enter="reload" />
            <el-button @click="reload">查询</el-button>
          </div>
        </div>
      </template>

      <el-table :data="pagedRows" v-loading="loading" stripe>
        <el-table-column label="候选人" min-width="100">
          <template #default="{ row }">{{ row.candidate_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="岗位" min-width="120">
          <template #default="{ row }">{{ row.job_title || '-' }}</template>
        </el-table-column>
        <el-table-column label="报到门店" min-width="130">
          <template #default="{ row }">{{ row.store_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="来源" width="95">
          <template #default="{ row }">
            <el-tag v-if="row.source === 'walk_in'" type="warning" size="small">到店直招</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">线上</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="105">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small" :class="`st-${row.status}`">
              {{ row.status_label || STATUS_LABELS[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="拟报到日期" width="110">
          <template #default="{ row }">{{ row.report_date || '-' }}</template>
        </el-table-column>
        <el-table-column label="确认截止" width="160">
          <template #default="{ row }">
            <span v-if="row.expires_at">{{ localTime(row.expires_at) }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <!-- 草稿 / 已驳回：可编辑、提交审批 -->
            <template v-if="['draft', 'rejected'].includes(row.status)">
              <el-button type="success" size="small" @click="doSubmit(row)">提交审批</el-button>
              <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="doWithdraw(row)">撤回</el-button>
            </template>
            <!-- 待审批：后台可兜底审批 -->
            <template v-else-if="row.status === 'pending_approval'">
              <el-button type="primary" size="small" @click="openApprove(row)">去审批</el-button>
              <el-button link type="danger" size="small" @click="doWithdraw(row)">撤回</el-button>
            </template>
            <!-- 已发出：复制链接（主要动作）/ 撤回 -->
            <template v-else-if="row.status === 'sent'">
              <el-button type="primary" size="small" @click="copyLink(row)">复制链接</el-button>
              <el-button link type="danger" size="small" @click="doWithdraw(row)">撤回</el-button>
            </template>
            <!-- 已接受：办理入职（主要动作）-->
            <template v-else-if="row.status === 'accepted'">
              <el-button type="success" size="small" @click="openOnboard(row)">办理入职</el-button>
            </template>
            <el-button link size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无 Offer。请先到「数据汇总」页，对全链路完成的候选人点「发起 Offer」" />
        </template>
      </el-table>

      <el-pagination
        v-if="rows.length > pageSize"
        v-model:current-page="page"
        :page-size="pageSize"
        :total="rows.length"
        layout="total, prev, pager, next"
        class="pager"
      />
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="showDetail" title="Offer 详情" size="460px">
      <div v-if="detail" class="detail">
        <el-alert
          v-if="nextStepHint(detail)"
          :type="['sent', 'accepted', 'pending_approval'].includes(detail.status) ? 'warning' : 'info'"
          :closable="false"
          show-icon
          class="next-step"
          :title="nextStepHint(detail)"
        />
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="候选人">{{ detail.candidate_name }}</el-descriptions-item>
          <el-descriptions-item label="岗位">{{ detail.job_title }}</el-descriptions-item>
          <el-descriptions-item label="报到门店">{{ detail.store_name }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ detail.source === 'walk_in' ? '到店直招' : '线上投递' }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(detail.status)" size="small">{{ detail.status_label }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="拟报到日期">{{ detail.report_date || '—' }}</el-descriptions-item>
          <el-descriptions-item label="试用期">{{ detail.probation_days }} 天</el-descriptions-item>
          <el-descriptions-item v-if="detail.note" label="备注">{{ detail.note }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.expires_at" label="确认截止">{{ localTime(detail.expires_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.approval_note" label="审批意见">{{ detail.approval_note }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.decline_reason_text" label="婉拒理由">{{ detail.decline_reason_text }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.no_show_note" label="未报到说明">{{ detail.no_show_note }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.actual_report_date" label="实际报到日期">{{ detail.actual_report_date }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="timeline-title">状态时间线</h4>
        <el-timeline>
          <el-timeline-item v-for="(item, i) in timeline(detail)" :key="i" :timestamp="item.time">
            {{ item.text }}
          </el-timeline-item>
          <el-timeline-item v-if="!timeline(detail).length" timestamp="—">暂无流转记录</el-timeline-item>
        </el-timeline>
      </div>
    </el-drawer>

    <!-- 发起 / 编辑弹窗 -->
    <OfferCreateDialog v-model="showCreate" :context="createContext" @saved="onSaved" />
    <!-- 审批弹窗（后台兜底入口）-->
    <OfferApproveDialog v-model="showApprove" :offer="current" @done="reload" />
    <!-- 办理入职弹窗 -->
    <OfferOnboardDialog v-model="showOnboard" :offer="current" @done="reload" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listOffers,
  getOffer,
  getOfferStats,
  submitOfferApproval,
  withdrawOffer,
  listJobs,
} from '../../api'
import OfferCreateDialog from '../../components/OfferCreateDialog.vue'
import OfferApproveDialog from '../../components/OfferApproveDialog.vue'
import OfferOnboardDialog from '../../components/OfferOnboardDialog.vue'

const route = useRoute()

// 状态中文名与后端 services/offer.py 的 STATUS_LABELS 保持一致（值以接口为准）
const STATUS_LABELS = {
  draft: '草稿',
  pending_approval: '待审批',
  rejected: '已驳回',
  sent: '已发出',
  accepted: '已接受',
  declined: '已婉拒',
  expired: '已过期',
  withdrawn: '已撤回',
  onboarded: '已入职',
  no_show: '未报到',
}

const rows = ref([])
const jobs = ref([])
const stats = ref({})
const statsMonth = ref('')
const loading = ref(false)
const filters = ref({ status: '', source: '', keyword: '', store: '' })

// 前端分页（接口一次最多返回 200 条，页面按页展示更清爽）
const page = ref(1)
const pageSize = ref(20)
const pagedRows = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return rows.value.slice(start, start + pageSize.value)
})

const showDetail = ref(false)
const showCreate = ref(false)
const showApprove = ref(false)
const showOnboard = ref(false)
const detail = ref(null)
const current = ref(null)
const createContext = ref(null)

const monthOptions = computed(() => {
  const out = []
  const now = new Date()
  for (let i = 0; i < 6; i += 1) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    out.push({ value, label: `${d.getFullYear()}年${d.getMonth() + 1}月` })
  }
  return out
})

function statusTagType(status) {
  return {
    draft: 'info',
    pending_approval: 'warning',
    rejected: 'danger',
    sent: 'primary',
    accepted: 'success',
    declined: 'info',
    expired: 'warning',
    withdrawn: 'info',
    onboarded: 'success',
    no_show: 'danger',
  }[status] || 'info'
}

// 按状态告诉使用者「下一步该做什么」，降低上手门槛
function nextStepHint(row) {
  return {
    draft: '下一步：点「提交审批」，审批人会在飞书上收到审批卡片。',
    pending_approval: '等待审批人在飞书卡片上处理；也可点「去审批」在本页直接处理。',
    rejected: '审批被驳回，可修改后重新提交。',
    sent: '已发出，等待候选人确认。可点「复制链接」重新发给候选人。',
    accepted: '候选人已接受。请在报到日之后点「办理入职」标记「已报到 / 未报到」。',
    declined: '候选人婉拒，建议启动下一顺位候选人。',
    expired: '超过确认时间未回复，可重新发起 Offer。',
    onboarded: '流程已完成 ✅',
    no_show: '候选人未报到。可重新发起 Offer，或先联系确认情况。',
    withdrawn: '已撤回。如需继续，可重新发起 Offer。',
  }[row.status] || ''
}

function rateText(v) {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

function localTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function timeline(row) {
  const items = [
    [row.approval_submitted_at, '提交审批'],
    [row.rejected_at, `审批驳回${row.approval_note ? `：${row.approval_note}` : ''}`],
    [row.approved_at, '审批通过'],
    [row.sent_at, '已发出，等待候选人确认'],
    [row.decided_at, row.status === 'declined' ? '候选人婉拒' : '候选人接受'],
    [row.expired_at, '超过确认时间自动过期'],
    [row.withdrawn_at, `HR 撤回${row.withdraw_reason ? `：${row.withdraw_reason}` : ''}`],
    [row.onboarded_at, row.status === 'no_show' ? '标记未报到' : '已报到'],
  ]
  return items
    .filter(([t]) => !!t)
    .map(([t, text]) => ({ time: localTime(t), text }))
}

async function reload() {
  loading.value = true
  page.value = 1
  try {
    const params = {}
    if (filters.value.status) params.status = filters.value.status
    if (filters.value.source) params.source = filters.value.source
    if (filters.value.keyword) params.keyword = filters.value.keyword
    if (filters.value.store) params.store = filters.value.store
    rows.value = await listOffers(params)
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  stats.value = await getOfferStats(statsMonth.value ? { month: statsMonth.value } : {})
}

async function openCreate() {
  createContext.value = null
  showCreate.value = true
}

async function openEdit(row) {
  createContext.value = { ...row, edit: true }
  showCreate.value = true
}

async function openDetail(row) {
  detail.value = await getOffer(row.id)
  showDetail.value = true
}

function openApprove(row) {
  current.value = row
  showApprove.value = true
}

function openOnboard(row) {
  current.value = row
  showOnboard.value = true
}

async function doSubmit(row) {
  try {
    const res = await submitOfferApproval(row.id)
    const notify = res?.notify || {}
    if (notify.status === 'failed') {
      ElMessage.warning(`已提交审批，但审批卡片推送失败：${notify.reason || '未知原因'}`)
    } else if (notify.status === 'skipped') {
      ElMessage.warning(`已提交审批，但未推送卡片：${notify.reason || '未配置审批人'}`)
    } else {
      ElMessage.success('已提交审批')
    }
    await reload()
    await loadStats()
  } catch (e) {
    /* 拦截器已提示 */
  }
}

async function doWithdraw(row) {
  try {
    const { value } = await ElMessageBox.prompt('请填写撤回原因（可选）', '撤回 Offer', {
      confirmButtonText: '确认撤回',
      cancelButtonText: '取消',
      inputPlaceholder: '如：岗位已招满',
      inputValue: '',
    })
    await withdrawOffer(row.id, { reason: value || '' })
    ElMessage.success('已撤回')
    await reload()
    await loadStats()
  } catch (e) {
    /* 取消或失败 */
  }
}

async function copyLink(row) {
  const link = row.offer_link
  if (!link) {
    ElMessage.warning('该 Offer 暂无链接（可能已撤回或尚未审批通过）')
    return
  }
  try {
    await navigator.clipboard.writeText(link)
    ElMessage.success('确认链接已复制，可粘贴发给候选人')
  } catch (e) {
    ElMessageBox.alert(link, '请手动复制链接', { confirmButtonText: '知道了' })
  }
}

async function onSaved() {
  await reload()
  await loadStats()
}

onMounted(async () => {
  jobs.value = await listJobs()
  // 支持从「数据汇总」页带 summary_id 直接唤起发起弹窗
  const summaryId = route.query.summary_id
  if (summaryId) {
    createContext.value = {
      summary_id: String(summaryId),
      candidate_name: route.query.candidate_name || '',
      job_title: route.query.job_title || '',
      store_name: route.query.store_name || '',
    }
    showCreate.value = true
  }
  await reload()
  await loadStats()
})
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.guide {
  margin-bottom: 16px;
}
.guide-text {
  line-height: 1.75;
  font-size: 13px;
}
.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
.next-step {
  margin-bottom: 14px;
}
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
  flex-wrap: wrap;
}
.stats {
  display: flex;
  gap: 14px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.stat {
  flex: 1;
  min-width: 110px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 14px 16px;
  text-align: center;
}
.stat .num {
  font-size: 26px;
  font-weight: 700;
  color: #6366f1;
}
.stat .num.warn {
  color: #dc2626;
}
.stat .label {
  color: #6b7280;
  font-size: 13px;
  margin-top: 4px;
}
.funnel {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  background: #f9fafb;
  border: 1px solid #f3f4f6;
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 13px;
  color: #4b5563;
}
.funnel-title {
  font-weight: 600;
  color: #374151;
  margin-right: 6px;
}
.funnel-item b {
  color: #6366f1;
  font-size: 15px;
  margin-left: 3px;
}
.arrow {
  color: #9ca3af;
}
.muted {
  color: #909399;
}
.detail .timeline-title {
  margin: 20px 0 12px;
  color: #374151;
}
/* 终态与特殊状态配色微调（对齐 PRD 配色建议） */
.st-onboarded {
  background: #dcfce7;
  border-color: #86efac;
  color: #15803d;
}
.st-no_show {
  background: #fee2e2;
  border-color: #fca5a5;
  color: #b91c1c;
}
.st-expired {
  background: #ffedd5;
  border-color: #fdba74;
  color: #c2410c;
}
.st-declined,
.st-withdrawn {
  background: #f3f4f6;
  border-color: #e5e7eb;
  color: #6b7280;
}
</style>
