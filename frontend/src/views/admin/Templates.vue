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
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" text @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有面试模板。先创建岗位，再为岗位配置面试问题与评分维度">
            <el-button type="primary" @click="openCreate">新建模板</el-button>
          </el-empty>
        </template>
      </el-table>
    </el-card>

    <!-- 新建模板弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑面试模板' : '新建面试模板'"
      width="min(760px, 94vw)"
    >
      <el-form label-width="100px">
        <el-form-item label="岗位" required>
          <el-select v-model="form.job_id" placeholder="选择岗位" style="width: 100%">
            <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
          </el-select>
        </el-form-item>

        <el-form-item label="预设问题" required>
          <div class="question-list">
            <div v-for="(q, i) in form.questions" :key="i" class="question-item">
              <div class="question-row">
                <el-select v-model="q.type" style="width: 110px">
                  <el-option label="主观题" value="subjective" />
                  <el-option label="选择题" value="choice" />
                </el-select>
                <el-input v-model="q.text" placeholder="面试问题" />
                <el-button type="danger" text @click="removeQuestion(i)">删除</el-button>
              </div>

              <!-- 选择题专属配置 -->
              <div v-if="q.type === 'choice'" class="choice-editor">
                <div v-for="(opt, oi) in q.options" :key="oi" class="option-row">
                  <span class="option-tag">{{ optionLabel(oi) }}</span>
                  <el-input v-model="q.options[oi]" placeholder="选项内容" />
                  <el-button type="danger" text @click="removeOption(q, oi)">删除</el-button>
                </div>
                <el-button type="primary" text size="small" @click="addOption(q)">
                  + 添加选项
                </el-button>

                <div class="choice-meta">
                  <span class="meta-label">正确答案</span>
                  <el-select
                    v-model="q.answer"
                    placeholder="不设标准答案"
                    clearable
                    style="width: 220px"
                  >
                    <el-option
                      v-for="(opt, oi) in q.options"
                      :key="oi"
                      :label="opt || `选项 ${optionLabel(oi)}`"
                      :value="opt"
                    />
                  </el-select>
                  <span class="meta-label">评分维度</span>
                  <el-select
                    v-model="q.dimension"
                    placeholder="不计入维度"
                    clearable
                    style="width: 160px"
                  >
                    <el-option
                      v-for="d in form.rubric"
                      :key="d.name"
                      :label="d.name"
                      :value="d.name"
                    />
                  </el-select>
                </div>
              </div>
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
import { listJobs, listTemplates, createTemplate, updateTemplate, deleteTemplate } from '../../api'

const templates = ref([])
const jobs = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
// 正在编辑的模板 id；为空串表示「新建」
const editingId = ref('')
const form = ref({
  job_id: '',
  questions: [{ type: 'subjective', text: '', options: [], answer: '', dimension: '' }],
  rubric: [
    { name: '技术深度', weight: 0.4, max_score: 10 },
    { name: '项目经验', weight: 0.3, max_score: 10 },
    { name: '沟通表达', weight: 0.15, max_score: 10 },
    { name: '岗位匹配', weight: 0.15, max_score: 10 },
  ],
  max_follow_ups: 2,
})

function newQuestion() {
  return { type: 'subjective', text: '', options: [], answer: '', dimension: '' }
}

function optionLabel(i) {
  return String.fromCharCode(65 + i)
}

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

function defaultRubric() {
  // 餐饮门店通用维度（原默认值是「技术深度/项目经验」，面向研发岗，餐饮 HR 会一头雾水）
  return [
    { name: '服务意识', weight: 0.35, max_score: 10 },
    { name: '稳定性', weight: 0.25, max_score: 10 },
    { name: '沟通表达', weight: 0.2, max_score: 10 },
    { name: '岗位匹配', weight: 0.2, max_score: 10 },
  ]
}

function openCreate() {
  editingId.value = ''
  form.value = {
    job_id: '',
    questions: [newQuestion()],
    rubric: defaultRubric(),
    max_follow_ups: 2,
  }
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.value = {
    job_id: row.job_id,
    // 深拷贝：编辑过程中不应直接改动表格里的数据（取消编辑需保持原样）
    questions: JSON.parse(JSON.stringify(row.questions || [newQuestion()])),
    rubric: JSON.parse(JSON.stringify(row.rubric || defaultRubric())),
    max_follow_ups: row.max_follow_ups ?? 2,
  }
  dialogVisible.value = true
}

function addQuestion() {
  form.value.questions.push(newQuestion())
}

function removeQuestion(i) {
  form.value.questions.splice(i, 1)
}

function addOption(q) {
  q.options.push('')
}

function removeOption(q, oi) {
  const removed = q.options[oi]
  q.options.splice(oi, 1)
  // 删除的选项若正是正确答案，清空答案
  if (q.answer === removed) {
    q.answer = ''
  }
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

  // 维度名 -> 维度 key（选择题「评分维度」下拉按名称选择，落库时转成 key）
  const nameToKey = {}
  rubric.forEach((d) => {
    nameToKey[d.name] = d.key
  })

  const questions = form.value.questions
    .filter((q) => q.text.trim())
    .map((q, i) => {
      const item = { id: q.id || `q${i + 1}`, text: q.text.trim() }
      if (q.type === 'choice') {
        const opts = (q.options || []).map((o) => (o || '').trim()).filter(Boolean)
        if (opts.length < 2) {
          ElMessage.warning(`选择题「${item.text}」至少需要 2 个选项`)
          throw new Error('choice-options-invalid')
        }
        const answer = (q.answer || '').trim()
        if (answer && !opts.includes(answer)) {
          ElMessage.warning(`选择题「${item.text}」的正确答案必须来自其选项`)
          throw new Error('choice-answer-invalid')
        }
        item.type = 'choice'
        item.options = opts
        item.answer = answer
        item.dimension = nameToKey[q.dimension] || ''
      }
      return item
    })
  if (questions.length === 0) {
    ElMessage.warning('请至少添加一个问题')
    return
  }

  saving.value = true
  const payload = {
    job_id: form.value.job_id,
    questions,
    rubric,
    duration_limit: 30,
    max_follow_ups: form.value.max_follow_ups,
  }
  try {
    if (editingId.value) {
      await updateTemplate(editingId.value, payload)
      ElMessage.success('模板已更新（已发出的面试不受影响）')
    } else {
      await createTemplate(payload)
      ElMessage.success('模板创建成功')
    }
    dialogVisible.value = false
    load()
  } catch (e) {
    if (e?.message !== 'choice-options-invalid' && e?.message !== 'choice-answer-invalid') {
      ElMessage.error(e?.message || '保存失败')
    }
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除「${jobTitle(row.job_id)}」的面试模板？该模板下的面试与评分数据也会一并删除，且不可恢复。`,
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

.question-item {
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafafa;
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

.choice-editor {
  padding: 8px 0 4px 0;
}

.option-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.option-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #eef2ff;
  color: #6366f1;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.choice-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.meta-label {
  font-size: 13px;
  color: #606266;
}
</style>
