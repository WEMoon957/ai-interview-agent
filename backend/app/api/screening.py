"""简历初筛路由：岗位文件夹批量上传 → AI 初筛打分排序 → 一键发起 AI 面试。

- 上传：一次上传一个「岗位文件夹」内的多份简历，逐份解析 + AI 初筛，返回按分降序的榜单；
- 邀请：对选中候选人批量创建 AI 面试会话（复用既有 interview_sessions / token 体系），
  返回答题链接，供 HR 推送。
"""
import logging
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import nulls_last
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok, require_role
from app.api.schemas import CandidateRename, ScreeningInvite, ScreeningNotify
from app.config import get_settings
from app.db.models import (
    Candidate,
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    OfflineInterview,
    Offer,
    RecruitmentSummary,
    Resume,
    ResumeScreening,
)
from app.db.models.audit_log import ACTION_CANDIDATE_RENAME
from app.exceptions import BizError
from app.services import audit
from app.services.cascade import candidate_is_orphan, purge_candidate
from app.services.company_profile import snapshot_company_profile
from app.services.feishu_notify import notify_interview_created, notify_screening_recommendations
from app.services.resume_parser import extract_candidate_name, parse_resume
from app.services.resume_screening import recommendation_from_score, screen_resume

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["screening"])

_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """解析 UUID 字符串，失败抛 422。"""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


def _extract_contacts(text: str) -> tuple[str | None, str | None]:
    """从简历文本尽力提取手机号与邮箱（提取不到返回 None）。"""
    phone = _PHONE_RE.search(text or "")
    email = _EMAIL_RE.search(text or "")
    return (phone.group(0) if phone else None, email.group(0) if email else None)


