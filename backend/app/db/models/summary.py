"""招聘汇总表模型。

将一个候选人在同一岗位下的全链路结果（简历初筛分 / AI 面试分 / 线下复试分）
聚合成一条汇总记录落库，供报表与外部系统消费。
一（岗位, 候选人）一条，重复生成时覆盖更新（幂等）。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


def _utcnow() -> datetime:
    """UTC 当前时间（与 Base 口径一致）。"""
    return datetime.now(timezone.utc)


class RecruitmentSummary(TimestampMixin, Base):
    """招聘汇总（recruitment_summaries）：一岗位一候选人一条。"""

    __tablename__ = "recruitment_summaries"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_summary_job_candidate"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    candidate_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # 各阶段得分（可能缺环节，故可空）
    screening_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_interview_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    offline_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 最终加权得分（百分制）
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 当前阶段：screened / ai_interview / ai_done / offline_scheduled / offline_done / closed
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="screened")
    # 最终结论：强烈推荐/推荐/待定/不推荐
    final_verdict: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    # 是否已走完全链路（初筛 + AI 面试 + 线下复试）
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 汇总文本 + 各阶段明细（快照）
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # 汇总复核提醒推送到 HR 的时间；NULL 表示尚未提醒（逐候选人幂等标记）
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── 终试闸门（老板拍板）─────────────────────────────────
    # 候选人在线下复试完成后，汇总自动进入「待终试」(pending_final)，
    # 老板在「老板操作台」点 通过/不通过；只有 passed_final 才允许 HR 发起 Offer。
    # none=历史/未进入终试流程（兼容老数据，允许发 Offer）；
    # pending_final=等老板拍板；passed_final=终试通过；failed_final=终试未通过。
    final_interview_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", index=True
    )
    final_interview_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 老板确认的终面时间（老板在操作台安排/改期；NULL = 尚未安排）
    final_interview_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_interview_decided_by: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    final_interview_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    candidate = relationship("Candidate", lazy="selectin")
    job = relationship("JobPosting", lazy="selectin")
