<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>评分报告</span>
          <el-select
            v-model="selectedSessionId"
            placeholder="选择一场已完成的面试"
            style="width: min(320px, 60vw)"
            filterable
            @change="loadReport"
          >
            <el-option
              v-for="s in finishedSessions"
              :key="s.id"
              :label="`${s.candidate_name || '候选人'} · ${formatTime(s.created_at)}`"
              :value="s.id"
            />
          </el-select>
        </div>
      </template>

      <!-- 区分「没选」和「根本没有已完成的面试」，避免 HR 误以为自己操作错了 -->
      <el-empty
        v-if="!report && finishedSessions.length"
        description="请在上方选择一场面试，查看该候选人的评分报告"
      />
      <el-empty
        v-else-if="!report && !finishedSessions.length"
        description="还没有已完成的面试。候选人答完题后，这里会自动生成评分报告"
      >
        <el-button @click="$router.push('/console/sessions')">去 AI 面试记录查看进度</el-button>
      </el-empty>

      <template v-else>
        <!-- 综合分 + 等级 -->
        <div class="score-header">
          <div class="total-score">
            <div class="score-num">{{ report.report.total_score?.toFixed(1) }}</div>
            <div class="score-label">综合分（百分制）</div>
          </div>
          <div class="verdict">
            <el-tag :type="verdictType" size="large">{{ report.report.verdict }}</el-tag>
          </div>
        </div>

        <!-- 雷达图 -->
        <div class="radar-wrap">
          <div ref="radarRef" class="radar"></div>
        </div>

        <!-- 维度得分表格 -->
        <el-table :data="report.report.dim_scores" style="margin-top: 20px" stripe>
          <el-table-column prop="name" label="维度" width="160" />
          <el-table-column prop="weight" label="权重" width="100">
            <template #default="{ row }">{{ (row.weight * 100).toFixed(0) }}%</template>
          </el-table-column>
          <el-table-column prop="score" label="维度得分" width="120" />
          <el-table-column prop="weighted_score" label="加权得分" width="120">
            <template #default="{ row }">{{ row.weighted_score?.toFixed(2) }}</template>
          </el-table-column>
        </el-table>

        <!-- 总体评价 -->
        <el-alert
          v-if="report.report.summary"
          :title="report.report.summary"
          type="info"
          :closable="false"
          style="margin-top: 20px"
        />

        <!-- 完整对话与评分：原话是事实源，评分挂在每一轮上 -->
        <el-divider content-position="left">
          完整对话与评分
          <span v-if="turns.length" class="turn-meta">
            共 {{ turns.length }} 轮 · 候选人原话 {{ answerCharCount }} 字
          </span>
        </el-divider>

        <el-alert
          v-if="!turns.length"
          title="该场面试早于「原话留存」上线，没有候选人原话记录，下方仅展示评分明细"
          type="warning"
          :closable="false"
          style="margin-bottom: 12px"
        />

        <el-collapse v-if="turns.length">
          <el-collapse-item
            v-for="t in turns"
            :key="t.turn_index"
            :name="String(t.turn_index)"
          >
            <template #title>
              <span class="turn-title">
                <el-tag
                  size="small"
                  :type="t.is_follow_up ? 'warning' : 'info'"
                  effect="plain"
                >
                  {{ t.is_follow_up ? '追问' : `第 ${t.turn_index} 轮` }}
                </el-tag>
                <span class="turn-question">{{ t.question }}</span>
              </span>
            </template>

            <div class="qa-block">
              <div class="qa-label">面试官提问</div>
              <div class="qa-question">{{ t.question }}</div>
            </div>

            <div class="qa-block">
              <div class="qa-label">
                候选人原话
                <span class="qa-count">{{ (t.answer || '').length }} 字</span>
              </div>
              <div class="qa-answer">{{ t.answer || '（未作答）' }}</div>
            </div>

            <el-descriptions
              v-if="t.evaluation"
              :column="1"
              border
              size="small"
              label-width="88px"
              style="margin-top: 12px"
            >
              <el-descriptions-item label="评分">
                <el-tag
                  v-for="(v, k) in t.evaluation.scores"
                  :key="k"
                  size="small"
                  style="margin-right: 6px"
                >
                  {{ dimName(k) }}: {{ v }}
                </el-tag>
                <span v-if="!Object.keys(t.evaluation.scores).length" class="muted">
                  本轮未计入维度分
                </span>
              </el-descriptions-item>
              <el-descriptions-item label="AI 评语">
                {{ t.evaluation.comment }}
              </el-descriptions-item>
              <el-descriptions-item v-if="t.evaluation.follow_up_question" label="追问">
                {{ t.evaluation.follow_up_question }}
              </el-descriptions-item>
            </el-descriptions>
            <el-alert
              v-else
              title="本轮暂无评分记录（评分失败或尚未评分），原话已完整保留"
              type="info"
              :closable="false"
              style="margin-top: 12px"
            />
          </el-collapse-item>
        </el-collapse>

        <!-- 历史会话无原话：回退为仅展示评分明细 -->
        <el-collapse v-else>
          <el-collapse-item
            v-for="(e, i) in report.evaluations"
            :key="i"
            :title="`第 ${i + 1} 次作答`"
          >
            <el-descriptions :column="1" border label-width="88px">
              <el-descriptions-item label="评分">
                <el-tag
                  v-for="(v, k) in e.scores"
                  :key="k"
                  size="small"
                  style="margin-right: 6px"
                >
                  {{ dimName(k) }}: {{ v }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="评语">{{ e.comment }}</el-descriptions-item>
              <el-descriptions-item v-if="e.follow_up_question" label="追问">
                {{ e.follow_up_question }}
              </el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { listSessions, getReport } from '../../api'

const route = useRoute()
const sessions = ref([])
const selectedSessionId = ref('')
const report = ref(null)
const radarRef = ref(null)

const finishedSessions = computed(() => sessions.value.filter((s) => s.status === 'finished'))

// 完整对话（候选人原话）：报告接口按轮次返回，旧会话为空数组
const turns = computed(() => report.value?.turns || [])
const answerCharCount = computed(() =>
  turns.value.reduce((sum, t) => sum + (t.answer || '').length, 0),
)

/** 维度 key -> 中文名（找不到时回落 key，兼容历史模板） */
function dimName(key) {
  const dim = report.value?.report?.dim_scores?.find((d) => d.key === key)
  return dim?.name || key
}

const verdictType = computed(() => {
  const map = { 强烈推荐: 'success', 推荐: 'success', 待定: 'warning', 不推荐: 'danger' }
  return map[report.value?.report?.verdict] || 'info'
})

async function load() {
  sessions.value = await listSessions()
  // 若 URL 带 session_id，则自动选中
  const sid = route.query.session_id
  if (sid) {
    selectedSessionId.value = sid
    await loadReport()
  }
}

async function loadReport() {
  if (!selectedSessionId.value) return
  report.value = await getReport(selectedSessionId.value)
  await nextTick()
  renderRadar()
}

function renderRadar() {
  if (!radarRef.value || !report.value?.report?.dim_scores?.length) return
  const dims = report.value.report.dim_scores
  const indicators = dims.map((d) => ({ name: d.name, max: 10 }))
  const values = dims.map((d) => d.score)

  const chart = echarts.init(radarRef.value)
  chart.setOption({
    tooltip: {},
    radar: {
      indicator: indicators,
      radius: '65%',
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: values,
            name: '维度得分',
            areaStyle: { opacity: 0.3 },
          },
        ],
      },
    ],
  })
  window.addEventListener('resize', () => chart.resize())
}

function formatTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.score-header {
  display: flex;
  align-items: center;
  gap: 40px;
  margin-bottom: 20px;
}

.total-score {
  text-align: center;
}

.score-num {
  font-size: 48px;
  font-weight: 700;
  color: #6366f1;
}

.score-label {
  color: #6b7280;
  font-size: 13px;
  margin-top: 4px;
}

.radar-wrap {
  display: flex;
  justify-content: center;
}

.radar {
  width: 480px;
  height: 360px;
}

.turn-meta {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: #9ca3af;
}

.turn-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding-right: 12px;
}

.turn-question {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: #374151;
}

.qa-block {
  margin-bottom: 12px;
}

.qa-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 12px;
  color: #6b7280;
}

.qa-count {
  color: #9ca3af;
}

.qa-question {
  padding: 8px 12px;
  border-radius: 4px;
  background: #f3f4f6;
  color: #111827;
  line-height: 1.7;
  white-space: pre-wrap;
}

.qa-answer {
  padding: 10px 14px;
  border-left: 3px solid #6366f1;
  border-radius: 4px;
  background: #f8fafc;
  color: #111827;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}

.muted {
  font-size: 12px;
  color: #9ca3af;
}
</style>
