"""面试状态定义：InterviewState 及子结构 + Reducer（唯一真相源，共享知识 #2）。"""
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class CandidateInfo(TypedDict):
    """候选人上下文。"""

    name: str
    resume_text: str
    job_desc: str  # JD 文本，供出题/评分上下文


class QuestionItem(TypedDict, total=False):
    """预设问题项。

    total=False：历史模板的 questions 仅有 id/text（主观题），新增字段均可缺省，
    运行期通过 .get("type") 等安全读取，避免旧数据缺键导致异常。
    """

    id: str  # 问题唯一 id（如 "q1"）
    text: str  # 问题内容
    type: str  # 题型："subjective"（默认）| "choice"
    options: list[str]  # 选择题选项（仅 choice 使用）
    answer: str  # 选择题正确答案（选项文本，空串表示不设标准答案）
    dimension: str  # 选择题映射的评分维度 key


class RubricDimension(TypedDict):
    """评分维度。"""

    key: str  # 维度 key，如 "tech_depth"
    name: str  # 中文名，如 "技术深度"
    description: str  # 评分标准说明
    weight: float  # 汇总权重，如 0.40
    max_score: float  # 维度满分，如 10


class Evaluation(TypedDict):
    """逐题评分结果（追加到 evaluations）。"""

    question_id: str
    scores: dict  # {"tech_depth": 8, ...}
    comment: str  # 评分理由，含原话佐证
    follow_up_needed: bool
    follow_up_question: str  # 追问问题，空串表示无


class InterviewState(TypedDict, total=False):
    """面试状态机总状态。"""

    thread_id: str
    session_id: str
    candidate: CandidateInfo
    # 公司文化档案快照（发链接时写入会话，形如 {"content": "..."}；空字典表示未配置）
    # 只读不写：由 _build_initial_state 从 interview_sessions.company_profile_snapshot 注入
    company_profile: dict
    questions: list[QuestionItem]
    rubric: list[RubricDimension]
    current_idx: int  # 当前题目下标
    max_follow_ups: int  # 每题追问上限（模板级）
    follow_up_count: int  # 当前题已追问次数
    # AI 问题 + 用户回答累加（add_messages reducer）
    messages: Annotated[list, add_messages]
    # 逐题评分累加（operator.add：节点返回值必须传 list，否则 reducer 报错）
    evaluations: Annotated[list[Evaluation], add]
    status: str  # pending / running / finished
    report: dict  # 最终报告（final_report 写入）
