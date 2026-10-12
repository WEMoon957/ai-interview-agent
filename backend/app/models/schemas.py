"""核心 Pydantic schema：评分结构化输出与报告（见架构 3.1）。"""
from pydantic import BaseModel, Field


class RubricDimension(BaseModel):
    """评分维度（模板 rubric 元素）。"""

    key: str = Field(description="维度 key，如 tech_depth")
    name: str = Field(description="中文名，如 技术深度")
    description: str = Field(default="", description="评分标准说明")
    weight: float = Field(description="汇总权重，如 0.40")
    max_score: float = Field(default=10, description="维度满分，如 10")


class Question(BaseModel):
    """预设问题（模板 questions 元素）。"""

    id: str = Field(description="问题唯一 id，如 q1")
    text: str = Field(description="问题内容")


class AnswerScore(BaseModel):
    """LLM 结构化输出：单题评分结果（with_structured_output 校验）。"""

    scores: dict[str, float] = Field(description="维度 key -> 得分（0~max_score）")
    comment: str = Field(description="评分理由，须引用候选人原话佐证")
    follow_up_needed: bool = Field(description="是否需要追问")
    follow_up_question: str = Field(default="", description="追问问题，空串表示无")


class EvaluationRecord(BaseModel):
    """持久化到 answer_evaluations 的评分记录（AnswerScore + question_id 组装）。"""

    question_id: str
    scores: dict[str, float]
    comment: str
    follow_up_needed: bool
    follow_up_question: str = ""


class DimensionScore(BaseModel):
    """维度汇总得分。"""

    key: str
    name: str
    weight: float
    score: float  # 该维度各题均分
    weighted_score: float  # score * weight


class Report(BaseModel):
    """最终面试报告。"""

    dim_scores: list[DimensionScore]
    total_score: float  # 百分制（0~100）
    verdict: str  # 强烈推荐/推荐/待定/不推荐
    summary: str  # 总体评价 + 录用风险提示
