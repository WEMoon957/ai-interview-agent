"""Offer 表模型（offers）。

招聘汇总完成后，HR 对候选人发起的 Offer 全流程记录：
起草 → 提交审批 → 审批通过并发出 → 候选人接受/婉拒/超期 → 办理入职（报到/未报到）。

状态机常量与合法流转规则统一维护在 ``app/services/offer.py``（唯一事实源），
本模块只负责持久化结构与数据库级约束（部分唯一索引）。
"""
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# 「进行中」状态（同一招聘汇总下同时只允许一条进行中的 Offer）。
# 与 services/offer.py 的 ACTIVE_STATUSES 保持一致；此处为数据库级兜底（部分唯一索引）。
_ACTIVE_STATUSES_SQL = "status IN ('draft','pending_approval','rejected','sent','accepted')"


class Offer(TimestampMixin, Base):
    """Offer（offers）：一条记录承载「发起 → 审批 → 发出 → 确认 → 入职」全生命周期。"""

    __tablename__ = "offers"
    __table_args__ = (
        # 同一汇总下只允许一条「进行中」Offer（终态后可另起新记录，保留历史痕迹）
        Index(
            "uq_offers_summary_active",
            "summary_id",
            unique=True,
            postgresql_where=sa.text(_ACTIVE_STATUSES_SQL),
        ),
        # 惰性过期扫描：WHERE status='sent' AND expires_at < now()
        Index("ix_offers_sent_expiry", "status", "expires_at"),
    )

    # ── 关联 ─────────────────────────────────────────────────
    summary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recruitment_summaries.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False, index=True
    )

    # ── 快照字段（创建时冗余，之后改岗位/候选人信息不影响已发出的 Offer）──
    candidate_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    job_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    store_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 来源快照：online（线上投递）/ walk_in（到店直招）
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="online")

    # ── Offer 内容 ───────────────────────────────────────────
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    probation_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # 备注（薪资等敏感信息不入库，需要时自由填写）
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 录用通知书字段（候选人确认页 /o/{token} 展示）──────────────
    # 这几项由 HR 发起 Offer 时填写（每个候选人可能不同）；
    # 公司名、报到携带资料、法律声明为**固定文案**，见 services/offer_letter.py
    work_location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    contact_phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    probation_salary: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    regular_salary: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    report_time: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # ── 状态（10 态，见 services/offer.py）────────────────────
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)

    # ── 审批 ─────────────────────────────────────────────────
    # 逗号分隔的审批人 open_id 列表（单个 open_id 35 字符；支持多审批人，故留足 512）
    approver_open_id: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    approval_submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 审批意见（驳回时必填）
    approval_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 审批卡片消息 id（飞书回调后用于把卡片更新为「已通过 / 已驳回」）
    approval_message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # ── 发出与候选人确认 ─────────────────────────────────────
    # 审批通过时生成，候选人凭此访问 /o/{token}
    token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    # 审批通过时 = 发出日次日的本地 08:00（配置项 offer_confirm_hour / offer_confirm_tz_offset）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 候选人接受/婉拒时间
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 惰性过期流转时间
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdraw_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 候选人确认时填写的邮箱（必填，用于接受后发送《入职指引》邮件）
    candidate_email: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # 候选人婉拒理由（自由文本）
    decline_reason_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 入职 ─────────────────────────────────────────────────
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    onboard_store: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 未报到说明（标记 no_show 时必填）
    no_show_note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── 关系（selectin 预加载，与既有模型口径一致）─────────────
    candidate = relationship("Candidate", lazy="selectin")
    job = relationship("JobPosting", lazy="selectin")
    summary = relationship("RecruitmentSummary", lazy="selectin")
