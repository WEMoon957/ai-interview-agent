"""终试决策路由（老板操作台核心接口）。

- ``GET  /admin/recruitment-summaries/final-pending``：列出待终试决策的候选人；
- ``GET  /admin/recruitment-summaries/{id}/brief``：终试面试档案（各环节评分维度 +
  优点 / 风险 / 待确认问题），供老板拍板前速览与终试追问；
- ``POST /admin/recruitment-summaries/{id}/final-decision``：老板做终试决策
  （pass=通过 / fail=不通过），仅 ``pending_final`` 可决策。

两个端点均限定 ``admin`` 或 ``boss`` 角色；真正的访问控制由后端依赖保证，
前端菜单/守卫只是体验层。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok, require_user
from app.db.models.admin_user import ADMIN_ROLE, BOSS_ROLE
from app.db.models.summary import RecruitmentSummary
from app.exceptions import BizError
from app.services.final_interview import (
    decide_final_interview,
    list_pending_final,
    schedule_final_interview,
    serialize_for_boss,
)
from app.services.interview_brief import build_interview_brief

router = APIRouter(prefix="/admin", tags=["final-interview"])


class FinalDecisionIn(BaseModel):
    """老板终试决策入参：decision=pass（通过）/ fail（不通过），note 选填。"""

    decision: str
    note: str = ""
    # 当场面试当场拍板时顺带记录终面时间（ISO8601，可空）
    scheduled_at: str | None = None


class FinalScheduleIn(BaseModel):
    """老板确认终面时间入参：scheduled_at 为 ISO8601；传 null 表示清空（改回待约）。"""

    scheduled_at: str | None = None
    note: str = ""


def _parse_dt(value: str | None) -> datetime | None:
    """解析 ISO8601（容忍 Z 结尾与仅日期）；空值返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise BizError(
            422,
            "时间格式无效，请用 ISO8601（如 2026-10-25T14:00:00+08:00）",
            http_status=422,
        )


def _parse_uuid(value: str, label: str) -> object:
    import uuid

    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


@router.get("/recruitment-summaries/final-pending")
def list_final_pending(
    job_id: str | None = Query(default=None, description="岗位 id"),
    keyword: str | None = Query(default=None, description="候选人姓名（模糊）"),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """待终试决策列表（老板操作台「待终试」队列）。"""
    rows = list_pending_final(
        db,
        job_id=_parse_uuid(job_id, "job_id") if job_id else None,
        keyword=keyword,
        limit=limit,
    )
    return ok([serialize_for_boss(r) for r in rows])


@router.get("/recruitment-summaries/{summary_id}/brief")
def get_recruitment_brief(
    summary_id: str,
    db: Session = Depends(get_db),
):
    """终试面试档案：此前各环节的评分维度 + 优点 / 风险 / 待确认问题。

    老板在操作台点「面试档案」时按需拉取（不随列表接口返回，保持队列轻量）。
    """
    row = db.get(RecruitmentSummary, _parse_uuid(summary_id, "summary_id"))
    if row is None:
        raise BizError(404, "汇总记录不存在", http_status=404)
    return ok(build_interview_brief(db, row))


@router.post("/recruitment-summaries/{summary_id}/final-schedule")
def schedule_final(
    summary_id: str,
    payload: FinalScheduleIn,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    """老板确认 / 修改终面时间（仅「待终试」可安排；传 null 清空改回待约）。

    与 final-decision 分开：先约时间、面完再拍板是常态；也可在 final-decision 里
    一并传时间（当场面试当场判定）。
    """
    row = db.get(RecruitmentSummary, _parse_uuid(summary_id, "summary_id"))
    if row is None:
        raise BizError(404, "汇总记录不存在", http_status=404)

    schedule_final_interview(
        db,
        row,
        scheduled_at=_parse_dt(payload.scheduled_at),
        operator_username=user.username,
    )
    if payload.note:
        row.final_interview_note = payload.note.strip()
    db.commit()
    db.refresh(row)
    return ok(serialize_for_boss(row))


@router.post("/recruitment-summaries/{summary_id}/final-decision")
def decide_final(
    summary_id: str,
    payload: FinalDecisionIn,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    """老板做终试决策：pass=通过终试 / fail=不通过（不通过可填备注）。"""
    row = db.get(RecruitmentSummary, _parse_uuid(summary_id, "summary_id"))
    if row is None:
        raise BizError(404, "汇总记录不存在", http_status=404)

    decision = payload.decision or ""
    note = payload.note or ""
    decide_final_interview(
        db,
        row,
        decision=decision,
        note=note,
        operator_username=user.username,
        scheduled_at=_parse_dt(payload.scheduled_at),
    )
    db.commit()
    db.refresh(row)
    return ok(serialize_for_boss(row))
