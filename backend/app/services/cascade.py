"""级联删除工具：保证删除候选人 / 岗位 / 会话时不留下悬挂外键。

背景：招聘全链路新增了 ``resume_screenings`` / ``offline_interviews`` /
``recruitment_summaries`` 三张表，它们分别外键引用 ``candidates`` / ``interview_sessions``
/ ``job_postings``。若沿用旧的「只删会话与候选人」逻辑，会因外键约束直接报错。
本模块把删除顺序收敛到一处，避免各路由各写一遍。

删除顺序（关键）：先删引用 session 的表（offline_interviews / resume_screenings），
再删 evaluations / reports，最后删 sessions —— 否则会撞 FK。
"""
import uuid

from sqlalchemy.orm import Session

from app.db.models import (
    AnswerEvaluation,
    Candidate,
    InterviewSession,
    InterviewTemplate,
    JobPosting,
    OfflineInterview,
    RecruitmentSummary,
    Resume,
    ResumeScreening,
    SessionReport,
)


def purge_candidate(db: Session, candidate_id: uuid.UUID) -> None:
    """删除候选人及其全部关联数据（不 commit，由调用方统一提交）。

    注意：会连同该候选人在**所有岗位**下的会话、初筛、线下复试、汇总记录一并删除
    （与既有「删候选人即清空其数据」的口径一致）。
    """
    # 1) 先删引用 session 的两张表，避免 FK 违约
    db.query(OfflineInterview).filter(
        OfflineInterview.candidate_id == candidate_id
    ).delete(synchronize_session=False)
    db.query(ResumeScreening).filter(
        ResumeScreening.candidate_id == candidate_id
    ).delete(synchronize_session=False)

    # 2) 该候选人的会话及其评分/报告
    session_ids = [
        row[0]
        for row in db.query(InterviewSession.id).filter(
            InterviewSession.candidate_id == candidate_id
        )
    ]
    if session_ids:
        db.query(AnswerEvaluation).filter(
            AnswerEvaluation.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        db.query(SessionReport).filter(
            SessionReport.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        db.query(InterviewSession).filter(
            InterviewSession.id.in_(session_ids)
        ).delete(synchronize_session=False)

    # 3) 汇总、简历、候选人本体
    db.query(RecruitmentSummary).filter(
        RecruitmentSummary.candidate_id == candidate_id
    ).delete(synchronize_session=False)
    db.query(Resume).filter(Resume.candidate_id == candidate_id).delete(synchronize_session=False)
    db.query(Candidate).filter(Candidate.id == candidate_id).delete(synchronize_session=False)


def candidate_is_orphan(db: Session, candidate_id: uuid.UUID) -> bool:
    """候选人是否已无任何业务引用（初筛 / 会话 / 线下复试均无）。"""
    if db.query(ResumeScreening).filter(ResumeScreening.candidate_id == candidate_id).count():
        return False
    if db.query(InterviewSession).filter(InterviewSession.candidate_id == candidate_id).count():
        return False
    if db.query(OfflineInterview).filter(OfflineInterview.candidate_id == candidate_id).count():
        return False
    return True


def purge_job(db: Session, job_id: uuid.UUID) -> None:
    """删除岗位及其下全部关联数据（模板 / 会话 / 候选人 / 初筛 / 线下 / 汇总）。"""
    candidate_ids: set[uuid.UUID] = set()

    for (cid,) in db.query(ResumeScreening.candidate_id).filter(ResumeScreening.job_id == job_id):
        candidate_ids.add(cid)
    for (cid,) in db.query(OfflineInterview.candidate_id).filter(OfflineInterview.job_id == job_id):
        candidate_ids.add(cid)
    for (cid,) in (
        db.query(InterviewSession.candidate_id)
        .join(InterviewTemplate, InterviewSession.template_id == InterviewTemplate.id)
        .filter(InterviewTemplate.job_id == job_id)
    ):
        candidate_ids.add(cid)

    for cid in candidate_ids:
        purge_candidate(db, cid)

    # 兜底：清掉本岗位遗留的汇总与模板，再删岗位
    db.query(RecruitmentSummary).filter(RecruitmentSummary.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(InterviewTemplate).filter(InterviewTemplate.job_id == job_id).delete(
        synchronize_session=False
    )
    db.query(JobPosting).filter(JobPosting.id == job_id).delete(synchronize_session=False)


def purge_template(db: Session, template_id: uuid.UUID) -> None:
    """删除模板及其下会话与相关候选人数据。"""
    candidate_ids = {
        row[0]
        for row in db.query(InterviewSession.candidate_id).filter(
            InterviewSession.template_id == template_id
        )
    }
    for cid in candidate_ids:
        purge_candidate(db, cid)
    db.query(InterviewTemplate).filter(InterviewTemplate.id == template_id).delete(
        synchronize_session=False
    )
