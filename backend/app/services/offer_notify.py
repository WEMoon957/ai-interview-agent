"""Offer 状态飞书卡片：构造 + 推送编排。

覆盖 7 类事件：待审批 / 审批通过 / 审批驳回 / 候选人接受 / 候选人婉拒 / 已过期 / 未报到。

设计要点：
- **审批卡片带「同意 / 驳回」按钮**，按钮 ``value`` 携带 ``{offer_id, action}``，
  由卡片回调接口消费（回调不可用时，审批仍可在后台完成，卡片退化为通知）；
- **收件人按事件分流**：审批事件 → 审批人；候选人接受 → HR + 店长；其余 → HR；
- 推送一律「尽力而为」：失败只记日志并由调用方兜底，绝不影响业务状态流转。
"""
import logging

from app.config import Settings, get_settings
from app.db.models.offer import Offer
from app.services.feishu_notify import (
    build_button,
    build_card,
    default_card_sender,
    resolve_recipients,
)
from app.services.offer import format_deadline, status_label

logger = logging.getLogger(__name__)

# 事件常量（与 services/offer.py 的状态一一对应，pending_approval 为审批卡事件）
EVENT_PENDING_APPROVAL = "pending_approval"
EVENT_SENT = "sent"
EVENT_REJECTED = "rejected"
EVENT_ACCEPTED = "accepted"
EVENT_DECLINED = "declined"
EVENT_EXPIRED = "expired"
EVENT_NO_SHOW = "no_show"

# 卡片标题栏配色：待审批/过期=橙，批准/接受=绿/蓝，驳回/未报到=红，婉拒=灰
_TEMPLATES = {
    EVENT_PENDING_APPROVAL: "orange",
    EVENT_SENT: "blue",
    EVENT_REJECTED: "red",
    EVENT_ACCEPTED: "green",
    EVENT_DECLINED: "grey",
    EVENT_EXPIRED: "orange",
    EVENT_NO_SHOW: "red",
}


def _offer_lines(offer: Offer) -> list[str]:
    """卡片通用的 Offer 基本信息行。"""
    report_date = offer.report_date.isoformat() if offer.report_date else "待定"
    return [
        f"**候选人**：{offer.candidate_name or '—'}",
        f"**岗位**：{offer.job_title or '—'}",
        f"**报到门店**：{offer.store_name or '—'}",
        f"**拟报到日期**：{report_date}",
        f"**试用期**：{offer.probation_days} 天",
    ]


def build_approval_card(offer: Offer, settings: Settings | None = None) -> dict:
    """待审批卡片（带「同意 / 驳回」按钮）。"""
    s = settings or get_settings()
    lines = [
        "**有新的 Offer 待审批，请点击下方按钮处理。**",
        "",
        *_offer_lines(offer),
    ]
    if offer.note:
        lines.append(f"**备注**：{offer.note}")
    if offer.source == "walk_in":
        lines.append("**来源**：到店直招")

    return build_card(
        "🔔 Offer 待审批",
        _TEMPLATES[EVENT_PENDING_APPROVAL],
        ["\n".join(lines)],
        actions=[
            build_button(
                "同意",
                {"offer_id": str(offer.id), "action": "approve"},
                button_type="primary",
            ),
            build_button(
                "驳回",
                {"offer_id": str(offer.id), "action": "reject"},
                button_type="danger",
            ),
        ],
    )


