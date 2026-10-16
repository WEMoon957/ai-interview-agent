<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>岗位管理</span>
          <el-button type="primary" @click="openCreate">新建岗位</el-button>
        </div>
      </template>

      <el-table :data="jobs" v-loading="loading" stripe>
        <el-table-column prop="title" label="岗位名称" min-width="160" />
        <el-table-column prop="jd_text" label="JD 描述" min-width="260" show-overflow-tooltip />
        <el-table-column label="技能要求" min-width="180">
          <template #default="{ row }">
            <el-tag
              v-for="skill in (row.requirements?.skills || [])"
              :key="skill"
              size="small"
              style="margin-right: 4px"
            >
              {{ skill }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="招聘流程" width="150">
          <template #default="{ row }">
            <el-switch
              :model-value="row.skip_screening"
              active-text="免初筛"
              inactive-text="需初筛"
              inline-prompt
              style="--el-switch-on-color: #f59e0b"
              @change="(val) => toggleSkipScreening(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="在招状态" width="140">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active !== false"
              active-text="在招"
              inactive-text="停招"
              inline-prompt
              style="--el-switch-on-color: #10b981"
              @change="(val) => toggleActive(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" text @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-top: 12px"
        title="「免初筛」岗位：在『简历初筛』页上传简历文件夹后，不做 AI 打分，直接为每位候选人生成 AI 面试链接。；「停招」岗位无法再发起新的 Offer。"
      />
    </el-card>

    <!-- 新建岗位弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑岗位' : '新建岗位'"
      width="min(600px, 94vw)"
    >
      <el-form :model="form" label-width="90px">
        <el-form-item label="岗位名称" required>
          <el-input v-model="form.title" placeholder="如：高级后端工程师" />
        </el-form-item>
        <el-form-item label="JD 描述">
          <el-input
            v-model="form.jd_text"
            type="textarea"
            :rows="4"
            placeholder="岗位职责与要求描述"
          />
        </el-form-item>
        <el-form-item label="技能要求">
          <el-select
            v-model="form.skills"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="输入技能后回车添加"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="招聘流程">
          <el-switch v-model="form.skip_screening" />
          <span class="switch-tip">
            {{ form.skip_screening ? '免初筛：上传简历后直接发 AI 面试链接' : '需初筛：先 AI 简历打分排序，再决定是否发面试' }}
          </span>
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
import { listJobs, createJob, updateJob, deleteJob } from '../../api'

const jobs = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
// 正在编辑的岗位 id；为空串表示「新建」
const editingId = ref('')
const form = ref({ title: '', jd_text: '', skills: [], skip_screening: false })

async function load() {
  loading.value = true
  try {
    jobs.value = await listJobs()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  form.value = { title: '', jd_text: '', skills: [], skip_screening: false }
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.value = {
    title: row.title || '',
    jd_text: row.jd_text || '',
    skills: [...(row.requirements?.skills || [])],
    skip_screening: !!row.skip_screening,
  }
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.value.title.trim()) {
    ElMessage.warning('请填写岗位名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      title: form.value.title.trim(),
      jd_text: form.value.jd_text,
      requirements: { skills: form.value.skills },
      skip_screening: form.value.skip_screening,
    }
    if (editingId.value) {
      await updateJob(editingId.value, payload)
      ElMessage.success('岗位已更新')
    } else {
      await createJob(payload)
      ElMessage.success('岗位创建成功')
    }
    dialogVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function toggleSkipScreening(row, val) {
  // 开启免初筛会改变整个招聘流程（上传即发面试链接），需二次确认，避免误触
  if (val) {
    try {
      await ElMessageBox.confirm(
        `将「${row.title}」设为免初筛后，上传简历将跳过 AI 初筛打分、直接生成 AI 面试链接。确定吗？`,
        '切换为免初筛',
        { confirmButtonText: '确定切换', cancelButtonText: '取消', type: 'warning' }
      )
    } catch {
      return // 取消：开关保持原值（受控组件会自动回弹）
    }
  }
  try {
    await updateJob(row.id, { skip_screening: val })
    row.skip_screening = val
    ElMessage.success(val ? '已设为免初筛：上传简历即发面试链接' : '已设为需初筛')
  } catch {
    // 失败时保持原值（响应拦截已提示）
  }
}

async function toggleActive(row, val) {
  // 停招后不允许再为该岗位发起 Offer（已发出的不受影响），需二次确认避免误触
  if (!val) {
    try {
      await ElMessageBox.confirm(
        `将「${row.title}」设为停招后，无法再为该岗位发起新的 Offer（已发出的 Offer 不受影响）。确定吗？`,
        '切换为停招',
        { confirmButtonText: '确定停招', cancelButtonText: '取消', type: 'warning' }
      )
    } catch {
      return // 取消：开关保持原值
    }
  }
  try {
    await updateJob(row.id, { is_active: val })
    row.is_active = val
    ElMessage.success(val ? '已恢复在招' : '已设为停招')
  } catch {
    // 失败时保持原值（响应拦截已提示）
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除岗位「${row.title}」将同时删除其下的所有模板、会话和评分数据，确定要删除吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await deleteJob(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    // 已提示
  }
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
.switch-tip {
  margin-left: 10px;
  color: #909399;
  font-size: 13px;
}
</style>
