/**
 * 后台「发起 / 编辑 Offer」弹窗单测。
 *
 * 重点：今天给表单加了「录用通知书内容」分组（工作地点 / 联系人 / 联系电话 /
 * 试用期薪资 / 转正后薪资 / 报到时间）。这些字段必须真的进入提交 payload，
 * 否则候选人打开的《录用通知书》会是空的 —— 这是本组测试要守住的核心。
 *
 * 编辑模式（context.edit = true）自带 store_name / report_date 预填，
 * 因此可以绕过日期选择器直接验证 payload。
 */

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  createOffer: vi.fn(),
  updateOffer: vi.fn(),
  submitOfferApproval: vi.fn(),
}))

import { createOffer, submitOfferApproval, updateOffer } from '../api'
import OfferCreateDialog from './OfferCreateDialog.vue'

// 说明：report_date 真实使用时由用户在日期选择器里选；测试直接给 context，
// 是为了绕开 el-date-picker 的交互（该值会被组件的 watch 回填进表单）。
const NEW_CONTEXT = {
  summary_id: 'sum-1',
  candidate_name: '张三',
  job_title: '前厅服务员',
  store_name: '昆明南屏店',
  report_date: '2026-10-20',
}

const EDIT_CONTEXT = {
  ...NEW_CONTEXT,
  id: 'offer-1',
  edit: true,
  report_date: '2026-10-20',
  probation_days: 30,
}

/**
 * 挂载弹窗。
 *
 * 组件用 ``watch(modelValue)`` 在**打开那一刻**回填表单，因此测试必须模拟
 * "先关闭、再打开"的真实用法；直接以 modelValue=true 挂载时 watch 不触发，
 * 表单会是空的（门店/日期为空 → 校验失败 → 不发请求）。
 */
function mountDialog(context) {
  const w = mount(OfferCreateDialog, {
    props: { modelValue: false, context },
    global: { plugins: [ElementPlus] },
  })
  w.setProps({ modelValue: true })
  return w
}

/** 按 placeholder 定位输入框（比按顺序取更稳）。 */
async function fillByPlaceholder(w, placeholderPart, value) {
  const input = w
    .findAll('input')
    .find((i) => (i.attributes('placeholder') || '').includes(placeholderPart))
  expect(input, `未找到 placeholder 含「${placeholderPart}」的输入框`).toBeTruthy()
  await input.setValue(value)
}

async function clickButton(w, label) {
  const btn = w.findAll('button').find((b) => b.text().includes(label))
  expect(btn, `未找到按钮「${label}」`).toBeTruthy()
  await btn.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  createOffer.mockResolvedValue({ id: 'offer-1' })
  updateOffer.mockResolvedValue({})
  submitOfferApproval.mockResolvedValue({ notify: { status: 'sent' } })
})

describe('表单渲染', () => {
  it('包含「录用通知书内容」分组的 6 个字段', async () => {
    const w = mountDialog(NEW_CONTEXT)
    await flushPromises()
    const text = w.text()
    expect(text).toContain('录用通知书内容')
    expect(text).toContain('工作地点')
    expect(text).toContain('联系人')
    expect(text).toContain('联系电话')
    expect(text).toContain('试用期薪资')
    expect(text).toContain('转正后薪资')
    expect(text).toContain('报到时间')
  })

  it('候选人姓名与门店可预填到输入框', async () => {
    const w = mountDialog(NEW_CONTEXT)
    await flushPromises()
    // 注意：el-input 的值在 input.value 里，wrapper.text() 看不到
    const values = w.findAll('input').map((i) => i.element.value)
    expect(values).toContain('张三')
    expect(values).toContain('昆明南屏店')
  })

  it('编辑模式回填通知书字段', async () => {
    const w = mountDialog({
      ...EDIT_CONTEXT,
      work_location: '昆明市西山区万达北塔2507',
      contact_name: '方玺',
    })
    await flushPromises()
    const values = w.findAll('input').map((i) => i.element.value)
    expect(values).toContain('昆明市西山区万达北塔2507')
    expect(values).toContain('方玺')
  })
})

describe('提交 payload', () => {
  it('6 个通知书字段全部进入创建请求', async () => {
    const w = mountDialog(NEW_CONTEXT)
    await flushPromises()

    await fillByPlaceholder(w, '昆明市西山区', '昆明市西山区万达北塔2507')
    await fillByPlaceholder(w, '方玺', '方玺')
    await fillByPlaceholder(w, '17702348593', '17702348593')
    await fillByPlaceholder(w, '固定 8500', '固定8500元')
    await fillByPlaceholder(w, '10000 元/月', '10000元/月')
    await fillByPlaceholder(w, '上午 9:00', '上午9:00')

    await clickButton(w, '保存草稿')

    expect(createOffer).toHaveBeenCalledTimes(1)
    const payload = createOffer.mock.calls[0][0]
    expect(payload.summary_id).toBe('sum-1')
    expect(payload.work_location).toBe('昆明市西山区万达北塔2507')
    expect(payload.contact_name).toBe('方玺')
    expect(payload.contact_phone).toBe('17702348593')
    expect(payload.probation_salary).toBe('固定8500元')
    expect(payload.regular_salary).toBe('10000元/月')
    expect(payload.report_time).toBe('上午9:00')
  })

  it('编辑模式走更新接口并带上通知书字段', async () => {
    const w = mountDialog(EDIT_CONTEXT)
    await flushPromises()

    await fillByPlaceholder(w, '昆明市西山区', '新地点')
    await clickButton(w, '保存草稿')

    expect(updateOffer).toHaveBeenCalledTimes(1)
    const [offerId, payload] = updateOffer.mock.calls[0]
    expect(offerId).toBe('offer-1')
    expect(payload.work_location).toBe('新地点')
    // 未填写的字段也应显式传空串，避免后端保留旧值
    expect(payload).toHaveProperty('contact_phone', '')
  })

  it('未填门店时拦截并提示，不发请求', async () => {
    const w = mountDialog({ summary_id: 'sum-1', candidate_name: '张三' })
    await flushPromises()
    await clickButton(w, '保存草稿')
    expect(createOffer).not.toHaveBeenCalled()
  })

  it('「提交审批」在保存成功后调用审批接口', async () => {
    const w = mountDialog(EDIT_CONTEXT)
    await flushPromises()
    await clickButton(w, '提交审批')
    expect(updateOffer).toHaveBeenCalled()
    expect(submitOfferApproval).toHaveBeenCalledWith('offer-1')
  })
})
