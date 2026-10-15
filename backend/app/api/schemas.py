"""API 请求/响应 Pydantic 模型。"""
import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import DimensionScore, Question, RubricDimension

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包装（用于 Swagger 文档说明）。"""

    code: int = 0
    message: str = "ok"
    data: T | None = None


class JobCreate(BaseModel):
    """创建岗位请求体。"""

    title: str = Field(min_length=1, description="岗位名称")
    jd_text: str = Field(default="", description="岗位 JD 描述")
    requirements: dict = Field(default_factory=dict, description="岗位要求（JSON 对象）")
    # 免简历初筛：True 时上传简历直接生成 AI 面试链接
    skip_screening: bool = Field(default=False, description="是否免简历初筛（直接发 AI 面试）")
    # 在招开关：停招后不允许再为该岗位发起 Offer
    is_active: bool = Field(default=True, description="是否在招（停招后不允许发起 Offer）")


class JobUpdate(BaseModel):
    """更新岗位请求体（字段均可选，仅更新传入项）。"""

    title: str | None = Field(default=None, description="岗位名称")
    jd_text: str | None = Field(default=None, description="岗位 JD 描述")
    requirements: dict | None = Field(default=None, description="岗位要求（JSON 对象）")
    skip_screening: bool | None = Field(default=None, description="是否免简历初筛")
    is_active: bool | None = Field(default=None, description="是否在招（停招后不允许发起 Offer）")


class TemplateCreate(BaseModel):
    """创建面试模板请求体。"""

    job_id: uuid.UUID = Field(description="岗位 UUID")
    questions: list[Question] = Field(min_length=1, description="预设问题集")
    rubric: list[RubricDimension] = Field(min_length=1, description="评分维度")
    duration_limit: int = Field(default=30, ge=1, description="面试时长上限（分钟）")
    max_follow_ups: int = Field(default=2, ge=0, description="每题追问上限")
    # 文化匹配配置：{"enabled": bool, "dimension_key": "culture_fit"}；缺省未启用
    culture_fit: dict = Field(default_factory=dict, description="文化匹配配置（enabled/dimension_key）")


class TemplateOut(BaseModel):
    """模板信息。"""

    id: str
    job_id: str
    questions: list[Question]
    rubric: list[RubricDimension]
    duration_limit: int
    max_follow_ups: int
    culture_fit: dict = Field(default_factory=dict, description="文化匹配配置")


class SessionCreate(BaseModel):
    """创建面试会话（POST /admin/sessions 表单字段参考，路由用 Form 参数接收）。"""

    template_id: str
    name: str
    email: str = ""
    phone: str = ""


# ── 公司文化档案（单例，一段公司介绍文字）─────────────────


class CompanyProfileOut(BaseModel):
    """公司文化档案（全局单例）。"""

    id: str | None = Field(default=None, description="档案 id（未配置时为 null）")
    content: str = Field(default="", description="公司介绍自由文字（公司是谁 + 现状 + 团队氛围 + 用工特点）")
    updated_by: str = Field(default="", description="最近保存人")
    updated_at: str | None = Field(default=None, description="最近保存时间（ISO8601）")


class CompanyProfileUpdate(BaseModel):
    """保存公司文化档案请求体（整篇覆盖）。"""

    content: str = Field(default="", description="公司介绍自由文字")
    updated_by: str = Field(default="", description="保存人（可选）")


class SessionOut(BaseModel):
    """会话信息。"""

    id: str
    token: str
    thread_id: str
    template_id: str
    candidate_id: str
    status: str


class Progress(BaseModel):
    """答题进度（current 为 0 基题目下标）。"""

    current: int
    total: int


class ConsentInfo(BaseModel):
    """《安全知情协议》同意状态。"""

    required: bool = True
    agreed: bool = False
    agreed_at: str | None = None


class InterviewContext(BaseModel):
    """候选人面试上下文。

    status 为 consent_required 时表示尚未同意协议（此时 question / opening 为空）。
    question_type 取值 subjective / choice；choice 时 options 为候选选项。
    """

    session_id: str
    opening: str
    question: str
    question_id: str
    question_type: str = "subjective"
    options: list[str] = Field(default_factory=list)
    progress: Progress
    status: str
    consent: ConsentInfo | None = None


class AnswerSubmit(BaseModel):
    """候选人提交回答请求体。"""

    answer: str = Field(min_length=1, description="候选人回答内容")


class AnswerResponse(BaseModel):
    """候选人提交回答响应。"""

    status: str  # follow_up / next_question / finished
    question: str
    question_id: str
    question_type: str = "subjective"
    options: list[str] = Field(default_factory=list)
    progress: Progress
    message: str


class EvaluationOut(BaseModel):
    """逐题评分明细。"""

    question_id: str
    scores: dict[str, float]
    comment: str
    follow_up_needed: bool
    follow_up_question: str


class ReportOut(BaseModel):
    """报告数据。"""

    dim_scores: list[DimensionScore]
    total_score: float
    verdict: str
    summary: str


class SessionReportOut(BaseModel):
    """会话报告 + 评分明细。"""

    session_id: str
    status: str
    report: ReportOut | None
    evaluations: list[EvaluationOut]


# ── 招聘全链路：初筛 / 线下复试 / 汇总 ─────────────────────


class ScreeningInvite(BaseModel):
    """发起 AI 面试请求体（单个或批量）。"""

    screening_ids: list[str] = Field(min_length=1, description="简历初筛记录 id 列表")
    template_id: str | None = Field(default=None, description="指定面试模板；留空则取该岗位最新模板")


class ScreeningNotify(BaseModel):
    """手动推送初筛推荐名单给 HR。"""

    job_id: str = Field(description="岗位 id")
    min_score: float | None = Field(default=None, description="推荐门槛；留空用服务端默认（65）")


class OfflineInterviewCreate(BaseModel):
    """创建线下复试安排。"""

    screening_id: str | None = Field(default=None, description="来源初筛记录 id（可自动带出候选人/岗位）")
    session_id: str | None = Field(default=None, description="来源 AI 面试会话 id")
    candidate_id: str | None = Field(default=None, description="候选人 id（与 screening/session 二选一）")
    job_id: str | None = Field(default=None, description="岗位 id")
    scheduled_at: str | None = Field(default=None, description="线下面时间（ISO8601）")
    location: str = Field(default="", description="面试地点/门店")
    interviewer: str = Field(default="", description="面试官姓名")
    interviewer_role: str = Field(default="", description="面试官角色：hr / store_manager")


class OfflineInterviewUpdate(BaseModel):
    """更新线下复试安排（改期 / 改状态）。"""

    scheduled_at: str | None = None
    location: str | None = None
    interviewer: str | None = None
    interviewer_role: str | None = None
    status: str | None = Field(default=None, description="scheduled / done / no_show / cancelled")


class OfflineScoreSubmit(BaseModel):
    """线下复试人工打分。"""

    score: float = Field(ge=0, le=100, description="线下复试总分（百分制）")
    dim_scores: list[dict] = Field(default_factory=list, description="维度得分明细")
    comment: str = Field(default="", description="面试评语")
    decision: str = Field(default="", description="录用结论：pass / hold / reject")


class MinutesBind(BaseModel):
    """手动绑定飞书妙记（录音节点降级通道）。"""

    minutes_url: str = Field(description="妙记链接或 token（形如 .../minutes/obcnxxx）")


class AdoptAiScore(BaseModel):
    """采纳 AI 参考分回填为人工打分。"""

    decision: str = Field(default="", description="录用结论：pass / hold / reject")


class SummaryRebuild(BaseModel):
    """生成汇总快照请求体。"""

    job_id: str = Field(description="岗位 id；留空岗位可先查询再指定")


# ── Offer → 入职闭环（第⑤阶段）──────────────────────────────


class OfferCreate(BaseModel):
    """起草 Offer 请求体。"""

    summary_id: str = Field(description="招聘汇总记录 id")
    store_name: str = Field(min_length=1, description="报到门店")
    report_date: str = Field(description="拟报到日期（YYYY-MM-DD）")
    job_title: str | None = Field(default=None, description="岗位名（留空取岗位当前名称）")
    probation_days: int = Field(default=30, ge=0, description="试用期天数")
    note: str = Field(default="", description="备注（薪资等敏感信息不入库，需要时可自由填写）")


class OfferUpdate(BaseModel):
    """修改 Offer 请求体（仅草稿 / 被驳回状态可改）。"""

    job_title: str | None = Field(default=None, description="岗位名")
    store_name: str | None = Field(default=None, description="报到门店")
    report_date: str | None = Field(default=None, description="拟报到日期（YYYY-MM-DD）")
    probation_days: int | None = Field(default=None, ge=0, description="试用期天数")
    note: str | None = Field(default=None, description="备注")


class OfferApprove(BaseModel):
    """审批通过请求体。"""

    note: str = Field(default="", description="审批意见（可选）")


class OfferReject(BaseModel):
    """审批驳回请求体（意见必填）。"""

    note: str = Field(min_length=1, description="驳回意见")


class OfferWithdraw(BaseModel):
    """撤回 Offer 请求体。"""

    reason: str = Field(default="", description="撤回原因")


class OfferOnboard(BaseModel):
    """办理入职请求体。"""

    result: str = Field(description="onboarded（已报到）/ no_show（未报到）")
    actual_report_date: str | None = Field(default=None, description="实际报到日期（YYYY-MM-DD）")
    onboard_store: str = Field(default="", description="实际报到门店（留空取 Offer 门店）")
    note: str = Field(default="", description="未报到说明（result=no_show 时必填）")


class OfferDecline(BaseModel):
    """候选人婉拒请求体（理由为自由文本，选填）。"""

    reason_text: str = Field(default="", description="婉拒理由（自由文本）")


# ── 后台登录鉴权（安全改造）────────────────────────────────────
class LoginRequest(BaseModel):
    """后台登录请求体。

    密码长度上限与 ``app.security.passwords.MAX_PASSWORD_LENGTH`` 对齐，
    在进入哈希计算前就把超长输入拦掉。
    """

    username: str = Field(min_length=1, max_length=64, description="用户名")
    password: str = Field(min_length=1, max_length=128, description="密码")


class ChangePasswordRequest(BaseModel):
    """本人修改密码请求体。"""

    current_password: str = Field(min_length=1, max_length=128, description="当前密码")
    new_password: str = Field(min_length=1, max_length=128, description="新密码")


class AdminUserOut(BaseModel):
    """后台账号对外视图（**绝不包含** password_hash 等敏感字段）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str
    email: str | None = None
    role: str
    is_active: bool
    # 账号管理页展示用（登录接口同样携带，不影响安全：都是非敏感状态字段）
    last_login_at: datetime | None = None
    # 非空且晚于当前时间 = 处于登录失败锁定窗口
    locked_until: datetime | None = None
    created_by: str = ""


class LoginOut(BaseModel):
    """登录成功响应体。"""

    user: AdminUserOut
    # CSRF token 同时写入非 HttpOnly Cookie 与响应体，前端可任选一处读取
    csrf_token: str
    expires_at: str


class CreateAdminUserRequest(BaseModel):
    """管理员创建后台账号请求体（系统不提供自助注册）。"""

    username: str = Field(min_length=3, max_length=64, description="用户名（字母/数字/._-）")
    password: str = Field(min_length=1, max_length=128, description="初始密码")
    role: str = Field(default="hr", description="角色：admin / hr / store_manager")
    email: str | None = Field(default=None, description="邮箱（选填）")
    display_name: str = Field(default="", max_length=128, description="展示名（选填）")


class ResetPasswordRequest(BaseModel):
    """管理员重置他人密码请求体。"""

    new_password: str = Field(min_length=1, max_length=128, description="新密码")
