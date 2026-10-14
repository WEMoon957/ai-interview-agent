"""公司文化档案：公司介绍单例表 + 模板文化配置 + 会话公司介绍快照。

Revision ID: 0009_company_profile
Revises: 0008_feishu_minutes
Create Date: 2026-10-14 12:40:00

说明（见 docs/PRD-企业文化融入面试题.md）：
- ``company_profiles``：全局单例，HR 自由撰写的一段公司介绍；含最近保存人与审计时间。
- ``interview_templates.culture_fit``：文化匹配配置（``{"enabled":..., "dimension_key":...}``）。
- ``interview_sessions.company_profile_snapshot``：发链接时快照的公司介绍
  （``{"content": "..."}``）；存量行回填 ``{}``，与「未配置」等价，旧会话行为不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0009_company_profile"
down_revision: Union[str, None] = "0008_feishu_minutes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增公司文化档案表，并为模板 / 会话补充文化相关列。"""
    # ── company_profiles（全局单例）─────────────────────────
    op.create_table(
        "company_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("updated_by", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── interview_templates.culture_fit ────────────────────
    op.add_column(
        "interview_templates",
        sa.Column(
            "culture_fit",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # ── interview_sessions.company_profile_snapshot ────────
    op.add_column(
        "interview_sessions",
        sa.Column(
            "company_profile_snapshot",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """回滚：移除本轮新增列与表。"""
    op.drop_column("interview_sessions", "company_profile_snapshot")
    op.drop_column("interview_templates", "culture_fit")
    op.drop_table("company_profiles")
