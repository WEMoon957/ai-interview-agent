"""ORM 模型汇总导出（供 Alembic autogenerate 与业务引用）。"""
from app.db.models.candidate import Candidate
from app.db.models.company import CompanyProfile
from app.db.models.evaluation import AnswerEvaluation
from app.db.models.job import JobPosting
from app.db.models.offline import OfflineInterview
from app.db.models.report import SessionReport
from app.db.models.resume import Resume
from app.db.models.screening import ResumeScreening
from app.db.models.session import InterviewSession
from app.db.models.summary import RecruitmentSummary
from app.db.models.template import InterviewTemplate

__all__ = [
    "JobPosting",
    "InterviewTemplate",
    "Candidate",
    "Resume",
    "InterviewSession",
    "AnswerEvaluation",
    "SessionReport",
    # 公司文化档案（单例，一段公司介绍文字）
    "CompanyProfile",
    # 招聘全链路（本轮新增）
    "ResumeScreening",
    "OfflineInterview",
    "RecruitmentSummary",
]
