"""候选人端路由：获取上下文 / 提交回答。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.api.schemas import AnswerSubmit
from app.db.models import (
    Candidate,
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    Resume,
)
from app.exceptions import BizError
from app.graph.builder import get_interview_graph

router = APIRouter(prefix="/interview", tags=["interview"])

# 未同意《安全知情协议》时的统一提示
CONSENT_REQUIRED_MSG = "请先阅读并同意《安全知情协议》后再进行面试"


def _load_session(db: Session, token: str) -> InterviewSession:
    session = db.query(InterviewSession).filter(InterviewSession.token == token).first()
    if session is None:
        raise BizError(404, "会话不存在或 token 无效", http_status=404)
    return session


def _consent_payload(sess: InterviewSession) -> dict:
    """协议同意状态（候选人端门禁与服务端校验共用）。"""
    return {
        "required": True,
        "agreed": sess.consent_agreed_at is not None,
        "agreed_at": sess.consent_agreed_at.isoformat() if sess.consent_agreed_at else None,
    }


def _ensure_consent(sess: InterviewSession) -> None:
    """未同意协议时拒绝进入面试（服务端强校验，防止绕过前端门禁）。"""
    if sess.consent_agreed_at is None:
        raise BizError(403, CONSENT_REQUIRED_MSG, http_status=403)


def _build_initial_state(db: Session, sess: InterviewSession) -> dict:
    """从 DB 组装图初始状态（共享知识 #7：thread_id 对应 checkpointer）。"""
    from app.config import get_settings

    template = db.get(InterviewTemplate, sess.template_id)
    candidate = db.get(Candidate, sess.candidate_id)
    resume = (
        db.query(Resume).filter(Resume.candidate_id == sess.candidate_id).first()
        if candidate is not None
        else None
    )
    job = db.get(JobPosting, template.job_id) if template and template.job_id else None

    # 追问上限：0 是合法值（禁止追问），必须用 is None 区分「未设置」，
    # 不能写 `... or 2`——那会把 0 强制改回 2（falsy 陷阱）。
    max_follow_ups = template.max_follow_ups if template is not None else None
    if max_follow_ups is None:
        max_follow_ups = get_settings().default_max_follow_ups

    return {
        "thread_id": sess.thread_id,
        "session_id": str(sess.id),
        "candidate": {
            "name": candidate.name if candidate else "",
            "resume_text": resume.parsed_text if resume else "",
            "job_desc": job.jd_text if job else "",
        },
        # 公司介绍取会话快照（发链接时写入，CC-4）；存量 / 未配置会话为空字典，
        # load_context 走无介绍分支，行为与改造前一致
        "company_profile": sess.company_profile_snapshot or {},
        "questions": template.questions if template else [],
        "rubric": template.rubric if template else [],
        "current_idx": 0,
        "max_follow_ups": max_follow_ups,
        "follow_up_count": 0,
        "messages": [],
        "evaluations": [],
        "status": "pending",
        "report": {},
    }


def _get_pending_interrupt(snapshot) -> dict | None:
    """从 StateSnapshot 的待处理任务中提取 interrupt 载荷。

    注意：langgraph 0.2.x 的 ``graph.invoke()`` 返回值不再携带 ``__interrupt__``
    键，真正的 interrupt 载荷在 ``graph.get_state(config).tasks[].interrupts[].value``。
    """
    tasks = getattr(snapshot, "tasks", None) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or []
        if interrupts:
            return getattr(interrupts[0], "value", interrupts[0])
    return None


def _opening_from(values: dict) -> str:
    messages = values.get("messages") or []
    if not messages:
        return ""
    return getattr(messages[0], "content", "") or ""


def _progress(values: dict, current_idx: int | None = None) -> dict:
    total = len(values.get("questions") or [])
    cur = values.get("current_idx", 0) if current_idx is None else current_idx
    return {"current": cur, "total": total}


def _question_meta(interrupt_value: dict | None) -> dict:
    """从 interrupt 载荷提取题型与选项（主观题返回空列表）。"""
    value = interrupt_value or {}
    return {
        "question_type": value.get("question_type") or "subjective",
        "options": list(value.get("options") or []),
    }


