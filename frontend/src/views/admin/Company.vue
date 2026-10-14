<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>公司文化</span>
          <span class="meta">
            <template v-if="updatedAt">最近更新：{{ formatTime(updatedAt) }}</template>
            <template v-if="updatedBy"> · {{ updatedBy }}</template>
            <template v-if="!updatedAt">尚未维护</template>
          </span>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="这段文字会在「发起面试」时快照进每一个会话，候选人一打开面试链接，开场白就会自动带上它。修改只影响之后发起的面试，已发出的面试仍沿用当时的快照。"
      />

      <el-form label-position="top" class="form" @submit.prevent>
        <el-form-item label="公司介绍">
          <el-input
            v-model="content"
            type="textarea"
            :rows="12"
            resize="vertical"
            placeholder="写清四件事即可：① 公司是谁（品牌/主营业务/门店或规模）② 现状如何（发展节奏、旺季、近期重点）③ 团队氛围（相处方式、管理风格）④ 用工特点（班次、节假日、晋升、福利）"
          />
          <div class="counter">
            <span>共 {{ content.length }} 字</span>
            <el-tag v-if="content.length > INTRO_MAX" type="warning" size="small" effect="plain">
              开场白只展示前 {{ INTRO_MAX }} 字，超出部分仅在后台留存
            </el-tag>
            <span v-else class="hint">开场白最多展示前 {{ INTRO_MAX }} 字，建议控制在 {{ INTRO_MAX }} 字以内</span>
          </div>
        </el-form-item>

        <el-form-item label="保存人（可选）">
          <el-input v-model="operator" placeholder="如：李俊颖" style="max-width: 240px" />
        </el-form-item>
      </el-form>

      <div class="actions">
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
        <el-button :disabled="!dirty" @click="handleReset">撤销修改</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="preview-card">
      <template #header>
        <div class="card-header">
          <span>候选人开场白预览</span>
          <span class="meta">以候选人「张三」为例</span>
        </div>
      </template>
      <div class="opening">{{ openingPreview }}</div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getCompanyProfile, updateCompanyProfile } from '../../api'

// 与后端 app/services/company_profile.py 的 OPENING_INTRO_MAX_CHARS 保持一致
const INTRO_MAX = 300
const TAIL =
  '接下来我会围绕岗位要求逐题向你提问，请尽量结合真实经历具体作答。' +
  '部分问题我可能会根据你的回答进行追问，请保持放松，正常发挥即可。'

const loading = ref(false)
const saving = ref(false)
const content = ref('')
const savedContent = ref('')
const operator = ref(localStorage.getItem('company_profile_operator') || '')
const updatedAt = ref(null)
const updatedBy = ref('')

const dirty = computed(() => content.value !== savedContent.value)

const openingPreview = computed(() => {
  const text = content.value.trim()
  const shown = text.length > INTRO_MAX ? `${text.slice(0, INTRO_MAX)}……` : text
  const head = '张三，你好！欢迎参加本次 AI 面试。'
  if (!shown) {
    return `${head}${TAIL}`
  }
  return `${head}\n\n先花一分钟了解我们：\n${shown}\n\n${TAIL}`
})

async function load() {
  loading.value = true
  try {
    const data = (await getCompanyProfile()) || {}
    content.value = data.content || ''
    savedContent.value = content.value
    updatedAt.value = data.updated_at || null
    updatedBy.value = data.updated_by || ''
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const data = await updateCompanyProfile({
      content: content.value,
      updated_by: operator.value.trim(),
    })
    content.value = data.content || ''
    savedContent.value = content.value
    updatedAt.value = data.updated_at || null
    updatedBy.value = data.updated_by || ''
    localStorage.setItem('company_profile_operator', operator.value.trim())
    ElMessage.success('已保存，之后发起的面试开场白将带上这段介绍')
  } finally {
    saving.value = false
  }
}

function handleReset() {
  content.value = savedContent.value
}

function formatTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN')
}

async function guardLeave() {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm('公司介绍有未保存的修改，确定离开吗？', '未保存', {
      confirmButtonText: '放弃修改并离开',
      cancelButtonText: '继续编辑',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}

onBeforeRouteLeave(guardLeave)

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.meta {
  font-size: 13px;
  color: #909399;
}
.form {
  margin-top: 16px;
}
.counter {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  font-size: 13px;
  color: #909399;
}
.preview-card {
  margin-top: 16px;
}
.opening {
  white-space: pre-wrap;
  line-height: 1.8;
  color: #303133;
  background: #f5f7fa;
  border-radius: 6px;
  padding: 14px 16px;
}
.actions {
  display: flex;
  gap: 10px;
}
</style>
