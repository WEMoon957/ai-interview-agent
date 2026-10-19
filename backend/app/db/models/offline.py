"""线下复试表模型。

AI 面试达标后，由 HR 或店长约线下复试并人工打分：本表记录约面信息
（时间/地点/面试官）+ 打分结果（总分/维度/评语/录用结论）。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class OfflineInterview(TimestampMixin, Base):
    """线下复试（offline_interviews）。"""

    __tablename__ = "offline_interviews"

    # 来源：可来自初筛记录 / AI 面试会话（均可空，兼容直接手工录入）
    screening_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_screenings.id"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False, index=True
    )

    # 来源路径：online（线上投递）/ walk_in（到店直招，无初筛无 AI 面试）
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="online")

    # 约面信息
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    interviewer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 面试官角色：hr / store_manager（店长）
    interviewer_role: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # 状态：scheduled（已约）/ done（已完成）/ no_show（爽约）/ cancelled（已取消）
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")

    # 店长上传的整场面试录音（相对 upload_dir 的路径；文件落盘，见 services/audio_storage.py）
    audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audio_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    audio_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── 飞书妙记录音转写（录音节点：店长开妙记录音 → 后端轮询拉逐字稿 → AI 参考分）────
    # 到店直招建档生成的 6 位短码，店长把它填成妙记标题，后端据此匹配关联
    walk_in_code: Mapped[str | None] = mapped_column(
        String(16), nullable=True, unique=True, index=True
    )
    # 店长飞书 open_id（通讯录解析，用于轮询按所有者过滤；解析不到则走手动绑定）
    interviewer_open_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 匹配到的妙记 token（/minutes/<token> 末段）
    minutes_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    minutes_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    minutes_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 逐字稿全文与转写状态：none → pending → matched → transcribed；失败置 failed
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    transcript_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AI 参考分（整场逐字稿整体评分，与人工 score 并存，不覆盖）
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_dim_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    ai_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 结构化问答抽取（问题 + 回答亮点）—— 由逐字稿富化而来，**仅数据看板（bi/）消费**；
    # 业务系统序列化（_offline_out）刻意不返回，属「不做业务展示」的约定。
    qa_extract: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    qa_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 打分结果（人工）
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 维度得分：[{"key","name","score","max_score"}]
    dim_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 录用结论：pass / hold / reject
    decision: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidate = relationship("Candidate", lazy="selectin")
    job = relationship("JobPosting", lazy="selectin")
