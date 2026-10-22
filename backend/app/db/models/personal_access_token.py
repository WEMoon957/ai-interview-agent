"""个人访问令牌（PAT）表模型。

为什么要这张表
--------------
浏览器会话（``admin_sessions``）是给人用的：12 小时过期、绑定 Cookie + CSRF。
而程序化客户端（MCP 服务器、CLI、未来桌面 Agent）没有浏览器，也不该
把 HR 的账号密码存在本地。PAT 就是这个缺口的补齐：

- 一次签发，长期使用（默认 90 天，可撤销）；
- 库里只存 ``sha256(token)``，泄库也无法冒用；
- 绑定到具体用户 → 角色权限（``require_role``）自动继续生效，
  审计能追到"是哪个人、哪台设备、是不是 Agent 调的"；
- 用 scope 区分只读与可写，让"只查数据"的 Agent 拿不到写权限。

与 ``admin_sessions`` 的差异：会话有 CSRF（因为靠 Cookie 自动携带），
PAT 走 ``Authorization`` 头、不会被浏览器自动附带，因此不需要 CSRF。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PersonalAccessToken(TimestampMixin, Base):
    """个人访问令牌（personal_access_tokens）。"""

    __tablename__ = "personal_access_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 设备/用途标识（如 "HR-李俊颖-MacBook"），便于本人识别与事后撤销
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # 令牌原文的 sha256 十六进制（64 字符），永不存原文
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # 明文前缀（前 12 字符，如 "ia_pat_AbCd"），仅供列表展示与日志识别，不足以冒用
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    # 逗号分隔的权限范围：read / write（write 隐含 read）
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="read")

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 主动失效时间：本人撤销或管理员回收时写入，不物理删除以保留审计线索
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
