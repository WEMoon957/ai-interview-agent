"""逐题评分表模型。"""
import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class AnswerEvaluation(TimestampMixin, Base):
    """逐题评分（answer_evaluations）。

    评分时间戳复用 TimestampMixin 的 created_at（对应类图中的 ts）。
    """

    __tablename__ = "answer_evaluations"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False, index=True
    )
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 维度 key -> 得分（0~max_score），见共享知识 #4
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 行为锚定证据：[{dimension, level, quote, reason, verified}]
    # quote 是候选人原话的连续片段，verified 表示它是否真的出现在回答原文里（防模型编造）
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    follow_up_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follow_up_question: Mapped[str] = mapped_column(Text, nullable=False, default="")

    session = relationship("InterviewSession", lazy="selectin")
