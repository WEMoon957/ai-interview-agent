import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // ── 单元测试（Vitest）────────────────────────────────
  // 环境用 happy-dom：轻量 DOM 实现，足以支撑组件挂载与事件断言。
  // 用法：npm test（单次）/ npm run test:watch（监听）
  test: {
    environment: 'happy-dom',
    globals: true,
    include: ['src/**/*.spec.js'],
    // 不处理 CSS，避免元素样式相关的解析开销与告警
    css: false,
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    // 允许 cloudflared 内网穿透的公网域名访问（vite 默认只允许 localhost）
    allowedHosts: [
      'additions-pink-supply-ton.trycloudflare.com',
      '.trycloudflare.com', // 允许所有 cloudflare 临时域名
    ],
    proxy: {
      // 后台登录鉴权（服务端会话，Cookie 需同源，故走代理）
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/interview': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Offer 候选人确认页（/o/{token}）的数据接口
      '/offer': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
