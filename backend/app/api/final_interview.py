"""终试决策路由（老板操作台核心接口）。

- ``GET  /admin/recruitment-summaries/final-pending``：列出待终试决策的候选人；
- ``GET  /admin/recruitment-summaries/{id}/brief``：终试面试档案（各环节评分维度 +
  优点 / 风险 / 待确认问题），供老板拍板前速览与终试追问；
- ``POST /admin/recruitment-summaries/{id}/final-decision``：老板做终试决策
  （pass=通过 / fail=不通过），仅 ``pending_final`` 可决策。

两个端点均限定 ``admin`` 或 ``boss`` 角色；真正的访问控制由后端依赖保证，
前端菜单/守卫只是体验层。
"""
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
    serialize_for_boss,
)
from app.services.interview_brief import build_interview_brief

router = APIRouter(prefix="/admin", tags=["final-interview"])


class FinalDecisionIn(BaseModel):
    """老板终试决策入参：decision=pass（通过）/ fail（不通过），note 选填。"""

    decision: str
    note: str = ""


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
    )
    db.commit()
    db.refresh(row)
    return ok(serialize_for_boss(row))
