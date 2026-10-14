"""到店直招录音节点：飞书妙记转写字段。

Revision ID: 0008_feishu_minutes
Revises: 0007_walk_in_audio
Create Date: 2026-10-14 11:20:00

说明：
- ``offline_interviews.walk_in_code``：建档生成的 6 位短码（唯一），店长把妙记标题填成它。
- ``offline_interviews.interviewer_open_id``：店长飞书 open_id，轮询按所有者过滤用。
- ``offline_interviews.minutes_*``：匹配到的妙记 token / 链接 / 标题快照。
- ``offline_interviews.transcript_*``：逐字稿全文 + 转写状态机。
- ``offline_interviews.ai_*``：整场逐字稿整体评分产出的 AI 参考分（与人工 score 并存）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0008_feishu_minutes"
down_revision: Union[str, None] = "0007_walk_in_audio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增飞书妙记转写相关字段。"""
    op.add_column(
        "offline_interviews",
        sa.Column("walk_in_code", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "uq_offline_walk_in_code",
        "offline_interviews",
        ["walk_in_code"],
        unique=True,
    )
    op.add_column(
        "offline_interviews",
        sa.Column("interviewer_open_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("minutes_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_offline_minutes_token",
        "offline_interviews",
        ["minutes_token"],
        unique=False,
    )
    op.add_column(
        "offline_interviews",
        sa.Column("minutes_url", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("minutes_title", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("transcript_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("transcript_status", sa.String(length=32), nullable=False, server_default="none"),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("transcript_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("ai_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("ai_dim_scores", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("ai_comment", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("ai_scored_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：移除本轮新增字段。"""
    op.drop_column("offline_interviews", "ai_scored_at")
    op.drop_column("offline_interviews", "ai_comment")
    op.drop_column("offline_interviews", "ai_dim_scores")
    op.drop_column("offline_interviews", "ai_score")
    op.drop_column("offline_interviews", "transcript_at")
    op.drop_column("offline_interviews", "transcript_status")
    op.drop_column("offline_interviews", "transcript_text")
    op.drop_column("offline_interviews", "minutes_title")
    op.drop_column("offline_interviews", "minutes_url")
    op.drop_index("ix_offline_minutes_token", table_name="offline_interviews")
    op.drop_column("offline_interviews", "minutes_token")
    op.drop_column("offline_interviews", "interviewer_open_id")
    op.drop_index("uq_offline_walk_in_code", table_name="offline_interviews")
    op.drop_column("offline_interviews", "walk_in_code")
