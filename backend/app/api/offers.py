"""Offer → 入职路由：管理端（发起 / 审批 / 入职）与候选人端（token 自助确认）。

- **管理端 10 个端点**：列表 / 详情 / 统计 / 起草 / 修改 / 提交审批 / 审批通过 /
  审批驳回 / 撤回 / 办理入职；
- **候选人端 3 个端点**：查看 / 接受 / 婉拒（token 即凭证，无需鉴权）；
- **事务口径**：服务层只写库，本层统一 ``commit``；飞书推送在 ``commit`` 之后「尽力而为」，
  失败只记日志、不影响响应（与既有 summary 路由一致）。
"""
import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok
from app.api.schemas import (
    OfferApprove,
    OfferCreate,
    OfferDecline,
    OfferOnboard,
    OfferReject,
    OfferUpdate,
    OfferWithdraw,
)
from app.config import get_settings
from app.db.models.candidate import Candidate
from app.db.models.offer import Offer
from app.db.models.summary import RecruitmentSummary
from app.exceptions import BizError
from app.services import offer as svc
from app.services.offer_letter import build_letter
from app.services.offer_notify import (
    mail_offer_accepted,
    mail_offer_invite,
    notify_offer_event,
    offer_candidate_email,
)

logger = logging.getLogger(__name__)

# 管理端（/admin/offers）+ 候选人端（/offer/{token}）
router = APIRouter(prefix="/admin", tags=["offers"])
candidate_router = APIRouter(prefix="/offer", tags=["offer-candidate"])


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    """解析 UUID 字符串，失败抛 422。"""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, f"{label}格式无效", http_status=422)


def _parse_date(value: str, label: str = "日期") -> date:
    """解析 YYYY-MM-DD 日期，失败抛 422。"""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise BizError(422, f"{label}格式应为 YYYY-MM-DD", http_status=422)


def _candidate_out(offer: Offer) -> dict:
    """候选人端可见字段（不暴露审批人、内部分数等管理信息）。"""
    settings = get_settings()
    return {
        "status": offer.status,
        "status_label": svc.status_label(offer.status),
        "candidate_name": offer.candidate_name,
        "job_title": offer.job_title,
        "store_name": offer.store_name,
        "report_date": offer.report_date.isoformat() if offer.report_date else None,
        "probation_days": offer.probation_days,
        "note": offer.note,
        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
        "deadline_text": svc.format_deadline(offer.expires_at, settings),
        "company_name": svc.DEFAULT_COMPANY_NAME,
        "hr_contact_phone": settings.hr_contact_phone,
        "decline_reason_text": offer.decline_reason_text,
    }


# ══════════════════════════ 管理端 ══════════════════════════


@router.get("/offers")
def list_offers(
    status: str | None = Query(default=None, description="状态筛选"),
    job_id: str | None = Query(default=None, description="岗位 id"),
    store: str | None = Query(default=None, description="报到门店（模糊）"),
    keyword: str | None = Query(default=None, description="候选人姓名（模糊）"),
    source: str | None = Query(default=None, description="来源：online / walk_in"),
    date_from: str | None = Query(default=None, description="发出日期起（YYYY-MM-DD）"),
    date_to: str | None = Query(default=None, description="发出日期止（YYYY-MM-DD）"),
    limit: int = Query(default=200, ge=1, le=500, description="返回条数上限"),
    db: Session = Depends(get_db),
):
    """Offer 列表（读取前先执行惰性过期扫描）。"""
    svc.expire_due_offers(db)
    rows = svc.list_offers(
        db,
        status=status,
        job_id=_parse_uuid(job_id, "job_id") if job_id else None,
        store=store,
        keyword=keyword,
        source=source,
        date_from=_parse_date(date_from, "date_from") if date_from else None,
        date_to=_parse_date(date_to, "date_to") if date_to else None,
        limit=limit,
    )
    return ok([svc.serialize_offer(row, with_link=True) for row in rows])


@router.get("/offers/stats")
def offer_stats(
    month: str | None = Query(default=None, description="月份 YYYY-MM；不传为全量"),
    job_id: str | None = Query(default=None, description="岗位 id"),
    db: Session = Depends(get_db),
):
    """Offer 转化统计 + 概览漏斗（先执行惰性过期扫描）。

    注意：本路由必须定义在 ``/offers/{offer_id}`` **之前**，否则 "stats" 会被当作 id 解析。
    """
    svc.expire_due_offers(db)
    return ok(
        svc.offer_stats(
            db,
            month=month,
            job_id=_parse_uuid(job_id, "job_id") if job_id else None,
        )
    )


