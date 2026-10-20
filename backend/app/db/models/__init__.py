"""ORM 模型汇总导出（供 Alembic autogenerate 与业务引用）。"""
from app.db.models.admin_session import AdminSession
from app.db.models.admin_user import AdminUser
from app.db.models.audit_log import AuditLog
from app.db.models.candidate import Candidate
from app.db.models.company import CompanyProfile
from app.db.models.evaluation import AnswerEvaluation
from app.db.models.feishu_minutes_auth import FeishuMinutesAuth
from app.db.models.job import JobPosting
from app.db.models.offline import OfflineInterview
from app.db.models.offer import Offer
from app.db.models.report import SessionReport
from app.db.models.resume import Resume
from app.db.models.screening import ResumeScreening
from app.db.models.session import InterviewSession
from app.db.models.summary import RecruitmentSummary
from app.db.models.template import InterviewTemplate
from app.db.models.turn import InterviewTurn

__all__ = [
    "JobPosting",
    "InterviewTemplate",
    "Candidate",
    "Resume",
    "InterviewSession",
    "AnswerEvaluation",
    "SessionReport",
    # 面试逐轮问答原话（候选人原话唯一事实源，报告页证据链）
    "InterviewTurn",
    # 公司文化档案（单例，一段公司介绍文字）
    "CompanyProfile",
    # 招聘全链路（本轮新增）
    "ResumeScreening",
    "OfflineInterview",
    "RecruitmentSummary",
    # 妙记个人授权（user_access_token，读私有妙记）
    "FeishuMinutesAuth",
    # Offer → 入职闭环（第⑤阶段）
    "Offer",
    # 后台账号体系（安全改造：登录鉴权）
    "AdminUser",
    "AdminSession",
    "AuditLog",
]
