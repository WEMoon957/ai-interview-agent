"""管理端路由：模板 / 会话 / 报告。"""
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.api.schemas import JobCreate, JobUpdate, TemplateCreate
from app.db.models import (
    AnswerEvaluation,
    Candidate,
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    Resume,
    SessionReport,
)
from app.exceptions import BizError
from app.services.cascade import purge_candidate, purge_job, purge_template
from app.services.resume_parser import parse_resume

router = APIRouter(prefix="/admin", tags=["admin"])


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """解析 UUID 字符串，失败抛 422。"""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


def _delete_session_cascade(db: Session, session_id: uuid.UUID) -> None:
    """级联删除一个面试会话及其关联数据。

    走 ``purge_candidate`` 统一口径：删除该候选人的会话/评分/报告/简历，
    以及招聘全链路的初筛记录、线下复试与汇总快照（否则会撞外键约束）。
    """
    session = db.get(InterviewSession, session_id)
    candidate_id = session.candidate_id if session else None
    if candidate_id:
        purge_candidate(db, candidate_id)
        return

    # 异常数据兜底：会话没有候选人时，只删该会话自身相关数据
    db.query(AnswerEvaluation).filter(AnswerEvaluation.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(SessionReport).filter(SessionReport.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(InterviewSession).filter(InterviewSession.id == session_id).delete(
        synchronize_session=False
    )


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    """岗位列表。"""
    jobs = db.query(JobPosting).order_by(JobPosting.created_at.desc()).all()
    return ok(
        [
            {
                "id": str(j.id),
                "title": j.title,
                "jd_text": j.jd_text,
                "requirements": j.requirements,
                "skip_screening": j.skip_screening,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ]
    )


@router.post("/jobs")
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    """创建岗位。"""
    if not payload.title or not payload.title.strip():
        raise BizError(422, "岗位名称不能为空", http_status=422)

    job = JobPosting(
        title=payload.title.strip(),
        jd_text=payload.jd_text or "",
        requirements=payload.requirements or {},
        skip_screening=bool(payload.skip_screening),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return ok(
        {
            "id": str(job.id),
            "title": job.title,
            "jd_text": job.jd_text,
            "requirements": job.requirements,
            "skip_screening": job.skip_screening,
        }
    )


@router.put("/jobs/{job_id}")
def update_job(job_id: str, payload: JobUpdate, db: Session = Depends(get_db)):
    """更新岗位（如切换「免初筛」开关）。仅更新传入字段。"""
    jid = _parse_uuid(job_id, "job_id")
    job = db.get(JobPosting, jid)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    if payload.title is not None:
        if not payload.title.strip():
            raise BizError(422, "岗位名称不能为空", http_status=422)
        job.title = payload.title.strip()
    if payload.jd_text is not None:
        job.jd_text = payload.jd_text
    if payload.requirements is not None:
        job.requirements = payload.requirements
    if payload.skip_screening is not None:
        job.skip_screening = bool(payload.skip_screening)

    db.commit()
    db.refresh(job)
    return ok(
        {
            "id": str(job.id),
            "title": job.title,
            "jd_text": job.jd_text,
            "requirements": job.requirements,
            "skip_screening": job.skip_screening,
        }
    )


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    """面试模板列表。"""
    templates = (
        db.query(InterviewTemplate).order_by(InterviewTemplate.created_at.desc()).all()
    )
    return ok(
        [
            {
                "id": str(t.id),
                "job_id": str(t.job_id),
                "questions": t.questions,
                "rubric": t.rubric,
                "duration_limit": t.duration_limit,
                "max_follow_ups": t.max_follow_ups,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in templates
        ]
    )


@router.post("/templates")
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    """创建面试模板（问题集 + 评分维度 + 追问上限）。"""
    job = db.get(JobPosting, payload.job_id)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    template = InterviewTemplate(
        job_id=payload.job_id,
        questions=[q.model_dump() for q in payload.questions],
        rubric=[r.model_dump(mode="json") for r in payload.rubric],
        duration_limit=payload.duration_limit,
        max_follow_ups=payload.max_follow_ups,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    return ok(
        {
            "id": str(template.id),
            "job_id": str(template.job_id),
            "questions": template.questions,
            "rubric": template.rubric,
            "duration_limit": template.duration_limit,
            "max_follow_ups": template.max_follow_ups,
        }
    )


@router.post("/sessions")
def create_session(
    template_id: str = Form(...),
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """创建面试会话：解析简历 → 建候选人/简历/会话 → 返回 token。"""
    if not name or not name.strip():
        raise BizError(422, "候选人姓名不能为空", http_status=422)

    tpl_id = _parse_uuid(template_id, "template_id")
    template = db.get(InterviewTemplate, tpl_id)
    if template is None:
        raise BizError(404, "模板不存在", http_status=404)

    candidate = Candidate(
        name=name.strip(),
        email=email.strip() or None,
        phone=phone.strip() or None,
    )
    db.add(candidate)
    db.flush()

    parsed_text = ""
    file_url = None
    if file is not None:
        content = file.file.read()
        if content:
            parsed_text = parse_resume(content, file.filename or "")
            file_url = file.filename or None

    db.add(
        Resume(
            candidate_id=candidate.id,
            file_url=file_url,
            parsed_text=parsed_text,
        )
    )

    session = InterviewSession(
        token=uuid.uuid4().hex,
        template_id=template.id,
        candidate_id=candidate.id,
        status="pending",
        thread_id=str(uuid.uuid4()),
        started_at=None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    return ok(
        {
            "id": str(session.id),
            "token": session.token,
            "thread_id": session.thread_id,
            "template_id": str(session.template_id),
            "candidate_id": str(session.candidate_id),
            "status": session.status,
        }
    )


@router.get("/sessions")
def list_sessions(
    job_id: uuid.UUID | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """会话列表，支持按岗位 / 状态筛选。"""
    query = db.query(InterviewSession)

    if status:
        query = query.filter(InterviewSession.status == status)
    if job_id:
        query = query.join(
            InterviewTemplate, InterviewSession.template_id == InterviewTemplate.id
        ).filter(InterviewTemplate.job_id == job_id)

    sessions = query.order_by(InterviewSession.created_at.desc()).all()

    return ok(
        [
            {
                "id": str(s.id),
                "token": s.token,
                "thread_id": s.thread_id,
                "template_id": str(s.template_id),
                "candidate_id": str(s.candidate_id),
                "candidate_name": s.candidate.name if s.candidate else None,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ]
    )


@router.get("/sessions/{session_id}/report")
def get_report(session_id: uuid.UUID, db: Session = Depends(get_db)):
    """获取面试报告 + 逐题评分明细。"""
    sess = db.get(InterviewSession, session_id)
    if sess is None:
        raise BizError(404, "会话不存在", http_status=404)

    report = db.query(SessionReport).filter(SessionReport.session_id == sess.id).first()
    evaluations = (
        db.query(AnswerEvaluation)
        .filter(AnswerEvaluation.session_id == sess.id)
        .order_by(AnswerEvaluation.created_at.asc())
        .all()
    )

    return ok(
        {
            "session_id": str(sess.id),
            "status": sess.status,
            "report": {
                "dim_scores": report.dim_scores if report else [],
                "total_score": report.total_score if report else None,
                "verdict": report.verdict if report else None,
                "summary": report.summary if report else None,
            },
            "evaluations": [
                {
                    "question_id": e.question_id,
                    "scores": e.scores,
                    "comment": e.comment,
                    "follow_up_needed": e.follow_up_needed,
                    "follow_up_question": e.follow_up_question,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in evaluations
            ],
        }
    )


# ── 删除接口 ─────────────────────────────────────────────


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    """删除岗位（级联删除模板、会话、评分、报告、候选人、简历、初筛、线下复试、汇总）。"""
    jid = _parse_uuid(job_id, "job_id")
    job = db.get(JobPosting, jid)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    purge_job(db, jid)
    db.commit()
    return ok({"deleted": True, "job_id": str(jid)})


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    """删除面试模板（级联删除关联的会话、评分、报告、候选人、简历、初筛、线下复试、汇总）。"""
    tid = _parse_uuid(template_id, "template_id")
    template = db.get(InterviewTemplate, tid)
    if template is None:
        raise BizError(404, "模板不存在", http_status=404)

    purge_template(db, tid)
    db.commit()
    return ok({"deleted": True, "template_id": str(tid)})


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    """删除面试会话（级联删除评分、报告、简历、候选人）。"""
    sid = _parse_uuid(session_id, "session_id")
    session = db.get(InterviewSession, sid)
    if session is None:
        raise BizError(404, "会话不存在", http_status=404)

    _delete_session_cascade(db, sid)
    db.commit()
    return ok({"deleted": True, "session_id": str(sid)})
