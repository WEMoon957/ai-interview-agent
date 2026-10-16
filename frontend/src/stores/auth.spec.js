/**
 * 后台登录态 store 单测。
 *
 * 重点保护：
 * - ``fetchMe`` 失败必须**静默**（返回 null 且不抛异常）—— 否则刷新页面/未登录
 *   访问就会抛错，路由守卫与页面渲染都会被打断；
 * - ``fetchMe`` 失败也要把 initialized 置 true（"尝试过"），否则路由守卫会反复请求；
 * - ``login`` 失败时 loading 必须复位（否则按钮永久转圈）；
 * - ``logout`` 即使后端失败也要清空本地状态（会话已过期是常态）；
 * - ``displayName`` 在无 display_name 时回退 username。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  authLogin: vi.fn(),
  authLogout: vi.fn(),
  authMe: vi.fn(),
}))

import { authLogin, authLogout, authMe } from '../api'
import { useAuthStore } from './auth'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('初始状态', () => {
  it('未登录且未初始化', () => {
    const s = useAuthStore()
    expect(s.isLoggedIn).toBe(false)
    expect(s.initialized).toBe(false)
    expect(s.isAdmin).toBe(false)
    expect(s.displayName).toBe('')
  })
})

describe('fetchMe', () => {
  it('成功时写入用户并标记已初始化', async () => {
    authMe.mockResolvedValue({
      username: 'admin001',
      role: 'admin',
      display_name: '李俊颖',
    })
    const s = useAuthStore()
    await s.fetchMe()

    expect(s.isLoggedIn).toBe(true)
    expect(s.isAdmin).toBe(true)
    expect(s.displayName).toBe('李俊颖')
    expect(s.initialized).toBe(true)
  })

  it('失败时静默返回 null，不抛异常', async () => {
    authMe.mockRejectedValue(new Error('未登录'))
    const s = useAuthStore()

    const out = await s.fetchMe() // 不应抛
    expect(out).toBeNull()
    expect(s.isLoggedIn).toBe(false)
    // 关键：失败也算"尝试过"，否则路由守卫会反复请求 /auth/me
    expect(s.initialized).toBe(true)
  })

  it('displayName 无 display_name 时回退 username', async () => {
    authMe.mockResolvedValue({ username: 'hr01', role: 'hr' })
    const s = useAuthStore()
    await s.fetchMe()
    expect(s.displayName).toBe('hr01')
  })
})

describe('login', () => {
  it('成功时写入用户、复位 loading、返回 user', async () => {
    authLogin.mockResolvedValue({ user: { username: 'hr01', role: 'hr' } })
    const s = useAuthStore()

    const user = await s.login('hr01', 'pw')
    expect(user.username).toBe('hr01')
    expect(s.isLoggedIn).toBe(true)
    expect(s.isAdmin).toBe(false)
    expect(s.loading).toBe(false)
  })

  it('失败时抛出异常、复位 loading、保持未登录', async () => {
    authLogin.mockRejectedValue(new Error('用户名或密码错误'))
    const s = useAuthStore()

    await expect(s.login('x', 'y')).rejects.toThrow('用户名或密码错误')
    expect(s.loading).toBe(false) // 否则登录按钮永久转圈
    expect(s.isLoggedIn).toBe(false)
  })
})

describe('logout', () => {
  it('后端成功时清空本地状态', async () => {
    authMe.mockResolvedValue({ username: 'a', role: 'hr' })
    const s = useAuthStore()
    await s.fetchMe()
    expect(s.isLoggedIn).toBe(true)

    authLogout.mockResolvedValue({})
    await s.logout()
    expect(s.isLoggedIn).toBe(false)
  })

  it('后端失败（会话已过期）也必须清空本地状态', async () => {
    authMe.mockResolvedValue({ username: 'a', role: 'hr' })
    const s = useAuthStore()
    await s.fetchMe()

    authLogout.mockRejectedValue(new Error('会话已过期'))
    await s.logout() // 不应抛
    expect(s.isLoggedIn).toBe(false)
  })
})

describe('reset', () => {
  it('清空登录态且不动后端', async () => {
    authMe.mockResolvedValue({ username: 'a', role: 'admin' })
    const s = useAuthStore()
    await s.fetchMe()

    s.reset()
    expect(s.isLoggedIn).toBe(false)
    expect(s.initialized).toBe(true)
    expect(authLogout).not.toHaveBeenCalled()
  })
})
