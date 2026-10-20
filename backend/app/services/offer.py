"""Offer → 入职闭环服务：状态机、流转校验、惰性过期、统计与话术。

设计要点（见 ``docs/设计方案-Offer入职闭环.md`` v2）：
- **状态机是本模块的唯一事实源**（``OFFER_STATUSES`` / ``ALLOWED_TRANSITIONS`` / ``STATUS_LABELS``），
  路由层与前端不得各自维护枚举；
- 服务层**只写库不提交**，由路由层统一 ``commit``（与既有事务口径一致）；
  唯一例外是惰性过期：内部先提交状态、再尽力推送卡片（推送失败不回滚）；
- 审批是核心路径：``draft → pending_approval → sent``，**审批通过时才生成 token 与 expires_at**；
- 过期判定为**惰性**：所有读入口先调用 :func:`expire_due_offers`，不引入定时任务；
- 飞书推送一律「尽力而为」：失败仅记日志，绝不阻塞主流程。
"""
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models.job import JobPosting
from app.db.models.offline import OfflineInterview
from app.db.models.offer import Offer
from app.db.models.summary import RecruitmentSummary
from app.exceptions import BizError

logger = logging.getLogger(__name__)

# ── 状态机（唯一事实源）──────────────────────────────────────
STATUS_DRAFT = "draft"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_REJECTED = "rejected"
STATUS_SENT = "sent"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"
STATUS_EXPIRED = "expired"
STATUS_WITHDRAWN = "withdrawn"
STATUS_ONBOARDED = "onboarded"
STATUS_NO_SHOW = "no_show"

OFFER_STATUSES: tuple[str, ...] = (
    STATUS_DRAFT,
    STATUS_PENDING_APPROVAL,
    STATUS_REJECTED,
    STATUS_SENT,
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
    STATUS_WITHDRAWN,
    STATUS_ONBOARDED,
    STATUS_NO_SHOW,
)

# 「进行中」：占用「同一汇总唯一」约束（与 db/models/offer.py 的部分唯一索引一致）
ACTIVE_STATUSES: frozenset[str] = frozenset(
    {STATUS_DRAFT, STATUS_PENDING_APPROVAL, STATUS_REJECTED, STATUS_SENT, STATUS_ACCEPTED}
)

# 终态：不可再流转（rejected 非终态，可修改后重新提交审批）
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_DECLINED, STATUS_EXPIRED, STATUS_WITHDRAWN, STATUS_ONBOARDED, STATUS_NO_SHOW}
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_PENDING_APPROVAL, STATUS_WITHDRAWN}),
    STATUS_PENDING_APPROVAL: frozenset({STATUS_SENT, STATUS_REJECTED, STATUS_WITHDRAWN}),
    STATUS_REJECTED: frozenset({STATUS_PENDING_APPROVAL, STATUS_WITHDRAWN}),
    STATUS_SENT: frozenset(
        {STATUS_ACCEPTED, STATUS_DECLINED, STATUS_EXPIRED, STATUS_WITHDRAWN}
    ),
    STATUS_ACCEPTED: frozenset({STATUS_ONBOARDED, STATUS_NO_SHOW}),
    STATUS_DECLINED: frozenset(),
    STATUS_EXPIRED: frozenset(),
    STATUS_WITHDRAWN: frozenset(),
    STATUS_ONBOARDED: frozenset(),
    STATUS_NO_SHOW: frozenset(),
}

STATUS_LABELS: dict[str, str] = {
    STATUS_DRAFT: "草稿",
    STATUS_PENDING_APPROVAL: "待审批",
    STATUS_REJECTED: "已驳回",
    STATUS_SENT: "已发出",
    STATUS_ACCEPTED: "已接受",
    STATUS_DECLINED: "已婉拒",
    STATUS_EXPIRED: "已过期",
    STATUS_WITHDRAWN: "已撤回",
    STATUS_ONBOARDED: "已入职",
    STATUS_NO_SHOW: "未报到",
}

# 入职办理的两种结果
ONBOARD_RESULTS: frozenset[str] = frozenset({STATUS_ONBOARDED, STATUS_NO_SHOW})

# 候选人确认页兜底公司名（与前端一致；如需复用公司文化档案可后续接入）
DEFAULT_COMPANY_NAME = "笨二娘隆江猪脚饭"


