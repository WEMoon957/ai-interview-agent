<template>
  <div class="dashboard">
    <el-row :gutter="20">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover">
          <div class="stat">
            <div class="stat-value">{{ card.value }}</div>
            <div class="stat-label">{{ card.label }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top: 20px">
      <template #header>
        <div class="card-header">
          <span>招聘转化漏斗（汇总完成 → 发 Offer → 接受 → 入职）</span>
          <el-select
            v-model="month"
            placeholder="全部时间"
            clearable
            style="width: 150px"
            @change="loadFunnel"
          >
            <el-option v-for="m in monthOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
        </div>
      </template>

      <div class="funnel">
        <div class="funnel-step">
          <div class="funnel-num">{{ funnel.summaries_complete ?? 0 }}</div>
          <div class="funnel-label">汇总完成</div>
        </div>
        <div class="funnel-arrow">→</div>
        <div class="funnel-step">
          <div class="funnel-num">{{ funnel.offers_sent ?? 0 }}</div>
          <div class="funnel-label">已发 Offer</div>
        </div>
        <div class="funnel-arrow">→</div>
        <div class="funnel-step">
          <div class="funnel-num">{{ funnel.offers_accepted ?? 0 }}</div>
          <div class="funnel-label">已接受</div>
        </div>
        <div class="funnel-arrow">→</div>
        <div class="funnel-step highlight">
          <div class="funnel-num">{{ funnel.onboarded ?? 0 }}</div>
          <div class="funnel-label">已入职</div>
        </div>
      </div>

      <div class="funnel-extra">
        <span>待审批 Offer：<b>{{ funnel.pending_approval ?? 0 }}</b></span>
        <span>Offer 接受率：<b>{{ acceptRateText }}</b></span>
      </div>
    </el-card>

    <el-card shadow="never" class="welcome" style="margin-top: 20px">
      <template #header>使用指引</template>
      <ol class="guide">
        <li>先在「岗位管理」创建招聘岗位</li>
        <li>在「面试模板」为岗位配置预设问题 + 评分维度</li>
        <li>在「面试会话」创建面试，上传候选人简历，生成答题链接</li>
        <li>候选人通过链接完成面试后，在「评分报告」查看维度得分与录用建议</li>
      </ol>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { listJobs, listSessions, getOfferStats } from '../../api'

const cards = ref([
  { label: '岗位数', value: '-' },
  { label: '会话数', value: '-' },
  { label: '已完成面试', value: '-' },
  { label: '待面试', value: '-' },
])

// ── 招聘转化漏斗 ─────────────────────────────────
const month = ref('')
const funnel = ref({})
const acceptRate = ref(null)

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

const acceptRateText = computed(() =>
  acceptRate.value == null ? '—' : `${Math.round(acceptRate.value * 100)}%`
)

async function loadFunnel() {
  try {
    const stats = await getOfferStats(month.value ? { month: month.value } : {})
    funnel.value = stats.funnel || {}
    acceptRate.value = stats.accept_rate
  } catch (e) {
    // 已提示
  }
}

onMounted(async () => {
  try {
    const [jobs, sessions] = await Promise.all([listJobs(), listSessions()])
    cards.value[0].value = jobs.length
    cards.value[1].value = sessions.length
    cards.value[2].value = sessions.filter((s) => s.status === 'finished').length
    cards.value[3].value = sessions.filter((s) => s.status !== 'finished').length
  } catch (e) {
    // 已提示
  }
  await loadFunnel()
})
</script>

<style scoped>
.stat {
  text-align: center;
  padding: 8px 0;
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  color: #6366f1;
}

.stat-label {
  margin-top: 8px;
  color: #6b7280;
  font-size: 14px;
}

.guide {
  padding-left: 20px;
  line-height: 2.2;
  color: #4b5563;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

.funnel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0 4px;
  flex-wrap: wrap;
}

.funnel-step {
  flex: 1;
  min-width: 110px;
  text-align: center;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 16px 10px;
}

.funnel-step.highlight {
  background: #eef2ff;
  border-color: #c7d2fe;
}

.funnel-num {
  font-size: 28px;
  font-weight: 700;
  color: #6366f1;
}

.funnel-label {
  margin-top: 6px;
  color: #6b7280;
  font-size: 13px;
}

.funnel-arrow {
  color: #9ca3af;
  font-size: 18px;
}

.funnel-extra {
  display: flex;
  gap: 24px;
  margin-top: 14px;
  color: #6b7280;
  font-size: 13px;
}

.funnel-extra b {
  color: #1f2937;
}
</style>
