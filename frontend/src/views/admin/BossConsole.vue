<template>
  <div>
    <el-alert type="info" :closable="false" show-icon class="guide">
      <template #title>
        <b>老板操作台</b>：只做最后两件事 —— 给走完复试的候选人拍板<b>终试</b>，以及审批 HR 提交的<b>录用 Offer</b>。
        拍板前先看<b>面试档案</b>：此前各环节的评分维度、优点、风险、待确认问题都在里面。
      </template>
    </el-alert>

    <!-- 概览 -->
    <el-card shadow="never" class="block">
      <div class="stats">
        <div class="stat">
          <div class="num warn">{{ pendingFinal.length }}</div>
          <div class="label">待终试</div>
        </div>
        <div class="stat">
          <div class="num warn">{{ pendingOffers.length }}</div>
          <div class="label">待录用审批</div>
        </div>
        <div class="stat">
          <div class="num">{{ stats.onboarded || 0 }}</div>
          <div class="label">本月已入职</div>
        </div>
      </div>
    </el-card>

    <!-- 待终试 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="card-header">
          <span>待终试（走完复试，等老板拍板）</span>
          <el-button @click="loadPendingFinal">刷新</el-button>
        </div>
      </template>
      <el-table :data="pendingFinal" v-loading="loadingFinal" stripe>
        <el-table-column label="候选人" min-width="110" prop="candidate_name" />
        <el-table-column label="档案" width="92">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openBrief(row)">
              面试档案
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="岗位" min-width="130" prop="job_title" />
        <el-table-column label="来源" width="95">
          <template #default="{ row }">
            <el-tag v-if="row.source === 'walk_in'" type="warning" size="small">到店直招</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">线上</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="初筛" width="70">
          <template #default="{ row }">{{ score(row.screening_score) }}</template>
        </el-table-column>
        <el-table-column label="AI 面试" width="80">
          <template #default="{ row }">{{ score(row.ai_interview_score) }}</template>
        </el-table-column>
        <el-table-column label="复试" width="70">
          <template #default="{ row }">{{ score(row.offline_score) }}</template>
        </el-table-column>
        <el-table-column label="终分" width="80">
          <template #default="{ row }">
            <b>{{ score(row.final_score) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button type="success" size="small" @click="openFinal(row, 'pass')">通过终试</el-button>
            <el-button type="danger" size="small" @click="openFinal(row, 'fail')">不通过</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无待终试的候选人" />
        </template>
      </el-table>
    </el-card>

    <!-- 待录用审批 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>待录用审批（HR 已提交，等老板批准）</span>
          <el-button @click="loadPendingOffers">刷新</el-button>
        </div>
      </template>
      <el-table :data="pendingOffers" v-loading="loadingOffers" stripe>
        <el-table-column label="候选人" min-width="110" prop="candidate_name" />
        <el-table-column label="岗位" min-width="130" prop="job_title" />
        <el-table-column label="报到门店" min-width="130" prop="store_name" />
        <el-table-column label="来源" width="95">
          <template #default="{ row }">
            <el-tag v-if="row.source === 'walk_in'" type="warning" size="small">到店直招</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">线上</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="105">
          <template #default="{ row }">
            <el-tag type="warning" size="small">待审批</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" @click="openApprove(row)">去审批</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无待审批的 Offer" />
        </template>
      </el-table>
    </el-card>

    <!-- 录用审批弹窗（复用后台审批弹窗：同意 / 驳回）-->
    <OfferApproveDialog v-model="showApprove" :offer="currentOffer" @done="loadPendingOffers" />

    <!-- 终试决策弹窗 -->
    <el-dialog
      v-model="showFinal"
      :title="finalDecision === 'pass' ? '通过终试' : '终试不通过'"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-descriptions v-if="currentFinal" :column="1" border size="small" class="mb">
        <el-descriptions-item label="候选人">{{ currentFinal.candidate_name }}</el-descriptions-item>
        <el-descriptions-item label="岗位">{{ currentFinal.job_title }}</el-descriptions-item>
        <el-descriptions-item label="终分">{{ score(currentFinal.final_score) }}</el-descriptions-item>
      </el-descriptions>

      <!-- 拍板前速览：优点 / 风险 / 终试追问（勾选表示已当面确认） -->
      <div v-if="finalBrief" class="final-brief">
        <div class="fb-head">
          <span>面试档案速览</span>
          <div>
            <el-tag size="small" type="success" effect="plain">优点 {{ finalBrief.strengths.length }}</el-tag>
            <el-tag size="small" type="warning" effect="plain" class="ml4">风险 {{ finalBrief.risks.length }}</el-tag>
            <el-tag size="small" type="primary" effect="plain" class="ml4">待确认 {{ finalBrief.pending_questions.length }}</el-tag>
          </div>
          <el-button link type="primary" size="small" @click="openBrief(currentFinal)">完整档案</el-button>
        </div>

        <div v-if="finalBrief.pending_questions.length" class="fb-block">
          <div class="fb-label">终试待确认（勾选 = 已当面问过）</div>
          <el-checkbox
            v-for="(q, i) in finalBrief.pending_questions.slice(0, 5)"
            :key="i"
            v-model="finalAsked[i]"
            class="fb-check"
          >
            {{ q.question }}
          </el-checkbox>
        </div>
        <div v-if="finalBrief.risks.length" class="fb-block">
          <div class="fb-label">风险</div>
          <div v-for="(r, i) in finalBrief.risks.slice(0, 3)" :key="i" class="fb-line warn">
            · {{ r.text }}
          </div>
        </div>
        <div v-if="finalBrief.strengths.length" class="fb-block">
          <div class="fb-label">优点</div>
          <div v-for="(s, i) in finalBrief.strengths.slice(0, 3)" :key="i" class="fb-line ok">
            · {{ s.text }}
          </div>
        </div>
      </div>
      <div v-else-if="loadingBrief" class="muted mb">面试档案加载中…</div>

      <el-form label-width="80px">
        <el-form-item :label="finalDecision === 'pass' ? '通过备注' : '不通过原因'">
          <el-input
            v-model="finalNote"
            type="textarea"
            :rows="2"
            maxlength="200"
            show-word-limit
            :placeholder="finalDecision === 'pass' ? '选填' : '请填写不通过的原因'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showFinal = false">取消</el-button>
        <el-button
          :type="finalDecision === 'pass' ? 'success' : 'danger'"
          :loading="finalActing"
          @click="submitFinal"
        >
          {{ finalDecision === 'pass' ? '确认通过' : '确认不通过' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 面试档案抽屉：评分维度对比 + 优点 / 风险 / 待确认问题 + 各环节明细 -->
    <el-drawer
      v-model="showBrief"
      size="880px"
      :title="brief ? `${brief.candidate_name} · 面试档案` : '面试档案'"
      destroy-on-close
    >
      <div v-loading="loadingBrief" class="brief-body">
        <template v-if="brief">
          <!-- 概要 -->
          <div class="brief-summary">
            <div class="bs-item">
              <span class="bs-label">最终加权分</span>
              <span class="bs-value" :class="scoreClass(brief.final_score)">
                {{ score(brief.final_score) }}
              </span>
            </div>
            <div class="bs-item">
              <span class="bs-label">综合结论</span>
              <el-tag :type="verdictType(brief.final_verdict)" size="small">
                {{ brief.final_verdict || '—' }}
              </el-tag>
            </div>
            <div class="bs-item">
              <span class="bs-label">来源</span>
              <el-tag v-if="brief.source === 'walk_in'" type="warning" size="small">到店直招</el-tag>
              <el-tag v-else type="info" size="small" effect="plain">线上投递</el-tag>
            </div>
            <div class="bs-item">
              <span class="bs-label">终试状态</span>
              <el-tag type="warning" size="small">{{ brief.final_interview_label }}</el-tag>
            </div>
            <div class="bs-item grow">
              <span class="bs-label">岗位</span>
              <span class="bs-text">{{ brief.job_title || '—' }}</span>
            </div>
          </div>

          <!-- 无历史记录时的整体提示（手工建档 / 历史数据的快照已无底层记录） -->
          <el-alert
            v-if="!brief.stages.length"
            type="warning"
            :closable="false"
            show-icon
            class="mb"
            title="此前没有可用的面试记录"
            description="该候选人可能是手工建档或历史数据（简历 / 面试 / 复试记录已不存在），无法生成评分维度与优缺点，请直接依据线下沟通判断。"
          />

          <!-- 维度对比 -->
          <section class="sec">
            <div class="sec-head">
              <span class="sec-title">评分维度对比</span>
              <span class="sec-hint">各环节满分不同，已统一折算为 10 分制</span>
            </div>
            <el-table
              v-if="brief.dimension_matrix.rows.length"
              :data="brief.dimension_matrix.rows"
              size="small"
              border
              class="dim-table"
            >
              <el-table-column prop="name" label="维度" min-width="120" />
              <el-table-column
                v-for="col in brief.dimension_matrix.columns"
                :key="col.key"
                :label="col.label"
                min-width="118"
              >
                <template #default="{ row }">
                  <div v-if="row.cells[col.key] != null" class="dim-cell">
                    <span class="dim-num" :class="scoreClass(row.cells[col.key] * 10)">
                      {{ row.cells[col.key].toFixed(1) }}
                    </span>
                    <span class="dim-bar">
                      <i
                        :style="{
                          width: (row.cells[col.key] / 10) * 100 + '%',
                          background: barColor(row.cells[col.key]),
                        }"
                      />
                    </span>
                  </div>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="此前各环节没有留下维度评分" :image-size="56" />
          </section>

          <!-- 优点 / 风险 / 待确认 -->
          <div class="insight-grid">
            <div class="insight ok">
              <div class="insight-head">
                <span>优点</span>
                <em>{{ brief.strengths.length }}</em>
              </div>
              <ul v-if="brief.strengths.length">
                <li v-for="(s, i) in brief.strengths" :key="i">
                  <span class="txt">{{ s.text }}</span>
                  <span class="src">{{ s.source }}</span>
                </li>
              </ul>
              <div v-else class="insight-empty">暂无明显亮点</div>
            </div>

            <div class="insight warn">
              <div class="insight-head">
                <span>风险</span>
                <em>{{ brief.risks.length }}</em>
              </div>
              <ul v-if="brief.risks.length">
                <li v-for="(r, i) in brief.risks" :key="i">
                  <span class="txt">{{ r.text }}</span>
                  <span class="src">{{ r.source }}</span>
                </li>
              </ul>
              <div v-else class="insight-empty">无明显风险</div>
            </div>

            <div class="insight ask">
              <div class="insight-head">
                <span>待确认问题</span>
                <em>{{ brief.pending_questions.length }}</em>
              </div>
              <ul v-if="brief.pending_questions.length">
                <li v-for="(q, i) in brief.pending_questions" :key="i">
                  <span class="txt">{{ q.question }}</span>
                  <span class="src">{{ q.source }}<template v-if="q.hint"> · {{ q.hint }}</template></span>
                </li>
              </ul>
              <div v-else class="insight-empty">此前环节没有留下未确认的问题</div>
            </div>
          </div>

          <!-- 各环节明细 -->
          <section class="sec">
            <div class="sec-head">
              <span class="sec-title">各环节明细</span>
              <span class="sec-hint">点开看该环节的评语与维度得分</span>
            </div>
            <el-collapse v-if="brief.stages.length">
              <el-collapse-item v-for="st in brief.stages" :key="st.key" :name="st.key">
                <template #title>
                  <div class="stage-title">
                    <span class="stage-name">{{ st.label }}</span>
                    <b :class="scoreClass(st.score)">{{ score(st.score) }}</b>
                    <el-tag v-if="st.conclusion" size="small" effect="plain">{{ st.conclusion }}</el-tag>
                  </div>
                </template>
                <div class="stage-meta">
                  <span v-if="st.interviewer">面试官：{{ st.interviewer }}</span>
                  <span v-if="st.happened_at">时间：{{ fmt(st.happened_at) }}</span>
                  <span v-if="st.extra && st.extra.ai_score != null">
                    录音 AI 参考：{{ st.extra.ai_score }}
                  </span>
                </div>
                <div v-if="st.dims.length" class="stage-dims">
                  <el-tag
                    v-for="d in st.dims"
                    :key="d.key"
                    size="small"
                    effect="plain"
                    class="stage-dim"
                  >
                    {{ d.name }} {{ d.score }}<template v-if="d.max_score && d.max_score !== 10">/{{ d.max_score }}</template>
                  </el-tag>
                </div>
                <p v-if="st.comment" class="stage-comment">{{ st.comment }}</p>
                <p v-else class="stage-comment muted">（该环节无评语）</p>
              </el-collapse-item>
            </el-collapse>
            <el-empty v-else description="尚无历史面试记录" :image-size="56" />
          </section>
        </template>
      </div>

      <template #footer>
        <div class="drawer-footer">
          <el-button @click="copyPending">复制追问清单</el-button>
          <div>
            <el-button type="danger" plain @click="decideFromBrief('fail')">终试不通过</el-button>
            <el-button type="success" @click="decideFromBrief('pass')">通过终试</el-button>
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listPendingFinal,
  decideFinalInterview,
  listOffers,
  getOfferStats,
  getInterviewBrief,
} from '../../api'
import OfferApproveDialog from '../../components/OfferApproveDialog.vue'

const pendingFinal = ref([])
const pendingOffers = ref([])
const stats = ref({})
const loadingFinal = ref(false)
const loadingOffers = ref(false)

const showApprove = ref(false)
const currentOffer = ref(null)

const showFinal = ref(false)
const currentFinal = ref(null)
const finalDecision = ref('pass')
const finalNote = ref('')
const finalActing = ref(false)
const finalBrief = ref(null)
const finalAsked = ref([])

const showBrief = ref(false)
const brief = ref(null)
const briefRow = ref(null)
const loadingBrief = ref(false)

function score(v) {
  return v == null ? '-' : v
}

function scoreClass(s) {
  if (s == null) return 'muted'
  if (s >= 80) return 's-high'
  if (s >= 65) return 's-mid'
  if (s >= 50) return 's-low'
  return 's-bad'
}

function barColor(norm) {
  if (norm >= 8) return '#16a34a'
  if (norm >= 6) return '#6366f1'
  if (norm >= 4) return '#d97706'
  return '#dc2626'
}

function verdictType(v) {
  return { 强烈推荐: 'success', 推荐: 'success', 待定: 'warning', 不推荐: 'danger' }[v] || 'info'
}

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('zh-CN') : '-'
}

async function loadPendingFinal() {
  loadingFinal.value = true
  try {
    pendingFinal.value = await listPendingFinal()
  } finally {
    loadingFinal.value = false
  }
}

async function loadPendingOffers() {
  loadingOffers.value = true
  try {
    pendingOffers.value = await listOffers({ status: 'pending_approval' })
  } finally {
    loadingOffers.value = false
  }
}

async function loadStats() {
  stats.value = await getOfferStats({})
}

function openApprove(row) {
  currentOffer.value = row
  showApprove.value = true
}

// ── 面试档案 ────────────────────────────────────────────────
async function openBrief(row) {
  if (!row) return
  briefRow.value = row
  brief.value = null
  showBrief.value = true
  loadingBrief.value = true
  try {
    brief.value = await getInterviewBrief(row.id)
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loadingBrief.value = false
  }
}

async function copyPending() {
  if (!brief.value) {
    ElMessage.info('档案还在加载中，请稍候')
    return
  }
  const list = brief.value.pending_questions || []
  if (!list.length) {
    ElMessage.info('该候选人暂无待确认问题')
    return
  }
  const text = list.map((q, i) => `${i + 1}. ${q.question}`).join('\n')
  const copied = await copyText(text)
  if (copied) ElMessage.success(`已复制 ${list.length} 条终试追问`)
  else ElMessage.warning('复制失败，请手动选择文本')
}

/** 兼容非 HTTPS（内网 IP 访问）的复制：Clipboard API 不可用时退回 execCommand */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (e) {
    /* 落到 execCommand */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch (e) {
    return false
  }
}

function decideFromBrief(decision) {
  const row = briefRow.value
  if (!row) return
  showBrief.value = false
  openFinal(row, decision)
}

// ── 终试决策 ────────────────────────────────────────────────
async function openFinal(row, decision) {
  currentFinal.value = row
  finalDecision.value = decision
  finalNote.value = ''
  finalAsked.value = []
  showFinal.value = true

  // 复用已拉过的档案，避免重复请求
  if (brief.value && briefRow.value?.id === row.id) {
    finalBrief.value = brief.value
    finalAsked.value = new Array(brief.value.pending_questions.length).fill(false)
    return
  }
  finalBrief.value = null
  loadingBrief.value = true
  try {
    const data = await getInterviewBrief(row.id)
    briefRow.value = row
    brief.value = data
    finalBrief.value = data
    finalAsked.value = new Array(data.pending_questions.length).fill(false)
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loadingBrief.value = false
  }
}

async function submitFinal() {
  if (!currentFinal.value) return
  if (finalDecision.value === 'fail' && !finalNote.value.trim()) {
    ElMessage.warning('请填写不通过的原因')
    return
  }
  finalActing.value = true
  try {
    await decideFinalInterview(currentFinal.value.id, {
      decision: finalDecision.value,
      note: finalNote.value,
    })
    ElMessage.success(finalDecision.value === 'pass' ? '已通过终试' : '已标记终试不通过')
    showFinal.value = false
    await loadPendingFinal()
    await loadStats()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    finalActing.value = false
  }
}

onMounted(() => {
  loadPendingFinal()
  loadPendingOffers()
  loadStats()
})
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.guide {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.stats {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.stat {
  flex: 1;
  min-width: 120px;
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
.mb {
  margin-bottom: 16px;
}
.ml4 {
  margin-left: 4px;
}
.muted {
  color: #909399;
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

/* ── 决策弹窗内的档案速览 ───────────────────────────── */
.final-brief {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
  padding: 10px 12px;
  margin-bottom: 14px;
}
.fb-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
}
.fb-head > div {
  flex: 1;
}
.fb-block + .fb-block {
  margin-top: 8px;
}
.fb-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.fb-line {
  font-size: 13px;
  line-height: 1.6;
  color: #374151;
}
.fb-line.warn {
  color: #b45309;
}
.fb-line.ok {
  color: #15803d;
}
.fb-check {
  display: block;
  height: auto;
  margin: 2px 0;
  font-size: 13px;
  white-space: normal;
}
.fb-check :deep(.el-checkbox__label) {
  font-size: 13px;
  line-height: 1.5;
  white-space: normal;
}

/* ── 面试档案抽屉 ───────────────────────────────────── */
.brief-body {
  min-height: 200px;
}
.brief-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
  padding: 12px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
  margin-bottom: 18px;
}
.bs-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bs-item.grow {
  flex: 1;
  min-width: 160px;
}
.bs-label {
  font-size: 12px;
  color: #6b7280;
}
.bs-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
}
.bs-text {
  font-size: 14px;
  color: #374151;
}
.sec {
  margin-bottom: 20px;
}
.sec-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}
.sec-title {
  font-size: 14px;
  font-weight: 600;
  color: #111827;
}
.sec-hint {
  font-size: 12px;
  color: #9ca3af;
}
.dim-table {
  width: 100%;
}
.dim-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dim-num {
  font-weight: 600;
  min-width: 28px;
}
.dim-bar {
  flex: 1;
  height: 5px;
  min-width: 28px;
  border-radius: 3px;
  background: #eef1f6;
  overflow: hidden;
}
.dim-bar i {
  display: block;
  height: 100%;
  border-radius: 3px;
}
.insight-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.insight {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
  border-top: 3px solid #d1d5db;
}
.insight.ok {
  border-top-color: #16a34a;
}
.insight.warn {
  border-top-color: #d97706;
}
.insight.ask {
  border-top-color: #6366f1;
}
.insight-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 6px;
}
.insight-head em {
  font-style: normal;
  font-size: 12px;
  color: #6b7280;
  background: #f3f4f6;
  border-radius: 10px;
  padding: 0 7px;
}
.insight ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.insight li {
  padding: 5px 0;
  border-bottom: 1px dashed #eef1f6;
  line-height: 1.5;
}
.insight li:last-child {
  border-bottom: none;
}
.insight .txt {
  display: block;
  font-size: 13px;
  color: #1f2937;
}
.insight .src {
  display: block;
  font-size: 11px;
  color: #9ca3af;
  margin-top: 2px;
}
.insight-empty {
  font-size: 12px;
  color: #b0b5bd;
  padding: 8px 0;
}
.stage-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stage-name {
  font-weight: 600;
  font-size: 13px;
  color: #1f2937;
}
.stage-meta {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 8px;
}
.stage-dims {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.stage-comment {
  font-size: 13px;
  color: #374151;
  line-height: 1.6;
  margin: 0;
  background: #f8fafc;
  border-radius: 6px;
  padding: 8px 10px;
}
.drawer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
