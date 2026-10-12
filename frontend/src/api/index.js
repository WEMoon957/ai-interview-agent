import request from '../utils/request'

// ── 管理端 ─────────────────────────────────────────
export const listJobs = () => request.get('/admin/jobs')

export const createJob = (data) => request.post('/admin/jobs', data)

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
