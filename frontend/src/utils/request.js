import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '',
  timeout: 120000,
})

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
    const msg =
      error.response?.data?.message || error.message || '网络错误，请检查后端服务是否启动'
    ElMessage.error(msg)
    // 统一抛出业务文案，避免调用方只能拿到 "Request failed with status code xxx"
    return Promise.reject(new Error(msg))
  },
)

export default request
