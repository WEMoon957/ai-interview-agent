<template>
  <div class="interview-page">
    <!-- 顶部：品牌 + 进度 -->
    <div class="interview-header">
      <div class="brand">笨二娘隆江猪脚饭</div>
      <div class="progress-wrap" v-if="progress.total > 0">
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
        </div>
        <span class="progress-text">第 {{ Math.min(progress.current + 1, progress.total) }} / {{ progress.total }} 题</span>
      </div>
    </div>

    <!-- 主体 -->
    <div class="interview-body">
      <!-- 加载中 -->
      <div v-if="loading" class="loading">
        <div class="spinner"></div>
        <p>正在进入面试…</p>
      </div>

      <!-- 面试进行中 -->
      <template v-else-if="status !== 'finished'">
        <!-- 开场白 -->
        <div class="opening" v-if="opening">
          <span class="opening-icon">💬</span>
          <p>{{ opening }}</p>
        </div>

        <!-- 当前问题 -->
        <div class="question-card">
          <div class="question-label">当前问题</div>
          <div class="question-text">{{ question }}</div>
        </div>

        <!-- 追问提示 -->
        <div v-if="lastType === 'follow_up'" class="follow-up-tip">
          面试官针对你的回答进行了追问，请结合实际情况详细作答
        </div>

        <!-- 答题区 -->
        <div class="answer-area">
          <textarea
            v-model="answer"
            class="answer-input"
            placeholder="请输入你的回答，尽量结合真实经历具体作答…"
            maxlength="4000"
          ></textarea>
          <div class="answer-meta">
            <span class="char-count">{{ answer.length }} / 4000</span>
            <button
              class="submit-btn"
              :disabled="!answer.trim() || submitting"
              @click="handleSubmit"
            >
              {{ submitting ? '提交中…' : '提交回答' }}
            </button>
          </div>
        </div>
      </template>

      <!-- 面试完成 -->
      <template v-else>
        <div class="finished">
          <div class="finished-icon">✅</div>
          <h2>面试已完成</h2>
          <p>感谢参与本次面试！</p>
          <button class="submit-btn" @click="reset">重新开始</button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getInterviewContext, submitAnswer } from '../api'

const route = useRoute()
const token = route.params.token

const loading = ref(true)
const opening = ref('')
const question = ref('')
const questionId = ref('')
const progress = ref({ current: 0, total: 0 })
const status = ref('running')
const answer = ref('')
const submitting = ref(false)
const lastType = ref('')
const errorMsg = ref('')

const progressPercent = computed(() => {
  if (!progress.value.total) return 0
  return Math.round((progress.value.current / progress.value.total) * 100)
})

async function loadContext() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await getInterviewContext(token)
    opening.value = data.opening || ''
    question.value = data.question || ''
    questionId.value = data.question_id || ''
    progress.value = data.progress || { current: 0, total: 0 }
    status.value = data.status || 'running'
  } catch (e) {
    errorMsg.value = e.message || '加载失败，请检查后端服务'
  } finally {
    loading.value = false
  }
}

async function handleSubmit() {
  if (!answer.value.trim()) return
  submitting.value = true
  try {
    const data = await submitAnswer(token, answer.value.trim())
    status.value = data.status
    lastType.value = data.status
    if (data.status === 'finished') {
      question.value = ''
    } else {
      question.value = data.question || ''
      questionId.value = data.question_id || ''
    }
    if (data.progress) progress.value = data.progress
    answer.value = ''
  } catch (e) {
    errorMsg.value = e.message || '提交失败'
  } finally {
    submitting.value = false
  }
}

function reset() {
  answer.value = ''
  lastType.value = ''
  loadContext()
}

onMounted(loadContext)
</script>

<style scoped>
.interview-page {
  min-height: 100vh;
  background: linear-gradient(160deg, #eef2ff 0%, #f8fafc 40%, #ffffff 100%);
  display: flex;
  flex-direction: column;
}

.interview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 40px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.brand {
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.progress-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-bar {
  width: 200px;
  height: 8px;
  background: #e5e7eb;
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #818cf8);
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 13px;
  color: #6b7280;
  white-space: nowrap;
}

.interview-body {
  flex: 1;
  padding: 40px 20px;
  max-width: 820px;
  width: 100%;
  margin: 0 auto;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: #6b7280;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #e5e7eb;
  border-top-color: #6366f1;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-bottom: 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.opening {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 24px;
  color: #4b5563;
  line-height: 1.7;
}

.opening-icon {
  font-size: 20px;
}

.question-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-left: 4px solid #6366f1;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 20px;
}

.question-label {
  font-size: 13px;
  color: #6366f1;
  margin-bottom: 8px;
  font-weight: 500;
}

.question-text {
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
  line-height: 1.6;
}

.follow-up-tip {
  background: #fef3c7;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 10px 16px;
  font-size: 13px;
  color: #92400e;
  margin-bottom: 20px;
}

.answer-area {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 20px;
}

.answer-input {
  width: 100%;
  min-height: 160px;
  border: none;
  outline: none;
  resize: vertical;
  font-size: 15px;
  line-height: 1.6;
  font-family: inherit;
  color: #1f2937;
}

.answer-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #f3f4f6;
}

.char-count {
  font-size: 12px;
  color: #9ca3af;
}

.submit-btn {
  background: #6366f1;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 10px 28px;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
}

.submit-btn:hover:not(:disabled) {
  background: #4f46e5;
}

.submit-btn:disabled {
  background: #c7d2fe;
  cursor: not-allowed;
}

.finished {
  text-align: center;
  padding: 60px 20px;
  background: #fff;
  border-radius: 12px;
  border: 1px solid #e5e7eb;
}

.finished-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.finished h2 {
  color: #1f2937;
  margin-bottom: 8px;
}

.finished p {
  color: #6b7280;
  margin-bottom: 24px;
}

/* ── 移动端适配（<768px）────────────────────── */
@media (max-width: 768px) {
  .interview-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
    padding: 14px 16px;
  }

  .brand {
    font-size: 16px;
  }

  .progress-wrap {
    width: 100%;
  }

  .progress-bar {
    flex: 1;
    width: auto;
  }

  .interview-body {
    padding: 20px 14px;
  }

  .opening {
    padding: 14px 16px;
    font-size: 14px;
  }

  .question-card {
    padding: 16px;
  }

  .question-text {
    font-size: 16px;
    line-height: 1.55;
  }

  .follow-up-tip {
    font-size: 12px;
    padding: 8px 12px;
  }

  .answer-area {
    padding: 16px;
  }

  .answer-input {
    font-size: 16px; /* iOS 输入框 <16px 会自动放大页面，设 16px 避免 */
    line-height: 1.6;
    min-height: 140px;
  }

  .answer-meta {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }

  .char-count {
    text-align: right;
  }

  .submit-btn {
    width: 100%;
    padding: 14px;
    font-size: 16px;
  }

  .finished {
    padding: 40px 16px;
  }

  .finished-icon {
    font-size: 40px;
  }
}

/* ── 小屏手机进一步优化（<480px）────────────── */
@media (max-width: 480px) {
  .question-text {
    font-size: 15px;
  }
}
</style>
