"""offer 表新增「录用通知书」字段

候选人确认页改为正式《录用通知书》版式，需要以下由 HR 填写的字段：
- work_location     工作地点
- contact_name      联系人姓名
- contact_phone     联系人电话
- probation_salary  试用期薪资（文本，HR 自由填写，如「固定 8500 元」）
- regular_salary    转正后薪资（文本，如「10000 元/月」）
- report_time       报到具体时间（如「上午 9:00」）

均为带默认值的非空文本列，存量数据自动填充为空串，不影响既有 Offer。

Revision ID: 0013_offer_letter
Revises: 0012_widen_approver
Create Date: 2026-10-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_offer_letter"
down_revision: Union[str, None] = "0012_widen_approver"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("work_location", sa.String(255)),
    ("contact_name", sa.String(64)),
    ("contact_phone", sa.String(64)),
    ("probation_salary", sa.String(128)),
    ("regular_salary", sa.String(128)),
    ("report_time", sa.String(64)),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column(
            "offers",
            sa.Column(name, type_, nullable=False, server_default=""),
        )


def downgrade() -> None:
    for name, _type in COLUMNS:
        op.drop_column("offers", name)
