"""招聘汇总增加「终试闸门」字段（老板拍板）。

背景：原系统只有 Offer 审批（录用）这一道老板关卡，且走飞书卡片。
新增「终试」关卡 —— 候选人线下复试完成后汇总自动进入 pending_final，
老板在「老板操作台」点 通过/不通过，passed_final 才允许 HR 发起 Offer。

Revision ID: 0017_final_interview
Revises: 0016_feishu_minutes_auth
Create Date: 2026-10-20 09:50:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0017_final_interview"
down_revision: Union[str, None] = "0016_feishu_minutes_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """招聘汇总表增加终试闸门字段（默认 none，兼容老数据）。"""
    op.add_column(
        "recruitment_summaries",
        sa.Column(
            "final_interview_status",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    op.create_index(
        "ix_recruitment_summaries_final_interview_status",
        "recruitment_summaries",
        ["final_interview_status"],
    )
    op.add_column(
        "recruitment_summaries",
        sa.Column("final_interview_note", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "recruitment_summaries",
        sa.Column(
            "final_interview_decided_by",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "recruitment_summaries",
        sa.Column("final_interview_decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：删除终试闸门字段与索引。"""
    op.drop_index(
        "ix_recruitment_summaries_final_interview_status",
        table_name="recruitment_summaries",
    )
    op.drop_column("recruitment_summaries", "final_interview_decided_at")
    op.drop_column("recruitment_summaries", "final_interview_decided_by")
    op.drop_column("recruitment_summaries", "final_interview_note")
    op.drop_column("recruitment_summaries", "final_interview_status")