def build_offer_card(
    event: str,
    offer: Offer,
    *,
    link: str = "",
    copy_text: str = "",
    settings: Settings | None = None,
    actor: str = "",
) -> dict | None:
    """按事件构造卡片；未知事件返回 None（调用方跳过推送）。

    Args:
        link: 候选人确认链接（审批通过时由服务层生成并传入）。
        copy_text: 供 HR 直接转发的话术（审批通过时传入）。
        actor: 审批人/操作人展示名（可用 open_id 兜底）。
    """
    s = settings or get_settings()
    deadline = format_deadline(offer.expires_at, s)

    if event == EVENT_SENT:
        blocks = [
            "\n".join(
                [
                    "**Offer 已审批通过**，请把下方链接发给候选人确认。",
                    "",
                    *_offer_lines(offer),
                    f"**截止时间**：{deadline}",
                ]
            ),
        ]
        if copy_text:
            blocks.append(f"**可直接转发的话术**：\n{copy_text}")
        elif link:
            blocks.append(f"**确认链接**：{link}")
        return build_card("✅ Offer 已批准", _TEMPLATES[event], blocks)

    if event == EVENT_REJECTED:
        lines = [
            "**该 Offer 未通过审批。**",
            "",
            *_offer_lines(offer),
            f"**审批意见**：{offer.approval_note or '（未填写）'}",
        ]
        return build_card("❌ Offer 被驳回", _TEMPLATES[event], ["\n".join(lines)])

    if event == EVENT_ACCEPTED:
        report_date = offer.report_date.isoformat() if offer.report_date else "—"
        lines = [
            "**候选人已接受 Offer，请安排后续入职。**",
            "",
            *_offer_lines(offer),
            "",
            f"以上为 Offer 约定信息（尚未报到）。"
            f"**实际报到情况请在报到日（{report_date}）之后回到后台标记「已报到 / 未报到」。**",
        ]
        return build_card("🎉 候选人已接受 Offer", _TEMPLATES[event], ["\n".join(lines)])

    if event == EVENT_DECLINED:
        lines = [
            "**候选人婉拒了 Offer。**",
            "",
            *_offer_lines(offer),
            f"**婉拒理由**：{offer.decline_reason_text or '（未填写）'}",
        ]
        return build_card("🙏 候选人婉拒 Offer", _TEMPLATES[event], ["\n".join(lines)])

    if event == EVENT_EXPIRED:
        lines = [
            f"**候选人未在 {deadline} 前确认，Offer 已过期。**",
            "",
            *_offer_lines(offer),
            "可考虑联系候选人，或重新发起 Offer。",
        ]
        return build_card("⏰ Offer 已过期", _TEMPLATES[event], ["\n".join(lines)])

    if event == EVENT_NO_SHOW:
        lines = [
            "**候选人未按约定报到。**",
            "",
            *_offer_lines(offer),
            f"**说明**：{offer.no_show_note or '（未填写）'}",
        ]
        return build_card("⚠️ 候选人未报到", _TEMPLATES[event], ["\n".join(lines)])

    if event == EVENT_PENDING_APPROVAL:
        return build_approval_card(offer, s)

    logger.warning("未知的 Offer 事件，跳过推送：%s", event)
    return None


def resolve_targets(
    event: str, offer: Offer, settings: Settings | None = None
) -> tuple[list[str], str]:
    """按事件解析收件人 open_id 列表。

    - 待审批 → 审批人（``OFFER_APPROVER_OPEN_IDS``）；
    - 候选人接受 → HR 收件人 ∪ 店长（``FEISHU_STORE_MANAGER_OPEN_IDS``）；
    - 其余 → HR 收件人（部门实时解析）。
    """
    s = settings or get_settings()

    if event == EVENT_PENDING_APPROVAL:
        approvers = s.offer_approver_list
        if not approvers:
            return [], "未配置 Offer 审批人（OFFER_APPROVER_OPEN_IDS）"
        return approvers, ""

    hr_ids, err = resolve_recipients(s)
    if event != EVENT_ACCEPTED:
        return hr_ids, err

    # 候选人接受：HR + 店长（去重保序）
    seen: set[str] = set()
    merged: list[str] = []
    for open_id in [*hr_ids, *s.store_manager_list]:
        if open_id and open_id not in seen:
            seen.add(open_id)
            merged.append(open_id)
    return merged, err


def _send_card_to(
    open_ids: list[str],
    card: dict,
    settings: Settings,
    sender=None,
) -> tuple[bool, str]:
    """向指定 open_id 列表发送卡片；任一人成功即视为成功。"""
    send = sender or default_card_sender
    ok_any = False
    errors: list[str] = []
    for open_id in open_ids:
        try:
            ok, err = send(open_id, card, settings)
        except Exception as exc:  # noqa: BLE001 - 单个收件人失败不拖垮整体
            ok, err = False, repr(exc)
        if ok:
            ok_any = True
        else:
            errors.append(f"{open_id}: {err}")
    return ok_any, "; ".join(errors)


def notify_offer_event(
    event: str,
    offer: Offer,
    *,
    settings: Settings | None = None,
    sender=None,
    link: str = "",
    copy_text: str = "",
    actor: str = "",
    dry_run: bool = False,
) -> dict:
    """推送 Offer 事件卡片（尽力而为，返回结果摘要）。

    Returns:
        ``{"status": "sent"|"failed"|"skipped", "event": ..., "recipients": [...], "reason": ...}``
    """
    s = settings or get_settings()
    if not s.feishu_offer_notify_enabled:
        return {"status": "skipped", "event": event, "recipients": [], "reason": "推送总开关关闭"}

    card = build_offer_card(
        event, offer, link=link, copy_text=copy_text, settings=s, actor=actor
    )
    if card is None:
        return {"status": "skipped", "event": event, "recipients": [], "reason": "未知事件"}

    targets, resolve_err = resolve_targets(event, offer, s)
    if not targets:
        return {
            "status": "skipped",
            "event": event,
            "recipients": [],
            "reason": resolve_err or "未解析到收件人",
        }

    if dry_run:
        return {"status": "dry_run", "event": event, "recipients": targets, "reason": ""}

    ok, err = _send_card_to(targets, card, s, sender)
    return {
        "status": "sent" if ok else "failed",
        "event": event,
        "recipients": targets,
        "reason": err,
    }