@router.get("/offers/{offer_id}")
def get_offer(offer_id: str, db: Session = Depends(get_db)):
    """Offer 详情（含状态时间线字段）。"""
    svc.expire_due_offers(db)
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    return ok(svc.serialize_offer(row, with_link=True))


@router.post("/offers")
def create_offer(payload: OfferCreate, db: Session = Depends(get_db)):
    """起草 Offer（draft）。

    错误：404 汇总不存在；422 岗位停招 / 分数低于门槛 / 尚未走完链路；409 已有进行中 Offer。
    """
    row = svc.create_offer(
        db,
        summary_id=_parse_uuid(payload.summary_id, "summary_id"),
        store_name=payload.store_name,
        report_date=_parse_date(payload.report_date, "report_date"),
        job_title=payload.job_title,
        probation_days=payload.probation_days,
        note=payload.note,
        # 录用通知书字段
        work_location=payload.work_location,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        probation_salary=payload.probation_salary,
        regular_salary=payload.regular_salary,
        report_time=payload.report_time,
    )
    db.commit()
    db.refresh(row)
    return ok(svc.serialize_offer(row))


@router.patch("/offers/{offer_id}")
def update_offer(offer_id: str, payload: OfferUpdate, db: Session = Depends(get_db)):
    """修改 Offer 内容（仅 draft / rejected 可改）。"""
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    svc.update_offer(
        db,
        row,
        job_title=payload.job_title,
        store_name=payload.store_name,
        report_date=_parse_date(payload.report_date, "report_date") if payload.report_date else None,
        probation_days=payload.probation_days,
        note=payload.note,
        # 录用通知书字段
        work_location=payload.work_location,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        probation_salary=payload.probation_salary,
        regular_salary=payload.regular_salary,
        report_time=payload.report_time,
    )
    db.commit()
    db.refresh(row)
    return ok(svc.serialize_offer(row))


def _approval_context(db: Session, offer: Offer) -> tuple[dict, dict]:
    """取出审批卡片要展示的「各环节评分」与「候选人联系信息」。

    审批人需要据此核对身份与评分。缺数据时返回空字典，卡片会自动省略对应行。
    """
    scores: dict = {}
    if offer.summary_id:
        summary = db.get(RecruitmentSummary, offer.summary_id)
        if summary is not None:
            scores = {
                "screening_score": summary.screening_score,
                "ai_interview_score": summary.ai_interview_score,
                "offline_score": summary.offline_score,
                "final_score": summary.final_score,
                "final_verdict": summary.final_verdict,
            }

    candidate: dict = {}
    if offer.candidate_id:
        cand = db.get(Candidate, offer.candidate_id)
        if cand is not None:
            candidate = {
                "name": cand.name,
                "phone": cand.phone,
                "email": cand.email,
                "source_channel": cand.source_channel,
            }
    return scores, candidate


@router.post("/offers/{offer_id}/submit-approval")
def submit_approval(offer_id: str, db: Session = Depends(get_db)):
    """提交审批：draft/rejected → pending_approval，并推送审批卡片。"""
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    svc.submit_approval(db, row)
    db.commit()
    db.refresh(row)

    # 审批人需核对候选人信息与各环节评分，故随卡片一并带上
    scores, candidate = _approval_context(db, row)
    notify = notify_offer_event(
        "pending_approval", row, scores=scores, candidate=candidate
    )
    logger.info("Offer 提交审批通知结果 offer_id=%s: %s", row.id, notify)
    return ok({"offer": svc.serialize_offer(row), "notify": notify})


@router.post("/offers/{offer_id}/approve")
def approve_offer(
    offer_id: str,
    payload: OfferApprove | None = None,
    db: Session = Depends(get_db),
):
    """审批通过（后台兜底入口，与飞书卡片回调共用服务层逻辑）。

    通过后生成候选人确认链接与截止时间，并推送「已批准」卡片给 HR。
    """
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    svc.approve_offer(db, row, note=(payload.note if payload else ""))
    db.commit()
    db.refresh(row)

    link = svc.build_offer_link(row.token or "")
    copy_text = svc.build_copy_text(row, link)
    notify = notify_offer_event("sent", row, link=link, copy_text=copy_text)
    logger.info("Offer 审批通过通知结果 offer_id=%s: %s", row.id, notify)

    # 主动推送：给候选人发邮件（未配置邮件通道时静默跳过，不影响审批结果）
    mail = mail_offer_invite(row, link=link, candidate_email=offer_candidate_email(db, row))
    logger.info("Offer 审批通过候选人邮件结果 offer_id=%s: %s", row.id, mail)

    return ok(
        {
            "offer": svc.serialize_offer(row, with_link=True),
            "link": link,
            "copy_text": copy_text,
            "notify": notify,
            "mail": mail,
        }
    )