def _utcnow() -> datetime:
    """UTC 当前时间（与全项目时间口径一致）。"""
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    """时间序列化（None 透传）。"""
    return value.isoformat() if value is not None else None


def status_label(status: str) -> str:
    """状态中文名（未知状态原样返回）。"""
    return STATUS_LABELS.get(status, status)


def can_transition(current: str, target: str) -> bool:
    """判断状态是否允许流转。"""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def ensure_transition(current: str, target: str) -> None:
    """校验状态流转，非法时抛 409。"""
    if not can_transition(current, target):
        raise BizError(
            409,
            f"Offer 当前状态为「{status_label(current)}」，不允许变更为「{status_label(target)}」",
            http_status=409,
        )


# ── 时间与链接 ───────────────────────────────────────────────


def compute_expires_at(
    now: datetime | None = None, settings: Settings | None = None
) -> datetime:
    """计算候选人确认截止时间：**发出后的「次日本地 HH:00」**，以 UTC 返回。

    HR 结论为「候选人当天早上 8 点前回复」。为避免上午发出的 Offer 只剩数小时窗口，
    统一截止到「**次日** 本地 08:00」（``offer_confirm_hour`` / ``offer_confirm_tz_offset`` 可配），
    即候选人有「当天 → 次日 08:00」的完整决策窗口。
    """
    s = settings or get_settings()
    moment = now or _utcnow()
    hour = int(s.offer_confirm_hour) % 24
    tz = timezone(timedelta(hours=int(s.offer_confirm_tz_offset or 0)))
    local_date = moment.astimezone(tz).date()
    deadline_local = datetime.combine(local_date + timedelta(days=1), time(hour=hour), tzinfo=tz)
    return deadline_local.astimezone(timezone.utc)


def format_deadline(expires_at: datetime | None, settings: Settings | None = None) -> str:
    """把截止时间格式化为「M月D日 HH:MM」（本地时区）；None 返回「尽快」。"""
    if expires_at is None:
        return "尽快"
    s = settings or get_settings()
    tz = timezone(timedelta(hours=int(s.offer_confirm_tz_offset or 0)))
    local = expires_at.astimezone(tz)
    return f"{local.month}月{local.day}日 {local:%H:%M}"


def build_offer_link(token: str, base_url: str = "") -> str:
    """候选人确认链接：``{PUBLIC_BASE_URL}/o/{token}``。"""
    base = (base_url or get_settings().public_base_url_normalized).rstrip("/")
    return f"{base}/o/{token}"


def build_copy_text(
    offer: Offer,
    link: str,
    *,
    company_name: str = "",
    settings: Settings | None = None,
) -> str:
    """HR 直接复制发微信的 Offer 话术（审批通过后返回）。"""
    s = settings or get_settings()
    name = company_name or DEFAULT_COMPANY_NAME
    report_date = offer.report_date.isoformat() if offer.report_date else "待定"
    return "\n".join(
        [
            f"【{name}】{offer.candidate_name} 您好，恭喜您通过我们的面试！",
            f"岗位：{offer.job_title}",
            f"报到门店：{offer.store_name}",
            f"拟报到日期：{report_date}",
            f"试用期：{offer.probation_days} 天",
            f"备注：{offer.note or '无'}",
            f"请在 {format_deadline(offer.expires_at, s)} 前点击链接确认是否接受 Offer：",
            link,
        ]
    )


def _notify(event: str, offer: Offer, **extra: Any) -> None:
    """尽力推送 Offer 状态卡片（延迟导入，失败只记日志，绝不阻塞业务）。"""
    settings = get_settings()
    if not settings.feishu_offer_notify_enabled:
        return
    try:
        from app.services.offer_notify import notify_offer_event

        notify_offer_event(event, offer, **extra)
    except Exception as exc:  # noqa: BLE001 - 外部依赖异常不得影响业务主流程
        logger.warning("Offer 通知失败 event=%s offer_id=%s: %s", event, offer.id, exc)


# ── 查询 ─────────────────────────────────────────────────────


def get_offer(db: Session, offer_id: uuid.UUID) -> Offer:
    """按 id 取 Offer，不存在抛 404。"""
    row = db.get(Offer, offer_id)
    if row is None:
        raise BizError(404, "Offer 不存在", http_status=404)
    return row


def get_offer_by_token(db: Session, token: str) -> Offer:
    """按 token 取 Offer（候选人端），不存在抛 404。"""
    row = db.query(Offer).filter(Offer.token == token).first()
    if row is None:
        raise BizError(404, "链接无效或已失效", http_status=404)
    return row


