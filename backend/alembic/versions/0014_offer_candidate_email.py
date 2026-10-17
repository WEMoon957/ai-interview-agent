"""offers 表新增「候选人确认邮箱」字段

候选人确认接受 Offer 时填写邮箱（必填），用于接受后自动发送《入职指引》邮件。
存量数据自动填充为空串，不影响既有 Offer。

Revision ID: 0014_offer_candidate_email
Revises: 0013_offer_letter
Create Date: 2026-10-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_offer_candidate_email"
down_revision: Union[str, None] = "0013_offer_letter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("candidate_email", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("offers", "candidate_email")
