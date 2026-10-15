<template>
  <el-dialog
    :model-value="modelValue"
    title="办理入职"
    width="520px"
    :close-on-click-modal="false"
    @update:model-value="close"
  >
    <el-descriptions v-if="offer" :column="1" border size="small" class="mb">
      <el-descriptions-item label="候选人">{{ offer.candidate_name }}</el-descriptions-item>
      <el-descriptions-item label="岗位">{{ offer.job_title }}</el-descriptions-item>
      <el-descriptions-item label="Offer 门店">{{ offer.store_name }}</el-descriptions-item>
      <el-descriptions-item label="拟报到日期">{{ offer.report_date || '—' }}</el-descriptions-item>
    </el-descriptions>

    <el-form :model="form" label-width="104px">
      <el-form-item label="入职结果" required>
        <el-radio-group v-model="form.result">
          <el-radio-button label="onboarded">已报到</el-radio-button>
          <el-radio-button label="no_show">未报到</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="实际报到日期">
        <el-date-picker
          v-model="form.actual_report_date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="默认取拟报到日期"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item label="报到门店">
        <el-input v-model="form.onboard_store" placeholder="默认取 Offer 门店" />
      </el-form-item>
      <el-form-item v-if="form.result === 'no_show'" label="未报到说明" required>
        <el-input
          v-model="form.note"
          type="textarea"
          :rows="2"
          maxlength="200"
          show-word-limit
          placeholder="必填，如：电话联系不上 / 临时反悔"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">确认提交</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { onboardOffer } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  offer: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'done'])

const saving = ref(false)
const form = ref({
  result: 'onboarded',
  actual_report_date: '',
  onboard_store: '',
  note: '',
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    form.value = {
      result: 'onboarded',
      actual_report_date: props.offer?.report_date || '',
      onboard_store: props.offer?.store_name || '',
      note: '',
    }
  },
)

function close() {
  emit('update:modelValue', false)
}

async function save() {
  if (!props.offer?.id) return
  if (form.value.result === 'no_show' && !form.value.note.trim()) {
    ElMessage.warning('标记「未报到」时必须填写说明')
    return
  }
  saving.value = true
  try {
    await onboardOffer(props.offer.id, {
      result: form.value.result,
      actual_report_date: form.value.actual_report_date || null,
      onboard_store: form.value.onboard_store,
      note: form.value.note,
    })
    ElMessage.success(form.value.result === 'onboarded' ? '已标记入职' : '已标记未报到')
    emit('done')
    close()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.mb {
  margin-bottom: 16px;
}
</style>
