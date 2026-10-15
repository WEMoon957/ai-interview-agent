"""Offer → 入职闭环：offers 表 + 岗位「在招」开关。

Revision ID: 0010_offer_onboarding
Revises: 0009_company_profile
Create Date: 2026-10-15 12:00:00

说明（见 docs/设计方案-Offer入职闭环.md v2）：
- ``offers``：承接招聘汇总之后的「发起 → 审批 → 发出 → 候选人确认 → 入职」全流程。
  状态机共 10 态（draft / pending_approval / rejected / sent / accepted / declined /
  expired / withdrawn / onboarded / no_show），常量与流转规则统一由 ``app/services/offer.py`` 维护。
  部分唯一索引 ``uq_offers_summary_active`` 保证同一汇总同时只有一条「进行中」Offer。
- ``job_postings.is_active``：岗位「在招 / 停招」开关；停招岗位不允许新发起 Offer，存量默认 True。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0010_offer_onboarding"
down_revision: Union[str, None] = "0009_company_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 「进行中」状态（与 app/db/models/offer.py 的 _ACTIVE_STATUSES_SQL 保持一致）
_ACTIVE_STATUSES_SQL = "status IN ('draft','pending_approval','rejected','sent','accepted')"


def upgrade() -> None:
    """创建 offers 表，并为 job_postings 增加 is_active 列。"""
    # ── offers ────────────────────────────────────────────
    op.create_table(
        "offers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("summary_id", UUID(as_uuid=True), sa.ForeignKey("recruitment_summaries.id"), nullable=False, index=True),
        sa.Column("candidate_id", UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False, index=True),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
        # 快照字段
        sa.Column("candidate_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("job_title", sa.String(255), nullable=False, server_default=""),
        sa.Column("store_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("source", sa.String(32), nullable=False, server_default="online"),
        # Offer 内容
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("probation_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        # 状态（10 态）
        sa.Column("status", sa.String(32), nullable=False, server_default="draft", index=True),
        # 审批
        sa.Column("approver_open_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("approval_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("approval_message_id", sa.String(128), nullable=False, server_default=""),
        # 发出与确认
        sa.Column("token", sa.String(64), nullable=True, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdraw_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("decline_reason_text", sa.Text(), nullable=False, server_default=""),
        # 入职
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_report_date", sa.Date(), nullable=True),
        sa.Column("onboard_store", sa.String(255), nullable=False, server_default=""),
        sa.Column("no_show_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 惰性过期扫描复合索引：WHERE status='sent' AND expires_at < now()
    op.create_index("ix_offers_sent_expiry", "offers", ["status", "expires_at"])

    # 部分唯一索引：同一汇总下同时只允许一条「进行中」Offer
    op.create_index(
        "uq_offers_summary_active",
        "offers",
        ["summary_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_STATUSES_SQL),
    )

    # ── job_postings.is_active（岗位在招开关）──────────────
    op.add_column(
        "job_postings",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    """回滚：移除 job_postings.is_active 并删除 offers 表。"""
    op.drop_column("job_postings", "is_active")
    op.drop_index("uq_offers_summary_active", table_name="offers")
    op.drop_index("ix_offers_sent_expiry", table_name="offers")
    op.drop_table("offers")
