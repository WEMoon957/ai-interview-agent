"""安全审计日志表模型。

记录"谁在什么时候对什么做了什么"，用于事后追溯与异常行为发现。

写入纪律（由 ``app.services.audit`` 保证）：

- **绝不写入**密码明文、密码哈希、会话 token 原文、CSRF 值；
- ``detail`` 只放已脱敏的辅助信息（如被修改的字段名、失败原因分类）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 审计动作常量（供查询与测试引用，避免各处拼字符串）
ACTION_LOGIN_SUCCESS = "auth.login.success"
ACTION_LOGIN_FAILED = "auth.login.failed"
ACTION_LOGIN_LOCKED = "auth.login.locked"
ACTION_LOGOUT = "auth.logout"
ACTION_PASSWORD_CHANGED = "auth.password.changed"
ACTION_USER_CREATED = "admin.user.created"
ACTION_USER_DISABLED = "admin.user.disabled"
ACTION_SESSION_REVOKED = "admin.session.revoked"
ACTION_ADMIN_WRITE = "admin.write"


class AuditLog(Base):
    """审计日志（audit_logs）。

    不挂业务外键级联：``actor_id`` 用 SET NULL，同时冗余 ``actor_name``，
    保证账号被删除后日志依然可读——审计记录本身必须比它记录的对象活得久。

    也不使用 ``TimestampMixin``：日志只写不改，``updated_at`` 是纯冗余；
    时间字段统一叫 ``occurred_at``，语义上强调"事件发生时刻"。
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 操作目标（如 "offers/xxxx"、"admin_users/hr01"），自由文本
    target: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 已脱敏的补充信息（JSON 字符串或纯文本），禁止写入任何凭据
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
