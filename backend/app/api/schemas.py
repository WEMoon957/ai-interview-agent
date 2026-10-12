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


class TemplateCreate(BaseModel):
    """创建面试模板请求体。"""

    job_id: uuid.UUID = Field(description="岗位 UUID")
    questions: list[Question] = Field(min_length=1, description="预设问题集")
    rubric: list[RubricDimension] = Field(min_length=1, description="评分维度")
    duration_limit: int = Field(default=30, ge=1, description="面试时长上限（分钟）")
    max_follow_ups: int = Field(default=2, ge=0, description="每题追问上限")


class TemplateOut(BaseModel):
    """模板信息。"""

    id: str
    job_id: str
    questions: list[Question]
    rubric: list[RubricDimension]
    duration_limit: int
    max_follow_ups: int


class SessionCreate(BaseModel):
    """创建面试会话（POST /admin/sessions 表单字段参考，路由用 Form 参数接收）。"""

    template_id: str
    name: str
    email: str = ""
    phone: str = ""


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
    """

    session_id: str
    opening: str
    question: str
    question_id: str
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
