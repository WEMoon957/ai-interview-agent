<template>
  <div>
    <!-- 待 HR / 店长处理：AI 面试已完成、尚未打分的人（数据来自汇总接口，仅 HR/管理员可见） -->
    <el-card v-if="isHrView" shadow="never" class="pending-card">
      <template #header>
        <div class="card-header">
          <span>
            待 HR / 店长处理
            <el-tag v-if="pending.length" type="danger" size="small" effect="dark" style="margin-left: 6px">
              {{ pending.length }}
            </el-tag>
          </span>
          <span class="tip">AI 面试已出结果、尚未录入 HR/店长打分的候选人</span>
        </div>
      </template>

      <el-table :data="pending" v-loading="pendingLoading" stripe size="small">
        <el-table-column label="候选人" min-width="110">
          <template #default="{ row }">{{ row.candidate_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="岗位" min-width="130">
          <template #default="{ row }">{{ row.job_title || jobTitle(row.job_id) }}</template>
        </el-table-column>
        <el-table-column label="AI 面试分" width="110">
          <template #default="{ row }">
            <span class="score" :class="scoreClass(row.ai_interview_score)">
              {{ row.ai_interview_score.toFixed(1) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="AI 面试结论" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.ai_interview_verdict" :type="verdictType(row.ai_interview_verdict)" size="small">
              {{ row.ai_interview_verdict }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="openScheduleFor(row)">约线下复试</el-button>
            <el-button size="small" @click="quickScore(row)">直接打分</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无待处理候选人（AI 面试完成、并录入 HR/店长打分后会移出此列表）" :image-size="60" />
        </template>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>线下复试（HR / 店长约面与打分）</span>
          <div class="header-actions">
            <el-select v-model="jobId" placeholder="全部岗位" clearable style="width: 180px" @change="load">
              <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
            </el-select>
            <el-select v-model="status" placeholder="全部状态" clearable style="width: 150px" @change="load">
              <el-option label="已约面" value="scheduled" />
              <el-option label="已完成" value="done" />
              <el-option label="爽约" value="no_show" />
              <el-option label="已取消" value="cancelled" />
            </el-select>
            <el-select v-model="source" placeholder="全部来源" clearable style="width: 140px" @change="load">
              <el-option label="线上投递" value="online" />
              <el-option label="到店直招" value="walk_in" />
            </el-select>
            <el-button v-if="isHrView" type="warning" @click="openWalkIn">到店直招</el-button>
            <el-button v-if="isHrView" type="primary" @click="openCreate">新增约面</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column label="候选人" min-width="110">
          <template #default="{ row }">{{ row.candidate_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="岗位" min-width="130">
          <template #default="{ row }">{{ row.job_title || '-' }}</template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.source === 'walk_in'" type="warning" size="small">到店直招</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">线上投递</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源渠道" min-width="110">
          <template #default="{ row }">
            <span v-if="row.source_channel">{{ row.source_channel }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="面试录音" min-width="140">
          <template #default="{ row }">
            <el-link v-if="row.minutes_url" :href="row.minutes_url" target="_blank" type="primary">
              打开录音
            </el-link>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="录音转文字" width="160">
          <template #default="{ row }">
            <el-tag :type="transcriptStatusType(row.transcript_status)" size="small" effect="plain">
              {{ transcriptStatusText(row.transcript_status) }}
            </el-tag>
            <el-button
              v-if="['none', 'pending', 'failed'].includes(row.transcript_status)"
              size="small"
              text
              type="primary"
              @click="openBindMinutes(row)"
            >
              粘贴链接
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="AI 参考分" width="120">
          <template #default="{ row }">
            <template v-if="row.ai_score != null">
              <span class="score" :class="scoreClass(row.ai_score)">{{ row.ai_score.toFixed(1) }}</span>
            </template>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="约面时间" min-width="160">
          <template #default="{ row }">{{ fmt(row.scheduled_at) }}</template>
        </el-table-column>
        <el-table-column prop="location" label="地点" min-width="120" show-overflow-tooltip />
        <el-table-column label="面试官" min-width="130">
          <template #default="{ row }">
            {{ row.interviewer || '-' }}
            <el-tag v-if="row.interviewer_role" size="small" effect="plain" style="margin-left: 4px">
              {{ roleText(row.interviewer_role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="HR/店长打分" width="110">
          <template #default="{ row }">
            <span v-if="row.score != null" class="score" :class="scoreClass(row.score)">
              {{ row.score.toFixed(1) }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.decision" :type="decisionType(row.decision)" size="small">
              {{ decisionText(row.decision) }}
            </el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240">
          <template #default="{ row }">
            <el-button size="small" type="primary" text @click="openScore(row)">
              {{ row.score != null ? '修改打分' : '打分' }}
            </el-button>
            <el-button size="small" text @click="openEdit(row)">改期</el-button>
            <!-- 删除会连带影响招聘汇总，仅 HR / 管理员（店长用「爽约/取消」状态代替） -->
            <el-button v-if="isHrView" size="small" text type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无线下复试安排" />
        </template>
      </el-table>
    </el-card>

    <!-- 新增约面 -->
    <el-dialog v-model="createVisible" title="新增线下复试安排" width="600px">
      <el-form label-width="100px">
        <el-form-item label="岗位" required>
          <el-select v-model="form.jobId" placeholder="选择岗位" style="width: 100%" filterable @change="loadCandidates">
            <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="候选人" required>
          <el-select v-model="form.screeningId" placeholder="选择候选人（来自简历初筛）" style="width: 100%" filterable :loading="candLoading">
            <el-option
              v-for="c in candidates"
              :key="c.id"
              :label="candidateLabel(c)"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="约面时间">
          <el-date-picker v-model="form.scheduledAt" type="datetime" placeholder="选择时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="form.location" placeholder="如：XX 门店 / 总部会议室" />
        </el-form-item>
        <el-form-item label="面试官">
          <el-input v-model="form.interviewer" placeholder="面试官姓名" />
        </el-form-item>
        <el-form-item label="面试官角色">
          <el-radio-group v-model="form.interviewerRole">
            <el-radio label="hr">HR</el-radio>
            <el-radio label="store_manager">店长</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 改期 / 改状态 -->
    <el-dialog v-model="editVisible" title="修改线下复试安排" width="560px">
      <el-form label-width="100px">
        <el-form-item label="约面时间">
          <el-date-picker v-model="editForm.scheduledAt" type="datetime" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="editForm.location" />
        </el-form-item>
        <el-form-item label="面试官">
          <el-input v-model="editForm.interviewer" />
        </el-form-item>
        <el-form-item label="面试官角色">
          <el-radio-group v-model="editForm.interviewerRole">
            <el-radio label="hr">HR</el-radio>
            <el-radio label="store_manager">店长</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option label="已约面" value="scheduled" />
            <el-option label="已完成" value="done" />
            <el-option label="爽约" value="no_show" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 打分 -->
    <el-dialog v-model="scoreVisible" title="线下复试打分" width="620px">
      <el-form label-width="100px">
        <el-form-item label="候选人">
          <span>{{ scoreTarget?.candidate_name }}</span>
          <el-tag v-if="scoreTarget?.source === 'walk_in'" type="warning" size="small" style="margin-left: 8px">
            到店直招
          </el-tag>
        </el-form-item>
        <el-form-item v-if="scoreTarget?.ai_score != null" label="AI 参考分">
          <span class="score" :class="scoreClass(scoreTarget.ai_score)" style="font-size: 16px">
            {{ scoreTarget.ai_score.toFixed(1) }}
          </span>
          <el-button size="small" type="primary" text @click="fillAiScore">采纳 AI 分</el-button>
          <div v-if="scoreTarget.ai_comment" class="muted" style="font-size: 12px; margin-top: 4px">
            {{ scoreTarget.ai_comment }}
          </div>
        </el-form-item>
        <el-form-item label="总分" required>
          <el-input-number v-model="scoreForm.score" :min="0" :max="100" :precision="1" />
          <span class="muted" style="margin-left: 8px">百分制</span>
        </el-form-item>
        <el-form-item label="维度得分">
          <div class="dim-wrap">
            <div v-for="(d, i) in scoreForm.dimScores" :key="i" class="dim-row">
              <el-input v-model="d.name" placeholder="维度名" style="width: 150px" />
              <el-input-number v-model="d.score" :min="0" :max="d.max_score || 10" :precision="1" />
              <span class="muted">/ {{ d.max_score || 10 }}</span>
            </div>
          </div>
        </el-form-item>
        <el-form-item label="面试评语">
          <el-input v-model="scoreForm.comment" type="textarea" :rows="3" placeholder="填写线下复试评价" />
        </el-form-item>
        <el-form-item label="录用结论" required>
          <el-radio-group v-model="scoreForm.decision">
            <el-radio label="pass">通过</el-radio>
            <el-radio label="hold">待定</el-radio>
            <el-radio label="reject">淘汰</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="scoreVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitScore">提交打分</el-button>
      </template>
    </el-dialog>

    <!-- 待办：从 AI 面试结果直接约线下面试 -->
    <el-dialog v-model="scheduleVisible" title="约线下复试" width="560px">
      <el-form label-width="100px">
        <el-form-item label="候选人">
          <span>{{ pendingTarget?.candidate_name }}（AI 面试 {{ pendingTarget?.ai_interview_score }} 分）</span>
        </el-form-item>
        <el-form-item label="岗位">
          <span>{{ pendingTarget?.job_title || jobTitle(pendingTarget?.job_id) }}</span>
        </el-form-item>
        <el-form-item label="约面时间" required>
          <el-date-picker v-model="pendingForm.scheduledAt" type="datetime" placeholder="选择时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地点">
          <el-input v-model="pendingForm.location" placeholder="如：XX 门店 / 总部会议室" />
        </el-form-item>
        <el-form-item label="面试官">
          <el-input v-model="pendingForm.interviewer" placeholder="面试官姓名" />
        </el-form-item>
        <el-form-item label="面试官角色">
          <el-radio-group v-model="pendingForm.interviewerRole">
            <el-radio label="hr">HR</el-radio>
            <el-radio label="store_manager">店长</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="scheduleVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitScheduleFor">创建约面</el-button>
      </template>
    </el-dialog>

    <!-- 到店直招：店长现场面试，直接建档（无需简历 / 初筛 / AI 面试） -->
    <el-dialog v-model="walkInVisible" title="到店直招 · 现场面试建档" width="620px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
        title="用于候选人直接上门找店长面试的情况：不做简历初筛、不发 AI 面试链接，建档后直接打分。"
      />
      <el-form :model="walkInForm" label-width="100px">
        <el-form-item label="应聘岗位" required>
          <el-select v-model="walkInForm.jobId" placeholder="选择岗位" style="width: 100%" filterable>
            <el-option v-for="j in jobs" :key="j.id" :label="j.title" :value="j.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="walkInForm.name" placeholder="候选人姓名" style="width: 220px" />
        </el-form-item>
        <el-form-item label="手机号" required>
          <el-input v-model="walkInForm.phone" placeholder="11 位手机号" style="width: 220px" />
        </el-form-item>
        <el-form-item label="来源渠道" required>
          <el-select
            v-model="walkInForm.sourceChannel"
            placeholder="选择或输入来源"
            filterable
            allow-create
            default-first-option
            style="width: 260px"
          >
            <el-option v-for="c in SOURCE_CHANNELS" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="面试时间">
          <el-date-picker
            v-model="walkInForm.scheduledAt"
            type="datetime"
            placeholder="默认当前时间"
            style="width: 260px"
          />
        </el-form-item>
        <el-form-item label="面试门店">
          <el-input v-model="walkInForm.location" placeholder="如：昆明总店" style="width: 260px" />
        </el-form-item>
        <el-form-item label="面试店长">
          <el-input v-model="walkInForm.interviewer" placeholder="面试官姓名" style="width: 220px" />
          <el-radio-group v-model="walkInForm.interviewerRole" style="margin-left: 12px">
            <el-radio label="store_manager">店长</el-radio>
            <el-radio label="hr">HR</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="录音方式">
          <div class="tip">
            录音保存在飞书：店长在飞书妙记录音时，把<b>标题写成候选人姓名</b>即可，系统会自动匹配并生成参考分。标题写错或漏写也没关系——在面试记录里点「粘贴录音链接」，贴入妙记链接同样能转写。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="walkInVisible = false">取消</el-button>
        <el-button :loading="saving" @click="submitWalkIn(false)">保存，稍后打分</el-button>
        <el-button type="primary" :loading="saving" @click="submitWalkIn(true)">
          保存并立即打分
        </el-button>
      </template>
    </el-dialog>

    <!-- 绑定飞书妙记（录音节点降级通道）：店长粘贴妙记链接，立即拉逐字稿 + 评分 -->
    <el-dialog v-model="bindVisible" title="粘贴面试录音链接" width="560px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
        title="粘什么链接都行，谁录的都能转：网页链接、App 分享链接、带标题的整段分享文案、裸妙记编号都可以。系统会依次用已授权的账号去读，任何一个人有权限即可；若提示无权读取，请让录音的人在飞书里把妙记设为「组织内可见」或分享给 HR。"
      />
      <el-form label-width="90px">
        <el-form-item label="候选人">
          <span>{{ bindTarget?.candidate_name }}（{{ bindTarget?.job_title }}）</span>
        </el-form-item>
        <el-form-item label="录音链接" required>
          <el-input
            v-model="bindForm.minutesUrl"
            type="textarea"
            :rows="3"
            placeholder="粘贴妙记链接即可，如 https://xxx.feishu.cn/minutes/obcnxxx（App 分享的链接或整段分享文案也行）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bindVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitBind">绑定并转写</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../../stores/auth'
import {
  listJobs,
  listScreenings,
  listOfflineInterviews,
  createOfflineInterview,
  createWalkInInterview,
  updateOfflineInterview,
  scoreOfflineInterview,
  deleteOfflineInterview,
  getSummary,
  bindMinutes,
} from '../../api'

// 来源渠道候选（可自由输入）
const SOURCE_CHANNELS = ['门店海报', '员工介绍', '商场客流', '自然到访', '朋友圈/社群', '老员工返聘', '其他']

const jobs = ref([])
const jobId = ref('')
const status = ref('')
const source = ref('')
const rows = ref([])
const loading = ref(false)
const saving = ref(false)

// 到店直招
const walkInVisible = ref(false)
const walkInForm = ref({
  jobId: '',
  name: '',
  phone: '',
  sourceChannel: '',
  scheduledAt: null,
  location: '',
  interviewer: '',
  interviewerRole: 'store_manager',
})

// 绑定飞书妙记（录音节点）
const bindVisible = ref(false)
const bindTarget = ref(null)
const bindForm = ref({ minutesUrl: '' })

// 待 HR / 店长处理（AI 面试已出结果、尚未打分）
const pending = ref([])
const pendingLoading = ref(false)
const scheduleVisible = ref(false)
const pendingTarget = ref(null)
const pendingForm = ref({ scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' })

const createVisible = ref(false)
const editVisible = ref(false)
const scoreVisible = ref(false)
const candidates = ref([])
const candLoading = ref(false)

const form = ref({ jobId: '', screeningId: '', scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' })
const editForm = ref({ id: '', scheduledAt: null, location: '', interviewer: '', interviewerRole: '', status: 'scheduled' })
const scoreTarget = ref(null)
const scoreForm = ref({ score: 70, dimScores: [], comment: '', decision: 'pass' })

const DEFAULT_DIMS = [
  { key: 'service', name: '服务意识', score: 7, max_score: 10 },
  { key: 'stability', name: '稳定性', score: 7, max_score: 10 },
  { key: 'skill', name: '岗位技能', score: 7, max_score: 10 },
]

function fmt(iso) {
  return iso ? new Date(iso).toLocaleString('zh-CN') : '-'
}
function jobTitle(id) {
  return jobs.value.find((j) => j.id === id)?.title || '-'
}
function verdictType(v) {
  return { 强烈推荐: 'success', 推荐: 'success', 待定: 'warning', 不推荐: 'danger' }[v] || 'info'
}
function candidateLabel(c) {
  // 免初筛岗位无初筛分，标注出来避免误显示 "null 分"
  return c.total_score != null
    ? `${c.candidate_name}（初筛 ${c.total_score.toFixed(1)} 分）`
    : `${c.candidate_name}（免初筛）`
}
function roleText(r) {
  return { hr: 'HR', store_manager: '店长' }[r] || r
}
function statusText(s) {
  return { scheduled: '已约面', done: '已完成', no_show: '爽约', cancelled: '已取消' }[s] || s
}
function statusType(s) {
  return { scheduled: 'warning', done: 'success', no_show: 'danger', cancelled: 'info' }[s] || 'info'
}
function decisionText(d) {
  return { pass: '通过', hold: '待定', reject: '淘汰' }[d] || d
}
function decisionType(d) {
  return { pass: 'success', hold: 'warning', reject: 'danger' }[d] || 'info'
}
function scoreClass(s) {
  if (s >= 80) return 's-high'
  if (s >= 65) return 's-mid'
  return 's-low'
}
function transcriptStatusText(s) {
  return {
    none: '未开启',
    pending: '待录音',
    matched: '转写中',
    transcribed: '已转写',
    failed: '转写失败',
  }[s] || s || '未开启'
}
function transcriptStatusType(s) {
  return {
    none: 'info',
    pending: 'warning',
    matched: 'warning',
    transcribed: 'success',
    failed: 'danger',
  }[s] || 'info'
}

// 角色视图：HR/管理员看全部入口；店长只见自己的面试记录与打分。
// 与后端权限矩阵一致（delete / summary / screenings 均限 HR+admin）。
const auth = useAuthStore()
const isHrView = computed(() => ['admin', 'hr'].includes(auth.user?.role))

async function load() {
  loading.value = true
  try {
    // 状态筛选只作用于下方列表；上方待办列表只看「AI 面试完成且未打分」
    const listParams = {}
    if (jobId.value) listParams.job_id = jobId.value
    if (status.value) listParams.status = status.value
    if (source.value) listParams.source = source.value
    rows.value = await listOfflineInterviews(listParams)
  } finally {
    loading.value = false
  }
  // 待办列表数据来自汇总接口（仅 HR/管理员有权调用），店长跳过避免 403 报错
  if (isHrView.value) await loadPending()
}

// ── 到店直招 ────────────────────────────────────────

/** 弹窗用 HTML 渲染，候选人姓名是用户输入，插入前必须转义 */
function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]
  )
}

function openWalkIn() {
  walkInForm.value = {
    jobId: jobId.value || '',
    name: '',
    phone: '',
    sourceChannel: '',
    scheduledAt: new Date(),
    location: '',
    interviewer: '',
    interviewerRole: 'store_manager',
  }
  walkInVisible.value = true
}

async function submitWalkIn(thenScore) {
  const f = walkInForm.value
  if (!f.jobId) return ElMessage.warning('请选择应聘岗位')
  if (!f.name.trim()) return ElMessage.warning('请填写候选人姓名')
  if (!f.phone.trim()) return ElMessage.warning('请填写手机号')
  if (!f.sourceChannel) return ElMessage.warning('请选择来源渠道')

  const fd = new FormData()
  fd.append('job_id', f.jobId)
  fd.append('name', f.name.trim())
  fd.append('phone', f.phone.trim())
  fd.append('source_channel', f.sourceChannel)
  fd.append('interviewer', f.interviewer || '')
  fd.append('interviewer_role', f.interviewerRole || '')
  fd.append('location', f.location || '')
  if (f.scheduledAt) fd.append('scheduled_at', new Date(f.scheduledAt).toISOString())

  saving.value = true
  try {
    const res = await createWalkInInterview(fd)
    walkInVisible.value = false
    ElMessage.success('到店直招已建档')
    await load()
    const name = res.record?.candidate_name
    if (name) {
      ElMessageBox.alert(
        `请让店长打开飞书妙记开始录音，并把妙记<b>标题写成候选人姓名「${escapeHtml(name)}」</b>；录完后系统会自动匹配、拉取逐字稿并生成 AI 参考分。<br><br>标题写错或漏写也不用重录——在面试记录里点「粘贴录音链接」，贴入妙记链接同样能转写。`,
        '飞书妙记录音',
        { confirmButtonText: '知道了', type: 'info', dangerouslyUseHTMLString: true, showClose: false }
      )
    }
    if (thenScore) openScore(res.record)
  } finally {
    saving.value = false
  }
}

async function loadPending() {
  pendingLoading.value = true
  try {
    const params = jobId.value ? { job_id: jobId.value } : {}
    const summary = await getSummary(params)
    // AI 面试已出结果、且尚未录入 HR/店长打分
    pending.value = summary.filter((r) => r.ai_interview_score != null && r.offline_id == null)
  } finally {
    pendingLoading.value = false
  }
}

/** 从待办直接约线下面试（无需先手工选候选人） */
function openScheduleFor(row) {
  pendingTarget.value = row
  pendingForm.value = { scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' }
  scheduleVisible.value = true
}

async function submitScheduleFor() {
  saving.value = true
  try {
    await createOfflineInterview({
      session_id: pendingTarget.value.session_id,
      job_id: pendingTarget.value.job_id,
      scheduled_at: pendingForm.value.scheduledAt ? pendingForm.value.scheduledAt.toISOString() : null,
      location: pendingForm.value.location,
      interviewer: pendingForm.value.interviewer,
      interviewer_role: pendingForm.value.interviewerRole,
    })
    ElMessage.success('已创建约面')
    scheduleVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

/** 直接打分：不必先约面，先建一条记录再打开打分弹窗 */
async function quickScore(row) {
  const created = await createOfflineInterview({
    session_id: row.session_id,
    job_id: row.job_id,
  })
  openScore(created)
}

function openCreate() {
  form.value = { jobId: jobId.value || '', screeningId: '', scheduledAt: null, location: '', interviewer: '', interviewerRole: 'hr' }
  candidates.value = []
  if (form.value.jobId) loadCandidates()
  createVisible.value = true
}

async function loadCandidates() {
  if (!form.value.jobId) {
    candidates.value = []
    return
  }
  candLoading.value = true
  try {
    candidates.value = await listScreenings({ job_id: form.value.jobId })
  } finally {
    candLoading.value = false
  }
}

async function submitCreate() {
  if (!form.value.screeningId) {
    ElMessage.warning('请选择候选人')
    return
  }
  saving.value = true
  try {
    await createOfflineInterview({
      screening_id: form.value.screeningId,
      scheduled_at: form.value.scheduledAt ? form.value.scheduledAt.toISOString() : null,
      location: form.value.location,
      interviewer: form.value.interviewer,
      interviewer_role: form.value.interviewerRole,
    })
    ElMessage.success('已创建约面')
    createVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openEdit(row) {
  editForm.value = {
    id: row.id,
    scheduledAt: row.scheduled_at ? new Date(row.scheduled_at) : null,
    location: row.location || '',
    interviewer: row.interviewer || '',
    interviewerRole: row.interviewer_role || 'hr',
    status: row.status,
  }
  editVisible.value = true
}

async function submitEdit() {
  saving.value = true
  try {
    await updateOfflineInterview(editForm.value.id, {
      scheduled_at: editForm.value.scheduledAt ? editForm.value.scheduledAt.toISOString() : null,
      location: editForm.value.location,
      interviewer: editForm.value.interviewer,
      interviewer_role: editForm.value.interviewerRole,
      status: editForm.value.status,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

function openScore(row) {
  scoreTarget.value = row
  scoreForm.value = {
    score: row.score != null ? row.score : 70,
    dimScores: row.dim_scores?.length
      ? JSON.parse(JSON.stringify(row.dim_scores))
      : JSON.parse(JSON.stringify(DEFAULT_DIMS)),
    comment: row.comment || '',
    decision: row.decision || 'pass',
  }
  scoreVisible.value = true
}

async function submitScore() {
  saving.value = true
  try {
    await scoreOfflineInterview(scoreTarget.value.id, {
      score: scoreForm.value.score,
      dim_scores: scoreForm.value.dimScores.map((d) => ({
        key: d.key || d.name,
        name: d.name,
        score: d.score,
        max_score: d.max_score || 10,
      })),
      comment: scoreForm.value.comment,
      decision: scoreForm.value.decision,
    })
    ElMessage.success('打分已提交')
    scoreVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

// ── 飞书妙记录音节点：绑定 / 采纳 ─────────────────────

function openBindMinutes(row) {
  bindTarget.value = row
  bindForm.value = { minutesUrl: row.minutes_url || '' }
  bindVisible.value = true
}

async function submitBind() {
  const url = (bindForm.value.minutesUrl || '').trim()
  if (!url) return ElMessage.warning('请粘贴妙记链接')
  saving.value = true
  try {
    const res = await bindMinutes(bindTarget.value.id, { minutes_url: url })
    bindVisible.value = false
    if (res.transcribed && res.record.ai_score != null) {
      ElMessage.success(`转写完成，AI 参考分 ${res.record.ai_score.toFixed(1)}`)
    } else if (res.transcribed) {
      ElMessage.success('转写完成（AI 评分暂不可用）')
    } else {
      ElMessage.warning(
        res.error ? `已绑定，但拉取逐字稿失败：${res.error}` : '已绑定，但拉取逐字稿失败，可稍后重试'
      )
    }
    await load()
  } finally {
    saving.value = false
  }
}

/** 在打分弹窗内一键采纳 AI 参考分（回填表单，HR 确认结论后提交） */
function fillAiScore() {
  const t = scoreTarget.value
  if (t?.ai_score == null) return
  scoreForm.value.score = t.ai_score
  if (t.ai_dim_scores?.length) {
    scoreForm.value.dimScores = JSON.parse(JSON.stringify(t.ai_dim_scores))
  }
  if (t.ai_comment) scoreForm.value.comment = t.ai_comment
  ElMessage.success('已填入 AI 参考分，确认录用结论后提交即可')
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `删除「${row.candidate_name}」的线下复试记录？删除后该记录会从招聘汇总中移除，且不可恢复。`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      }
    )
  } catch {
    return
  }
  await deleteOfflineInterview(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(async () => {
  jobs.value = await listJobs()
  await load()
})
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.pending-card {
  margin-bottom: 16px;
}
.tip {
  color: #909399;
  font-size: 13px;
}
.header-actions {
  display: flex;
  gap: 10px;
}
.score {
  font-weight: 600;
}
.s-high {
  color: #16a34a;
}
.s-mid {
  color: #6366f1;
}
.s-low {
  color: #d97706;
}
.muted {
  color: #909399;
}
.dim-wrap {
  width: 100%;
}
.dim-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
</style>
