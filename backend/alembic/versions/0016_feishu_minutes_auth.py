"""新增「飞书妙记个人授权」表（user_access_token）。

背景：应用身份（bot）只能读「公开」妙记，而公开只能由所有者在客户端逐条设置。
为实现「店长零操作」，系统改为以妙记所有者本人的 user_access_token 读取
（用户 token 的数据范围 = 该用户自己可读的妙记，私有也能读）。

Revision ID: 0016_feishu_minutes_auth
Revises: 0015_offline_qa
Create Date: 2026-10-19 15:20:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0016_feishu_minutes_auth"
down_revision: Union[str, None] = "0015_offline_qa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建妙记个人授权表。"""
    op.create_table(
        "feishu_minutes_auth",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("open_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("access_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("refresh_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorized_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_feishu_minutes_auth_open_id", "feishu_minutes_auth", ["open_id"], unique=True
    )
    op.create_index(
        "ix_feishu_minutes_auth_expires_at", "feishu_minutes_auth", ["expires_at"]
    )


def downgrade() -> None:
    """回滚：删除妙记个人授权表。"""
    op.drop_index("ix_feishu_minutes_auth_expires_at", table_name="feishu_minutes_auth")
    op.drop_index("ix_feishu_minutes_auth_open_id", table_name="feishu_minutes_auth")
    op.drop_table("feishu_minutes_auth")
