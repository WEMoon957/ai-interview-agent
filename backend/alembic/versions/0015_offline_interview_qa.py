"""offline_interviews 新增「结构化问答抽取」字段（供 BI 看板消费）。

Revision ID: 0015_offline_qa
Revises: 0014_offer_candidate_email
Create Date: 2026-10-19 14:30:00

说明：
- ``qa_extract``（JSONB）：整场逐字稿抽取出的「面试官问题 + 候选人回答亮点」结构化结果，
  形如 ``{"questions": [{"question", "answer_summary", "highlights": [...]}], "overall": "..."}``。
- ``qa_extracted_at``：抽取落库时间。
- 该字段**仅数据看板（bi/）消费**；业务后端序列化（``_offline_out``）不返回，故系统侧无表现。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0015_offline_qa"
down_revision: Union[str, None] = "0014_offer_candidate_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增结构化问答字段（存量记录默认空对象，不受影响）。"""
    op.add_column(
        "offline_interviews",
        sa.Column("qa_extract", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("qa_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：移除结构化问答字段。"""
    op.drop_column("offline_interviews", "qa_extracted_at")
    op.drop_column("offline_interviews", "qa_extract")
