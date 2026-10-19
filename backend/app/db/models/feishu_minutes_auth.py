"""飞书妙记个人授权表模型。

为什么需要这张表
----------------
飞书妙记的**应用身份**（tenant_access_token）只能读取「已公开」的妙记，而公开
只能由妙记所有者在客户端逐条设置 —— 无法满足「店长零操作」的要求。

读类接口（search / get / transcript / media）同时支持 **user_access_token**：
用户 token 的数据范围 = 该用户自己可读的妙记，**私有妙记也能读**，无需公开。
因此系统改为：每个店长/HR 首次做一次设备授权（扫码确认），后端保存其
access_token + refresh_token，后续以该用户身份拉取她名下的妙记。

安全说明
--------
- refresh_token 有效期约 30 天，access_token 约 2 小时；刷新时 refresh_token
  会轮换，必须回写最新值（旧值立即失效）。
- 表内 token 为明文（服务端需要原文调用飞书 API）；仅内网数据库可见，接口层
  永不返回 token 原文，只返回状态与过期时间。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class FeishuMinutesAuth(TimestampMixin, Base):
    """飞书妙记个人授权（feishu_minutes_auth）。"""

    __tablename__ = "feishu_minutes_auth"

    # 授权主体：飞书用户 open_id（一个用户一条，重复授权覆盖）
    open_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # user_access_token / refresh_token（明文，供服务端调用飞书；接口层不返回）
    access_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scope: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # access_token 过期时间（UTC）；提前 5 分钟视为需要刷新
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # refresh_token 过期时间；过期后必须重新授权
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # active（可用）/ expired（refresh 过期或刷新失败）/ revoked（管理员撤销）
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 触发授权的后台账号（admin_users.username），用于审计
    authorized_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
