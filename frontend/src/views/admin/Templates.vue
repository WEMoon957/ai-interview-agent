<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>面试模板</span>
          <el-button type="primary" @click="openCreate">新建模板</el-button>
        </div>
      </template>

      <el-table :data="templates" v-loading="loading" stripe>
        <el-table-column label="岗位" min-width="140">
          <template #default="{ row }">{{ jobTitle(row.job_id) }}</template>
        </el-table-column>
        <el-table-column label="问题数" width="90">
          <template #default="{ row }">{{ row.questions?.length || 0 }}</template>
        </el-table-column>
        <el-table-column label="评分维度" min-width="220">
          <template #default="{ row }">
            <el-tag
              v-for="d in row.rubric || []"
              :key="d.key"
              size="small"
              style="margin-right: 4px"
            >
              {{ d.name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="max_follow_ups" label="追问上限" width="90" />
        <el-table-column prop="duration_limit" label="时长(分)" width="90" />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建模板弹窗 -->
    <el-dialog v-model="dialogVisible" title="新建面试模板" width="760px">
      <el-form label-width="100px">
        <el-form-item label="岗位" required>
          <el-select v-model="form.job_id" placeholder="选择岗位" style="width: 100%">
            <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
          </el-select>
        </el-form-item>

        <el-form-item label="预设问题" required>
          <div class="question-list">
            <div v-for="(q, i) in form.questions" :key="i" class="question-row">
              <el-input v-model="q.text" placeholder="面试问题" />
              <el-button type="danger" text @click="removeQuestion(i)">删除</el-button>
            </div>
            <el-button type="primary" text @click="addQuestion">+ 添加问题</el-button>
          </div>
        </el-form-item>

        <el-form-item label="评分维度" required>
          <div class="rubric-list">
            <div v-for="(d, i) in form.rubric" :key="i" class="rubric-row">
              <el-input v-model="d.name" placeholder="维度名" style="width: 140px" />
              <el-input-number v-model="d.weight" :min="0" :max="1" :step="0.05" :precision="2" placeholder="权重" />
              <el-input-number v-model="d.max_score" :min="1" :max="100" placeholder="满分" />
              <el-button type="danger" text @click="removeRubric(i)">删除</el-button>
            </div>
            <el-button type="primary" text @click="addRubric">+ 添加维度</el-button>
          </div>
        </el-form-item>

        <el-form-item label="追问上限">
          <el-input-number v-model="form.max_follow_ups" :min="0" :max="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listJobs, listTemplates, createTemplate, deleteTemplate } from '../../api'

const templates = ref([])
const jobs = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const form = ref({
  job_id: '',
  questions: [{ text: '' }],
  rubric: [
    { name: '技术深度', weight: 0.4, max_score: 10 },
    { name: '项目经验', weight: 0.3, max_score: 10 },
    { name: '沟通表达', weight: 0.15, max_score: 10 },
    { name: '岗位匹配', weight: 0.15, max_score: 10 },
  ],
  max_follow_ups: 2,
})

function jobTitle(id) {
  return jobs.value.find((j) => j.id === id)?.title || id
}

async function load() {
  loading.value = true
  try {
    ;[jobs.value, templates.value] = await Promise.all([listJobs(), listTemplates()])
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = {
    job_id: '',
    questions: [{ text: '' }],
    rubric: [
      { name: '技术深度', weight: 0.4, max_score: 10 },
      { name: '项目经验', weight: 0.3, max_score: 10 },
      { name: '沟通表达', weight: 0.15, max_score: 10 },
      { name: '岗位匹配', weight: 0.15, max_score: 10 },
    ],
    max_follow_ups: 2,
  }
  dialogVisible.value = true
}

function addQuestion() {
  form.value.questions.push({ text: '' })
}

function removeQuestion(i) {
  form.value.questions.splice(i, 1)
}

function addRubric() {
  form.value.rubric.push({ name: '', weight: 0.1, max_score: 10 })
}

function removeRubric(i) {
  form.value.rubric.splice(i, 1)
}

async function handleSave() {
  if (!form.value.job_id) {
    ElMessage.warning('请选择岗位')
    return
  }
  const questions = form.value.questions
    .filter((q) => q.text.trim())
    .map((q, i) => ({ id: `q${i + 1}`, text: q.text.trim() }))
  if (questions.length === 0) {
    ElMessage.warning('请至少添加一个问题')
    return
  }
  const rubric = form.value.rubric
    .filter((d) => d.name.trim())
    .map((d, i) => ({
      key: `dim_${i}`,
      name: d.name.trim(),
      description: '',
      weight: d.weight,
      max_score: d.max_score,
    }))
  if (rubric.length === 0) {
    ElMessage.warning('请至少添加一个评分维度')
    return
  }
  const weightSum = rubric.reduce((s, d) => s + d.weight, 0)
  if (Math.abs(weightSum - 1) > 0.01) {
    ElMessage.warning(`评分维度权重之和应为 1，当前为 ${weightSum.toFixed(2)}`)
    return
  }

  saving.value = true
  try {
    await createTemplate({
      job_id: form.value.job_id,
      questions,
      rubric,
      duration_limit: 30,
      max_follow_ups: form.value.max_follow_ups,
    })
    ElMessage.success('模板创建成功')
    dialogVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除该模板将同时删除其下的所有会话和评分数据，确定要删除吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteTemplate(row.id)
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

.question-list,
.rubric-list {
  width: 100%;
}

.question-row,
.rubric-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.rubric-row .el-input-number {
  width: 120px;
}
</style>
