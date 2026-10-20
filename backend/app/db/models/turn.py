"""面试逐轮问答原话表模型。

背景：候选人的回答原文此前只写进 LangGraph 的 ``messages``（落地在 checkpoint 表，
msgpack 序列化），业务库 ``answer_evaluations`` 只保存分数与 AI 评语，HR 事后
无法回看「候选人当时到底怎么说的」，也无法把评分结论追溯到具体原话。

本表把每一轮「AI 提问原文 + 候选人回答原文」独立落库，作为**原话的唯一事实源**：
- 评分失败、候选人中途退出、会话被重跑，都不影响已产生的原话留存；
- 与 ``answer_evaluations`` 通过 ``(session_id, turn_index)`` 顺序对齐，
  报告页据此展示「问题 → 原话 → 分数 → 评语」的完整证据链。

唯一约束 ``(session_id, turn_index)`` 用于幂等：LangGraph 断点恢复可能重放
同一轮，重复写入必须被丢弃而不是产生重复原话。
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class InterviewTurn(TimestampMixin, Base):
    """面试逐轮问答原话（interview_turns）。"""

    __tablename__ = "interview_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uq_interview_turns_session_turn"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False, index=True
    )
    # 第几轮作答，从 1 开始（含追问，按发生顺序递增）
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 题目 id（追问沿用被追问题目的 id，与 answer_evaluations.question_id 一致）
    question_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 题型：subjective（主观题）/ choice（选择题）
    question_type: Mapped[str] = mapped_column(String(32), nullable=False, default="subjective")
    # 本轮是否为追问
    is_follow_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # AI 提问原文
    question_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 候选人回答原文（逐字保留，不做摘要/改写）
    answer_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
