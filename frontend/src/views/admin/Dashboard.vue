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
import { ref, onMounted } from 'vue'
import { listJobs, listSessions } from '../../api'

const cards = ref([
  { label: '岗位数', value: '-' },
  { label: '会话数', value: '-' },
  { label: '已完成面试', value: '-' },
  { label: '待面试', value: '-' },
])

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
</style>
