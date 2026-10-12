"""简历表模型。"""
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Resume(TimestampMixin, Base):
    """简历（resumes）：parsed_text 存解析后的纯文本。"""

    __tablename__ = "resumes"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    file_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 简历解析结果（纯文本），解析失败降级为空串（共享知识 A5）
    parsed_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    candidate = relationship("Candidate", lazy="selectin")
