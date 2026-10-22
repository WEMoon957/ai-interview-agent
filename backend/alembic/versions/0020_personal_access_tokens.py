"""个人访问令牌（PAT）：给程序化客户端一张可撤销、可限权的长期凭据。

背景：后台 60 个管理端点只认浏览器 Session + CSRF，而桌面 Agent / CLI
没有浏览器，也不该把 HR 的账号密码存在本地。本表补齐「机器身份」：
令牌绑定到具体用户（角色权限自动继承），只存 sha256 摘要，
可按 read/write scope 限权，可随时撤销。

Revision ID: 0020_personal_access_tokens
Revises: 0019_eval_evidence
Create Date: 2026-10-22 09:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0020_personal_access_tokens"
down_revision: Union[str, None] = "0019_eval_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建 personal_access_tokens 表。"""
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        # 只存 sha256 十六进制摘要，永不存原文
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        # 明文前 12 字符，仅供列表展示与日志识别
        sa.Column("token_prefix", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("scopes", sa.String(length=255), nullable=False, server_default="read"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_personal_access_tokens_user_id", "personal_access_tokens", ["user_id"]
    )
    op.create_index(
        "ix_personal_access_tokens_token_hash",
        "personal_access_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_personal_access_tokens_expires_at", "personal_access_tokens", ["expires_at"]
    )


def downgrade() -> None:
    """回滚：删表（含索引）。"""
    op.drop_index("ix_personal_access_tokens_expires_at", table_name="personal_access_tokens")
    op.drop_index("ix_personal_access_tokens_token_hash", table_name="personal_access_tokens")
    op.drop_index("ix_personal_access_tokens_user_id", table_name="personal_access_tokens")
    op.drop_table("personal_access_tokens")
