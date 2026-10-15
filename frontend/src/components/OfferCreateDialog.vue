<template>
  <el-dialog
    :model-value="modelValue"
    :title="isEdit ? '编辑 Offer' : '发起 Offer'"
    width="540px"
    :close-on-click-modal="false"
    @update:model-value="close"
  >
    <el-form :model="form" label-width="104px">
      <el-form-item label="候选人">
        <el-input :model-value="form.candidate_name" disabled placeholder="—" />
      </el-form-item>
      <el-form-item label="应聘岗位" required>
        <el-input v-model="form.job_title" placeholder="默认取岗位名称，可修改" />
      </el-form-item>
      <el-form-item label="报到门店" required>
        <el-input v-model="form.store_name" placeholder="如：昆明南屏店" />
      </el-form-item>
      <el-form-item label="拟报到日期" required>
        <el-date-picker
          v-model="form.report_date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="选择日期"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item label="试用期(天)">
        <el-input-number v-model="form.probation_days" :min="0" :max="365" />
      </el-form-item>
      <el-form-item label="备注">
        <el-input
          v-model="form.note"
          type="textarea"
          :rows="2"
          maxlength="200"
          show-word-limit
          placeholder="如薪资福利说明（可选，薪资不入库）"
        />
      </el-form-item>
      <el-alert
        v-if="!isEdit"
        type="info"
        :closable="false"
        show-icon
        title="提交审批后由审批人在飞书卡片上处理；通过后生成候选人确认链接。"
      />
    </el-form>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button :loading="saving" @click="save(false)">保存草稿</el-button>
      <el-button type="primary" :loading="saving" @click="save(true)">
        保存并提交审批
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { createOffer, updateOffer, submitOfferApproval } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 预填上下文：{ summary_id, candidate_name, job_title, store_name } 或编辑时传整行（含 id/edit:true）
  context: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const saving = ref(false)
const isEdit = computed(() => !!props.context?.edit)

const form = ref({
  candidate_name: '',
  job_title: '',
  store_name: '',
  report_date: '',
  probation_days: 30,
  note: '',
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const c = props.context || {}
    form.value = {
      candidate_name: c.candidate_name || '',
      job_title: c.job_title || '',
      store_name: c.store_name || '',
      report_date: c.report_date || '',
      probation_days: c.probation_days ?? 30,
      note: c.note || '',
    }
  },
)

function close() {
  emit('update:modelValue', false)
}

function validate() {
  if (!form.value.store_name.trim()) {
    ElMessage.warning('请填写报到门店')
    return false
  }
  if (!form.value.report_date) {
    ElMessage.warning('请选择拟报到日期')
    return false
  }
  if (!isEdit.value && !props.context?.summary_id) {
    ElMessage.warning('缺少候选人汇总信息，请从「数据汇总」页发起')
    return false
  }
  return true
}

async function save(submitAfter) {
  if (!validate()) return
  saving.value = true
  try {
    const payload = {
      job_title: form.value.job_title || null,
      store_name: form.value.store_name,
      report_date: form.value.report_date,
      probation_days: form.value.probation_days,
      note: form.value.note,
    }
    let offerId = props.context?.id
    if (isEdit.value) {
      await updateOffer(offerId, payload)
      ElMessage.success('已保存')
    } else {
      const created = await createOffer({ summary_id: props.context.summary_id, ...payload })
      offerId = created.id
      ElMessage.success('Offer 草稿已创建')
    }

    if (submitAfter) {
      const res = await submitOfferApproval(offerId)
      const notify = res?.notify || {}
      if (notify.status === 'failed') {
        ElMessage.warning(`已提交审批，但审批卡片推送失败：${notify.reason || '未知原因'}`)
      } else if (notify.status === 'skipped') {
        ElMessage.warning(`已提交审批，但未推送卡片：${notify.reason || '未配置审批人'}`)
      } else {
        ElMessage.success('已提交审批')
      }
    }
    emit('saved')
    close()
  } catch (e) {
    /* 拦截器已提示错误 */
  } finally {
    saving.value = false
  }
}
</script>
