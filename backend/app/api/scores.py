"""评分查询路由：供外部系统按候选人查询面试评分结果。

独立于 /admin（后台管理）与 /interview（候选人答题），语义为「评分结果对外读取」，
返回候选人信息 + 综合分 + 等级建议 + 各维度得分，便于其他系统直接消费。

鉴权：该接口面向**外部系统**（无浏览器会话），因此不复用后台登录态，
而是要求调用方在请求头带 ``X-API-Key``（密钥配置项 ``SCORES_API_KEY``）。
未配置密钥时接口一律拒绝（503），遵循"安全默认"。
"""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok, require_api_key
from app.db.models import Candidate, InterviewSession, SessionReport
from app.exceptions import BizError

router = APIRouter(
    prefix="/api/scores",
    tags=["scores"],
    dependencies=[Depends(require_api_key)],
)


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """解析 UUID 字符串，失败抛 422。"""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


@router.get("/candidate/{candidate_id}")
def get_score_by_candidate(candidate_id: str, db: Session = Depends(get_db)):
    """按候选人 id 查询其面试评分结果。

    返回候选人信息（姓名/邮箱/电话）+ 会话状态 + 综合分 + 等级 + 各维度得分。
    若该候选人有多场面试会话，取最近一场；若无任何会话或尚未出报告，返回对应提示。
    """
    cid = _parse_uuid(candidate_id, "candidate_id")

    candidate = db.get(Candidate, cid)
    if candidate is None:
        raise BizError(404, "候选人不存在", http_status=404)

    # 取该候选人最近的一场面试会话
    session = (
        db.query(InterviewSession)
        .filter(InterviewSession.candidate_id == cid)
        .order_by(InterviewSession.created_at.desc())
        .first()
    )

    if session is None:
        return ok(
            {
                "candidate": {
                    "id": str(candidate.id),
                    "name": candidate.name,
                    "email": candidate.email,
                    "phone": candidate.phone,
                },
                "session_id": None,
                "status": None,
                "score": None,
            }
        )

    report = (
        db.query(SessionReport)
        .filter(SessionReport.session_id == session.id)
        .first()
    )

    # 报告维度得分：[{"key","name","weight","score","weighted_score"}]
    dim_scores = report.dim_scores if report else []

    return ok(
        {
            "candidate": {
                "id": str(candidate.id),
                "name": candidate.name,
                "email": candidate.email,
                "phone": candidate.phone,
            },
            "session_id": str(session.id),
            "status": session.status,
            "score": {
                "total_score": report.total_score if report else None,
                "verdict": report.verdict if report else None,
                "summary": report.summary if report else None,
                "dim_scores": dim_scores,
            },
        }
    )
