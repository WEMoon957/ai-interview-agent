"""简历初筛表模型。

一次「岗位文件夹」上传会为文件夹内每份简历生成一条初筛记录（batch_id 归组），
记录 AI 依据岗位 JD 给出的初筛总分、维度得分、亮点/风险与建议，用于按分排序。
"""
import uuid

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ResumeScreening(TimestampMixin, Base):
    """简历初筛（resume_screenings）：一岗位一简历一条。"""

    __tablename__ = "resume_screenings"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=False, index=True
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id"), nullable=True
    )
    # 同一次文件夹上传共享的批次 id（便于按批查看与回滚）
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    # 上传的岗位文件夹名（前端 webkitdirectory 的文件夹名）
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 简历文件名
    file_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    # 初筛总分（百分制 0~100）；免初筛岗位为 NULL（未打分的投递记录）
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 维度得分：[{"key","name","score","max_score","weight","weighted_score"}]
    dim_scores: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # AI 综合评价
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 亮点 / 风险（字符串数组）
    highlights: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 建议：强烈推荐/推荐/待定/不推荐
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # 流转状态：screened（已初筛）/ interview_sent（已发面试链接）/ rejected（淘汰）
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="screened")
    # 发起 AI 面试后的会话 id（用于与 interview_sessions 打通）
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=True
    )

    job = relationship("JobPosting", lazy="selectin")
    candidate = relationship("Candidate", lazy="selectin")
