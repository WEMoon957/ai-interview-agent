"""新增「面试逐轮问答原话」表。

背景：AI 面试中候选人的回答原文此前只写进 LangGraph 的 messages（checkpoint 表，
msgpack 序列化），业务库 answer_evaluations 仅有分数与 AI 评语。HR 在评分报告里
只能看到 AI 转述的评语，拿不到候选人原话，无法复盘「他到底是怎么回答的」。

本迁移新增 interview_turns：每轮「AI 提问原文 + 候选人回答原文」一行，
作为原话的唯一事实源；与 answer_evaluations 按 (session_id, turn_index) 对齐，
支撑报告页展示完整证据链。

Revision ID: 0018_interview_turns
Revises: 0017_final_interview
Create Date: 2026-10-20 15:40:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0018_interview_turns"
down_revision: Union[str, None] = "0017_final_interview"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建表 interview_turns（原话唯一事实源）。"""
    op.create_table(
        "interview_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "question_type", sa.String(length=32), nullable=False, server_default="subjective"
        ),
        sa.Column("is_follow_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("question_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["interview_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "turn_index", name="uq_interview_turns_session_turn"
        ),
    )
    op.create_index(
        "ix_interview_turns_session_id", "interview_turns", ["session_id"]
    )


def downgrade() -> None:
    """回滚：删除原话表。"""
    op.drop_index("ix_interview_turns_session_id", table_name="interview_turns")
    op.drop_table("interview_turns")