@router.get("/{token}")
def get_context(token: str, db: Session = Depends(get_db)):
    """获取面试上下文（首次调用触发跑图并挂起在首题）。

    未同意《安全知情协议》时不跑图、不返回题目，仅返回 consent_required（前端展示协议页）。
    """
    sess = _load_session(db, token)

    if sess.consent_agreed_at is None:
        return ok(
            {
                "session_id": str(sess.id),
                "opening": "",
                "question": "",
                "question_id": "",
                "progress": {"current": 0, "total": 0},
                "status": "consent_required",
                "consent": _consent_payload(sess),
            }
        )

    graph = get_interview_graph()
    config = {"configurable": {"thread_id": sess.thread_id}}

    snapshot = graph.get_state(config)
    values = snapshot.values or {}

    if not values:
        # 首次：组装初始状态并跑到第一个 interrupt
        graph.invoke(_build_initial_state(db, sess), config)

        snapshot = graph.get_state(config)
        values = snapshot.values or {}
        interrupt_value = _get_pending_interrupt(snapshot)

        # 首次取上下文时将会话置为 running（A9）
        if sess.status != "finished":
            sess.status = "running"
            if sess.started_at is None:
                sess.started_at = datetime.now(timezone.utc)
            db.commit()

        opening = _opening_from(values)
        question = (interrupt_value or {}).get("question", "")
        question_id = (interrupt_value or {}).get("question_id", "")
        current_idx = (interrupt_value or {}).get("current_idx", 0)
        return ok(
            {
                "session_id": str(sess.id),
                "opening": opening,
                "question": question,
                "question_id": question_id,
                **_question_meta(interrupt_value),
                "progress": _progress(values, current_idx),
                "status": "running",
                "consent": _consent_payload(sess),
            }
        )

    # 非首次：直接读当前状态
    if values.get("status") == "finished":
        total = len(values.get("questions") or [])
        return ok(
            {
                "session_id": str(sess.id),
                "opening": _opening_from(values),
                "question": "",
                "question_id": "",
                "progress": {"current": total, "total": total},
                "status": "finished",
                "consent": _consent_payload(sess),
            }
        )

    interrupt_value = _get_pending_interrupt(snapshot)
    return ok(
        {
            "session_id": str(sess.id),
            "opening": _opening_from(values),
            "question": (interrupt_value or {}).get("question", ""),
            "question_id": (interrupt_value or {}).get("question_id", ""),
            **_question_meta(interrupt_value),
            "progress": _progress(values, (interrupt_value or {}).get("current_idx", 0)),
            "status": "running",
            "consent": _consent_payload(sess),
        }
    )


@router.post("/{token}/consent")
def agree_consent(token: str, db: Session = Depends(get_db)):
    """候选人勾选同意《安全知情协议》（幂等：重复调用保留首次同意时间）。"""
    sess = _load_session(db, token)
    if sess.consent_agreed_at is None:
        sess.consent_agreed_at = datetime.now(timezone.utc)
        db.commit()
    return ok(_consent_payload(sess))


@router.post("/{token}/answer")
def submit_answer(token: str, payload: AnswerSubmit, db: Session = Depends(get_db)):
    """提交回答：恢复图执行，返回追问 / 下一题 / 完成。"""
    sess = _load_session(db, token)
    _ensure_consent(sess)
    graph = get_interview_graph()
    config = {"configurable": {"thread_id": sess.thread_id}}

    snapshot = graph.get_state(config)
    values = snapshot.values or {}

    # 已完成：直接返回，避免对无 pending interrupt 的线程继续 resume
    if sess.status == "finished" or values.get("status") == "finished":
        total = len(values.get("questions") or [])
        return ok(
            {
                "status": "finished",
                "question": "",
                "question_id": "",
                "progress": {"current": total, "total": total},
                "message": "面试已完成，感谢参与！",
            }
        )

    graph.invoke(Command(resume=payload.answer), config)

    # 恢复后的最新状态：interrupt 载荷从 get_state().tasks 中提取
    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    interrupt_value = _get_pending_interrupt(snapshot)

    if interrupt_value is not None:
        q_type = interrupt_value.get("type")
        status = "follow_up" if q_type == "follow_up" else "next_question"
        return ok(
            {
                "status": status,
                "question": interrupt_value.get("question", ""),
                "question_id": interrupt_value.get("question_id", ""),
                **_question_meta(interrupt_value),
                "progress": _progress(values, interrupt_value.get("current_idx", 0)),
                "message": "",
            }
        )

    # 无 pending interrupt：已收尾
    total = len(values.get("questions") or [])
    return ok(
        {
            "status": "finished",
            "question": "",
            "question_id": "",
            "progress": {"current": total, "total": total},
            "message": "面试已完成，感谢参与！",
        }
    )
