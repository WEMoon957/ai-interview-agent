import axios from 'axios'
import { ElMessage } from 'element-plus'

// 登录态放在 HttpOnly Cookie 里，前端读不到、也不需要读。
// withCredentials 仅跨域场景需要（同域部署时浏览器会自动携带 Cookie）。
const request = axios.create({
  baseURL: '',
  timeout: 120000,
  withCredentials: true,
})

// CSRF Cookie 名，需与后端 app/services/auth.py 的 CSRF_COOKIE_NAME 保持一致
const CSRF_COOKIE = 'ia_csrf'

/** 读取非 HttpOnly 的 CSRF Cookie（后端 double-submit 方案的另一半）。 */
function readCsrfToken() {
  const match = document.cookie.match(new RegExp('(?:^|;\\s*)' + CSRF_COOKIE + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : ''
}

// 请求拦截：写操作带上 CSRF 头。
// 后端只在 POST/PUT/PATCH/DELETE 上校验，GET 带上也无害。
request.interceptors.request.use((config) => {
  const csrf = readCsrfToken()
  if (csrf) {
    config.headers = config.headers || {}
    config.headers['X-CSRF-Token'] = csrf
  }
  return config
})

/**
 * 登录失效跳转。
 *
 * 只在后台页面（/console*）触发：候选人页 `/i/{token}`、`/o/{token}` 是
 * 公开页面，它们收到的 401 与后台登录无关，不能被重定向走。
 */
function handleSessionExpired() {
  if (!window.location.pathname.startsWith('/console')) return
  const redirect = encodeURIComponent(window.location.pathname + window.location.search)
  window.location.replace(`/login?redirect=${redirect}`)
}

// 响应拦截：统一处理后端 {code, message, data} 结构
request.interceptors.response.use(
  (response) => {
    const res = response.data
    // 若后端返回统一结构
    if (res && typeof res === 'object' && 'code' in res) {
      if (res.code === 0) {
        return res.data
      }
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message || '请求失败'))
    }
    return res
  },
  (error) => {
    const status = error.response?.status
    const msg =
      error.response?.data?.message || error.message || '网络错误，请检查后端服务是否启动'

    // 静默模式：用于"探测登录态"这类预期内会失败的请求，不弹提示打扰用户
    const silent = error.config?.silent === true

    if (status === 401 && !silent) {
      ElMessage.error(msg)
      handleSessionExpired()
    } else if (!silent) {
      ElMessage.error(msg)
    }

    // 统一抛出业务文案，避免调用方只能拿到 "Request failed with status code xxx"
    return Promise.reject(new Error(msg))
  },
)

export default request
