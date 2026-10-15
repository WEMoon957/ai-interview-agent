import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authLogin, authLogout, authMe } from '../api'

/**
 * 后台登录态。
 *
 * 说明：
 * - 真正的凭据是浏览器里的 HttpOnly 会话 Cookie，本 store 只缓存
 *   "当前是谁"用于界面渲染，**不是**权限判定依据 —— 权限一律由后端判定。
 * - `initialized` 表示是否已尝试过恢复登录态。路由守卫靠它决定要不要
 *   在刷新页面后再打一次 /auth/me，避免每次跳转都发请求。
 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const initialized = ref(false)
  const loading = ref(false)

  const isLoggedIn = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const displayName = computed(() => user.value?.display_name || user.value?.username || '')

  /** 拉取当前登录用户（未登录时静默返回 null，不弹错误提示）。 */
  async function fetchMe() {
    try {
      user.value = await authMe()
    } catch (err) {
      user.value = null
    } finally {
      initialized.value = true
    }
    return user.value
  }

  /** 账号密码登录；失败时异常由调用方（登录页）处理。 */
  async function login(username, password) {
    loading.value = true
    try {
      const data = await authLogin({ username, password })
      user.value = data.user
      initialized.value = true
      return data.user
    } finally {
      loading.value = false
    }
  }

  /** 退出登录：即使请求失败（会话已过期）也要清空本地状态。 */
  async function logout() {
    try {
      await authLogout()
    } catch (err) {
      // 会话可能已经失效，忽略即可
    } finally {
      reset()
    }
  }

  /** 清空本地登录态（不调用后端）。 */
  function reset() {
    user.value = null
    initialized.value = true
  }

  return {
    user,
    initialized,
    loading,
    isLoggedIn,
    isAdmin,
    displayName,
    fetchMe,
    login,
    logout,
    reset,
  }
})
