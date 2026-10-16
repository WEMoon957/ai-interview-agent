/**
 * 请求层单测：CSRF 注入、统一响应解包、401 跳转边界、静默模式。
 *
 * 这些规则直接关系到「后台安全」与「候选人页能否正常打开」，是前端最需要
 * 回归保护的部分：
 * - 写操作必须带上 X-CSRF-Token（后端 double-submit 校验的另一半）；
 * - 后端统一结构 {code, message, data}：code=0 解包 data，否则抛业务文案；
 * - **401 只在后台页面（/console*）跳登录** —— 候选人页 /i/{token}、/o/{token}
 *   是公开页面，被重定向走会导致候选人打不开链接；
 * - silent 请求失败不弹提示（用于"探测登录态"这类预期内会失败的请求）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

// 避免依赖 Element Plus 完整挂载
vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

import { ElMessage } from 'element-plus'

import request, { readCsrfToken } from './request'

/** 取请求拦截器的成功处理函数。 */
function requestHandler() {
  return request.interceptors.request.handlers[0].fulfilled
}

/** 取响应拦截器的成功 / 失败处理函数。 */
function responseHandlers() {
  const h = request.interceptors.response.handlers[0]
  return { onFulfilled: h.fulfilled, onRejected: h.rejected }
}

/** 造一个类似 axios 的错误对象。 */
function httpError(status, message, extra = {}) {
  return {
    message,
    response: { status, data: { message } },
    config: { silent: false, ...extra },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  document.cookie = 'ia_csrf='
})

// ── CSRF ────────────────────────────────────────────────────
describe('readCsrfToken', () => {
  it('从 Cookie 中读出 CSRF 令牌', () => {
    document.cookie = 'ia_csrf=token-abc'
    expect(readCsrfToken()).toBe('token-abc')
  })

  it('没有 Cookie 时返回空串', () => {
    expect(readCsrfToken()).toBe('')
  })

  it('能与其他 Cookie 共存', () => {
    document.cookie = 'other=1'
    document.cookie = 'ia_csrf=xyz'
    expect(readCsrfToken()).toBe('xyz')
  })
})

describe('请求拦截器', () => {
  it('有 CSRF Cookie 时注入 X-CSRF-Token 头', () => {
    document.cookie = 'ia_csrf=csrf-123'
    const config = requestHandler()({ headers: {} })
    expect(config.headers['X-CSRF-Token']).toBe('csrf-123')
  })

  it('没有 CSRF Cookie 时不加该头（避免后端 403）', () => {
    const config = requestHandler()({ headers: {} })
    expect(config.headers['X-CSRF-Token']).toBeUndefined()
  })
})

// ── 统一响应结构 ────────────────────────────────────────────
describe('响应拦截器：业务结构解包', () => {
  it('code=0 时直接返回 data', async () => {
    const { onFulfilled } = responseHandlers()
    const out = await onFulfilled({ data: { code: 0, message: 'ok', data: { id: 7 } } })
    expect(out).toEqual({ id: 7 })
  })

  it('code!=0 时弹提示并抛业务文案', async () => {
    const { onFulfilled } = responseHandlers()
    await expect(
      onFulfilled({ data: { code: 422, message: '最终评分低于门槛' } }),
    ).rejects.toThrow('最终评分低于门槛')
    expect(ElMessage.error).toHaveBeenCalledWith('最终评分低于门槛')
  })

  it('非统一结构（如文件流）原样返回', () => {
    const { onFulfilled } = responseHandlers()
    const raw = { hello: 'world' }
    // 成功分支是同步返回，不是 Promise
    expect(onFulfilled({ data: raw })).toBe(raw)
  })
})

// ── 错误处理与 401 边界 ─────────────────────────────────────
describe('响应拦截器：错误处理', () => {
  it('优先使用后端 message 作为提示语', async () => {
    const { onRejected } = responseHandlers()
    await expect(onRejected(httpError(400, '参数不合法'))).rejects.toThrow('参数不合法')
    expect(ElMessage.error).toHaveBeenCalledWith('参数不合法')
  })

  it('无 response 时回退到网络错误文案', async () => {
    const { onRejected } = responseHandlers()
    await expect(
      onRejected({ message: 'Network Error', config: { silent: false } }),
    ).rejects.toThrow('Network Error')
    expect(ElMessage.error).toHaveBeenCalled()
  })

  it('silent 请求失败时不弹任何提示', async () => {
    const { onRejected } = responseHandlers()
    await expect(
      onRejected(httpError(401, '未登录', { silent: true })),
    ).rejects.toThrow('未登录')
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it('候选人页（/o/xxx）收到 401 不跳转登录，仅提示', async () => {
    // 候选人页是公开页面，被重定向走会导致打不开链接
    const original = window.location.pathname
    window.history.pushState({}, '', '/o/abc123token')
    const { onRejected } = responseHandlers()
    await expect(onRejected(httpError(401, '未登录'))).rejects.toThrow('未登录')
    expect(ElMessage.error).toHaveBeenCalled()
    window.history.pushState({}, '', original)
  })

  it('候选人面试页（/i/xxx）同理不跳转', async () => {
    const original = window.location.pathname
    window.history.pushState({}, '', '/i/abc123token')
    const { onRejected } = responseHandlers()
    await expect(onRejected(httpError(401, '未登录'))).rejects.toThrow('未登录')
    window.history.pushState({}, '', original)
  })
})
