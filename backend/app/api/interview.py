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


def _load_session(db: Session, token: str) -> InterviewSession:
    session = db.query(InterviewSession).filter(InterviewSession.token == token).first()
    if session is None:
        raise BizError(404, "会话不存在或 token 无效", http_status=404)
    return session


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


@router.get("/{token}")
def get_context(token: str, db: Session = Depends(get_db)):
    """获取面试上下文（首次调用触发跑图并挂起在首题）。"""
    sess = _load_session(db, token)
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
                "progress": _progress(values, current_idx),
                "status": "running",
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
            }
        )

    interrupt_value = _get_pending_interrupt(snapshot)
    return ok(
        {
            "session_id": str(sess.id),
            "opening": _opening_from(values),
            "question": (interrupt_value or {}).get("question", ""),
            "question_id": (interrupt_value or {}).get("question_id", ""),
            "progress": _progress(values, (interrupt_value or {}).get("current_idx", 0)),
            "status": "running",
        }
    )


@router.post("/{token}/answer")
def submit_answer(token: str, payload: AnswerSubmit, db: Session = Depends(get_db)):
    """提交回答：恢复图执行，返回追问 / 下一题 / 完成。"""
    sess = _load_session(db, token)
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
