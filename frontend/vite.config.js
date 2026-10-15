import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    // 允许 cloudflared 内网穿透的公网域名访问（vite 默认只允许 localhost）
    allowedHosts: [
      'additions-pink-supply-ton.trycloudflare.com',
      '.trycloudflare.com', // 允许所有 cloudflare 临时域名
    ],
    proxy: {
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