def list_offers(
    db: Session,
    *,
    status: str | None = None,
    job_id: uuid.UUID | None = None,
    store: str | None = None,
    keyword: str | None = None,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 200,
) -> list[Offer]:
    """后台列表查询（按创建时间倒序）。"""
    query = db.query(Offer)
    if status:
        query = query.filter(Offer.status == status)
    if job_id is not None:
        query = query.filter(Offer.job_id == job_id)
    if store:
        query = query.filter(Offer.store_name.ilike(f"%{store}%"))
    if keyword:
        query = query.filter(Offer.candidate_name.ilike(f"%{keyword}%"))
    if source:
        query = query.filter(Offer.source == source)
    if date_from is not None:
        query = query.filter(
            Offer.sent_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc)
        )
    if date_to is not None:
        query = query.filter(
            Offer.sent_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        )
    return query.order_by(Offer.created_at.desc()).limit(max(1, limit)).all()


def resolve_source(db: Session, candidate_id: uuid.UUID, job_id: uuid.UUID) -> str:
    """推导 Offer 来源快照：找到该（候选人, 岗位）最近的线下复试记录取 source，缺省 online。"""
    row = (
        db.query(OfflineInterview)
        .filter(
            OfflineInterview.candidate_id == candidate_id,
            OfflineInterview.job_id == job_id,
        )
        .order_by(OfflineInterview.created_at.desc())
        .first()
    )
    return (row.source or "online") if row is not None else "online"


# ── 业务动作（服务层只写库，由路由层统一 commit）──────────────


def create_offer(
    db: Session,
    *,
    summary_id: uuid.UUID,
    store_name: str,
    report_date: date,
    job_title: str | None = None,
    probation_days: int = 30,
    note: str = "",
    # ── 录用通知书字段（候选人确认页 /o/{token} 展示）──
    work_location: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    probation_salary: str = "",
    regular_salary: str = "",
    report_time: str = "",
    settings: Settings | None = None,
) -> Offer:
    """从招聘汇总起草一条 Offer（draft）。

    校验顺序：汇总存在 → 岗位在招 → 最终分达门槛 → 同一汇总无进行中 Offer。
    """
    s = settings or get_settings()

    summary = db.get(RecruitmentSummary, summary_id)
    if summary is None:
        raise BizError(404, "招聘汇总记录不存在", http_status=404)

    job = db.get(JobPosting, summary.job_id)
    if job is not None and not job.is_active:
        raise BizError(422, f"岗位「{job.title}」已停招，无法发起 Offer", http_status=422)

    if not summary.is_complete:
        raise BizError(422, "该候选人尚未走完全链路，无法发起 Offer", http_status=422)

    # 终试闸门：老板尚未拍板（或已否掉）的候选人，HR 不得发起 Offer
    from app.services.final_interview import (
        FINAL_FAILED,
        FINAL_PASSED,
        FINAL_PENDING,
        final_label,
    )

    if summary.final_interview_status == FINAL_PENDING:
        raise BizError(
            422,
            "该候选人终试尚未通过（待老板拍板），暂不能发起 Offer",
            http_status=422,
        )
    if summary.final_interview_status == FINAL_FAILED:
        raise BizError(
            422,
            f"该候选人终试未通过（{final_label(FINAL_FAILED)}），不能发起 Offer",
            http_status=422,
        )
    # FINAL_NONE（历史/未进入终试流程）与 FINAL_PASSED 均放行

    final_score = summary.final_score
    if final_score is None or float(final_score) < float(s.offer_min_final_score):
        raise BizError(
            422,
            f"最终评分 {final_score if final_score is not None else '—'} 低于门槛 "
            f"{s.offer_min_final_score}，不允许发起 Offer",
            http_status=422,
        )

    existing = (
        db.query(Offer)
        .filter(Offer.summary_id == summary_id, Offer.status.in_(tuple(ACTIVE_STATUSES)))
        .first()
    )
    if existing is not None:
        raise BizError(
            409,
            f"该候选人已有进行中的 Offer（当前状态：{status_label(existing.status)}）",
            http_status=409,
        )

    row = Offer(
        summary_id=summary_id,
        candidate_id=summary.candidate_id,
        job_id=summary.job_id,
        candidate_name=summary.candidate_name or "",
        job_title=(job_title or (job.title if job is not None else "") or ""),
        store_name=store_name or "",
        source=resolve_source(db, summary.candidate_id, summary.job_id),
        report_date=report_date,
        probation_days=int(probation_days or 30),
        note=note or "",
        # 录用通知书字段
        work_location=work_location or "",
        contact_name=contact_name or "",
        contact_phone=contact_phone or "",
        probation_salary=probation_salary or "",
        regular_salary=regular_salary or "",
        report_time=report_time or "",
        status=STATUS_DRAFT,
    )
    db.add(row)
    return row


