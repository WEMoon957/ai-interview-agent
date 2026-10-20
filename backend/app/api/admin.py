"""管理端路由：模板 / 会话 / 报告。"""
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.api.schemas import (
    CompanyProfileOut,
    CompanyProfileUpdate,
    JobCreate,
    JobUpdate,
    TemplateCreate,
)
from app.db.models import (
    AnswerEvaluation,
    Candidate,
    CompanyProfile,
    InterviewSession,
    InterviewTemplate,
    InterviewTurn,
    JobPosting,
    Resume,
    SessionReport,
)
from app.exceptions import BizError
from app.services.cascade import purge_candidate, purge_job, purge_template
logger = logging.getLogger(__name__)
from app.services.company_profile import get_active_profile, save_profile, snapshot_company_profile
from app.services.feishu_notify import notify_interview_created
from app.services.interview_turns import align_evaluations, list_turns
from app.services.resume_parser import parse_resume

router = APIRouter(prefix="/admin", tags=["admin"])

# 基础数据路由：登录即可访问（含店长），不放 router 级角色限制。
# 目前只有「岗位列表」—— 店长在建到店直招 / 线下复试时需要岗位下拉框。
# 注意：岗位的增删改仍在上方 router（HR / 管理员专属）。
base_data_router = APIRouter(prefix="/admin", tags=["admin"])


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
    db.query(InterviewTurn).filter(InterviewTurn.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(AnswerEvaluation).filter(AnswerEvaluation.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(SessionReport).filter(SessionReport.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(InterviewSession).filter(InterviewSession.id == session_id).delete(
        synchronize_session=False
    )


@base_data_router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    """岗位列表（基础数据：HR / 管理员 / 店长登录后均可读）。"""
    jobs = db.query(JobPosting).order_by(JobPosting.created_at.desc()).all()
    return ok(
        [
            {
                "id": str(j.id),
                "title": j.title,
                "jd_text": j.jd_text,
                "requirements": j.requirements,
                "skip_screening": j.skip_screening,
                "is_active": j.is_active,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ]
    )


# ── 公司文化档案（全局单例，一段公司介绍文字）──────────────


@router.get("/company-profile")
def get_company_profile(db: Session = Depends(get_db)):
    """读取公司文化档案；未配置时返回空档案（content 为空串）。"""
    row = get_active_profile(db)
    if row is None:
        return ok(CompanyProfileOut().model_dump())
    return ok(
        CompanyProfileOut(
            id=str(row.id),
            content=row.content or "",
            updated_by=row.updated_by or "",
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        ).model_dump()
    )


@router.put("/company-profile")
def update_company_profile(payload: CompanyProfileUpdate, db: Session = Depends(get_db)):
    """整篇保存公司介绍（单例：首次创建，后续更新同一行）。

    保存后仅影响之后发起的面试（发链接时快照），已发出的会话不受影响。
    """
    row = save_profile(db, payload.content or "", payload.updated_by or "")
    db.commit()
    db.refresh(row)
    return ok(
        CompanyProfileOut(
            id=str(row.id),
            content=row.content or "",
            updated_by=row.updated_by or "",
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        ).model_dump()
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
        is_active=bool(payload.is_active),
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
            "is_active": job.is_active,
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
    if payload.is_active is not None:
        job.is_active = bool(payload.is_active)

    db.commit()
    db.refresh(job)
    return ok(
        {
            "id": str(job.id),
            "title": job.title,
            "jd_text": job.jd_text,
            "requirements": job.requirements,
            "skip_screening": job.skip_screening,
            "is_active": job.is_active,
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
                "culture_fit": t.culture_fit or {},
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in templates
        ]
    )


@router.post("/templates")
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    """创建面试模板（问题集 + 评分维度 + 追问上限 + 文化匹配配置）。"""
    job = db.get(JobPosting, payload.job_id)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    template = InterviewTemplate(
        job_id=payload.job_id,
        questions=[q.model_dump() for q in payload.questions],
        rubric=[r.model_dump(mode="json") for r in payload.rubric],
        duration_limit=payload.duration_limit,
        max_follow_ups=payload.max_follow_ups,
        culture_fit=payload.culture_fit or {},
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
            "culture_fit": template.culture_fit or {},
        }
    )


@router.put("/templates/{template_id}")
def update_template(
    template_id: str, payload: TemplateCreate, db: Session = Depends(get_db)
):
    """更新面试模板（问题集 / 评分维度 / 追问上限 / 文化匹配配置）。

    重要：模板内容在候选人**首次进入面试时**会被复制进面试状态（快照），
    之后不再回读模板 —— 因此修改模板**不会影响已发出或进行中的面试**，
    只对之后新发起的面试生效。
    """
    tid = _parse_uuid(template_id, "template_id")
    template = db.get(InterviewTemplate, tid)
    if template is None:
        raise BizError(404, "模板不存在", http_status=404)

    job = db.get(JobPosting, payload.job_id)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    template.job_id = payload.job_id
    template.questions = [q.model_dump() for q in payload.questions]
    template.rubric = [r.model_dump(mode="json") for r in payload.rubric]
    template.duration_limit = payload.duration_limit
    template.max_follow_ups = payload.max_follow_ups
    template.culture_fit = payload.culture_fit or {}

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
            "culture_fit": template.culture_fit or {},
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
        # 发链接时快照当前生效的公司介绍（HR 事后改档案不影响本会话）
        company_profile_snapshot=snapshot_company_profile(db),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # 创建面试后通知 HR（飞书卡片，附答题链接）；失败不影响创建结果
    notify_result = {"status": "skipped", "reason": "未触发创建通知"}
    try:
        job = db.get(JobPosting, template.job_id)
        notify_result = notify_interview_created(
            job_title=job.title if job else "",
            items=[
                {
                    "candidate_name": candidate.name,
                    "interview_path": f"/i/{session.token}",
                }
            ],
            source="后台手动创建",
        )
        logger.info("面试创建推送：%s", notify_result)
    except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断创建
        logger.exception("面试创建推送异常，已忽略")

    return ok(
        {
            "id": str(session.id),
            "token": session.token,
            "thread_id": session.thread_id,
            "template_id": str(session.template_id),
            "candidate_id": str(session.candidate_id),
            "status": session.status,
            "notify": notify_result,
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
    """获取面试报告 + 逐题评分明细 + 候选人逐轮原话。

    ``turns`` 是「AI 提问原文 + 候选人回答原文」的完整对话记录，按发生顺序排列，
    每条内嵌对齐到的评分（``evaluation``，可能为 null）；改造前的历史会话没有原话，
    ``turns`` 返回空数组，前端回退为仅展示评分明细。
    """
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

    turns = list_turns(db, sess.id)
    eval_by_turn = align_evaluations(turns, evaluations)

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
            # 完整对话：原话是唯一事实源，评分只是挂在每轮上的派生结果
            "turns": [
                {
                    "turn_index": t.turn_index,
                    "question_id": t.question_id,
                    "question_type": t.question_type,
                    "is_follow_up": t.is_follow_up,
                    "question": t.question_text,
                    "answer": t.answer_text,
                    "answered_at": t.created_at.isoformat() if t.created_at else None,
                    "evaluation": eval_by_turn.get(t.turn_index),
                }
                for t in turns
            ],
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
