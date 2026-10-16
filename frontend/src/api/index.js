import request from '../utils/request'

// ── 管理端 ─────────────────────────────────────────
export const listJobs = () => request.get('/admin/jobs')

export const createJob = (data) => request.post('/admin/jobs', data)

export const updateJob = (jobId, data) => request.put(`/admin/jobs/${jobId}`, data)

export const deleteJob = (jobId) => request.delete(`/admin/jobs/${jobId}`)

export const listTemplates = () => request.get('/admin/templates')

export const createTemplate = (data) => request.post('/admin/templates', data)

export const updateTemplate = (templateId, data) =>
  request.put(`/admin/templates/${templateId}`, data)

export const deleteTemplate = (templateId) => request.delete(`/admin/templates/${templateId}`)

export const createSession = (formData) =>
  request.post('/admin/sessions', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

export const listSessions = (params) => request.get('/admin/sessions', { params })

export const deleteSession = (sessionId) => request.delete(`/admin/sessions/${sessionId}`)

export const getReport = (sessionId) => request.get(`/admin/sessions/${sessionId}/report`)

// ── 公司文化档案（全局单例：一篇公司介绍文字）────────────
// 发起面试时把这篇文字快照进会话，候选人开场白自动带上它
export const getCompanyProfile = () => request.get('/admin/company-profile')

export const updateCompanyProfile = (data) => request.put('/admin/company-profile', data)

// ── 候选人端 ───────────────────────────────────────
export const getInterviewContext = (token) => request.get(`/interview/${token}`)

export const agreeConsent = (token) => request.post(`/interview/${token}/consent`)

export const submitAnswer = (token, answer) =>
  request.post(`/interview/${token}/answer`, { answer })

// ── 评分查询（供外部系统）──────────────────────────
export const getScoreByCandidate = (candidateId) =>
  request.get(`/api/scores/candidate/${candidateId}`)

// ── 简历初筛（岗位文件夹上传 → AI 打分排序 → 发面试）────
export const uploadResumes = (formData) =>
  request.post('/admin/screenings/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

export const listScreenings = (params) => request.get('/admin/screenings', { params })

export const getScreening = (screeningId) => request.get(`/admin/screenings/${screeningId}`)

export const inviteScreenings = (data) => request.post('/admin/screenings/invite', data)

// 手动把「推荐进入 AI 面试」名单推送 HR 飞书
export const notifyScreenings = (data) => request.post('/admin/screenings/notify', data)

export const deleteScreening = (screeningId) =>
  request.delete(`/admin/screenings/${screeningId}`)

// ── 线下复试（HR/店长约面 + 打分）────────────────────
export const createOfflineInterview = (data) => request.post('/admin/offline-interviews', data)

export const listOfflineInterviews = (params) =>
  request.get('/admin/offline-interviews', { params })

export const updateOfflineInterview = (id, data) =>
  request.put(`/admin/offline-interviews/${id}`, data)

export const scoreOfflineInterview = (id, data) =>
  request.post(`/admin/offline-interviews/${id}/score`, data)

export const deleteOfflineInterview = (id) =>
  request.delete(`/admin/offline-interviews/${id}`)

// 到店直招：一次创建候选人（姓名/手机/来源渠道）+ 线下复试记录，可选带面试录音
export const createWalkInInterview = (formData) =>
  request.post('/admin/offline-interviews/walk-in', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

// 上传 / 替换整场面试录音
export const uploadOfflineAudio = (id, formData) =>
  request.post(`/admin/offline-interviews/${id}/audio`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

// 录音播放 / 下载地址（默认内联播放，download=true 强制下载）
export const offlineAudioUrl = (id, download = false) =>
  `/admin/offline-interviews/${id}/audio${download ? '?download=true' : ''}`

// ── 飞书妙记录音节点：手动绑定妙记 / 采纳 AI 参考分 / 手动触发转写 ──
export const bindMinutes = (id, data) =>
  request.post(`/admin/offline-interviews/${id}/bind-minutes`, data)

export const adoptAiScore = (id, data) =>
  request.post(`/admin/offline-interviews/${id}/adopt-ai-score`, data)

export const transcribeOffline = (id) =>
  request.post(`/admin/offline-interviews/${id}/transcribe`)

// ── 招聘汇总（全链路聚合 + 落库）────────────────────
export const getSummary = (params) => request.get('/admin/summary', { params })

export const rebuildSummary = (params) => request.post('/admin/summary/rebuild', null, { params })

// 手动把已完成的汇总结果推送 HR 飞书（补推，忽略幂等）
export const notifySummary = (params) => request.post('/admin/summary/notify', null, { params })

export const listSummarySnapshots = (params) =>
  request.get('/admin/summary/snapshots', { params })

// ── Offer / 入职闭环（第⑤阶段）──────────────────────
export const listOffers = (params) => request.get('/admin/offers', { params })

export const getOffer = (offerId) => request.get(`/admin/offers/${offerId}`)

export const getOfferStats = (params) => request.get('/admin/offers/stats', { params })

export const createOffer = (data) => request.post('/admin/offers', data)

export const updateOffer = (offerId, data) => request.patch(`/admin/offers/${offerId}`, data)

// 提交审批（draft / rejected → 待审批，推审批卡片给审批人）
export const submitOfferApproval = (offerId) =>
  request.post(`/admin/offers/${offerId}/submit-approval`)

// 审批通过（后台兜底入口；通过后生成候选人确认链接）
export const approveOffer = (offerId, data) =>
  request.post(`/admin/offers/${offerId}/approve`, data || {})

export const rejectOffer = (offerId, data) =>
  request.post(`/admin/offers/${offerId}/reject`, data)

export const withdrawOffer = (offerId, data) =>
  request.post(`/admin/offers/${offerId}/withdraw`, data || {})

// 办理入职：result = onboarded（已报到）/ no_show（未报到，note 必填）
export const onboardOffer = (offerId, data) =>
  request.post(`/admin/offers/${offerId}/onboard`, data)

// 候选人端（token 即凭证）
export const getOfferContext = (token) => request.get(`/offer/${token}`)

export const acceptOffer = (token) => request.post(`/offer/${token}/accept`)

export const declineOffer = (token, data) => request.post(`/offer/${token}/decline`, data || {})

// ── 后台登录鉴权（安全改造）──────────────────────────
// 会话凭据由后端写入 HttpOnly Cookie，前端不接触 token
export const authLogin = (data) => request.post('/auth/login', data)

export const authLogout = () => request.post('/auth/logout')

// silent：探测登录态时预期会 401，不弹错误提示、不触发跳转
export const authMe = () => request.get('/auth/me', { silent: true })

export const authChangePassword = (data) => request.post('/auth/change-password', data)

// 账号管理（仅 admin 角色可用）
export const listAdminUsers = () => request.get('/auth/users')

export const createAdminUser = (data) => request.post('/auth/users', data)

// 编辑账号（显示名 / 角色 / 邮箱；登录用户名不可改）
export const updateAdminUser = (userId, data) => request.put(`/auth/users/${userId}`, data)

export const disableAdminUser = (userId) => request.post(`/auth/users/${userId}/disable`)

export const enableAdminUser = (userId) => request.post(`/auth/users/${userId}/enable`)

export const resetAdminUserPassword = (userId, data) =>
  request.post(`/auth/users/${userId}/reset-password`, data)

export const unlockAdminUser = (userId) => request.post(`/auth/users/${userId}/unlock`)