def update_offer(
    db: Session,
    offer: Offer,
    *,
    job_title: str | None = None,
    store_name: str | None = None,
    report_date: date | None = None,
    probation_days: int | None = None,
    note: str | None = None,
    # ── 录用通知书字段 ──
    work_location: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    probation_salary: str | None = None,
    regular_salary: str | None = None,
    report_time: str | None = None,
) -> Offer:
    """修改 Offer 内容（仅 draft / rejected 可改）。"""
    if offer.status not in (STATUS_DRAFT, STATUS_REJECTED):
        raise BizError(
            409,
            f"Offer 当前状态为「{status_label(offer.status)}」，不允许修改",
            http_status=409,
        )
    if job_title is not None:
        offer.job_title = job_title
    if store_name is not None:
        offer.store_name = store_name
    if report_date is not None:
        offer.report_date = report_date
    if probation_days is not None:
        offer.probation_days = int(probation_days)
    if note is not None:
        offer.note = note
    # 录用通知书字段
    if work_location is not None:
        offer.work_location = work_location
    if contact_name is not None:
        offer.contact_name = contact_name
    if contact_phone is not None:
        offer.contact_phone = contact_phone
    if probation_salary is not None:
        offer.probation_salary = probation_salary
    if regular_salary is not None:
        offer.regular_salary = regular_salary
    if report_time is not None:
        offer.report_time = report_time
    return offer


def submit_approval(
    db: Session, offer: Offer, *, settings: Settings | None = None
) -> Offer:
    """提交审批：draft/rejected → pending_approval，返回待推送的审批卡片信息。

    审批人取配置 ``OFFER_APPROVER_OPEN_IDS``；未配置时直接拒绝（审批流必须有明确收件人）。
    """
    s = settings or get_settings()
    ensure_transition(offer.status, STATUS_PENDING_APPROVAL)

    approvers = s.offer_approver_list
    if not approvers:
        raise BizError(
            422,
            "未配置 Offer 审批人（OFFER_APPROVER_OPEN_IDS），无法提交审批",
            http_status=422,
        )

    offer.status = STATUS_PENDING_APPROVAL
    offer.approver_open_id = ",".join(approvers)
    offer.approval_submitted_at = _utcnow()
    # 重新提交时清空上一次的驳回信息
    offer.rejected_at = None
    offer.approval_note = ""
    return offer


def approve_offer(
    db: Session, offer: Offer, *, note: str = "", settings: Settings | None = None
) -> Offer:
    """审批通过：pending_approval → sent，并生成 token / 截止时间。

    该函数被**飞书卡片回调**与**后台审批入口**共用，两者行为必须一致。
    """
    s = settings or get_settings()
    ensure_transition(offer.status, STATUS_SENT)

    now = _utcnow()
    offer.status = STATUS_SENT
    offer.token = uuid.uuid4().hex
    offer.approved_at = now
    offer.sent_at = now
    offer.expires_at = compute_expires_at(now, s)
    if note:
        offer.approval_note = note
    return offer


def reject_offer(db: Session, offer: Offer, *, note: str) -> Offer:
    """审批驳回：pending_approval → rejected（驳回意见必填）。"""
    ensure_transition(offer.status, STATUS_REJECTED)
    if not (note or "").strip():
        raise BizError(422, "驳回时必须填写审批意见", http_status=422)
    offer.status = STATUS_REJECTED
    offer.rejected_at = _utcnow()
    offer.approval_note = note.strip()
    return offer


def withdraw_offer(db: Session, offer: Offer, *, reason: str = "") -> Offer:
    """撤回 Offer（draft / pending_approval / rejected / sent 可撤回）。"""
    ensure_transition(offer.status, STATUS_WITHDRAWN)
    offer.status = STATUS_WITHDRAWN
    offer.withdrawn_at = _utcnow()
    offer.withdraw_reason = reason or ""
    return offer


