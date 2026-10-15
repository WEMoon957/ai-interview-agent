<template>
  <el-dialog
    :model-value="modelValue"
    title="Offer 审批"
    width="520px"
    :close-on-click-modal="false"
    @update:model-value="close"
  >
    <el-descriptions v-if="offer" :column="1" border size="small" class="mb">
      <el-descriptions-item label="候选人">{{ offer.candidate_name }}</el-descriptions-item>
      <el-descriptions-item label="岗位">{{ offer.job_title }}</el-descriptions-item>
      <el-descriptions-item label="报到门店">{{ offer.store_name }}</el-descriptions-item>
      <el-descriptions-item label="拟报到日期">{{ offer.report_date || '—' }}</el-descriptions-item>
      <el-descriptions-item label="试用期">{{ offer.probation_days }} 天</el-descriptions-item>
      <el-descriptions-item v-if="offer.note" label="备注">{{ offer.note }}</el-descriptions-item>
      <el-descriptions-item v-if="offer.approver_open_id" label="审批人">
        {{ offer.approver_open_id }}
      </el-descriptions-item>
    </el-descriptions>

    <el-form label-width="80px">
      <el-form-item label="审批意见">
        <el-input
          v-model="note"
          type="textarea"
          :rows="2"
          maxlength="200"
          show-word-limit
          placeholder="通过可不填；驳回必填"
        />
      </el-form-item>
    </el-form>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="审批通过后会生成候选人确认链接，HR 复制后发给候选人；候选人确认截止时间为次日本地 08:00。"
    />

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="danger" :loading="acting === 'reject'" @click="doReject">驳回</el-button>
      <el-button type="primary" :loading="acting === 'approve'" @click="doApprove">同意</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approveOffer, rejectOffer } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  offer: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'done'])

const note = ref('')
const acting = ref('')

watch(
  () => props.modelValue,
  (open) => {
    if (open) note.value = ''
  },
)

function close() {
  emit('update:modelValue', false)
}

async function doApprove() {
  if (!props.offer?.id) return
  acting.value = 'approve'
  try {
    const res = await approveOffer(props.offer.id, { note: note.value })
    const link = res?.link
    if (link) {
      try {
        await navigator.clipboard.writeText(res.copy_text || link)
        ElMessage.success('已通过审批，链接与话术已复制到剪贴板')
      } catch (e) {
        await ElMessageBox.alert(res.copy_text || link, '已通过审批，请复制发给候选人', {
          confirmButtonText: '知道了',
        })
      }
    } else {
      ElMessage.success('已通过审批')
    }
    emit('done')
    close()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    acting.value = ''
  }
}

async function doReject() {
  if (!props.offer?.id) return
  if (!note.value.trim()) {
    ElMessage.warning('驳回时必须填写审批意见')
    return
  }
  acting.value = 'reject'
  try {
    await rejectOffer(props.offer.id, { note: note.value })
    ElMessage.success('已驳回')
    emit('done')
    close()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    acting.value = ''
  }
}
</script>

<style scoped>
.mb {
  margin-bottom: 16px;
}
</style>
