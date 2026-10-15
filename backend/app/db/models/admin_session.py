"""后台登录会话表模型。

会话方案：**服务端 Session + HttpOnly Cookie**。

- 浏览器只拿到随机 token 原文（放在 HttpOnly Cookie 里）；
- 库里只存 ``sha256(token)``，即使数据库被读走也无法直接冒用会话；
- 每条会话可被独立撤销（登出、改密、管理员踢下线），
  这正是选择服务端会话而非无状态 JWT 的原因。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AdminSession(TimestampMixin, Base):
    """后台登录会话（admin_sessions）。"""

    __tablename__ = "admin_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 会话令牌的 sha256 十六进制（64 字符），永不存原文
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # CSRF double-submit 校验值：同时写入非 HttpOnly 的 Cookie 与请求头，二者必须一致
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # 主动失效时间：登出 / 改密 / 管理员撤销时写入
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 会话创建来源（审计用，也可用于"异常地点登录"告警）
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(255), nullable=False, default="")
