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
        <el-table-column prop="jd_text" label="JD 描述" min-width="300" show-overflow-tooltip />
        <el-table-column label="技能要求" min-width="200">
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
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建岗位弹窗 -->
    <el-dialog v-model="dialogVisible" title="新建岗位" width="600px">
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
import { listJobs, createJob, deleteJob } from '../../api'

const jobs = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const form = ref({ title: '', jd_text: '', skills: [] })

async function load() {
  loading.value = true
  try {
    jobs.value = await listJobs()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.value = { title: '', jd_text: '', skills: [] }
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.value.title.trim()) {
    ElMessage.warning('请填写岗位名称')
    return
  }
  saving.value = true
  try {
    await createJob({
      title: form.value.title.trim(),
      jd_text: form.value.jd_text,
      requirements: { skills: form.value.skills },
    })
    ElMessage.success('岗位创建成功')
    dialogVisible.value = false
    load()
  } finally {
    saving.value = false
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
</style>
