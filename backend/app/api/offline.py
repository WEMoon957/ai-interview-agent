"""线下复试路由：HR / 店长 约线下面试 + 人工打分 + 到店直招。

两条入口：
1. **线上投递**：AI 面试达标后，由 HR / 店长约线下复试、录入打分（source=online）；
2. **到店直招**：候选人直接上门找店长面试，无简历、不走初筛与 AI 面试 ——
   店长用「到店直招」一次创建候选人（姓名 + 手机号 + 来源渠道）与面试记录，
   并可上传**整场面试录音**存档，再录入打分（source=walk_in）。

数据落库 offline_interviews，并参与招聘汇总。
"""
import logging
import mimetypes
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.api.schemas import OfflineInterviewCreate, OfflineInterviewUpdate, OfflineScoreSubmit
from app.db.models import (
    Candidate,
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    OfflineInterview,
    ResumeScreening,
)
from app.exceptions import BizError
from app.services.audio_storage import audio_abs_path, delete_audio, save_audio_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["offline"])

VALID_STATUS = {"scheduled", "done", "no_show", "cancelled"}
VALID_ROLE = {"hr", "store_manager"}
VALID_DECISION = {"pass", "hold", "reject"}
# 来源路径
SOURCE_ONLINE = "online"
SOURCE_WALK_IN = "walk_in"


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
        "candidate_phone": (o.candidate.phone or "") if o.candidate else "",
        "source_channel": (o.candidate.source_channel or "") if o.candidate else "",
        "job_id": str(o.job_id),
        "job_title": o.job.title if o.job else "",
        "source": o.source or SOURCE_ONLINE,
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
        # 面试录音（有则给出播放/下载地址，前端据此渲染播放器）
        "has_audio": bool(o.audio_path),
        "audio_name": o.audio_name or "",
        "audio_size": o.audio_size,
        "audio_url": (
            f"/admin/offline-interviews/{o.id}/audio" if o.audio_path else None
        ),
        "audio_uploaded_at": o.audio_uploaded_at.isoformat() if o.audio_uploaded_at else None,
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


