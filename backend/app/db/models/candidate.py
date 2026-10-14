"""候选人表模型。"""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Candidate(TimestampMixin, Base):
    """候选人（candidates）。"""

    __tablename__ = "candidates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 来源渠道：门店海报 / 员工介绍 / 自然到访 …（到店直招必填，线上投递可空）
    source_channel: Mapped[str] = mapped_column(String(64), nullable=False, default="")
