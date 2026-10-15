<template>
  <div class="offer-page">
    <div class="offer-box">
      <!-- 加载中 -->
      <div v-if="loading" class="state">
        <div class="spinner"></div>
        <p>正在加载 Offer…</p>
      </div>

      <!-- 链接无效 -->
      <div v-else-if="errorMsg" class="state">
        <div class="big-icon">⚠️</div>
        <h2>链接无效或已失效</h2>
        <p class="hint">{{ errorMsg }}</p>
        <p class="hint">如有疑问，请联系招聘 HR。</p>
      </div>

      <!-- 待确认 -->
      <template v-else-if="status === 'sent'">
        <div class="brand">{{ ctx.company_name || '招聘' }}</div>
        <h1 class="title">🎉 恭喜！您的面试已通过</h1>
        <p class="subtitle">我们诚挚邀请您加入</p>

        <div class="info-card">
          <div class="info-row">
            <span class="label">岗位</span>
            <span class="value">{{ ctx.job_title || '—' }}</span>
          </div>
          <div class="info-row">
            <span class="label">报到门店</span>
            <span class="value">{{ ctx.store_name || '—' }}</span>
          </div>
          <div class="info-row">
            <span class="label">拟报到日期</span>
            <span class="value">{{ ctx.report_date || '待定' }}</span>
          </div>
          <div class="info-row">
            <span class="label">试用期</span>
            <span class="value">{{ ctx.probation_days }} 天</span>
          </div>
          <div v-if="ctx.note" class="info-row">
            <span class="label">备注</span>
            <span class="value">{{ ctx.note }}</span>
          </div>
        </div>

        <div class="deadline">
          请在 <strong>{{ ctx.deadline_text }}</strong> 前确认是否接受
          <span v-if="remainText" class="remain">· {{ remainText }}</span>
        </div>

        <div class="actions">
          <button class="btn primary" :disabled="submitting" @click="handleAccept">
            {{ submitting ? '提交中…' : '接受 Offer' }}
          </button>
          <button class="btn ghost" :disabled="submitting" @click="showDecline = true">
            婉拒
          </button>
        </div>

        <p v-if="ctx.hr_contact_phone" class="contact">
          如有疑问，请联系招聘 HR：{{ ctx.hr_contact_phone }}
        </p>
      </template>

      <!-- 已接受 -->
      <div v-else-if="status === 'accepted'" class="state">
        <div class="big-icon">🎉</div>
        <h2>欢迎加入！</h2>
        <p class="result-line">{{ ctx.job_title }} · {{ ctx.store_name }}</p>
        <p class="hint">HR 将与您联系入职事宜，请保持手机畅通。</p>
      </div>

      <!-- 已婉拒 -->
      <div v-else-if="status === 'declined'" class="state">
        <div class="big-icon">🙏</div>
        <h2>感谢您的回复</h2>
        <p class="hint">很遗憾这次未能合作，期待未来有机会再会。</p>
      </div>

      <!-- 已过期 -->
      <div v-else-if="status === 'expired'" class="state">
        <div class="big-icon">⏰</div>
        <h2>Offer 已过期</h2>
        <p class="hint">该 Offer 已超过确认时间。如需继续，请联系招聘 HR。</p>
      </div>

      <!-- 其他状态兜底（草稿/待审批/已撤回等） -->
      <div v-else class="state">
        <div class="big-icon">ℹ️</div>
        <h2>{{ ctx.status_label || '当前不可确认' }}</h2>
        <p class="hint">该 Offer 当前无法操作，请联系招聘 HR。</p>
      </div>
    </div>

    <!-- 婉拒弹层：理由为自由文本（选填） -->
    <div v-if="showDecline" class="mask" @click.self="showDecline = false">
      <div class="sheet">
        <h3>方便告诉我们原因吗？</h3>
        <p class="sheet-sub">您的反馈会帮助我们做得更好（选填）</p>
        <textarea
          v-model="declineReason"
          rows="3"
          maxlength="200"
          placeholder="例如：已经找到其他工作 / 距离太远 / 时间不合适…"
        ></textarea>
        <div class="sheet-actions">
          <button class="btn ghost" :disabled="submitting" @click="showDecline = false">取消</button>
          <button class="btn danger" :disabled="submitting" @click="handleDecline">
            {{ submitting ? '提交中…' : '确认婉拒' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { getOfferContext, acceptOffer, declineOffer } from '../api'

const route = useRoute()
const token = route.params.token

const loading = ref(true)
const errorMsg = ref('')
const ctx = ref({})
const status = ref('')
const submitting = ref(false)
const showDecline = ref(false)
const declineReason = ref('')
const remainText = ref('')
let timer = null

/** 实时剩余时间提示（每 30 秒刷新），让截止时间更直观。 */
function updateRemain() {
  const expires = ctx.value?.expires_at
  if (!expires) {
    remainText.value = ''
    return
  }
  const diff = new Date(expires).getTime() - Date.now()
  if (Number.isNaN(diff)) {
    remainText.value = ''
    return
  }
  if (diff <= 0) {
    remainText.value = '已超过确认时间'
    return
  }
  const hours = Math.floor(diff / 3600000)
  const minutes = Math.floor((diff % 3600000) / 60000)
  if (hours >= 24) {
    remainText.value = `还剩 ${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
  } else if (hours > 0) {
    remainText.value = `还剩 ${hours} 小时 ${minutes} 分`
  } else {
    remainText.value = `还剩 ${minutes} 分钟`
  }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await getOfferContext(token)
    ctx.value = data || {}
    status.value = data?.status || ''
  } catch (err) {
    errorMsg.value = err?.message || '链接无效或已失效'
  } finally {
    loading.value = false
  }
}

async function handleAccept() {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await acceptOffer(token)
    status.value = res?.status || 'accepted'
  } catch (err) {
    // 拦截器已提示错误；重新拉取以同步真实状态（例如已过期）
    await load()
  } finally {
    submitting.value = false
  }
}

async function handleDecline() {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await declineOffer(token, { reason_text: declineReason.value })
    status.value = res?.status || 'declined'
    showDecline.value = false
  } catch (err) {
    await load()
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await load()
  updateRemain()
  timer = setInterval(updateRemain, 30000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.offer-page {
  min-height: 100vh;
  background: #f5f7fa;
  display: flex;
  justify-content: center;
  padding: 40px 16px;
  box-sizing: border-box;
}

.offer-box {
  width: 100%;
  max-width: 520px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  padding: 32px 28px;
  box-sizing: border-box;
  height: fit-content;
}

.brand {
  text-align: center;
  color: #6366f1;
  font-weight: 600;
  letter-spacing: 1px;
  margin-bottom: 18px;
}

.title {
  font-size: 22px;
  color: #1f2937;
  text-align: center;
  margin: 0 0 6px;
}

.subtitle {
  text-align: center;
  color: #6b7280;
  font-size: 14px;
  margin-bottom: 22px;
}

.info-card {
  background: #f9fafb;
  border: 1px solid #f3f4f6;
  border-radius: 10px;
  padding: 6px 18px;
  margin-bottom: 18px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0;
  border-bottom: 1px dashed #e5e7eb;
  font-size: 14px;
}

.info-row:last-child {
  border-bottom: none;
}

.info-row .label {
  color: #6b7280;
  flex-shrink: 0;
}

.info-row .value {
  color: #1f2937;
  font-weight: 500;
  text-align: right;
  word-break: break-all;
}

.deadline {
  background: #fff7ed;
  border: 1px solid #fed7aa;
  color: #b45309;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 14px;
  text-align: center;
  margin-bottom: 20px;
}

.deadline .remain {
  color: #dc2626;
  font-weight: 600;
  white-space: nowrap;
}

.actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.btn {
  border: none;
  border-radius: 8px;
  padding: 13px;
  font-size: 16px;
  cursor: pointer;
  font-weight: 500;
  transition: opacity 0.15s;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn.primary {
  background: #6366f1;
  color: #fff;
}

.btn.ghost {
  background: #fff;
  color: #6b7280;
  border: 1px solid #d1d5db;
}

.btn.danger {
  background: #dc2626;
  color: #fff;
}

.contact {
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
  margin-top: 18px;
}

/* ── 状态页（加载 / 结果）────────────────────── */
.state {
  text-align: center;
  padding: 40px 10px;
}

.state .big-icon {
  font-size: 46px;
  margin-bottom: 14px;
}

.state h2 {
  color: #1f2937;
  font-size: 20px;
  margin: 0 0 10px;
}

.state .hint {
  color: #6b7280;
  font-size: 14px;
  line-height: 1.7;
  margin: 6px 0;
}

.result-line {
  color: #374151;
  font-size: 15px;
  margin-bottom: 8px;
}

.spinner {
  width: 34px;
  height: 34px;
  margin: 0 auto 16px;
  border: 3px solid #e5e7eb;
  border-top-color: #6366f1;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* ── 婉拒弹层 ───────────────────────────────── */
.mask {
  position: fixed;
  inset: 0;
  background: rgba(17, 24, 39, 0.45);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  z-index: 100;
}

.sheet {
  width: 100%;
  max-width: 520px;
  background: #fff;
  border-radius: 16px 16px 0 0;
  padding: 22px 20px calc(22px + env(safe-area-inset-bottom));
  box-sizing: border-box;
}

.sheet h3 {
  margin: 0 0 6px;
  color: #1f2937;
  font-size: 17px;
}

.sheet-sub {
  color: #9ca3af;
  font-size: 13px;
  margin-bottom: 14px;
}

.sheet textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 12px;
  font-size: 16px; /* iOS：<16px 会触发页面缩放 */
  line-height: 1.6;
  resize: vertical;
  outline: none;
  font-family: inherit;
}

.sheet textarea:focus {
  border-color: #6366f1;
}

.sheet-actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

.sheet-actions .btn {
  flex: 1;
}

/* ── 移动端适配 ─────────────────────────────── */
@media (max-width: 768px) {
  .offer-page {
    padding: 16px 12px;
  }

  .offer-box {
    padding: 24px 18px;
    border-radius: 12px;
  }

  .title {
    font-size: 19px;
  }

  .info-row {
    font-size: 14px;
  }
}
</style>
