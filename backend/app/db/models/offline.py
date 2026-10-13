"""线下复试表模型。

AI 面试达标后，由 HR 或店长约线下复试并人工打分：本表记录约面信息
（时间/地点/面试官）+ 打分结果（总分/维度/评语/录用结论）。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class OfflineInterview(TimestampMixin, Base):
    """线下复试（offline_interviews）。"""

    __tablename__ = "offline_interviews"

    # 来源：可来自初筛记录 / AI 面试会话（均可空，兼容直接手工录入）
    screening_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_screenings.id"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False, index=True
    )

    # 约面信息
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    interviewer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 面试官角色：hr / store_manager（店长）
    interviewer_role: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # 状态：scheduled（已约）/ done（已完成）/ no_show（爽约）/ cancelled（已取消）
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")

    # 打分结果（人工）
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 维度得分：[{"key","name","score","max_score"}]
    dim_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 录用结论：pass / hold / reject
    decision: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidate = relationship("Candidate", lazy="selectin")
    job = relationship("JobPosting", lazy="selectin")
