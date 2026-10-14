import request from '../utils/request'

// ── 管理端 ─────────────────────────────────────────
export const listJobs = () => request.get('/admin/jobs')

export const createJob = (data) => request.post('/admin/jobs', data)

export const updateJob = (jobId, data) => request.put(`/admin/jobs/${jobId}`, data)

export const deleteJob = (jobId) => request.delete(`/admin/jobs/${jobId}`)

export const listTemplates = () => request.get('/admin/templates')

export const createTemplate = (data) => request.post('/admin/templates', data)

export const deleteTemplate = (templateId) => request.delete(`/admin/templates/${templateId}`)

export const createSession = (formData) =>
  request.post('/admin/sessions', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

export const listSessions = (params) => request.get('/admin/sessions', { params })

export const deleteSession = (sessionId) => request.delete(`/admin/sessions/${sessionId}`)

export const getReport = (sessionId) => request.get(`/admin/sessions/${sessionId}/report`)

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
