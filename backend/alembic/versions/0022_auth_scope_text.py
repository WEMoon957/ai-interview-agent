"""妙记授权 scope 改为不限制长度。

背景：设备授权换 token 时，飞书会把应用已开通的权限一并写进 `scope`
（除妙记两三项外，还有 base / docx / wiki / search 等），长度可达数千字符。
原 `varchar(1024)` 在扫码授权那一步直接抛
`StringDataRightTruncation: value too long for type character varying(1024)`，
前端只看到「服务器错误」，授权永远存不进去。

access_token / refresh_token 本身就是 Text，本次只放宽 scope。

Revision ID: 0022_auth_scope_text
Revises: 0021_final_scheduled_at
Create Date: 2026-10-22 14:45:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0022_auth_scope_text"
down_revision: Union[str, None] = "0021_final_scheduled_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """scope: varchar(1024) -> text。"""
    op.alter_column(
        "feishu_minutes_auth",
        "scope",
        existing_type=sa.String(length=1024),
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using="scope::text",
    )


def downgrade() -> None:
    """回滚：截断到 1024 字符再收回 varchar（超长部分会丢失，仅用于回退）。"""
    op.alter_column(
        "feishu_minutes_auth",
        "scope",
        existing_type=sa.Text(),
        type_=sa.String(length=1024),
        existing_nullable=False,
        postgresql_using="left(scope, 1024)",
    )
