"""session_reports 新增 notified_at（面试达标推送幂等标记）。

Revision ID: 0003_report_notified
Revises: 0002_session_consent
Create Date: 2026-10-13 00:00:00

说明：
- notified_at 为 NULL 表示该报告尚未推送给 HR；非空表示已推送（幂等标记）。
- 仅在 FEISHU_NOTIFY_ENABLED=true 且综合分达阈值时由 final_report 写入。
- 存量报告默认 NULL（不回溯推送，避免上线瞬间刷屏）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_report_notified"
down_revision: Union[str, None] = "0002_session_consent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增推送幂等时间列。"""
    op.add_column(
        "session_reports",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：移除推送幂等时间列。"""
    op.drop_column("session_reports", "notified_at")
