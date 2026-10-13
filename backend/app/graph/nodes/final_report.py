"""final_report 节点：汇总加权报告，落库 session_reports，更新会话状态。

面试收尾时若综合分达标，会顺带把结果推送给 HR（飞书）——该推送为「尽力而为」：
任何异常都被捕获并记录，绝不影响面试主流程的收尾与返回值。
"""
import logging
import uuid

from app.db.models import InterviewSession, SessionReport
from app.db.session import SessionLocal
from app.graph.state import InterviewState
from app.models.schemas import Report
from app.services.feishu_notify import notify_report_qualified
from app.services.scoring import aggregate_report

logger = logging.getLogger(__name__)


def _candidate_name(session: InterviewSession | None) -> str:
    """安全读取候选人姓名（关系缺失时返回空串）。"""
    if session is None:
        return ""
    candidate = getattr(session, "candidate", None)
    if candidate is None:
        return ""
    return getattr(candidate, "name", "") or ""


def _persist_report(state: InterviewState, report: Report) -> None:
    """落库 session_reports（一会话一条），更新 session.status=finished，并触发 HR 推送。"""
    session_id = state.get("session_id")
    if not session_id:
        return

    sid = uuid.UUID(session_id)
    dim_scores_json = [d.model_dump(mode="json") for d in report.dim_scores]

    db = SessionLocal()
    try:
        row = db.query(SessionReport).filter(SessionReport.session_id == sid).first()
        if row is not None:
            # 幂等：重跑时更新而非重复插入
            row.dim_scores = dim_scores_json
            row.total_score = report.total_score
            row.verdict = report.verdict
            row.summary = report.summary
        else:
            row = SessionReport(
                session_id=sid,
                dim_scores=dim_scores_json,
                total_score=report.total_score,
                verdict=report.verdict,
                summary=report.summary,
            )
            db.add(row)

        session = db.get(InterviewSession, sid)
        if session is not None:
            session.status = "finished"

        # 达标自动推送 HR（尽力而为：失败只记日志，不影响面试收尾）
        try:
            result = notify_report_qualified(
                row,
                session_id=session_id,
                candidate_name=_candidate_name(session),
            )
            logger.info("飞书 HR 通知处理结果：%s", result)
        except Exception:  # noqa: BLE001 - 外部依赖异常不得阻断主流程
            logger.exception("飞书 HR 通知异常，已忽略（不影响面试收尾）")

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
