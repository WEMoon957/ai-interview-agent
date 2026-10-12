"""面试模板表模型。"""
import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class InterviewTemplate(TimestampMixin, Base):
    """面试模板（interview_templates）：预设问题集 + 评分维度 + 追问上限。"""

    __tablename__ = "interview_templates"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False, index=True
    )
    # 问题集：[{"id": "q1", "text": "..."}]（JSONB，见共享知识 #3）
    questions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 评分维度：[{"key","name","description","weight","max_score"}]
    rubric: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 面试时长上限（分钟），MVP 仅存储
    duration_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # 每题追问上限（共享知识 #9，默认 2）
    max_follow_ups: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    job = relationship("JobPosting", lazy="selectin")