@router.post("/offers/{offer_id}/reject")
def reject_offer(offer_id: str, payload: OfferReject, db: Session = Depends(get_db)):
    """审批驳回：pending_approval → rejected（意见必填）。"""
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    svc.reject_offer(db, row, note=payload.note)
    db.commit()
    db.refresh(row)

    notify = notify_offer_event("rejected", row)
    return ok({"offer": svc.serialize_offer(row), "notify": notify})


@router.post("/offers/{offer_id}/withdraw")
def withdraw_offer(
    offer_id: str,
    payload: OfferWithdraw | None = None,
    db: Session = Depends(get_db),
):
    """撤回 Offer（draft / pending_approval / rejected / sent 可撤回）。"""
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    svc.withdraw_offer(db, row, reason=(payload.reason if payload else ""))
    db.commit()
    db.refresh(row)
    return ok(svc.serialize_offer(row))


@router.post("/offers/{offer_id}/onboard")
def onboard_offer(offer_id: str, payload: OfferOnboard, db: Session = Depends(get_db)):
    """办理入职：accepted → onboarded（已报到）/ no_show（未报到，说明必填）。"""
    row = svc.get_offer(db, _parse_uuid(offer_id, "offer_id"))
    svc.onboard_offer(
        db,
        row,
        result=payload.result,
        actual_report_date=(
            _parse_date(payload.actual_report_date, "actual_report_date")
            if payload.actual_report_date
            else None
        ),
        onboard_store=payload.onboard_store,
        note=payload.note,
    )
    db.commit()
    db.refresh(row)

    notify: dict | None = None
    if payload.result == svc.STATUS_NO_SHOW:
        notify = notify_offer_event("no_show", row)
    return ok({"offer": svc.serialize_offer(row), "notify": notify})


# ══════════════════════════ 候选人端 ══════════════════════════


@candidate_router.get("/{token}")
def get_offer_context(token: str, db: Session = Depends(get_db)):
    """候选人查看 Offer（进入即执行惰性过期；过期则返回 expired 供结果页渲染）。"""
    row = svc.get_offer_by_token(db, token)
    svc.expire_due_offers(db)
    db.refresh(row)
    # 附上《录用通知书》完整文案（固定文案 + HR 填写字段），候选人页直接排版
    return ok({**_candidate_out(row), "letter": build_letter(row)})


@candidate_router.post("/{token}/accept")
def accept_offer(token: str, db: Session = Depends(get_db)):
    """候选人接受 Offer（幂等；非 sent 状态拒绝）。"""
    row = svc.get_offer_by_token(db, token)
    svc.expire_due_offers(db)
    db.refresh(row)

    svc.accept_offer(db, row)
    db.commit()
    db.refresh(row)

    notify = notify_offer_event("accepted", row)
    logger.info("候选人接受 Offer 通知结果 offer_id=%s: %s", row.id, notify)

    # 第二封邮件：录用要点 + 报到指引（幂等：非 sent 状态在服务层已被拒）
    mail = mail_offer_accepted(row, candidate_email=offer_candidate_email(db, row))
    logger.info("候选人接受确认邮件结果 offer_id=%s: %s", row.id, mail)

    return ok(
        {
            "status": row.status,
            "status_label": svc.status_label(row.status),
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "job_title": row.job_title,
            "store_name": row.store_name,
            "report_date": row.report_date.isoformat() if row.report_date else None,
            "mail": mail,
        }
    )


@candidate_router.post("/{token}/decline")
def decline_offer(token: str, payload: OfferDecline | None = None, db: Session = Depends(get_db)):
    """候选人婉拒 Offer（理由为自由文本，选填）。"""
    row = svc.get_offer_by_token(db, token)
    svc.expire_due_offers(db)
    db.refresh(row)

    svc.decline_offer(db, row, reason_text=(payload.reason_text if payload else ""))
    db.commit()
    db.refresh(row)

    notify = notify_offer_event("declined", row)
    return ok(
        {
            "status": row.status,
            "status_label": svc.status_label(row.status),
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "notify": notify,
        }
    )
