"""岗位表模型。"""
from sqlalchemy import Boolean, String, Text
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
    # 免简历初筛：置 True 时上传简历直接生成 AI 面试链接，跳过 AI 初筛打分
    skip_screening: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 在招开关：停招（False）后不允许再为该岗位发起 Offer，已有 Offer 不受影响
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
