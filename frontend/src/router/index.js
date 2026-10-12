import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/console',
  },
  // 候选人面试页（通过 token 进入）
  {
    path: '/i/:token',
    name: 'interview',
    component: () => import('../views/Interview.vue'),
    meta: { title: '面试' },
  },
  // 管理员后台（前端路由用 /console 前缀，避免与后端 /admin API 冲突）
  {
    path: '/console',
    name: 'admin',
    component: () => import('../views/admin/AdminLayout.vue'),
    redirect: '/console/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('../views/admin/Dashboard.vue'),
        meta: { title: '概览' },
      },
      {
        path: 'jobs',
        name: 'jobs',
        component: () => import('../views/admin/Jobs.vue'),
        meta: { title: '岗位管理' },
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('../views/admin/Templates.vue'),
        meta: { title: '面试模板' },
      },
      {
        path: 'sessions',
        name: 'sessions',
        component: () => import('../views/admin/Sessions.vue'),
        meta: { title: '面试会话' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('../views/admin/Reports.vue'),
        meta: { title: '评分报告' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · 笨二娘隆江猪脚饭` : '笨二娘隆江猪脚饭'
})

export default router
