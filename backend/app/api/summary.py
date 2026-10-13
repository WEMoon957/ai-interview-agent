"""招聘汇总路由：实时聚合全链路数据 + 落库快照。

- GET  /admin/summary            实时聚合（不落库，供页面即时查看）
- POST /admin/summary/rebuild    聚合后幂等写入 recruitment_summaries（汇总数据落库）
- GET  /admin/summary/snapshots  读取已落库的汇总快照
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import nulls_last
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.db.models import JobPosting, RecruitmentSummary
from app.exceptions import BizError
from app.services.feishu_notify import notify_summary_review
from app.services.summary import build_summary_rows

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["summary"])


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """解析 UUID 字符串，失败抛 422。"""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


def _summary_out(row: RecruitmentSummary) -> dict:
    """汇总记录序列化。"""
    return {
        "id": str(row.id),
        "job_id": str(row.job_id),
        "job_title": row.job.title if row.job else "",
        "candidate_id": str(row.candidate_id),
        "candidate_name": row.candidate_name,
        "screening_score": row.screening_score,
        "ai_interview_score": row.ai_interview_score,
        "offline_score": row.offline_score,
        "final_score": row.final_score,
        "stage": row.stage,
        "final_verdict": row.final_verdict,
        "is_complete": row.is_complete,
        "summary": row.summary,
        "detail": row.detail,
        "snapshot_at": row.snapshot_at.isoformat() if row.snapshot_at else None,
    }


@router.get("/summary")
def get_summary(
    job_id: str | None = Query(default=None, description="岗位 id；不传则聚合全部岗位"),
    only_complete: bool = Query(default=False, description="仅返回已走完全链路的候选人"),
    db: Session = Depends(get_db),
):
    """实时聚合招聘全链路数据（不落库）。"""
    if job_id:
        rows = build_summary_rows(db, _parse_uuid(job_id, "job_id"))
    else:
        jobs = db.query(JobPosting).order_by(JobPosting.created_at.desc()).all()
        rows = []
        for j in jobs:
            rows.extend(build_summary_rows(db, j.id))
        rows.sort(key=lambda r: (r["final_score"] is None, -(r["final_score"] or 0)))

    if only_complete:
        rows = [r for r in rows if r.get("is_complete")]

    return ok(rows)


@router.post("/summary/rebuild")
def rebuild_summary(
    job_id: str | None = Query(default=None, description="岗位 id；不传则重建全部岗位"),
    notify: bool = Query(default=True, description="是否向 HR 推送「复核 + offer/入职」提醒卡片"),
    db: Session = Depends(get_db),
):
    """聚合并把汇总数据幂等落库（一岗位一候选人一条，存在则覆盖更新）。

    落库后，若 ``notify=true``，把**本次新达成全链路完成、且尚未提醒过**的候选人
    汇成一张卡片推送给 HR（提醒复核并办理 offer 发放 / 入职）。逐候选人幂等
    （``recruitment_summaries.notified_at``），重复点击不会重复刷屏。
    """
    jobs = (
        [db.get(JobPosting, _parse_uuid(job_id, "job_id"))]
        if job_id
        else db.query(JobPosting).all()
    )
    if job_id and jobs[0] is None:
        raise BizError(404, "岗位不存在", http_status=404)

    now = datetime.now(timezone.utc)
    saved = 0
    notify_results: list[dict] = []

    for job in jobs:
        if job is None:
            continue
        # 本次待提醒的候选人（新达成完成 + 尚未提醒过）
        pending_notify: list[RecruitmentSummary] = []

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
            }
            if existing is None:
                existing = RecruitmentSummary(
                    job_id=job.id,
                    candidate_id=uuid.UUID(r["candidate_id"]),
                )
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
            existing.detail = detail
            existing.snapshot_at = now
            saved += 1

            if r["is_complete"] and existing.notified_at is None:
                pending_notify.append(existing)

        db.commit()

        # 汇总完成提醒 HR（尽力而为：失败只记日志，不影响落库结果）
        if not notify:
            notify_results.append(
                {"job_title": job.title, "status": "skipped", "reason": "调用方指定不推送（notify=false）"}
            )
        elif pending_notify:
            items = [_notify_item(row) for row in pending_notify]
            try:
                res = notify_summary_review(
                    job_title=job.title,
                    items=items,
                    snapshot_at=now.strftime("%Y-%m-%d %H:%M"),
                )
                logger.info("汇总复核推送结果（%s）：%s", job.title, res)
            except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断落库
                logger.exception("汇总复核推送异常，已忽略")
                res = {"status": "failed", "reason": "内部异常"}

            if res.get("status") == "sent":
                for row in pending_notify:
                    row.notified_at = now
                db.commit()

            notify_results.append({"job_title": job.title, **res})
        elif notify:
            notify_results.append(
                {"job_title": job.title, "status": "skipped", "reason": "无新完成待提醒的候选人"}
            )

    return ok({"saved": saved, "jobs": len(jobs), "notify": notify_results})


def _notify_item(row: RecruitmentSummary) -> dict:
    """汇总行 -> 推送卡片用的精简字段。"""
    return {
        "candidate_name": row.candidate_name,
        "final_score": row.final_score,
        "final_verdict": row.final_verdict,
        "screening_score": row.screening_score,
        "ai_interview_score": row.ai_interview_score,
        "offline_score": row.offline_score,
    }


@router.post("/summary/notify")
def notify_summary(
    job_id: str | None = Query(default=None, description="岗位 id；不传则全部岗位"),
    db: Session = Depends(get_db),
):
    """手动把已完成的汇总结果推送给 HR（忽略幂等，用于补推）。"""
    query = db.query(RecruitmentSummary).filter(RecruitmentSummary.is_complete.is_(True))
    if job_id:
        query = query.filter(RecruitmentSummary.job_id == _parse_uuid(job_id, "job_id"))
    rows = query.all()
    if not rows:
        raise BizError(422, "没有已完成的汇总记录可推送", http_status=422)

    # 按岗位分组，每岗位一张卡片
    by_job: dict[uuid.UUID, list[RecruitmentSummary]] = {}
    for row in rows:
        by_job.setdefault(row.job_id, []).append(row)

    results: list[dict] = []
    for jid, job_rows in by_job.items():
        job = db.get(JobPosting, jid)
        res = notify_summary_review(
            job_title=job.title if job else "",
            items=[_notify_item(r) for r in job_rows],
            snapshot_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        if res.get("status") == "sent":
            for r in job_rows:
                r.notified_at = datetime.now(timezone.utc)
        results.append({"job_title": job.title if job else "", **res})

    db.commit()
    if all(r.get("status") == "failed" for r in results):
        raise BizError(502, "飞书推送失败", http_status=502)
    return ok({"jobs": len(results), "results": results})


@router.get("/summary/snapshots")
def list_summary_snapshots(
    job_id: str | None = Query(default=None, description="岗位 id"),
    db: Session = Depends(get_db),
):
    """读取已落库的汇总快照（按最终分降序）。"""
    query = db.query(RecruitmentSummary)
    if job_id:
        query = query.filter(RecruitmentSummary.job_id == _parse_uuid(job_id, "job_id"))
    rows = query.order_by(
        nulls_last(RecruitmentSummary.final_score.desc()),
        RecruitmentSummary.created_at.desc(),
    ).all()
    return ok([_summary_out(r) for r in rows])
