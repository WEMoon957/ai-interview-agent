/**
 * 候选人 Offer 确认页单测。
 *
 * 该页今天改成了正式《录用通知书》版式，这里守住几条关键约定：
 * - **固定文案**完整渲染：公司名、5 项报到携带材料、法律声明；
 * - **HR 填写字段**正确显示：工作地点 / 联系人 / 薪资 / 报到时间；
 * - **不写称呼**：开头直接是「姓名：」（不做「先生/女士」推断）；
 * - 各状态分支（待确认 / 已接受 / 已婉拒 / 已过期 / 链接无效）文案正确，
 *   且公开页不显示管理端才有的信息；
 * - 点「接受」调用正确的接口。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { token: 'tok-1' } }),
}))

vi.mock('../api', () => ({
  getOfferContext: vi.fn(),
  acceptOffer: vi.fn(),
  declineOffer: vi.fn(),
}))

import { acceptOffer, getOfferContext } from '../api'
import OfferConfirm from './OfferConfirm.vue'

/** 后端 /offer/{token} 返回的 letter（与 services/offer_letter.py 一致）。 */
const LETTER = {
  company_name: '昆明笨二娘餐饮管理有限公司',
  title: '录用通知书',
  candidate_name: '李俊颖',
  job_title: 'AI落地工程师',
  work_location: '昆明市西山区万达北塔2507',
  contact_name: '方玺',
  contact_phone: '17702348593',
  probation_phrase: '试用期为两个月',
  probation_suffix: '，可视情况提前转正',
  probation_salary: '固定8500元',
  regular_salary: '10000元/月',
  report_date: '2026年9月27日',
  report_time: '上午9:00',
  materials: [
    '身份证原件',
    '毕业证原件',
    '本人中国银行银行卡',
    '原单位离职证明',
    '近三个月内体检报告一份（需包含常规内外科检查、肝功能、血常规、尿常规、胸部X光、甲功三项等）',
  ],
  legal_note: '本通知为录用确认，具体权利义务以正式劳动合同为准。',
  deadline: '2026年9月23日20:00',
  letter_date: '2026年9月23日',
}

async function mountWith(data) {
  getOfferContext.mockResolvedValue(data)
  const wrapper = mount(OfferConfirm)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

// ── 待确认：通知书版式 ──────────────────────────────────────
describe('待确认（通知书版式）', () => {
  it('渲染标题与「姓名：」开头（不写称呼）', async () => {
    const w = await mountWith({ status: 'sent', letter: LETTER })
    expect(w.text()).toContain('录用通知书')
    expect(w.text()).toContain('李俊颖：')
    // 约定：不生成「先生 / 女士」称呼
    expect(w.text()).not.toContain('先生')
    expect(w.text()).not.toContain('女士')
  })

  it('渲染 HR 填写的全部字段', async () => {
    const w = await mountWith({ status: 'sent', letter: LETTER })
    const text = w.text()
    expect(text).toContain('AI落地工程师')
    expect(text).toContain('昆明市西山区万达北塔2507')
    expect(text).toContain('方玺')
    expect(text).toContain('17702348593')
    expect(text).toContain('试用期为两个月')
    expect(text).toContain('可视情况提前转正')
    expect(text).toContain('固定8500元')
    expect(text).toContain('10000元/月')
    expect(text).toContain('2026年9月27日')
    expect(text).toContain('上午9:00')
  })

  it('渲染 5 项报到携带材料', async () => {
    const w = await mountWith({ status: 'sent', letter: LETTER })
    const text = w.text()
    expect(text).toContain('报到时请携带以下资料')
    expect(text).toContain('身份证原件')
    expect(text).toContain('毕业证原件')
    expect(text).toContain('本人中国银行银行卡')
    expect(text).toContain('原单位离职证明')
    expect(text).toContain('体检报告')
  })

  it('渲染固定文案：公司名、法律声明、确认截止时间、落款日期', async () => {
    const w = await mountWith({ status: 'sent', letter: LETTER })
    const text = w.text()
    expect(text).toContain('昆明笨二娘餐饮管理有限公司')
    expect(text).toContain('本通知为录用确认，具体权利义务以正式劳动合同为准。')
    expect(text).toContain('2026年9月23日20:00')
    expect(text).toContain('逾期视为放弃')
    expect(text).toContain('2026年9月23日') // 落款日期
  })

  it('缺字段时对应行省略而不报错', async () => {
    const partial = { ...LETTER, work_location: '', contact_name: '', report_time: '' }
    const w = await mountWith({ status: 'sent', letter: partial })
    expect(w.text()).toContain('录用通知书') // 仍能渲染
    expect(w.text()).not.toContain('工作地点')
  })

  it('letter 缺失时页面不崩（存量数据兼容）', async () => {
    const w = await mountWith({ status: 'sent', job_title: '服务员' })
    expect(w.text()).toContain('服务员')
    expect(w.exists()).toBe(true)
  })

  it('填邮箱后点「接受 Offer」调用 acceptOffer(token, email)', async () => {
    acceptOffer.mockResolvedValue({ status: 'accepted' })
    const w = await mountWith({ status: 'sent', letter: LETTER })
    const emailInput = w.find('input[type="email"]')
    expect(emailInput.exists()).toBe(true)
    await emailInput.setValue('li@example.com')
    const btn = w.findAll('button').find((b) => b.text().includes('接受'))
    expect(btn).toBeTruthy()
    await btn.trigger('click')
    await flushPromises()
    expect(acceptOffer).toHaveBeenCalledWith('tok-1', 'li@example.com')
  })

  it('未填邮箱点接受时拦截并提示', async () => {
    acceptOffer.mockResolvedValue({ status: 'accepted' })
    const w = await mountWith({ status: 'sent', letter: LETTER })
    const btn = w.findAll('button').find((b) => b.text().includes('接受'))
    await btn.trigger('click')
    await flushPromises()
    expect(acceptOffer).not.toHaveBeenCalled()
    expect(w.text()).toContain('请先填写接收通知的邮箱')
  })

  it('邮箱格式不正确时拦截并提示', async () => {
    acceptOffer.mockResolvedValue({ status: 'accepted' })
    const w = await mountWith({ status: 'sent', letter: LETTER })
    const emailInput = w.find('input[type="email"]')
    await emailInput.setValue('not-an-email')
    const btn = w.findAll('button').find((b) => b.text().includes('接受'))
    await btn.trigger('click')
    await flushPromises()
    expect(acceptOffer).not.toHaveBeenCalled()
    expect(w.text()).toContain('邮箱格式不正确')
  })
})

// ── 其他状态分支 ────────────────────────────────────────────
describe('状态分支', () => {
  it('已接受显示欢迎文案', async () => {
    const w = await mountWith({ status: 'accepted', job_title: '服务员' })
    expect(w.text()).toContain('欢迎加入')
  })

  it('已婉拒显示感谢文案', async () => {
    const w = await mountWith({ status: 'declined' })
    expect(w.text()).toContain('感谢')
  })

  it('已过期显示过期提示', async () => {
    const w = await mountWith({ status: 'expired' })
    expect(w.text()).toContain('已过期')
  })

  it('待审批等不可操作状态给出兜底文案', async () => {
    const w = await mountWith({ status: 'pending_approval', status_label: '待审批' })
    expect(w.text()).toContain('无法操作')
  })

  it('接口失败显示链接无效', async () => {
    getOfferContext.mockRejectedValue(new Error('链接无效或已失效'))
    const w = mount(OfferConfirm)
    await flushPromises()
    expect(w.text()).toContain('链接无效')
  })
})