def _session_token_map(db: Session, session_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """批量取会话 token，避免列表序列化时 N+1。"""
    ids = [sid for sid in session_ids if sid]
    if not ids:
        return {}
    rows = db.query(InterviewSession).filter(InterviewSession.id.in_(ids)).all()
    return {r.id: r.token for r in rows}


def _screening_out(s: ResumeScreening, token: str | None = None) -> dict:
    """初筛记录序列化。"""
    return {
        "id": str(s.id),
        "job_id": str(s.job_id),
        "job_title": s.job.title if s.job else "",
        "candidate_id": str(s.candidate_id),
        "candidate_name": s.candidate.name if s.candidate else "",
        "folder_name": s.folder_name,
        "file_name": s.file_name,
        "batch_id": str(s.batch_id) if s.batch_id else None,
        "total_score": s.total_score,
        "dim_scores": s.dim_scores,
        "summary": s.summary,
        "highlights": s.highlights,
        "risks": s.risks,
        "recommendation": s.recommendation,
        "status": s.status,
        "session_id": str(s.session_id) if s.session_id else None,
        "session_token": token,
        "interview_path": f"/i/{token}" if token else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.post("/screenings/upload")
async def upload_and_screen(
    job_id: str = Form(...),
    folder_name: str = Form(""),
    screen: bool = Form(True),
    direct_invite: bool | None = Form(None),
    template_id: str = Form(""),
    notify: bool = Form(True),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """岗位文件夹批量上传。

    两种模式（由 ``direct_invite`` 决定，留空则跟随岗位的 skip_screening 开关）：
    - **AI 初筛模式**（默认）：逐份解析 + AI 打分，返回按分降序的榜单；
    - **直通面试模式**（免初筛岗位）：不做 AI 打分，逐份导入后直接生成 AI 面试会话，
      返回每人答题链接——「一来就发 AI 面试链接」。

    任何单份简历解析失败都不影响其余简历入库（逐份容错）。
    """
    jid = _parse_uuid(job_id, "job_id")
    job = db.get(JobPosting, jid)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    if not files:
        raise BizError(422, "未收到简历文件", http_status=422)

    # 模式解析：调用方显式传参优先，否则跟随岗位「免初筛」开关
    use_direct = bool(job.skip_screening) if direct_invite is None else bool(direct_invite)

    # 直通模式需先确保该岗位有可用模板；放在循环前以便「快失败」，避免产生半截数据
    template = _resolve_template(db, jid, template_id or None) if use_direct else None

    batch_id = uuid.uuid4()
    succeeded, failed = 0, 0
    errors: list[dict] = []
    # 免初筛直通模式下，逐个收集「已创建面试」用于结束后统一推 HR 卡片
    direct_created: list[dict] = []
    direct_job_title = ""

    for f in files:
        filename = f.filename or "未命名文件"
        try:
            content = await f.read()
            if not content:
                raise ValueError("文件内容为空")

            parsed_text = parse_resume(content, filename)
            name = extract_candidate_name(parsed_text, filename)
            phone, email = _extract_contacts(parsed_text)

            candidate = Candidate(name=name, email=email, phone=phone)
            db.add(candidate)
            db.flush()

            resume = Resume(
                candidate_id=candidate.id,
                file_url=filename,
                parsed_text=parsed_text,
            )
            db.add(resume)
            db.flush()

            if use_direct:
                # 免初筛：不做 AI 打分，直接建面试会话并把链接回写
                session = _create_session(db, candidate, template)
                db.flush()
                direct_created.append(
                    {
                        "candidate_name": candidate.name,
                        "interview_path": f"/i/{session.token}",
                    }
                )
                direct_job_title = job.title
                screening = ResumeScreening(
                    job_id=jid,
                    candidate_id=candidate.id,
                    resume_id=resume.id,
                    batch_id=batch_id,
                    folder_name=folder_name or "",
                    file_name=filename,
                    total_score=None,  # 未打分：汇总时不计入初筛权重
                    status="interview_sent",
                    session_id=session.id,
                )
            elif screen:
                result = screen_resume(
                    job_title=job.title,
                    jd_text=job.jd_text,
                    candidate_name=name,
                    resume_text=parsed_text,
                )
                screening = ResumeScreening(
                    job_id=jid,
                    candidate_id=candidate.id,
                    resume_id=resume.id,
                    batch_id=batch_id,
                    folder_name=folder_name or "",
                    file_name=filename,
                    total_score=result.total_score,
                    dim_scores=[d.model_dump() for d in result.dim_scores],
                    summary=result.summary,
                    highlights=result.highlights,
                    risks=result.risks,
                    recommendation=recommendation_from_score(result.total_score),
                    status="screened",
                )
            else:
                # 仅导入不评分（保留人工榜单：可稍后单独发起面试）
                screening = ResumeScreening(
                    job_id=jid,
                    candidate_id=candidate.id,
                    resume_id=resume.id,
                    batch_id=batch_id,
                    folder_name=folder_name or "",
                    file_name=filename,
                    total_score=None,
                    status="imported",
                )
            db.add(screening)
            db.commit()
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - 单份失败不阻断整批
            db.rollback()
            failed += 1
            errors.append({"file_name": filename, "error": str(exc)})
            logger.exception("简历处理失败：%s", filename)

    items = (
        db.query(ResumeScreening)
        .filter(ResumeScreening.batch_id == batch_id)
        .order_by(ResumeScreening.created_at.asc())
        .all()
    )
    # 初筛模式按分降序展示；直通模式按导入顺序展示（均带答题链接）
    if not use_direct:
        items = sorted(items, key=lambda s: (s.total_score is None, -(s.total_score or 0)))

    # 初筛通过（达到推送门槛）的候选人：自动生成 AI 面试链接，便于卡片/待办直接带上
    settings = get_settings()
    auto_invite_note = ""
    if not use_direct and settings.feishu_screening_auto_invite:
        passed = [
            s
            for s in items
            if s.total_score is not None and s.total_score >= settings.feishu_screening_min_score
        ]
        if passed:
            try:
                invite_tpl = _resolve_template(db, jid, template_id or None)
            except BizError as exc:
                # 岗位没配模板：不阻断上传，只记提示（链接留空，HR 可在后台手动发起）
                invite_tpl = None
                auto_invite_note = f"（未自动生成面试链接：{exc.message}）"
                logger.warning("初筛通过但自动发起面试被跳过：%s", exc.message)
            if invite_tpl is not None:
                for s in passed:
                    try:
                        _invite_one(db, s, invite_tpl)  # 初筛模式自动发起的链接随推荐卡片推送
                    except Exception:  # noqa: BLE001 - 单人失败不影响其余
                        db.rollback()
                        logger.exception("自动生成面试链接失败（screening=%s）", s.id)

    token_map = _session_token_map(db, [s.session_id for s in items])
    out_items = [_screening_out(s, token_map.get(s.session_id)) for s in items]

    # 推送分支：直通模式与初筛模式走不同通知
    notify_result: dict = {"status": "skipped", "reason": "未触发推送"}
    if use_direct:
        # 免初筛直通：每人都已生成 AI 面试链接，给 HR 发一张「已发起」卡片
        if direct_created:
            try:
                notify_result = notify_interview_created(
                    job_title=direct_job_title,
                    items=direct_created,
                    source="免初筛直通",
                )
                logger.info("免初筛直通面试创建推送：%s", notify_result)
            except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断上传
                logger.exception("免初筛直通面试创建推送异常，已忽略")
                notify_result = {"status": "failed", "reason": "内部异常"}
        else:
            notify_result = {"status": "skipped", "reason": "本次未创建任何面试会话"}
    elif not notify:
        notify_result = {"status": "skipped", "reason": "调用方指定不推送（notify=false）"}
    else:
        # 初筛模式：推送推荐名单（含自动发起的链接，已覆盖「创建」通知，不再重复发本卡片）
        try:
            notify_result = notify_screening_recommendations(
                job_title=job.title,
                folder_name=folder_name or "",
                items=out_items,
                total=len(files),
                succeeded=succeeded,
            )
            logger.info("简历初筛推送结果：%s", notify_result)
        except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断上传
            logger.exception("简历初筛推送异常，已忽略")
            notify_result = {"status": "failed", "reason": "内部异常"}

    return ok(
        {
            "batch_id": str(batch_id),
            "folder_name": folder_name,
            "job_id": str(jid),
            "job_title": job.title,
            "mode": "direct_invite" if use_direct else "screening",
            "total": len(files),
            "succeeded": succeeded,
            "failed": failed,
            "errors": errors,
            "items": out_items,
            "notify": notify_result,
            "note": auto_invite_note,
        }
    )


@router.get("/screenings")
def list_screenings(
    job_id: str | None = None,
    batch_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """简历初筛榜单（默认按初筛总分降序）。"""
    query = db.query(ResumeScreening)
    if job_id:
        query = query.filter(ResumeScreening.job_id == _parse_uuid(job_id, "job_id"))
    if batch_id:
        query = query.filter(ResumeScreening.batch_id == _parse_uuid(batch_id, "batch_id"))
    if status:
        query = query.filter(ResumeScreening.status == status)

    rows = query.order_by(
        nulls_last(ResumeScreening.total_score.desc()),
        ResumeScreening.created_at.desc(),
    ).all()
    token_map = _session_token_map(db, [r.session_id for r in rows])
    return ok([_screening_out(r, token_map.get(r.session_id)) for r in rows])


@router.patch("/candidates/{candidate_id}")
def rename_candidate(
    candidate_id: str,
    payload: CandidateRename,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_role("admin", "hr")),
):
    """修改候选人姓名，并同步汇总/Offer 中的姓名快照。"""
    cid = _parse_uuid(candidate_id, "candidate_id")
    candidate = db.get(Candidate, cid)
    if candidate is None:
        raise BizError(404, "候选人不存在", http_status=404)

    new_name = (payload.name or "").strip()
    if not new_name:
        raise BizError(422, "候选人姓名不能为空", http_status=422)
    old_name = candidate.name
    if new_name == old_name:
        return ok({"id": str(cid), "name": old_name, "changed": False})

    candidate.name = new_name
    # 汇总/Offer 存的是姓名快照，必须同步，否则列表改了、报表还是旧名
    db.query(RecruitmentSummary).filter(
        RecruitmentSummary.candidate_id == cid
    ).update({"candidate_name": new_name}, synchronize_session=False)
    db.query(Offer).filter(
        Offer.candidate_id == cid
    ).update({"candidate_name": new_name}, synchronize_session=False)

    # 先提交业务改动，再写审计：audit.record 失败时会 rollback，
    # 若此时业务改动尚未提交，改名会被一起回滚。
    db.commit()
    audit.record(
        db,
        action=ACTION_CANDIDATE_RENAME,
        request=request,
        actor=user,
        target=f"candidates/{cid}",
        detail=f"{old_name} -> {new_name}",
    )
    return ok({"id": str(cid), "name": new_name, "changed": True})


@router.post("/screenings/notify")
def notify_screenings(payload: ScreeningNotify, db: Session = Depends(get_db)):
    """手动把某岗位的「推荐进入 AI 面试」名单推送给 HR 飞书（用户主动触发，不受幂等限制）。"""
    jid = _parse_uuid(payload.job_id, "job_id")
    job = db.get(JobPosting, jid)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    rows = (
        db.query(ResumeScreening)
        .filter(ResumeScreening.job_id == jid)
        .order_by(nulls_last(ResumeScreening.total_score.desc()))
        .all()
    )
    token_map = _session_token_map(db, [r.session_id for r in rows])
    items = [_screening_out(r, token_map.get(r.session_id)) for r in rows]

    result = notify_screening_recommendations(
        job_title=job.title,
        folder_name="",
        items=items,
        total=len(items),
        succeeded=len(items),
        min_score=payload.min_score,
    )
    if result.get("status") == "failed":
        raise BizError(502, f"飞书推送失败：{result.get('reason')}", http_status=502)
    return ok(result)


@router.get("/screenings/{screening_id}")
def get_screening(screening_id: str, db: Session = Depends(get_db)):
    """初筛记录详情。"""
    sid = _parse_uuid(screening_id, "screening_id")
    s = db.get(ResumeScreening, sid)
    if s is None:
        raise BizError(404, "初筛记录不存在", http_status=404)
    token_map = _session_token_map(db, [s.session_id])
    return ok(_screening_out(s, token_map.get(s.session_id)))


def _resolve_template(db: Session, job_id: uuid.UUID, template_id: str | None) -> InterviewTemplate:
    """解析面试模板：显式指定优先，否则取该岗位最新模板。"""
    if template_id:
        tpl = db.get(InterviewTemplate, _parse_uuid(template_id, "template_id"))
        if tpl is None:
            raise BizError(404, "面试模板不存在", http_status=404)
        return tpl
    tpl = (
        db.query(InterviewTemplate)
        .filter(InterviewTemplate.job_id == job_id)
        .order_by(InterviewTemplate.created_at.desc())
        .first()
    )
    if tpl is None:
        raise BizError(422, "该岗位尚未配置面试模板，请先创建模板", http_status=422)
    return tpl


def _create_session(
    db: Session, candidate: Candidate, template: InterviewTemplate
) -> InterviewSession:
    """创建 AI 面试会话（带公司介绍快照）。调用方负责 commit。

    统一收口会话创建逻辑，供「免初筛直通」「批量发起」「初筛自动发起」复用，
    避免公司介绍快照等字段遗漏。
    """
    session = InterviewSession(
        token=uuid.uuid4().hex,
        template_id=template.id,
        candidate_id=candidate.id,
        status="pending",
        thread_id=str(uuid.uuid4()),
        started_at=None,
        # 发链接时快照当前生效的公司介绍（批量 invite 与初筛自动发起都走这里）
        company_profile_snapshot=snapshot_company_profile(db),
    )
    db.add(session)
    db.flush()
    return session


def _invite_one(
    db: Session, screening: ResumeScreening, template: InterviewTemplate
) -> tuple[dict, bool]:
    """为一条初筛记录创建（或复用）AI 面试会话并回写状态。

    Returns:
        (序列化结果, 是否新建了会话)。复用既有会话时第二个元素为 False，
        调用方据此决定是否计入「已发起」通知（幂等，避免重复推送）。
    """
    # 已发起过则复用既有会话（幂等），避免重复建会话
    if screening.session_id:
        sess = db.get(InterviewSession, screening.session_id)
        if sess is not None:
            return _screening_out(screening, sess.token), False

    session = _create_session(db, screening.candidate, template)
    screening.session_id = session.id
    screening.status = "interview_sent"
    db.commit()
    db.refresh(screening)
    return _screening_out(screening, session.token), True


@router.post("/screenings/invite")
def invite_candidates(payload: ScreeningInvite, db: Session = Depends(get_db)):
    """批量发起 AI 面试：为选中候选人创建答题链接（幂等，重复调用复用既有会话）。"""
    if not payload.screening_ids:
        raise BizError(422, "请至少选择一位候选人", http_status=422)

    results: list[dict] = []
    created_items: list[dict] = []
    created_job_title = ""
    created_job_id: uuid.UUID | None = None
    for raw_id in payload.screening_ids:
        sid = _parse_uuid(raw_id, "screening_id")
        screening = db.get(ResumeScreening, sid)
        if screening is None:
            raise BizError(404, f"初筛记录不存在：{raw_id}", http_status=404)
        template = _resolve_template(db, screening.job_id, payload.template_id)
        out, created = _invite_one(db, screening, template)
        results.append(out)
        if created:
            created_items.append(
                {
                    "candidate_name": out.get("candidate_name")
                    or (screening.candidate.name if screening.candidate else ""),
                    "interview_path": out.get("interview_path"),
                }
            )
            created_job_id = screening.job_id

    # 批量发起：结束后给 HR 发一张「已发起」卡片（幂等复用的不重复计入）
    notify_result: dict = {"status": "skipped", "reason": "本次无新建面试"}
    if created_items:
        job = db.get(JobPosting, created_job_id) if created_job_id else None
        created_job_title = job.title if job else ""
        try:
            notify_result = notify_interview_created(
                job_title=created_job_title,
                items=created_items,
                source="批量发起",
            )
            logger.info("批量发起面试创建推送：%s", notify_result)
        except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断发起
            logger.exception("批量发起面试创建推送异常，已忽略")
            notify_result = {"status": "failed", "reason": "内部异常"}

    return ok({"count": len(results), "items": results, "notify": notify_result})


@router.delete("/screenings/{screening_id}")
def delete_screening(screening_id: str, db: Session = Depends(get_db)):
    """删除初筛记录。

    - 先解除线下复试对该记录的引用（offline_interviews.screening_id 外键），避免 FK 违约；
    - 若该候选人已无任何业务引用（初筛/会话/线下复试），则一并清理其简历与候选人数据；
    - 已发起的 AI 面试会话不在此删除（保留答题链接与评分）。
    """
    sid = _parse_uuid(screening_id, "screening_id")
    s = db.get(ResumeScreening, sid)
    if s is None:
        raise BizError(404, "初筛记录不存在", http_status=404)

    candidate_id = s.candidate_id
    db.query(OfflineInterview).filter(OfflineInterview.screening_id == sid).update(
        {"screening_id": None}, synchronize_session=False
    )
    db.query(ResumeScreening).filter(ResumeScreening.id == sid).delete(synchronize_session=False)
    db.flush()

    if candidate_is_orphan(db, candidate_id):
        purge_candidate(db, candidate_id)

    db.commit()
    return ok({"deleted": True, "screening_id": str(sid)})
