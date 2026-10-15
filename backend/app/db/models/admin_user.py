"""后台账号表模型。

一张表同时承载"登录凭据"与"授权角色"，避免为了几个 HR 账号引入
RBAC 四张表的过度设计；角色用字符串常量表达，见 :data:`ADMIN_ROLE`。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 角色常量：admin 管账号与高危操作；hr 全业务功能；store_manager（店长）
# 只能录线下复试（含到店直招与打分），看不到简历/面试对话/汇总/Offer
ADMIN_ROLE = "admin"
HR_ROLE = "hr"
STORE_MANAGER_ROLE = "store_manager"
VALID_ROLES = (ADMIN_ROLE, HR_ROLE, STORE_MANAGER_ROLE)


class AdminUser(TimestampMixin, Base):
    """后台账号（admin_users）。

    安全要点：

    - ``password_hash`` 只存 Argon2id 编码串（见 ``app.security.passwords``），
      任何情况下不得写入明文。
    - ``failed_login_count`` / ``locked_until`` 支撑登录失败锁定（防爆破）。
    - 账号停用用 ``is_active`` 软标记，不物理删除，以保留审计关联。
    """

    __tablename__ = "admin_users"

    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # 展示名（前端右上角显示），不参与身份判定
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 角色：admin / hr
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=HR_ROLE, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── 防爆破状态 ─────────────────────────────────────────────
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── 凭据变更时间：用于"改密后旧会话失效"的兜底判定 ─────────
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 创建来源：CLI 脚本时为 "cli"，后台创建时为创建者的用户名
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
