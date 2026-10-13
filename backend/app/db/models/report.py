"""面试报告表模型。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class SessionReport(TimestampMixin, Base):
    """面试报告（session_reports）：一会话一条。"""

    __tablename__ = "session_reports"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interview_sessions.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    # 维度得分：[{"key","name","weight","score","weighted_score"}]
    dim_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 达标推送至 HR 的时间；NULL 表示尚未推送（幂等标记，避免重跑重复推送）
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session = relationship("InterviewSession", lazy="selectin")
