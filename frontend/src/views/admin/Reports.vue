<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>评分报告</span>
          <el-select
            v-model="selectedSessionId"
            placeholder="选择面试会话"
            style="width: 320px"
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

      <el-empty v-if="!report" description="请选择一个已完成的面试会话查看报告" />

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

        <!-- 逐题评分明细 -->
        <el-divider content-position="left">逐题评分明细</el-divider>
        <el-collapse>
          <el-collapse-item
            v-for="(e, i) in report.evaluations"
            :key="i"
            :title="`第 ${i + 1} 次作答 · ${e.question_id}`"
          >
            <el-descriptions :column="1" border>
              <el-descriptions-item label="评分">
                <el-tag
                  v-for="(v, k) in e.scores"
                  :key="k"
                  size="small"
                  style="margin-right: 6px"
                >
                  {{ k }}: {{ v }}
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
</style>
