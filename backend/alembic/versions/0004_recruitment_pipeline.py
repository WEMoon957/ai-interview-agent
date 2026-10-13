"""招聘全链路：简历初筛 / 线下复试 / 招聘汇总 三张新表。

Revision ID: 0004_recruitment_pipeline
Revises: 0003_report_notified
Create Date: 2026-10-13 16:00:00

说明：
- resume_screenings：岗位文件夹批量上传后，AI 对每份简历的初筛打分（按分排序）。
- offline_interviews：HR/店长对晋级候选人的线下复试约面与人工打分。
- recruitment_summaries：一（岗位, 候选人）一条，聚合全链路分数后落库（幂等覆盖）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0004_recruitment_pipeline"
down_revision: Union[str, None] = "0003_report_notified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建招聘全链路三张表。"""
    # ── resume_screenings ─────────────────────────────────
    op.create_table(
        "resume_screenings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
        sa.Column("candidate_id", UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False, index=True),
        sa.Column("resume_id", UUID(as_uuid=True), sa.ForeignKey("resumes.id"), nullable=True),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("folder_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("file_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("total_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("dim_scores", JSONB(), nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("highlights", JSONB(), nullable=False, server_default="[]"),
        sa.Column("risks", JSONB(), nullable=False, server_default="[]"),
        sa.Column("recommendation", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="screened"),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("interview_sessions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── offline_interviews ────────────────────────────────
    op.create_table(
        "offline_interviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("screening_id", UUID(as_uuid=True), sa.ForeignKey("resume_screenings.id"), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("interview_sessions.id"), nullable=True),
        sa.Column("candidate_id", UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False, index=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("interviewer", sa.String(255), nullable=False, server_default=""),
        sa.Column("interviewer_role", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("dim_scores", JSONB(), nullable=False, server_default="[]"),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("decision", sa.String(32), nullable=False, server_default=""),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── recruitment_summaries ─────────────────────────────
    op.create_table(
        "recruitment_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
        sa.Column("candidate_id", UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False, index=True),
        sa.Column("candidate_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("screening_score", sa.Float(), nullable=True),
        sa.Column("ai_interview_score", sa.Float(), nullable=True),
        sa.Column("offline_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False, server_default="screened"),
        sa.Column("final_verdict", sa.String(32), nullable=False, server_default=""),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "candidate_id", name="uq_summary_job_candidate"),
    )


def downgrade() -> None:
    """回滚：删除三张表。"""
    op.drop_table("recruitment_summaries")
    op.drop_table("offline_interviews")
    op.drop_table("resume_screenings")
