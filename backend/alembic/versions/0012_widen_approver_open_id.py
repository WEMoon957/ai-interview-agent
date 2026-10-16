"""加宽 offers.approver_open_id 以支持多审批人

原因：该字段原为 ``VARCHAR(64)``，只够存一个 open_id（``ou_`` + 32 位 = 35 字符）。
配置项 ``OFFER_APPROVER_OPEN_IDS`` 允许逗号分隔的**多个**审批人，
两个即达 71 字符 → Postgres 抛 ``StringDataRightTruncation``，提交审批接口返回 500。

Revision ID: 0012_widen_approver
Revises: 0011_admin_auth
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_widen_approver"
down_revision: Union[str, None] = "0011_admin_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "offers",
        "approver_open_id",
        existing_type=sa.String(64),
        type_=sa.String(512),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "offers",
        "approver_open_id",
        existing_type=sa.String(512),
        type_=sa.String(64),
        existing_nullable=False,
    )
