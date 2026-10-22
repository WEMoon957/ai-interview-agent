"""终试闸门服务（老板拍板）。

设计要点：
- 终试是「录用」之前的一道老板关卡。候选人线下复试完成后，汇总快照在
  ``services.summary.rebuild_job_summary`` 里自动置为 ``pending_final``；
- 老板在「老板操作台」对 pending_final 的候选人点「通过 / 不通过」；
- 只有 ``passed_final`` 才允许 HR 发起 Offer（见 ``services.offer.create_offer``）。

本模块是终试状态的**唯一事实源**（常量 / 决策逻辑），路由层与前端不各自维护。
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.summary import RecruitmentSummary
from app.exceptions import BizError

# ── 状态常量（唯一事实源）────────────────────────────────────
FINAL_NONE = "none"  # 历史/未进入终试流程（兼容老数据，允许发 Offer）
FINAL_PENDING = "pending_final"  # 等老板拍板
FINAL_PASSED = "passed_final"  # 终试通过
FINAL_FAILED = "failed_final"  # 终试未通过

FINAL_STATUSES: tuple[str, ...] = (FINAL_NONE, FINAL_PENDING, FINAL_PASSED, FINAL_FAILED)

FINAL_LABELS: dict[str, str] = {
    FINAL_NONE: "未进入终试",
    FINAL_PENDING: "待终试",
    FINAL_PASSED: "终试通过",
    FINAL_FAILED: "终试未通过",
}

# 老板决策入参 → 目标状态
DECISION_TO_STATUS: dict[str, str] = {
    "pass": FINAL_PASSED,
    "fail": FINAL_FAILED,
}


def final_label(status: str) -> str:
    """终试状态中文名（未知原样返回）。"""
    return FINAL_LABELS.get(status, status)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _offline_decision(summary: RecruitmentSummary) -> str:
    """汇总快照里的线下复试结论（pass / hold / reject，空串表示未填）。"""
    detail = getattr(summary, "detail", None) or {}
    return str(detail.get("offline_decision") or "").strip().lower()


def is_rejected_offline(summary: RecruitmentSummary) -> bool:
    """线下复试是否已判定「不推荐」（reject）。

    店长只负责在飞书妙记录音、不操作系统；这里的结论由 HR 在后台录入线下复试
    打分时填写。判了 reject 的候选人**直接跳过终试**，不进老板队列、也不推送。
    """
    return _offline_decision(summary) == "reject"


def mark_pending_final_if_complete(summary: RecruitmentSummary) -> bool:
    """汇总刚走完全链路时，自动进入「待终试」。

    仅在当前为 ``none``（尚未进入终试流程）时置为 ``pending_final``，
    已处于 passed/failed/pending 的不回退，保证老板决策不被后续汇总重建覆盖。
    ``is_complete`` 只表示流程走完（reject 也算走完），因此这里必须额外排除
    线下判了「不推荐」的人，否则店长/HR 已经否掉的候选人也会推给老板。
    返回是否发生了状态变更。
    """
    if (
        summary.final_interview_status == FINAL_NONE
        and summary.is_complete
        and not is_rejected_offline(summary)
    ):
        summary.final_interview_status = FINAL_PENDING
        return True
    return False


def revoke_pending_final_if_rejected(summary: RecruitmentSummary) -> bool:
    """线下改判「不推荐」后，把候选人从老板队列撤回（pending → failed）。

    场景：先按 pass 入库进了待终试队列（已推送老板），之后 HR 复核改判 reject。
    若不撤回，老板会对着不合适的人安排终面。已经由老板决策过（passed / failed）
    的不动——老板的结论优先。
    """
    if summary.final_interview_status == FINAL_PENDING and is_rejected_offline(summary):
        summary.final_interview_status = FINAL_FAILED
        summary.final_interview_note = "线下复试判定不推荐，自动跳过终试"
        summary.final_interview_decided_by = "system"
        summary.final_interview_decided_at = _utcnow()
        return True
    return False


def list_pending_final(
    db: Session,
    *,
    job_id: Any | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> list[RecruitmentSummary]:
    """列出待终试决策的汇总（按最终分降序）。"""
    query = db.query(RecruitmentSummary).filter(
        RecruitmentSummary.final_interview_status == FINAL_PENDING
    )
    if job_id is not None:
        query = query.filter(RecruitmentSummary.job_id == job_id)
    if keyword:
        query = query.filter(RecruitmentSummary.candidate_name.ilike(f"%{keyword}%"))
    return query.order_by(
        RecruitmentSummary.final_score.is_(None),
        RecruitmentSummary.final_score.desc(),
        RecruitmentSummary.created_at.desc(),
    ).limit(max(1, limit)).all()


def schedule_final_interview(
    db: Session,
    summary: RecruitmentSummary,
    *,
    scheduled_at: datetime | None,
    operator_username: str = "",
) -> RecruitmentSummary:
    """老板确认/修改终面时间（仅 ``pending_final`` 可安排）。

    与 ``decide_final_interview`` 分开：先约时间、面试完再拍板是常态，
    老板也可能当场面试当场判定，所以决策时也允许顺带传入时间（见 decide 的 scheduled_at）。
    传 ``None`` 表示清空已安排的时间（改回「待约」）。
    """
    if summary.final_interview_status == FINAL_PASSED:
        raise BizError(409, "该候选人终试已通过，无需再安排终面", http_status=409)
    if summary.final_interview_status == FINAL_FAILED:
        raise BizError(409, "该候选人终试未通过，不能再安排终面", http_status=409)
    if summary.final_interview_status != FINAL_PENDING:
        raise BizError(
            409,
            f"当前状态为「{final_label(summary.final_interview_status)}」，无法安排终面",
            http_status=409,
        )

    if scheduled_at is not None and scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    summary.final_interview_scheduled_at = scheduled_at
    if operator_username:
        summary.final_interview_decided_by = operator_username
    return summary


def decide_final_interview(
    db: Session,
    summary: RecruitmentSummary,
    *,
    decision: str,
    note: str = "",
    operator_username: str = "",
    scheduled_at: datetime | None = None,
) -> RecruitmentSummary:
    """老板做终试决策：pending_final → passed_final / failed_final。

    非 pending_final 状态不允许决策（已决/历史记录不可改）；
    decision 仅接受 pass / fail。``scheduled_at`` 非空时一并记录终面时间
    （老板当场面试当场拍板的场景）。
    """
    if summary.final_interview_status == FINAL_PASSED:
        raise BizError(409, "该候选人终试已通过，无需重复决策", http_status=409)
    if summary.final_interview_status == FINAL_FAILED:
        raise BizError(409, "该候选人终试已判定为未通过，不可重复决策", http_status=409)
    if summary.final_interview_status != FINAL_PENDING:
        raise BizError(
            409,
            f"当前状态为「{final_label(summary.final_interview_status)}」，无法做终试决策",
            http_status=409,
        )

    target = DECISION_TO_STATUS.get(decision)
    if target is None:
        raise BizError(422, "终试决策无效（可选：pass / fail）", http_status=422)

    summary.final_interview_status = target
    summary.final_interview_note = (note or "").strip()
    summary.final_interview_decided_by = operator_username or ""
    summary.final_interview_decided_at = _utcnow()
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        summary.final_interview_scheduled_at = scheduled_at
    return summary


def serialize_for_boss(summary: RecruitmentSummary) -> dict[str, Any]:
    """老板操作台所需的精简序列化（含各环节评分 + 终试状态）。"""
    return {
        "id": str(summary.id),
        "job_id": str(summary.job_id),
        "job_title": summary.job.title if summary.job else "",
        "candidate_id": str(summary.candidate_id),
        "candidate_name": summary.candidate_name,
        "screening_score": summary.screening_score,
        "ai_interview_score": summary.ai_interview_score,
        "offline_score": summary.offline_score,
        "final_score": summary.final_score,
        "final_verdict": summary.final_verdict,
        "is_complete": summary.is_complete,
        "source": (
            "walk_in"
            if (summary.detail or {}).get("is_walk_in")
            else (summary.detail or {}).get("offline_source") or "online"
        ),
        "final_interview_status": summary.final_interview_status,
        "final_interview_label": final_label(summary.final_interview_status),
        "final_interview_note": summary.final_interview_note,
        # 老板确认的终面时间（NULL = 还没安排）
        "final_interview_scheduled_at": (
            summary.final_interview_scheduled_at.isoformat()
            if summary.final_interview_scheduled_at
            else None
        ),
        "final_interview_decided_by": summary.final_interview_decided_by,
        "final_interview_decided_at": (
            summary.final_interview_decided_at.isoformat()
            if summary.final_interview_decided_at
            else None
        ),
        "snapshot_at": summary.snapshot_at.isoformat() if summary.snapshot_at else None,
    }


def pending_final_count(db: Session) -> int:
    """待终试数量（操作台概览用）。"""
    return int(
        db.query(func.count(RecruitmentSummary.id))
        .filter(RecruitmentSummary.final_interview_status == FINAL_PENDING)
        .scalar()
        or 0
    )
