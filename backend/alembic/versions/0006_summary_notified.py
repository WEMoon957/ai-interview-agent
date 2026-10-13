"""recruitment_summaries 新增 notified_at（汇总复核推送幂等标记）。

Revision ID: 0006_summary_notified
Revises: 0005_skip_screening
Create Date: 2026-10-13 17:10:00

说明：
- 汇总（rebuild）完成后会向 HR 飞书推送「请复核并办理 offer / 入职」卡片；
- ``notified_at`` 为 NULL 表示该候选人的汇总尚未提醒过 HR，非空表示已提醒（逐候选人幂等，
  重复点击「生成/更新汇总落库」不会重复刷屏）；
- **存量已完成的汇总行一律回填为当前时间**，即视为「历史已提示」——
  避免本次上线后第一次 rebuild 就把历史候选人一次性全推给 HR 造成刷屏。
  如需补推，走后台「推送复核给 HR」按钮（手动、忽略幂等）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_summary_notified"
down_revision: Union[str, None] = "0005_skip_screening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增推送幂等时间列，并回填存量完成行。"""
    op.add_column(
        "recruitment_summaries",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE recruitment_summaries SET notified_at = now() WHERE is_complete = true"
    )


def downgrade() -> None:
    """回滚：移除推送幂等时间列。"""
    op.drop_column("recruitment_summaries", "notified_at")
