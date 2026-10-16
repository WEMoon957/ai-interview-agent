<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">笨二娘隆江猪脚饭</div>
      <div class="subtitle">招聘管理系统 · 后台登录</div>

      <!-- 登录失败常驻提示：原来只有一闪而过的提示，HR 容易以为是自己点歪了 -->
      <el-alert
        v-if="loginError"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
        :title="loginError"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="onSubmit"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            size="large"
            placeholder="请输入管理员发给你的账号"
            autocomplete="username"
            :prefix-icon="User"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            size="large"
            placeholder="密码"
            show-password
            autocomplete="current-password"
            :prefix-icon="Lock"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          class="submit"
          :loading="auth.loading"
          @click="onSubmit"
        >
          登 录
        </el-button>
      </el-form>

      <p class="hint">账号由管理员开通；忘记密码请联系管理员重置</p>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'

import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref(null)
const form = reactive({ username: '', password: '' })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

/**
 * 解析登录后跳转地址。
 *
 * 只接受站内绝对路径，挡掉 `//evil.com` 这类开放重定向：
 * 攻击者可以伪造 `/login?redirect=//evil.com` 把用户带去钓鱼站。
 */
function resolveRedirect(value) {
  if (typeof value !== 'string') return '/console/dashboard'
  if (!value.startsWith('/') || value.startsWith('//')) return '/console/dashboard'
  return value
}

// 登录失败原因（常驻展示，避免一闪而过）
const loginError = ref('')

async function onSubmit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  loginError.value = ''
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    router.replace(resolveRedirect(route.query.redirect))
  } catch (err) {
    // 后端不区分"账号不存在"与"密码错误"，这里统一提示
    loginError.value = err?.message || '登录失败，请检查账号密码后重试'
    form.password = ''
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #1f2937;
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: #fff;
  border-radius: 12px;
  padding: 36px 32px 26px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
}

.brand {
  font-size: 20px;
  font-weight: 600;
  color: #1f2937;
  text-align: center;
  letter-spacing: 1px;
}

.subtitle {
  margin: 8px 0 26px;
  font-size: 13px;
  color: #6b7280;
  text-align: center;
}

.submit {
  width: 100%;
  margin-top: 4px;
}

.hint {
  margin-top: 20px;
  font-size: 12px;
  line-height: 1.6;
  color: #9ca3af;
  text-align: center;
}
</style>
