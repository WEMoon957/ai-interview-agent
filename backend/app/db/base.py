"""SQLAlchemy 声明式基类与公共审计字段。"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """返回 UTC 当前时间（统一时间口径，见共享知识 #12）。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


class TimestampMixin:
    """公共审计字段：id（UUID 主键）+ created_at + updated_at。

    7 张业务表统一使用该 Mixin（见共享知识 #11）。
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
