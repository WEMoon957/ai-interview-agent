"""岗位免初筛开关 + 简历初筛分可空。

Revision ID: 0005_skip_screening
Revises: 0004_recruitment_pipeline
Create Date: 2026-10-13 16:20:00

说明：
- job_postings.skip_screening：置 true 的岗位「免简历初筛」，上传简历即直接生成 AI 面试链接。
- resume_screenings.total_score 放开 NOT NULL：免初筛的投递记录不打分（NULL），
  汇总时自然不计入加权（compute_final_score 对 None 自动跳过并重新归一化）。
  存量记录仍为具体分值，不受影响。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005_skip_screening"
down_revision: Union[str, None] = "0004_recruitment_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增岗位免初筛开关，并允许初筛分为空。"""
    op.add_column(
        "job_postings",
        sa.Column(
            "skip_screening",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "resume_screenings",
        "total_score",
        existing_type=sa.Float(),
        nullable=True,
        existing_server_default=None,
    )


def downgrade() -> None:
    """回滚：初筛分回填空值后恢复 NOT NULL，并移除免初筛开关。"""
    # 回滚前把 NULL 初筛分归一为 0，避免恢复 NOT NULL 失败
    op.execute("UPDATE resume_screenings SET total_score = 0 WHERE total_score IS NULL")
    op.alter_column(
        "resume_screenings",
        "total_score",
        existing_type=sa.Float(),
        nullable=False,
    )
    op.drop_column("job_postings", "skip_screening")
