"""final_report 节点：汇总加权报告，落库 session_reports，更新会话状态。"""
import uuid

from app.db.models import InterviewSession, SessionReport
from app.db.session import SessionLocal
from app.graph.state import InterviewState
from app.models.schemas import Report
from app.services.scoring import aggregate_report


def _persist_report(state: InterviewState, report: Report) -> None:
    """落库 session_reports（一会话一条），并更新 session.status=finished。"""
    session_id = state.get("session_id")
    if not session_id:
        return

    sid = uuid.UUID(session_id)
    dim_scores_json = [d.model_dump(mode="json") for d in report.dim_scores]

    db = SessionLocal()
    try:
        existing = db.query(SessionReport).filter(SessionReport.session_id == sid).first()
        if existing is not None:
            # 幂等：重跑时更新而非重复插入
            existing.dim_scores = dim_scores_json
            existing.total_score = report.total_score
            existing.verdict = report.verdict
            existing.summary = report.summary
        else:
            db.add(
                SessionReport(
                    session_id=sid,
                    dim_scores=dim_scores_json,
                    total_score=report.total_score,
                    verdict=report.verdict,
                    summary=report.summary,
                )
            )

        session = db.get(InterviewSession, sid)
        if session is not None:
            session.status = "finished"

        db.commit()
    finally:
        db.close()


def final_report(state: InterviewState) -> dict:
    """汇总逐题评分为最终报告，写入 state["report"] 并落库。"""
    report = aggregate_report(
        state.get("evaluations") or [],
        state.get("rubric") or [],
    )

    _persist_report(state, report)

    return {
        "report": report.model_dump(mode="json"),
        "status": "finished",
    }