def onboard_offer(
    db: Session,
    offer: Offer,
    *,
    result: str,
    actual_report_date: date | None = None,
    onboard_store: str = "",
    note: str = "",
) -> Offer:
    """办理入职：accepted → onboarded（已报到）/ no_show（未报到，说明必填）。"""
    if result not in ONBOARD_RESULTS:
        raise BizError(422, "入职结果必须为 onboarded 或 no_show", http_status=422)
    ensure_transition(offer.status, result)

    if result == STATUS_NO_SHOW and not (note or "").strip():
        raise BizError(422, "标记「未报到」时必须填写说明", http_status=422)

    offer.status = result
    offer.onboarded_at = _utcnow()
    offer.actual_report_date = actual_report_date or offer.report_date
    offer.onboard_store = onboard_store or offer.store_name
    offer.no_show_note = (note or "").strip() if result == STATUS_NO_SHOW else ""
    return offer


def accept_offer(db: Session, offer: Offer, *, candidate_email: str = "") -> Offer:
    """候选人接受（sent → accepted）；已接受时幂等返回。

    candidate_email：候选人确认时填写的邮箱（必填），用于接受后发送《入职指引》邮件。
    无论是否首次接受都允许落库（幂等重复调用时也可补填 / 修正邮箱）。
    """
    email = (candidate_email or "").strip()
    if email:
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
            raise BizError(422, "邮箱格式不正确", http_status=422)
        offer.candidate_email = email

    if offer.status == STATUS_ACCEPTED:
        return offer
    ensure_transition(offer.status, STATUS_ACCEPTED)
    offer.status = STATUS_ACCEPTED
    offer.decided_at = _utcnow()
    return offer


def decline_offer(db: Session, offer: Offer, *, reason_text: str = "") -> Offer:
    """候选人婉拒（sent → declined）；理由为自由文本（选填）。"""
    ensure_transition(offer.status, STATUS_DECLINED)
    offer.status = STATUS_DECLINED
    offer.decided_at = _utcnow()
    offer.decline_reason_text = (reason_text or "").strip()
    return offer


# ── 惰性过期 ─────────────────────────────────────────────────


def expire_due_offers(db: Session, *, notify: bool = True) -> list[Offer]:
    """惰性过期：把已发出且超过确认截止时间的 Offer 流转为 expired。

    在所有读入口调用（后台列表 / 详情 / 统计、候选人页 GET）。
    逐条流转后**立即提交**（先落库再推送，推送失败不影响已流转状态）。
    """
    now = _utcnow()
    rows = (
        db.query(Offer)
        .filter(
            Offer.status == STATUS_SENT,
            Offer.expires_at.isnot(None),
            Offer.expires_at < now,
        )
        .all()
    )
    if not rows:
        return []
    for row in rows:
        row.status = STATUS_EXPIRED
        row.expired_at = now
    db.commit()
    if notify:
        for row in rows:
            _notify("expired", row)
    return rows


# ── 统计 ─────────────────────────────────────────────────────


def _month_range(month: str | None) -> tuple[datetime, datetime] | None:
    """把 'YYYY-MM' 解析为 [月初, 次月初) 的 UTC 区间；空返回 None，非法抛 422。"""
    if not month:
        return None
    try:
        start = datetime.strptime(month.strip(), "%Y-%m").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise BizError(422, "month 参数格式应为 YYYY-MM", http_status=422)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt


