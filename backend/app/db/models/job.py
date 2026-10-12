"""岗位表模型。"""
from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class JobPosting(TimestampMixin, Base):
    """岗位（job_postings）。"""

    __tablename__ = "job_postings"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 岗位要求：JSON 对象，如 {"years": 3, "skills": ["Python", "PostgreSQL"]}
    requirements: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
