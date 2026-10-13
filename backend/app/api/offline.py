"""线下复试路由：HR / 店长 约线下面试 + 人工打分。

AI 面试达标并通过初筛的候选人，由 HR 或店长在系统内约线下复试时间、指定面试官，
面试结束后录入打分与录用结论；数据落库 offline_interviews，并参与招聘汇总。
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.api.schemas import OfflineInterviewCreate, OfflineInterviewUpdate, OfflineScoreSubmit
from app.db.models import InterviewSession, InterviewTemplate, OfflineInterview, ResumeScreening
from app.exceptions import BizError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["offline"])

VALID_STATUS = {"scheduled", "done", "no_show", "cancelled"}
VALID_ROLE = {"hr", "store_manager"}
VALID_DECISION = {"pass", "hold", "reject"}


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """解析 UUID 字符串，失败抛 422。"""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


def _parse_dt(value: str | None, label: str) -> datetime | None:
    """解析 ISO8601 时间字符串（容错 Z 结尾），空值返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(422, f"{label}格式无效（应为 ISO8601）", http_status=422)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _offline_out(o: OfflineInterview) -> dict:
    """线下复试记录序列化。"""
    return {
        "id": str(o.id),
        "screening_id": str(o.screening_id) if o.screening_id else None,
        "session_id": str(o.session_id) if o.session_id else None,
        "candidate_id": str(o.candidate_id),
        "candidate_name": o.candidate.name if o.candidate else "",
        "job_id": str(o.job_id),
        "job_title": o.job.title if o.job else "",
        "scheduled_at": o.scheduled_at.isoformat() if o.scheduled_at else None,
        "location": o.location,
        "interviewer": o.interviewer,
        "interviewer_role": o.interviewer_role,
        "status": o.status,
        "score": o.score,
        "dim_scores": o.dim_scores,
        "comment": o.comment,
        "decision": o.decision,
        "scored_at": o.scored_at.isoformat() if o.scored_at else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


@router.post("/offline-interviews")
def create_offline_interview(payload: OfflineInterviewCreate, db: Session = Depends(get_db)):
    """创建线下复试安排（可从初筛记录或 AI 面试会话自动带出候选人与岗位）。"""
    candidate_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    screening_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None

    if payload.screening_id:
        screening_id = _parse_uuid(payload.screening_id, "screening_id")
        screening = db.get(ResumeScreening, screening_id)
        if screening is None:
            raise BizError(404, "初筛记录不存在", http_status=404)
        candidate_id = screening.candidate_id
        job_id = screening.job_id
        session_id = screening.session_id

    if payload.session_id:
        session_id = _parse_uuid(payload.session_id, "session_id")
        sess = db.get(InterviewSession, session_id)
        if sess is None:
            raise BizError(404, "面试会话不存在", http_status=404)
        candidate_id = candidate_id or sess.candidate_id
        if job_id is None:
            tpl = db.get(InterviewTemplate, sess.template_id)
            job_id = tpl.job_id if tpl else None

    if payload.candidate_id:
        candidate_id = candidate_id or _parse_uuid(payload.candidate_id, "candidate_id")
    if payload.job_id:
        job_id = job_id or _parse_uuid(payload.job_id, "job_id")

    if candidate_id is None or job_id is None:
        raise BizError(422, "无法确定候选人与岗位，请提供 screening_id 或 candidate_id + job_id", http_status=422)

    if payload.interviewer_role and payload.interviewer_role not in VALID_ROLE:
        raise BizError(422, "面试官角色无效（应为 hr / store_manager）", http_status=422)

    row = OfflineInterview(
        screening_id=screening_id,
        session_id=session_id,
        candidate_id=candidate_id,
        job_id=job_id,
        scheduled_at=_parse_dt(payload.scheduled_at, "scheduled_at"),
        location=payload.location or "",
        interviewer=payload.interviewer or "",
        interviewer_role=payload.interviewer_role or "",
        status="scheduled",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok(_offline_out(row))


@router.get("/offline-interviews")
def list_offline_interviews(
    job_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """线下复试列表（按约面时间倒序）。"""
    query = db.query(OfflineInterview)
    if job_id:
        query = query.filter(OfflineInterview.job_id == _parse_uuid(job_id, "job_id"))
    if status:
        query = query.filter(OfflineInterview.status == status)
    rows = query.order_by(OfflineInterview.created_at.desc()).all()
    return ok([_offline_out(o) for o in rows])


@router.put("/offline-interviews/{offline_id}")
def update_offline_interview(
    offline_id: str, payload: OfflineInterviewUpdate, db: Session = Depends(get_db)
):
    """更新线下复试安排（改期 / 改地点 / 改面试官 / 改状态）。"""
    oid = _parse_uuid(offline_id, "offline_id")
    row = db.get(OfflineInterview, oid)
    if row is None:
        raise BizError(404, "线下复试记录不存在", http_status=404)

    if payload.scheduled_at is not None:
        row.scheduled_at = _parse_dt(payload.scheduled_at, "scheduled_at")
    if payload.location is not None:
        row.location = payload.location
    if payload.interviewer is not None:
        row.interviewer = payload.interviewer
    if payload.interviewer_role is not None:
        if payload.interviewer_role and payload.interviewer_role not in VALID_ROLE:
            raise BizError(422, "面试官角色无效（应为 hr / store_manager）", http_status=422)
        row.interviewer_role = payload.interviewer_role
    if payload.status is not None:
        if payload.status not in VALID_STATUS:
            raise BizError(422, "状态无效（scheduled / done / no_show / cancelled）", http_status=422)
        row.status = payload.status

    db.commit()
    db.refresh(row)
    return ok(_offline_out(row))


@router.post("/offline-interviews/{offline_id}/score")
def score_offline_interview(
    offline_id: str, payload: OfflineScoreSubmit, db: Session = Depends(get_db)
):
    """录入线下复试打分（总分 + 维度 + 评语 + 录用结论），并置状态为已完成。"""
    oid = _parse_uuid(offline_id, "offline_id")
    row = db.get(OfflineInterview, oid)
    if row is None:
        raise BizError(404, "线下复试记录不存在", http_status=404)

    if payload.decision and payload.decision not in VALID_DECISION:
        raise BizError(422, "录用结论无效（pass / hold / reject）", http_status=422)

    row.score = payload.score
    row.dim_scores = payload.dim_scores or []
    row.comment = payload.comment or ""
    row.decision = payload.decision or ""
    row.scored_at = datetime.now(timezone.utc)
    row.status = "done"
    db.commit()
    db.refresh(row)
    return ok(_offline_out(row))


@router.delete("/offline-interviews/{offline_id}")
def delete_offline_interview(offline_id: str, db: Session = Depends(get_db)):
    """删除线下复试记录。"""
    oid = _parse_uuid(offline_id, "offline_id")
    row = db.get(OfflineInterview, oid)
    if row is None:
        raise BizError(404, "线下复试记录不存在", http_status=404)
    db.delete(row)
    db.commit()
    return ok({"deleted": True, "offline_id": str(oid)})
