"""API 请求/响应 Pydantic 模型。"""
import uuid
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

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


class JobUpdate(BaseModel):
    """更新岗位请求体（字段均可选，仅更新传入项）。"""

    title: str | None = Field(default=None, description="岗位名称")
    jd_text: str | None = Field(default=None, description="岗位 JD 描述")
    requirements: dict | None = Field(default=None, description="岗位要求（JSON 对象）")
    skip_screening: bool | None = Field(default=None, description="是否免简历初筛")


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
