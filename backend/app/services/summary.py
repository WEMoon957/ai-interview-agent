"""招聘汇总聚合：把候选人全链路各阶段结果汇成一条汇总记录。

聚合口径（一岗位一候选人一条）：
- 简历初筛分：``resume_screenings.total_score``（同候选人取最近一条）
- AI 面试分：``session_reports.total_score``（该候选人在本岗位下的会话，取最近一份报告）
- 线下复试分：``offline_interviews.score``（同岗位同候选人，取最近一条已打分记录）

最终分 = 各可用阶段得分按权重加权后重新归一化（缺环节不惩罚，只对已有环节加权）；
纯函数式聚合，便于单测。
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    OfflineInterview,
    ResumeScreening,
    SessionReport,
)
from app.services.resume_screening import recommendation_from_score

# 阶段常量
STAGE_SCREENED = "screened"
STAGE_AI_INTERVIEW = "ai_interview"
STAGE_AI_DONE = "ai_done"
STAGE_OFFLINE_SCHEDULED = "offline_scheduled"
STAGE_OFFLINE_DONE = "offline_done"
# 仅导入未初筛（screen=false 上传，尚未发起面试）
STAGE_IMPORTED = "imported"

STAGE_LABELS = {
    STAGE_SCREENED: "已初筛",
    STAGE_IMPORTED: "已导入待面试",
    STAGE_AI_INTERVIEW: "AI面试中",
    STAGE_AI_DONE: "AI面试完成",
    STAGE_OFFLINE_SCHEDULED: "已约线下复试",
    STAGE_OFFLINE_DONE: "线下复试完成",
}


def _round(value: float | None) -> float | None:
    """四舍五入到两位小数，None 透传。"""
    return None if value is None else round(float(value), 2)


def compute_final_score(
    screening: float | None,
    ai: float | None,
    offline: float | None,
    *,
    w_screening: float = 0.20,
    w_ai: float = 0.40,
    w_offline: float = 0.40,
) -> float | None:
    """按可用阶段加权并重新归一化（无任何分数返回 None）。"""
    parts: list[tuple[float, float]] = []
    if screening is not None:
        parts.append((screening, w_screening))
    if ai is not None:
        parts.append((ai, w_ai))
    if offline is not None:
        parts.append((offline, w_offline))
    total_w = sum(w for _, w in parts)
    if not parts or total_w <= 0:
        return None
    return _round(sum(score * w for score, w in parts) / total_w)


def resolve_is_walk_in(
    *,
    screening: float | None,
    ai: float | None,
    screening_id: Any = None,
    session_id: Any = None,
    offline_source: str = "online",
) -> bool:
    """是否属于「到店直招」。

    显式标了 ``source=walk_in`` 即为是；否则当「既无初筛分/初筛记录、也无 AI 面试分/会话」时
    推导为到店直招（兼容手工录入的历史数据）。
    """
    return offline_source == "walk_in" or (
        screening is None
        and ai is None
        and screening_id is None
        and session_id is None
    )


def resolve_is_complete(
    *,
    screening: float | None,
    ai: float | None,
    offline: float | None,
    screening_id: Any = None,
    session_id: Any = None,
    offline_source: str = "online",
    skip_screening: bool = False,
) -> bool:
    """判断候选人是否走完「该走的」全链路。

    - **到店直招**（source=walk_in，或既无初筛分/初筛记录、也无 AI 面试分/会话）：没有简历初筛与
      AI 面试，只要求线下打分完成即算完成 —— 否则他们永远进不了汇总复核提醒；
    - **线上投递**：初筛（免初筛岗位可跳过）+ AI 面试 + 线下 三者齐备才算完成。
    """
    if resolve_is_walk_in(
        screening=screening,
        ai=ai,
        screening_id=screening_id,
        session_id=session_id,
        offline_source=offline_source,
    ):
        return offline is not None
    return (screening is not None or skip_screening) and ai is not None and offline is not None


def _resolve_stage(
    screening: float | None,
    has_session: bool,
    ai: float | None,
    has_offline: bool,
    offline: float | None,
) -> str:
    """推导候选人当前所处阶段。"""
    if offline is not None:
        return STAGE_OFFLINE_DONE
    if has_offline:
        return STAGE_OFFLINE_SCHEDULED
    if ai is not None:
        return STAGE_AI_DONE
    if has_session:
        return STAGE_AI_INTERVIEW
    return STAGE_SCREENED


def build_summary_rows(db: Session, job_id: uuid.UUID) -> list[dict[str, Any]]:
    """聚合某岗位下所有候选人的全链路数据（按最终分降序，None 置底）。"""
    settings = get_settings()

    # 免初筛岗位：判定「全链路完成」时不要求初筛分
    job = db.get(JobPosting, job_id)
    skip_screening = bool(job.skip_screening) if job is not None else False

    # 候选人篮子：candidate_id -> 汇总中间态
    basket: dict[uuid.UUID, dict[str, Any]] = {}

    def _ensure(cid: uuid.UUID, name: str | None = None) -> dict[str, Any]:
        row = basket.get(cid)
        if row is None:
            row = {
                "candidate_id": cid,
                "candidate_name": name or "",
                "screening_score": None,
                "screening_id": None,
                "screening_status": None,
                "ai_interview_score": None,
                "ai_interview_verdict": "",
                "ai_interview_summary": "",
                "session_id": None,
                "session_token": None,
                "session_status": None,
                "offline_score": None,
                "offline_id": None,
                "offline_status": None,
                "offline_scheduled_at": None,
                "offline_source": "online",
                "interviewer": "",
            }
            basket[cid] = row
        elif name and not row["candidate_name"]:
            row["candidate_name"] = name
        return row

    # 1) 简历初筛（按创建时间正序，后者覆盖前者 -> 取最近）
    screenings = (
        db.query(ResumeScreening)
        .filter(ResumeScreening.job_id == job_id)
        .order_by(ResumeScreening.created_at.asc())
        .all()
    )
    for s in screenings:
        row = _ensure(s.candidate_id, s.candidate.name if s.candidate else None)
        row["screening_score"] = _round(s.total_score)
        row["screening_id"] = s.id
        row["screening_status"] = s.status

    # 2) AI 面试会话 + 报告（仅本岗位模板下的会话）
    sessions = (
        db.query(InterviewSession)
        .join(InterviewTemplate, InterviewSession.template_id == InterviewTemplate.id)
        .filter(InterviewTemplate.job_id == job_id)
        .order_by(InterviewSession.created_at.asc())
        .all()
    )
    for sess in sessions:
        row = _ensure(sess.candidate_id, sess.candidate.name if sess.candidate else None)
        row["session_id"] = sess.id
        row["session_token"] = sess.token
        row["session_status"] = sess.status
        report = (
            db.query(SessionReport)
            .filter(SessionReport.session_id == sess.id)
            .first()
        )
        if report is not None:
            row["ai_interview_score"] = _round(report.total_score)
            row["ai_interview_verdict"] = report.verdict or ""
            row["ai_interview_summary"] = report.summary or ""

    # 3) 线下复试（按创建时间正序，取最近一条已打分记录）
    offlines = (
        db.query(OfflineInterview)
        .filter(OfflineInterview.job_id == job_id)
        .order_by(OfflineInterview.created_at.asc())
        .all()
    )
    for o in offlines:
        row = _ensure(o.candidate_id, o.candidate.name if o.candidate else None)
        row["offline_id"] = o.id
        row["offline_status"] = o.status
        row["offline_scheduled_at"] = o.scheduled_at.isoformat() if o.scheduled_at else None
        row["offline_source"] = o.source or "online"
        row["interviewer"] = o.interviewer or ""
        if o.score is not None:
            row["offline_score"] = _round(o.score)

    rows: list[dict[str, Any]] = []
    for row in basket.values():
        final_score = compute_final_score(
            row["screening_score"],
            row["ai_interview_score"],
            row["offline_score"],
            w_screening=settings.summary_weight_screening,
            w_ai=settings.summary_weight_ai,
            w_offline=settings.summary_weight_offline,
        )
        stage = _resolve_stage(
            row["screening_score"],
            row["session_id"] is not None,
            row["ai_interview_score"],
            row["offline_id"] is not None,
            row["offline_score"],
        )
        # 仅导入未初筛（尚未发起面试）单独成阶段，避免误显示为「已初筛」
        if stage == STAGE_SCREENED and row["screening_status"] == "imported":
            stage = STAGE_IMPORTED
        # 「完成」判定：到店直招只要求线下打分完成，线上投递要求三段齐备（见 resolve_is_complete）
        is_walk_in = resolve_is_walk_in(
            screening=row["screening_score"],
            ai=row["ai_interview_score"],
            screening_id=row["screening_id"],
            session_id=row["session_id"],
            offline_source=row["offline_source"],
        )
        is_complete = resolve_is_complete(
            screening=row["screening_score"],
            ai=row["ai_interview_score"],
            offline=row["offline_score"],
            screening_id=row["screening_id"],
            session_id=row["session_id"],
            offline_source=row["offline_source"],
            skip_screening=skip_screening,
        )
        verdict = recommendation_from_score(final_score) if final_score is not None else ""

        row.update(
            {
                "final_score": final_score,
                "stage": stage,
                "stage_label": STAGE_LABELS.get(stage, stage),
                "is_complete": is_complete,
                "final_verdict": verdict,
                "summary": _build_summary_text(row, final_score, stage),
                "job_title": job.title if job is not None else "",
                "offline_source": row["offline_source"],
                "is_walk_in": is_walk_in,
                "job_id": str(row.get("job_id") or job_id),
                "screening_id": str(row["screening_id"]) if row["screening_id"] else None,
                "session_id": str(row["session_id"]) if row["session_id"] else None,
                "offline_id": str(row["offline_id"]) if row["offline_id"] else None,
                "candidate_id": str(row["candidate_id"]),
            }
        )
        rows.append(row)

    rows.sort(
        key=lambda r: (r["final_score"] is None, -(r["final_score"] or 0)),
    )
    return rows


def _build_summary_text(row: dict[str, Any], final_score: float | None, stage: str) -> str:
    """生成确定性汇总文案（不依赖 LLM，可复现）。"""
    parts = [
        f"简历初筛 {row['screening_score'] if row['screening_score'] is not None else '—'} 分",
        f"AI面试 {row['ai_interview_score'] if row['ai_interview_score'] is not None else '—'} 分",
        f"线下复试 {row['offline_score'] if row['offline_score'] is not None else '—'} 分",
    ]
    head = "；".join(parts)
    tail = f"，最终加权 {final_score} 分（{STAGE_LABELS.get(stage, stage)}）" if final_score is not None else ""
    return head + tail + "。"
