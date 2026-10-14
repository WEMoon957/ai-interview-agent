"""面试会话表模型。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class InterviewSession(TimestampMixin, Base):
    """面试会话（interview_sessions）：候选人凭 token 进入，thread_id 关联 LangGraph 检查点。"""

    __tablename__ = "interview_sessions"

    # 候选人答题入口 token（共享知识 A4：MVP 用 UUID 十六进制，无过期）
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_templates.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    # pending / running / finished
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # 对应 LangGraph checkpointer 的 thread_id（共享知识 #7）
    thread_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 《安全知情协议》同意时间（P1-7 合规声明）；NULL 表示未同意，面试不可进行
    consent_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 发链接时快照的公司介绍：{"content": "..."}；{} 表示发链接时未配置（CC-4/CC-5）。
    # 快照隔离：HR 事后修改公司文化档案不影响本会话的开场白与后续评分。
    company_profile_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    template = relationship("InterviewTemplate", lazy="selectin")
    candidate = relationship("Candidate", lazy="selectin")
