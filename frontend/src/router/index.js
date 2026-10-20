import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/',
    redirect: '/console',
  },
  // 后台登录页（公开）
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/Login.vue'),
    meta: { title: '登录' },
  },
  // 候选人面试页（通过 token 进入，公开：token 本身即凭证）
  {
    path: '/i/:token',
    name: 'interview',
    component: () => import('../views/Interview.vue'),
    meta: { title: '面试' },
  },
  // 候选人 Offer 确认页（通过 token 进入，公开：无需登录）
  {
    path: '/o/:token',
    name: 'offer-confirm',
    component: () => import('../views/OfferConfirm.vue'),
    meta: { title: 'Offer 确认' },
  },
  // 管理员后台（前端路由用 /console 前缀，避免与后端 /admin API 冲突）
  // 注意：前端守卫只是体验层，真正的访问控制在后端 /admin/* 的鉴权依赖上
  {
    path: '/console',
    name: 'admin',
    component: () => import('../views/admin/AdminLayout.vue'),
    redirect: '/console/dashboard',
    meta: { requiresAuth: true },
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('../views/admin/Dashboard.vue'),
        meta: { title: '概览', roles: ['admin', 'hr'] },
      },
      {
        path: 'jobs',
        name: 'jobs',
        component: () => import('../views/admin/Jobs.vue'),
        meta: { title: '岗位管理', roles: ['admin', 'hr'] },
      },
      {
        path: 'company',
        name: 'company',
        component: () => import('../views/admin/Company.vue'),
        meta: { title: '公司介绍', roles: ['admin', 'hr'] },
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('../views/admin/Templates.vue'),
        meta: { title: '面试模板', roles: ['admin', 'hr'] },
      },
      {
        path: 'screening',
        name: 'screening',
        component: () => import('../views/admin/Screening.vue'),
        meta: { title: '简历初筛', roles: ['admin', 'hr'] },
      },
      {
        path: 'sessions',
        name: 'sessions',
        component: () => import('../views/admin/Sessions.vue'),
        meta: { title: 'AI 面试记录', roles: ['admin', 'hr'] },
      },
      {
        // 店长的核心工作台：三角色都可进
        path: 'offline',
        name: 'offline',
        component: () => import('../views/admin/Offline.vue'),
        meta: { title: '线下复试', roles: ['admin', 'hr', 'store_manager'] },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('../views/admin/Reports.vue'),
        meta: { title: '评分报告', roles: ['admin', 'hr'] },
      },
      {
        path: 'summary',
        name: 'summary',
        component: () => import('../views/admin/Summary.vue'),
        meta: { title: '数据汇总', roles: ['admin', 'hr'] },
      },
      {
        path: 'offers',
        name: 'offers',
        component: () => import('../views/admin/Offers.vue'),
        meta: { title: '录用 / 入职', roles: ['admin', 'hr'] },
      },
      {
        // 老板操作台：终试决策 + 录用审批，老板的最后拍板环节
        path: 'boss',
        name: 'boss',
        component: () => import('../views/admin/BossConsole.vue'),
        meta: { title: '老板操作台', roles: ['admin', 'boss'] },
      },
      {
        // 妙记个人授权：应用身份只能读公开妙记，授权后可读私有妙记
        path: 'minutes-auth',
        name: 'minutes-auth',
        component: () => import('../views/admin/MinutesAuth.vue'),
        meta: { title: '妙记授权', roles: ['admin', 'hr'] },
      },
      {
        // 账号管理：建号/停用/重置密码/解锁，仅管理员
        path: 'accounts',
        name: 'accounts',
        component: () => import('../views/admin/Accounts.vue'),
        meta: { title: '账号管理', roles: ['admin'] },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局守卫：后台页面需要登录，登录页与候选人页（/i、/o）保持公开。
//
// 重要：前端守卫只是体验层拦截（避免闪出后台界面再报错），
// **真正的访问控制在后端** —— 所有 /admin/* 接口都挂了 require_user 依赖。
router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.meta?.requiresAuth || to.meta?.roles) {
    // 刷新页面后 store 是空的，先向后端确认一次登录态（静默，不弹错误提示）
    if (!auth.initialized) {
      await auth.fetchMe()
    }
    if (!auth.isLoggedIn) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    // 角色不符：送到该角色能看的第一个页面，而不是把人挡在门外
    const allowed = to.meta?.roles
    if (allowed && !allowed.includes(auth.user?.role)) {
      const role = auth.user?.role
      const home =
        role === 'boss'
          ? '/console/boss'
          : role === 'store_manager'
            ? '/console/offline'
            : '/console/dashboard'
      return { path: home }
    }
  }

  // 已登录用户访问登录页时直接送进后台（老板送操作台，店长送线下复试，其余送概览）
  if (to.name === 'login' && auth.isLoggedIn) {
    const role = auth.user?.role
    const home =
      role === 'boss'
        ? '/console/boss'
        : role === 'store_manager'
          ? '/console/offline'
          : '/console/dashboard'
    return { path: home }
  }

  return true
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · 笨二娘隆江猪脚饭` : '笨二娘隆江猪脚饭'
})

export default router
