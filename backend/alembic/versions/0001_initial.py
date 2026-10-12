"""首版迁移：创建 7 张业务表。

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00

说明：
- 7 张业务表与 LangGraph checkpointer 复用同一 PostgreSQL 实例；
- checkpointer 相关表（checkpoints / checkpoint_blobs / checkpoint_writes）由
  PostgresSaver.setup() 自建，不在本迁移中（见共享知识 #6）。
- id / created_at / updated_at 的默认值由 ORM 端提供（TimestampMixin），
  迁移脚本不设 server_default，仅约束 NOT NULL。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _audit_columns() -> list[sa.Column]:
    """公共审计列：id（UUID 主键）+ created_at + updated_at。"""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    """创建 7 张业务表（按外键依赖顺序）。"""

    # 1. job_postings
    op.create_table(
        "job_postings",
        *_audit_columns(),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # 2. interview_templates
    op.create_table(
        "interview_templates",
        *_audit_columns(),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_postings.id"),
            nullable=False,
        ),
        sa.Column(
            "questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rubric",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "duration_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "max_follow_ups",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
    )
    op.create_index("ix_interview_templates_job_id", "interview_templates", ["job_id"])

    # 3. candidates
    op.create_table(
        "candidates",
        *_audit_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
    )

    # 4. resumes
    op.create_table(
        "resumes",
        *_audit_columns(),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id"),
            nullable=False,
        ),
        sa.Column("file_url", sa.String(length=512), nullable=True),
        sa.Column("parsed_text", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.create_index("ix_resumes_candidate_id", "resumes", ["candidate_id"])

    # 5. interview_sessions
    op.create_table(
        "interview_sessions",
        *_audit_columns(),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_templates.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_interview_sessions_token", "interview_sessions", ["token"], unique=True)
    op.create_index("ix_interview_sessions_thread_id", "interview_sessions", ["thread_id"], unique=True)
    op.create_index("ix_interview_sessions_template_id", "interview_sessions", ["template_id"])
    op.create_index("ix_interview_sessions_candidate_id", "interview_sessions", ["candidate_id"])

    # 6. answer_evaluations
    op.create_table(
        "answer_evaluations",
        *_audit_columns(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column(
            "scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("comment", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "follow_up_needed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "follow_up_question",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.create_index("ix_answer_evaluations_session_id", "answer_evaluations", ["session_id"])

    # 7. session_reports
    op.create_table(
        "session_reports",
        *_audit_columns(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "dim_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "total_score",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("verdict", sa.String(length=32), nullable=False, server_default=sa.text("''")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.create_index("ix_session_reports_session_id", "session_reports", ["session_id"], unique=True)


def downgrade() -> None:
    """按依赖逆序删除 7 张业务表。"""
    op.drop_table("session_reports")
    op.drop_table("answer_evaluations")
    op.drop_table("interview_sessions")
    op.drop_table("resumes")
    op.drop_table("candidates")
    op.drop_table("interview_templates")
    op.drop_table("job_postings")
