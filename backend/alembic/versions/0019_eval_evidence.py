"""逐题评分增加「行为锚定证据」字段。

背景：评分从「整体印象打分」改为「先摘原话证据并定级，再按等级给分」。
evidence 保存每条评分依据（维度 / 等级 / 原话片段 / 理由 / 是否通过原文校验），
用于报告页展示「凭哪句话给的分」，并让引用可被程序化审计。

Revision ID: 0019_eval_evidence
Revises: 0018_interview_turns
Create Date: 2026-10-20 16:25:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0019_eval_evidence"
down_revision: Union[str, None] = "0018_interview_turns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """answer_evaluations 增加 evidence（JSONB 数组，默认空）。"""
    op.add_column(
        "answer_evaluations",
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    """回滚：删除证据字段。"""
    op.drop_column("answer_evaluations", "evidence")
