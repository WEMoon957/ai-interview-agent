"""interview_sessions 新增 consent_agreed_at（P1-7 候选人知情同意）。

Revision ID: 0002_session_consent
Revises: 0001_initial
Create Date: 2026-10-12 00:00:00

说明：
- consent_agreed_at 为 NULL 表示候选人尚未同意《安全知情协议》，
  此时候选人端不可进入面试（不跑图、不可提交回答）；
- 存量会话不回溯填充（知情同意必须由候选人本人在线确认）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_session_consent"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增知情同意时间列。"""
    op.add_column(
        "interview_sessions",
        sa.Column("consent_agreed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：移除知情同意时间列。"""
    op.drop_column("interview_sessions", "consent_agreed_at")