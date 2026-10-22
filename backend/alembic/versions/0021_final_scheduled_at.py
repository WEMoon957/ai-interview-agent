"""终面时间：老板在操作台确认/改期终面时记录时间。

背景：此前老板只能对「待终试」的候选人做 pass/fail 决策，约终面的时间停留在
线下口头沟通，系统里没有落点，HR 和店长都看不到。本迁移补一个可空时间字段，
由老板在操作台安排，决策前可反复改期。

Revision ID: 0021_final_scheduled_at
Revises: 0020_personal_access_tokens
Create Date: 2026-10-22 12:30:00

注意：``alembic_version.version_num`` 是 ``varchar(32)``，revision id 必须 ≤32 字符
（首版命名 0021_final_interview_scheduled_at 有 34 字符，直接在 UPDATE 版本号时炸了）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021_final_scheduled_at"
down_revision: Union[str, None] = "0020_personal_access_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """recruitment_summaries 增加「终面时间」字段（可空，未安排时为 NULL）。"""
    op.add_column(
        "recruitment_summaries",
        sa.Column("final_interview_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：删除终面时间字段。"""
    op.drop_column("recruitment_summaries", "final_interview_scheduled_at")
