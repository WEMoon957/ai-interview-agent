"""ORM 模型汇总导出（供 Alembic autogenerate 与业务引用）。"""
from app.db.models.candidate import Candidate
from app.db.models.evaluation import AnswerEvaluation
from app.db.models.job import JobPosting
from app.db.models.report import SessionReport
from app.db.models.resume import Resume
from app.db.models.session import InterviewSession
from app.db.models.template import InterviewTemplate

__all__ = [
    "JobPosting",
    "InterviewTemplate",
    "Candidate",
    "Resume",
    "InterviewSession",
    "AnswerEvaluation",
    "SessionReport",
]