@router.post("/offline-interviews/walk-in")
async def create_walk_in_interview(
    job_id: str = Form(..., description="应聘岗位 id"),
    name: str = Form(..., description="候选人姓名"),
    phone: str = Form(..., description="手机号"),
    source_channel: str = Form(..., description="来源渠道，如 门店海报 / 员工介绍 / 自然到访"),
    interviewer: str = Form("", description="面试官（店长）姓名"),
    interviewer_role: str = Form("", description="面试官角色：hr / store_manager"),
    scheduled_at: str = Form("", description="面试时间（ISO8601，可空）"),
    location: str = Form("", description="面试门店/地点"),
    audio: UploadFile | None = File(None, description="整场面试录音（可选，格式见白名单）"),
    db: Session = Depends(get_db),
):
    """**到店直招**：一次创建候选人 + 线下复试记录（无初筛、无 AI 面试），可选同时上传面试录音。

    - 姓名 / 手机号 / 来源渠道 **必填**；
    - 同「姓名 + 手机号」的候选人已存在时**复用**，不重复建人；
    - 同一候选人在同一岗位已有面试记录时返回 409，避免重复提交；
    - 录音上传失败不影响记录创建（可稍后通过 /audio 接口补传）。
    """
    jid = _parse_uuid(job_id, "job_id")
    job = db.get(JobPosting, jid)
    if job is None:
        raise BizError(404, "岗位不存在", http_status=404)

    name = (name or "").strip()
    phone = (phone or "").strip()
    source_channel = (source_channel or "").strip()
    if not name:
        raise BizError(422, "请填写候选人姓名", http_status=422)
    if not phone:
        raise BizError(422, "请填写手机号", http_status=422)
    if not source_channel:
        raise BizError(422, "请选择来源渠道", http_status=422)
    if interviewer_role and interviewer_role not in VALID_ROLE:
        raise BizError(422, "面试官角色无效（应为 hr / store_manager）", http_status=422)

    # 复用同姓名+手机号的候选人（防止店长重复提交造成多份候选人档案）
    candidate = (
        db.query(Candidate)
        .filter(Candidate.name == name, Candidate.phone == phone)
        .order_by(Candidate.created_at.asc())
        .first()
    )
    if candidate is None:
        candidate = Candidate(
            name=name,
            phone=phone,
            source_channel=source_channel,
        )
        db.add(candidate)
        db.flush()
    elif source_channel and not candidate.source_channel:
        candidate.source_channel = source_channel

    # 同一岗位重复创建 -> 409（提示店长去改已有记录而不是重复建）
    dup = (
        db.query(OfflineInterview)
        .filter(
            OfflineInterview.candidate_id == candidate.id,
            OfflineInterview.job_id == jid,
        )
        .first()
    )
    if dup is not None:
        raise BizError(
            409,
            f"「{name}」在岗位「{job.title}」下已有面试记录，请直接在列表中编辑或打分",
            http_status=409,
        )

    row = OfflineInterview(
        screening_id=None,
        session_id=None,
        candidate_id=candidate.id,
        job_id=jid,
        source=SOURCE_WALK_IN,
        scheduled_at=_parse_dt(scheduled_at, "scheduled_at"),
        location=location or "",
        interviewer=interviewer or "",
        interviewer_role=interviewer_role or "",
        status="scheduled",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # 录音为可选：失败只记录日志，不阻断建档（店长可稍后补传）
    if audio is not None and (audio.filename or ""):
        try:
            saved = await save_audio_upload(audio)
            row.audio_path = saved["rel_path"]
            row.audio_name = saved["name"]
            row.audio_size = saved["size"]
            row.audio_uploaded_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(row)
        except BizError as exc:
            logger.warning("到店直招录音上传被拒：%s", exc.message)
            return ok({"record": _offline_out(row), "audio_error": exc.message})
        except Exception:  # noqa: BLE001 - 录音失败不影响建档
            logger.exception("到店直招录音保存异常")
            return ok({"record": _offline_out(row), "audio_error": "录音保存失败，请稍后重试上传"})

    return ok({"record": _offline_out(row), "audio_error": ""})


@router.post("/offline-interviews/{offline_id}/audio")
async def upload_offline_audio(
    offline_id: str,
    audio: UploadFile = File(..., description="整场面试录音"),
    db: Session = Depends(get_db),
):
    """上传 / 替换某场线下复试的面试录音（整场一段）。"""
    oid = _parse_uuid(offline_id, "offline_id")
    row = db.get(OfflineInterview, oid)
    if row is None:
        raise BizError(404, "线下复试记录不存在", http_status=404)

    saved = await save_audio_upload(audio)
    old_path = row.audio_path
    row.audio_path = saved["rel_path"]
    row.audio_name = saved["name"]
    row.audio_size = saved["size"]
    row.audio_uploaded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)

    # 替换成功后再清掉旧文件
    if old_path and old_path != row.audio_path:
        delete_audio(old_path)

    return ok(_offline_out(row))


@router.get("/offline-interviews/{offline_id}/audio")
def download_offline_audio(
    offline_id: str,
    download: bool = Query(default=False, description="true 则强制下载（否则浏览器内联播放）"),
    db: Session = Depends(get_db),
):
    """获取面试录音：默认内联播放（支持 Range，可拖动进度条），``?download=true`` 强制下载。"""
    oid = _parse_uuid(offline_id, "offline_id")
    row = db.get(OfflineInterview, oid)
    if row is None:
        raise BizError(404, "线下复试记录不存在", http_status=404)
    if not row.audio_path:
        raise BizError(404, "该记录未上传面试录音", http_status=404)

    path = audio_abs_path(row.audio_path)
    if not path.exists():
        raise BizError(404, "录音文件已丢失，请重新上传", http_status=404)

    media_type = mimetypes.guess_type(row.audio_name or path.name)[0] or "application/octet-stream"
    if not download:
        # 不给 filename -> 不带 Content-Disposition: attachment，浏览器内联播放
        return FileResponse(path, media_type=media_type)

    filename = row.audio_name or path.name
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/offline-interviews")
def list_offline_interviews(
    job_id: str | None = None,
    status: str | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
):
    """线下复试列表（按创建时间倒序）；可按岗位 / 状态 / 来源（online / walk_in）过滤。"""
    query = db.query(OfflineInterview)
    if job_id:
        query = query.filter(OfflineInterview.job_id == _parse_uuid(job_id, "job_id"))
    if status:
        query = query.filter(OfflineInterview.status == status)
    if source:
        query = query.filter(OfflineInterview.source == source)
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
    """删除线下复试记录（连同已上传的面试录音文件）。"""
    oid = _parse_uuid(offline_id, "offline_id")
    row = db.get(OfflineInterview, oid)
    if row is None:
        raise BizError(404, "线下复试记录不存在", http_status=404)
    audio_path = row.audio_path
    db.delete(row)
    db.commit()
    delete_audio(audio_path)
    return ok({"deleted": True, "offline_id": str(oid)})
