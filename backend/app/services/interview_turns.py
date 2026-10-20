"""面试逐轮问答原话的落库与读取。

设计要点：
- **原话是独立事实**：不等评分成功、不等面试结束，候选人一提交就落库，
  这样候选人中途退出、评分 LLM 抖动、会话重跑都不会让原话丢失；
- **幂等**：LangGraph 断点恢复会重放节点，用 PostgreSQL ``ON CONFLICT DO NOTHING``
  配合唯一约束 ``(session_id, turn_index)`` 丢弃重复写入，不重复也不覆盖；
- **旁路**：落库失败只记日志，绝不影响面试主流程（与既有评分/推送的「尽力而为」口径一致）。
"""
import logging
import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import InterviewTurn
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 开场白特征：load_context 写入的第一条 AIMessage，没有对应的候选人回答，
# 不能当成提问配对，否则第一题的回答会被错挂到开场白上。
OPENING_MARKERS = ("欢迎参加本次 AI 面试",)


def _is_opening(text: str) -> bool:
    return any(marker in (text or "") for marker in OPENING_MARKERS)


def extract_qa_pairs(messages: list) -> list[tuple[str, str]]:
    """从 LangGraph ``messages`` 中按顺序提取 ``(AI 提问, 候选人回答)`` 配对。

    用于回填历史会话（checkpoint 里只有 messages，没有业务表记录）：
    跳过开场白；孤立出现的回答（没有前置提问）直接忽略，避免错配。
    """
    pairs: list[tuple[str, str]] = []
    pending: str | None = None
    for msg in messages or []:
        content = getattr(msg, "content", "") or ""
        if isinstance(msg, AIMessage):
            pending = None if _is_opening(content) else content
        elif isinstance(msg, HumanMessage):
            if pending is None:
                continue
            pairs.append((pending, content))
            pending = None
    return pairs


def record_turn(
    *,
    session_id: str | uuid.UUID | None,
    turn_index: int,
    question_id: str,
    question_type: str,
    is_follow_up: bool,
    question_text: str,
    answer_text: str,
) -> None:
    """记录一轮「AI 提问 + 候选人回答」原文（幂等，失败不抛）。

    Args:
        session_id: 面试会话 id（缺省或非法时直接跳过，不影响面试）。
        turn_index: 第几轮作答，从 1 开始，同一会话内唯一。
        question_id: 题目 id；追问沿用被追问题目的 id。
        question_type: ``subjective`` / ``choice``。
        is_follow_up: 本轮是否为追问。
        question_text: AI 提问原文。
        answer_text: 候选人回答原文（逐字保留）。
    """
    if not session_id:
        return
    try:
        sid = uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        logger.warning("原话落库跳过：session_id 非法（%r）", session_id)
        return

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        stmt = (
            pg_insert(InterviewTurn)
            .values(
                id=uuid.uuid4(),
                session_id=sid,
                turn_index=turn_index,
                question_id=question_id or "",
                question_type=question_type or "subjective",
                is_follow_up=bool(is_follow_up),
                question_text=question_text or "",
                answer_text=answer_text or "",
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["session_id", "turn_index"],
            )
        )
        db.execute(stmt)
        db.commit()
    except Exception:  # noqa: BLE001 - 旁路记录，不得打断面试
        db.rollback()
        logger.exception("面试原话落库失败 session=%s turn=%s（已忽略）", session_id, turn_index)
    finally:
        db.close()


def list_turns(db: Session, session_id: uuid.UUID) -> list[InterviewTurn]:
    """按发生顺序读取某会话的全部原话（报告页证据链用）。"""
    return list(
        db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == session_id)
            .order_by(InterviewTurn.turn_index.asc())
        )
    )


def align_evaluations(turns: list[InterviewTurn], evaluations: list) -> dict[int, dict]:
    """把评分记录按顺序对齐到轮次，返回 ``{turn_index: 评分 dict}``。

    对齐口径：
    - 逐轮消费评分列表（**按顺序 pop**）——同一道题被追问多轮时会有多条同 ``question_id``
      的评分记录，按顺序消费才能一一对上；
    - 优先匹配 ``question_id`` 相同的记录；匹配不到时退化为「顺序取剩余的第一条」，
      兼容历史数据（改造前的评分记录没有原话对应）；
    - 评分条数少于轮次时，多出的轮次不带评分（``evaluation`` 为 null），
      但原话仍完整展示 —— 原话不依赖评分存在。
    """
    remaining = list(evaluations or [])
    result: dict[int, dict] = {}
    for turn in turns or []:
        idx = next(
            (
                i
                for i, ev in enumerate(remaining)
                if (getattr(ev, "question_id", "") or "") == (turn.question_id or "")
            ),
            None,
        )
        if idx is None:
            idx = 0 if remaining else None
        if idx is None:
            continue
        ev = remaining.pop(idx)
        result[turn.turn_index] = {
            "question_id": getattr(ev, "question_id", ""),
            "scores": getattr(ev, "scores", {}) or {},
            "evidence": getattr(ev, "evidence", []) or [],
            "comment": getattr(ev, "comment", "") or "",
            "follow_up_needed": bool(getattr(ev, "follow_up_needed", False)),
            "follow_up_question": getattr(ev, "follow_up_question", "") or "",
            "created_at": (
                ev.created_at.isoformat() if getattr(ev, "created_at", None) else None
            ),
        }
    return result