def _rate(numerator: int, denominator: int) -> float | None:
    """比率（保留两位小数）；分母为 0 返回 None（前端显示「—」）。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def offer_stats(
    db: Session, *, month: str | None = None, job_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Offer 转化统计 + 概览漏斗（可选按月 / 按岗位过滤）。

    口径（共享知识 #9）：
    - 接受率 = accepted / (accepted + declined + expired)，其中 accepted 含其后转为 onboarded/no_show 的记录；
    - 入职率 = onboarded / accepted；
    - 未报到数单独计数。
    """
    span = _month_range(month)

    def _base_query():
        q = db.query(Offer)
        if job_id is not None:
            q = q.filter(Offer.job_id == job_id)
        if span is not None:
            start, end = span
            q = q.filter(Offer.sent_at >= start, Offer.sent_at < end)
        return q

    counts: dict[str, int] = {s: 0 for s in OFFER_STATUSES}
    for status, num in _base_query().with_entities(Offer.status, func.count(Offer.id)).group_by(Offer.status):
        if status in counts:
            counts[status] = int(num or 0)

    accepted_total = counts[STATUS_ACCEPTED] + counts[STATUS_ONBOARDED] + counts[STATUS_NO_SHOW]
    decided_base = accepted_total + counts[STATUS_DECLINED] + counts[STATUS_EXPIRED]

    # 概览漏斗：汇总完成 → 发 Offer → 接受 → 入职
    summary_q = db.query(func.count(RecruitmentSummary.id)).filter(
        RecruitmentSummary.is_complete.is_(True)
    )
    if job_id is not None:
        summary_q = summary_q.filter(RecruitmentSummary.job_id == job_id)
    if span is not None:
        start, end = span
        summary_q = summary_q.filter(
            RecruitmentSummary.snapshot_at >= start, RecruitmentSummary.snapshot_at < end
        )
    summaries_complete = int(summary_q.scalar() or 0)

    offers_sent = sum(counts[s] for s in (STATUS_SENT, STATUS_ACCEPTED, STATUS_DECLINED,
                                          STATUS_EXPIRED, STATUS_ONBOARDED, STATUS_NO_SHOW))
    if span is None:
        # 未按月过滤时，draft / pending_approval / rejected / withdrawn 尚未发出，不计入「已发出」
        pass

    return {
        **{s: counts[s] for s in OFFER_STATUSES},
        "total": sum(counts.values()),
        "accept_rate": _rate(accepted_total, decided_base),
        "onboarding_rate": _rate(counts[STATUS_ONBOARDED], accepted_total),
        "no_show_rate": _rate(counts[STATUS_NO_SHOW], accepted_total),
        "funnel": {
            "summaries_complete": summaries_complete,
            "offers_sent": offers_sent,
            "offers_accepted": accepted_total,
            "onboarded": counts[STATUS_ONBOARDED],
            "pending_approval": counts[STATUS_PENDING_APPROVAL],
        },
    }


# ── 序列化 ───────────────────────────────────────────────────


def serialize_offer(
    offer: Offer, *, base_url: str = "", with_link: bool = False
) -> dict[str, Any]:
    """Offer → 接口响应结构（含状态时间线字段）。"""
    data: dict[str, Any] = {
        "id": str(offer.id),
        "summary_id": str(offer.summary_id),
        "candidate_id": str(offer.candidate_id),
        "candidate_name": offer.candidate_name,
        "job_id": str(offer.job_id),
        "job_title": offer.job_title,
        "store_name": offer.store_name,
        "source": offer.source,
        "report_date": offer.report_date.isoformat() if offer.report_date else None,
        "probation_days": offer.probation_days,
        "note": offer.note,
        # 录用通知书字段（HR 发起 Offer 时填写）
        "work_location": offer.work_location,
        "contact_name": offer.contact_name,
        "contact_phone": offer.contact_phone,
        "probation_salary": offer.probation_salary,
        "regular_salary": offer.regular_salary,
        "report_time": offer.report_time,
        "status": offer.status,
        "status_label": status_label(offer.status),
        "approver_open_id": offer.approver_open_id,
        "approval_submitted_at": _iso(offer.approval_submitted_at),
        "approved_at": _iso(offer.approved_at),
        "rejected_at": _iso(offer.rejected_at),
        "approval_note": offer.approval_note,
        "expires_at": _iso(offer.expires_at),
        "sent_at": _iso(offer.sent_at),
        "decided_at": _iso(offer.decided_at),
        "expired_at": _iso(offer.expired_at),
        "withdrawn_at": _iso(offer.withdrawn_at),
        "withdraw_reason": offer.withdraw_reason,
        "decline_reason_text": offer.decline_reason_text,
        "onboarded_at": _iso(offer.onboarded_at),
        "actual_report_date": (
            offer.actual_report_date.isoformat() if offer.actual_report_date else None
        ),
        "onboard_store": offer.onboard_store,
        "no_show_note": offer.no_show_note,
        "created_at": _iso(offer.created_at),
        "updated_at": _iso(offer.updated_at),
    }
    if with_link and offer.token and offer.status in (STATUS_SENT, STATUS_ACCEPTED):
        data["offer_link"] = build_offer_link(offer.token, base_url)
    return data
