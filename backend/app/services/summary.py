"""招聘汇总聚合：把候选人全链路各阶段结果汇成一条汇总记录。

聚合口径（一岗位一候选人一条）：
- 简历初筛分：``resume_screenings.total_score``（同候选人取最近一条）
- AI 面试分：``session_reports.total_score``（该候选人在本岗位下的会话，取最近一份报告）
- 线下复试分：``offline_interviews.score``（同岗位同候选人，取最近一条已打分记录）

最终分 = 各可用阶段得分按权重加权后重新归一化（缺环节不惩罚，只对已有环节加权）；
纯函数式聚合，便于单测。

落库（``rebuild_job_summary``）同样是**唯一实现**：后台「生成 / 更新汇总落库」按钮与
业务事件自动落库（初筛完成 / AI 报告生成 / 线下打分 / 改名）共用同一段代码，
保证手动与自动写出的快照口径完全一致。
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    OfflineInterview,
    RecruitmentSummary,
    ResumeScreening,
    SessionReport,
)
from app.services.feishu_notify import notify_final_pending, notify_summary_review
from app.services.final_interview import (
    mark_pending_final_if_complete,
    revoke_pending_final_if_rejected,
)
from app.services.resume_screening import recommendation_from_score

logger = logging.getLogger(__name__)

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
                "offline_decision": "",
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
        # 店长的录用结论（pass / hold / reject）：reject 直接跳过终试，不进老板队列
        row["offline_decision"] = o.decision or ""
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
                "offline_decision": row["offline_decision"],
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


def notify_item(row: RecruitmentSummary) -> dict[str, Any]:
    """汇总行 -> 推送卡片用的精简字段。"""
    return {
        "candidate_name": row.candidate_name,
        "final_score": row.final_score,
        "final_verdict": row.final_verdict,
        "screening_score": row.screening_score,
        "ai_interview_score": row.ai_interview_score,
        "offline_score": row.offline_score,
    }


def rebuild_job_summary(
    db: Session,
    job_id: uuid.UUID,
    *,
    notify: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """聚合单个岗位的全链路数据并**幂等落库**（一岗位一候选人一条，存在则覆盖）。

    返回 ``{"saved": 落库条数, "notify": 推送结果}``。

    ``notify=True`` 时，把本次**新达成全链路完成且尚未提醒过**的候选人汇成一张卡片推送 HR
    （``recruitment_summaries.notified_at`` 保证逐候选人只推一次，重复调用不会刷屏）；
    自动落库路径若只想刷新快照、不想打扰 HR，传 ``notify=False``。
    """
    job = db.get(JobPosting, job_id)
    if job is None:
        return {"saved": 0, "notify": {"job_title": "", "status": "skipped", "reason": "岗位不存在"}}

    stamp = now or datetime.now(timezone.utc)
    saved = 0
    pending_notify: list[RecruitmentSummary] = []
    newly_pending_final: list[RecruitmentSummary] = []

    for r in build_summary_rows(db, job.id):
        existing = (
            db.query(RecruitmentSummary)
            .filter(
                RecruitmentSummary.job_id == job.id,
                RecruitmentSummary.candidate_id == uuid.UUID(r["candidate_id"]),
            )
            .first()
        )
        detail = {
            "screening_score": r["screening_score"],
            "ai_interview_score": r["ai_interview_score"],
            "ai_interview_verdict": r.get("ai_interview_verdict", ""),
            "ai_interview_summary": r.get("ai_interview_summary", ""),
            "offline_score": r["offline_score"],
            "offline_decision": r.get("offline_decision", ""),
            "screening_id": r["screening_id"],
            "session_id": r["session_id"],
            "session_token": r["session_token"],
            "offline_id": r["offline_id"],
            "offline_status": r["offline_status"],
            "offline_scheduled_at": r["offline_scheduled_at"],
            "interviewer": r["interviewer"],
            "stage": r["stage"],
            "screening_status": r["screening_status"],
            "session_status": r["session_status"],
            "offline_source": r.get("offline_source", "online"),
            "is_walk_in": r.get("is_walk_in", False),
        }
        if existing is None:
            existing = RecruitmentSummary(job_id=job.id, candidate_id=uuid.UUID(r["candidate_id"]))
            db.add(existing)
        existing.candidate_name = r["candidate_name"]
        existing.screening_score = r["screening_score"]
        existing.ai_interview_score = r["ai_interview_score"]
        existing.offline_score = r["offline_score"]
        existing.final_score = r["final_score"]
        existing.stage = r["stage"]
        existing.final_verdict = r["final_verdict"]
        existing.is_complete = r["is_complete"]
        existing.summary = r["summary"]
        # detail 必须先落库再走终试闸门：闸门要读里面的线下结论（offline_decision）
        existing.detail = detail
        # 刚走完全链路、且店长没标「不推荐」→ 自动进入「待终试」，等老板确认终面时间
        if mark_pending_final_if_complete(existing):
            newly_pending_final.append(existing)
        # 已在队列、之后被店长改判「不推荐」→ 自动撤回，避免老板对着不合适的人排终面
        if revoke_pending_final_if_rejected(existing):
            logger.info(
                "线下复试改判不推荐，已撤回待终试 candidate=%s job=%s",
                existing.candidate_name,
                job.title,
            )
        existing.snapshot_at = stamp
        saved += 1

        if r["is_complete"] and existing.notified_at is None:
            pending_notify.append(existing)

    db.commit()

    # 终试推送：独立于 HR 汇总提醒，不随 notify 开关关闭——状态只会从 none 变一次，
    # 天然幂等；老板收不到通知，整条「等老板确认终面」的链路就断了。
    final_pending_res = _notify_boss_new_pending(job, newly_pending_final)

    if not notify:
        return {
            "saved": saved,
            "notify": {"job_title": job.title, "status": "skipped", "reason": "调用方指定不推送（notify=false）"},
            "final_pending": final_pending_res,
        }
    if not pending_notify:
        return {
            "saved": saved,
            "notify": {"job_title": job.title, "status": "skipped", "reason": "无新完成待提醒的候选人"},
            "final_pending": final_pending_res,
        }

    try:
        res = notify_summary_review(
            job_title=job.title,
            items=[notify_item(row) for row in pending_notify],
            snapshot_at=stamp.strftime("%Y-%m-%d %H:%M"),
        )
    except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断落库
        logger.exception("汇总复核推送异常，已忽略")
        res = {"status": "failed", "reason": "内部异常"}

    if res.get("status") == "sent":
        for row in pending_notify:
            row.notified_at = stamp
        db.commit()

    return {
        "saved": saved,
        "notify": {"job_title": job.title, **res},
        "final_pending": final_pending_res,
    }


def _notify_boss_new_pending(
    job: JobPosting, rows: list[RecruitmentSummary]
) -> dict[str, Any]:
    """把新进入「待终试」的候选人推给老板（卡片 + 待办）。绝不抛异常。"""
    if not rows:
        return {"status": "skipped", "reason": "无新进入待终试的候选人"}
    items: list[dict[str, Any]] = []
    for row in rows:
        item = notify_item(row)
        item["job_title"] = job.title
        scheduled = getattr(row, "final_interview_scheduled_at", None)
        item["final_interview_scheduled_at"] = (
            scheduled.strftime("%Y-%m-%d %H:%M") if scheduled else ""
        )
        items.append(item)
    try:
        return notify_final_pending(items=items)
    except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断落库
        logger.exception("终试推送异常，已忽略")
        return {"status": "failed", "reason": "内部异常"}


def auto_rebuild_summary(job_id: Any, *, notify: bool = True) -> None:
    """业务事件后自动重建**单个岗位**的汇总快照（尽力而为，绝不影响主流程）。

    必须在调用方 ``db.commit()`` **之后**调用 —— 这里开独立会话读库，
    读不到调用方尚未提交的改动。任何异常只记日志，不向外抛。
    """
    if not job_id:
        return
    from app.db.session import SessionLocal  # 延迟导入，避免模块级循环依赖

    db = SessionLocal()
    try:
        result = rebuild_job_summary(db, uuid.UUID(str(job_id)), notify=notify)
        logger.info(
            "自动汇总落库 job=%s saved=%s notify=%s",
            job_id,
            result.get("saved"),
            (result.get("notify") or {}).get("status"),
        )
    except Exception:  # noqa: BLE001 - 自动落库失败不得影响业务
        db.rollback()
        logger.exception("自动汇总落库失败 job=%s（已忽略，不影响主流程）", job_id)
    finally:
        db.close()


def auto_rebuild_for_candidate(candidate_id: Any, *, notify: bool = False) -> None:
    """候选人维度触发（改名 / 建档等）：重建该候选人涉及的**全部岗位**快照。

    改名只改展示名，不应触发 HR 复核推送，因此默认 ``notify=False``。
    """
    if not candidate_id:
        return
    from app.db.session import SessionLocal  # 延迟导入，避免模块级循环依赖

    cid = uuid.UUID(str(candidate_id))
    db = SessionLocal()
    try:
        job_ids: set[uuid.UUID] = set()
        job_ids.update(
            jid for (jid,) in db.query(ResumeScreening.job_id).filter(ResumeScreening.candidate_id == cid).all()
        )
        job_ids.update(
            jid
            for (jid,) in (
                db.query(InterviewTemplate.job_id)
                .join(InterviewSession, InterviewSession.template_id == InterviewTemplate.id)
                .filter(InterviewSession.candidate_id == cid)
                .all()
            )
        )
        job_ids.update(
            jid for (jid,) in db.query(OfflineInterview.job_id).filter(OfflineInterview.candidate_id == cid).all()
        )
        for jid in job_ids:
            rebuild_job_summary(db, jid, notify=notify)
        logger.info("候选人汇总自动重建 candidate=%s jobs=%s", candidate_id, len(job_ids))
    except Exception:  # noqa: BLE001 - 自动落库失败不得影响业务
        db.rollback()
        logger.exception("候选人汇总自动重建失败 candidate=%s（已忽略）", candidate_id)
    finally:
        db.close()
