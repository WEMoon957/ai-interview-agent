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
    create_tasks,
    default_card_sender,
    merge_observers,
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


def _fmt_score(value) -> str:
    """评分格式化：None → 「—」，数字保留一位小数（供审批卡片展示）。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


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


# 审批卡片标题栏配色：深灰（「档案」观感，视觉重心留给正文；与已批准/驳回等彩色事件卡区分）
APPROVAL_HEADER_TEMPLATE = "grey"

# 录用建议 → lark_md 字体颜色（飞书富文本仅支持 red / green / grey 三色）
_VERDICT_COLORS = {
    "强烈推荐": "green",
    "推荐": "green",
    "待定": "grey",
    "不推荐": "red",
    "不建议": "red",
}


def _salary_text(value) -> str:
    """薪资字段展示：空值显式写「未填写」，避免审批人误以为卡片漏了这项。"""
    text = str(value or "").strip()
    return text or "未填写"


def _verdict_html(verdict: str) -> str:
    """录用建议渲染成带颜色的富文本片段（无建议时返回空串，不占位）。"""
    text = (verdict or "").strip()
    if not text:
        return ""
    color = _VERDICT_COLORS.get(text, "grey")
    return f"  <font color='{color}'>{text}</font>"


def _fields_div(pairs: list[tuple[str, str]]) -> dict:
    """「标签在上、值在下」的字段块：飞书里两列并排，撑起档案的网格感。"""
    return {
        "tag": "div",
        "fields": [
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**{label}**\n{value}"},
            }
            for label, value in pairs
        ],
    }


def _section(title: str, pairs: list[tuple[str, str]]) -> list[dict]:
    """档案分区：小标题 + 字段块（对应设计稿里「薪酬 / 入职 / 评估」三段）。"""
    return [
        {"tag": "div", "text": {"tag": "lark_md", "content": title}},
        _fields_div(pairs),
    ]


def build_approval_card(
    offer: Offer,
    *,
    scores: dict | None = None,
    candidate: dict | None = None,
    settings: Settings | None = None,
) -> dict:
    """待审批卡片（D 版「候选人档案」：薪酬 / 入职 / 评估 三段式 + 同意·驳回）。

    与改版前的差异
    --------------
    - **补上薪资**：试用期 / 转正后两档直接读 ``offer.probation_salary`` /
      ``regular_salary``（库中已有字段，无需迁移）；缺数据时显式写「未填写」。
    - **标题栏改深灰**（:data:`APPROVAL_HEADER_TEMPLATE`），视觉重心让给正文。
    - 其余信息一项不减：岗位 / 门店 / 来源 / 报到 / 四档评分 / 联系方式 / 备注。

    Args:
        scores: 初筛 / AI 面试 / 线下复试 / 最终加权分 + 录用建议，缺数据时整段评估省略。
        candidate: 电话、邮箱、投递渠道，缺数据时对应行自动省略。
        settings: 保留参数以兼容既有调用方（本函数不再依赖配置）。
    """
    score_map = scores or {}
    verdict = str(score_map.get("final_verdict") or "")

    report_date = offer.report_date.isoformat() if offer.report_date else "待定"
    report_time = str(offer.report_time or "").strip()
    report_when = f"{report_date} {report_time}".strip()

    # ① 档案头：姓名 + 录用建议、岗位 · 门店、来源
    meta_bits = ["到店直招" if offer.source == "walk_in" else "线上投递"]
    channel = str((candidate or {}).get("source_channel") or "").strip()
    if channel:
        meta_bits.append(channel)
    head = "\n".join(
        [
            f"**{offer.candidate_name or '—'}**{_verdict_html(verdict)}",
            f"{offer.job_title or '—'} ｜ {offer.store_name or '—'}",
            f"<font color='grey'>{' · '.join(meta_bits)}</font>",
        ]
    )
    elements: list[dict] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": head}},
        {"tag": "hr"},
    ]

    # ② 薪酬：本次改版的核心，审批人第一眼看的就是这行数字
    elements += _section(
        "💰 **薪酬**",
        [
            ("试用期薪资", _salary_text(offer.probation_salary)),
            ("转正后薪资", _salary_text(offer.regular_salary)),
        ],
    )
    elements.append({"tag": "hr"})

    # ③ 入职安排
    onboard: list[tuple[str, str]] = [
        ("拟报到", report_when),
        ("试用期", f"{offer.probation_days} 天"),
    ]
    location = str(offer.work_location or "").strip()
    if location:
        onboard.append(("工作地点", location))
    elements += _section("📅 **入职**", onboard)

    # ④ 评估结果（无评分数据时整段省略，避免满屏「—」）
    if score_map:
        elements.append({"tag": "hr"})
        elements += _section(
            "📊 **评估**",
            [
                ("简历初筛", _fmt_score(score_map.get("screening_score"))),
                ("AI 面试", _fmt_score(score_map.get("ai_interview_score"))),
                ("线下复试", _fmt_score(score_map.get("offline_score"))),
                ("最终加权", _fmt_score(score_map.get("final_score"))),
            ],
        )

    # ⑤ 补充信息：联系方式 / 备注（灰色小字，不抢正文）
    contact_bits = []
    if candidate:
        if candidate.get("phone"):
            contact_bits.append(f"电话 {candidate['phone']}")
        if candidate.get("email"):
            contact_bits.append(f"邮箱 {candidate['email']}")
    extras: list[str] = []
    if contact_bits:
        extras.append("联系方式：" + " ｜ ".join(contact_bits))
    if offer.note:
        extras.append(f"备注：{offer.note}")
    if extras:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(
                        f"<font color='grey'>{line}</font>" for line in extras
                    ),
                },
            }
        )

    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "action",
            "actions": [
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
        }
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": APPROVAL_HEADER_TEMPLATE,
            "title": {"tag": "plain_text", "content": "Offer 待审批"},
        },
        "elements": elements,
    }


def build_offer_card(
    event: str,
    offer: Offer,
    *,
    link: str = "",
    copy_text: str = "",
    settings: Settings | None = None,
    actor: str = "",
    scores: dict | None = None,
    candidate: dict | None = None,
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
        return build_approval_card(
            offer, scores=scores, candidate=candidate, settings=s
        )

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
        # 审批人之外，全局抄送人始终额外收到一份（测试/监看）
        return merge_observers(approvers, s), ""

    hr_ids, err = resolve_recipients(s)
    if event != EVENT_ACCEPTED:
        return merge_observers(hr_ids, s), err

    # 候选人接受：HR + 店长 + 全局抄送人（去重保序，抄送人排最后）
    return merge_observers([*hr_ids, *s.store_manager_list], s), err


# 需要额外建飞书待办的事件（卡片容易被消息刷过去，待办会留在待办列表里）
_TASK_EVENTS = (EVENT_PENDING_APPROVAL, EVENT_SENT, EVENT_ACCEPTED)


def build_offer_task(
    event: str, offer: Offer, *, link: str = "", settings: Settings | None = None
) -> tuple[str, str] | None:
    """按事件构造飞书待办（标题, 描述）；该事件不需要待办时返回 None。

    - 待审批 → 审批人：「请审批 XX 的 Offer」
    - 已批准 → HR：「把 Offer 链接发给 XX」
    - 已接受 → HR / 店长：「安排 XX 入职（报到日 Y）」
    """
    if event not in _TASK_EVENTS:
        return None

    report_date = offer.report_date.isoformat() if offer.report_date else "—"
    base_lines = [
        f"候选人：{offer.candidate_name or '—'}",
        f"岗位：{offer.job_title or '—'}",
        f"报到门店：{offer.store_name or '—'}",
        f"拟报到日期：{report_date}",
        f"试用期：{offer.probation_days} 天",
    ]

    if event == EVENT_PENDING_APPROVAL:
        title = f"【待审批】「{offer.candidate_name}」的 Offer · {offer.job_title or ''}".strip(" ·")
        desc = "\n".join(
            [
                *base_lines,
                "",
                "请在飞书审批卡片上点「同意 / 驳回」，或登录后台「Offer / 入职」页处理。",
            ]
        )
        return title, desc

    if event == EVENT_SENT:
        title = f"【待发送】把 Offer 链接发给「{offer.candidate_name}」"
        lines = [*base_lines, ""]
        if link:
            lines.append(f"候选人确认链接：{link}")
        lines.append("请把链接（或卡片里的转发话术）发给候选人，并留意其确认结果。")
        return title, "\n".join(lines)

    # EVENT_ACCEPTED
    title = f"【待入职】「{offer.candidate_name}」已接受 Offer（{report_date} 报到）"
    desc = "\n".join(
        [
            *base_lines,
            "",
            f"请在报到日（{report_date}）之后，到后台「Offer / 入职」页标记「已报到 / 未报到」。",
        ]
    )
    return title, desc


def _create_offer_task(
    event: str,
    offer: Offer,
    targets: list[str],
    settings: Settings,
    *,
    link: str = "",
    creator=None,
) -> dict | None:
    """为关键事件创建飞书待办（失败只记日志，不影响卡片与业务流转）。

    Returns:
        ``None`` 表示该事件不需要待办；否则返回 ``{"status", "assignees", "reason"}``。
    """
    if not settings.feishu_task_enabled:
        return {"status": "skipped", "reason": "待办开关关闭（FEISHU_TASK_ENABLED=false）"}

    built = build_offer_task(event, offer, link=link, settings=settings)
    if built is None:
        return None

    title, desc = built
    ok, assignees, err = create_tasks(
        title, desc, settings=settings, assignees=targets, creator=creator
    )
    if ok:
        logger.info("Offer 待办已创建 event=%s assignees=%s", event, assignees)
    else:
        logger.warning("Offer 待办创建失败 event=%s：%s", event, err)
    return {"status": "created" if ok else "failed", "assignees": assignees, "reason": err}


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


# ──────────────────── 候选人邮件（Offer 主动推送） ────────────────────
# 说明：邮件是「主动推送」通道 —— 候选人不必自己保存页面。
# 邮件发送失败只记日志，**绝不影响**审批通过与候选人接受的主流程。


def mail_offer_invite(
    offer: Offer,
    *,
    link: str,
    deadline: str = "",
    candidate_email: str = "",
    settings: Settings | None = None,
) -> dict:
    """审批通过后给候选人发第一封邮件（含「查看并接受 Offer」按钮）。

    Returns:
        ``{"status": "sent" | "skipped" | "failed", "to": 邮箱, "reason": 原因}``
    """
    from app.services import mailer

    s = settings or get_settings()
    to = (candidate_email or "").strip()
    if not mailer.is_mail_ready(s):
        return {"status": "skipped", "to": to, "reason": "邮件通道未启用"}
    if not to:
        return {"status": "skipped", "to": "", "reason": "候选人未留邮箱"}

    if not deadline and getattr(offer, "expires_at", None):
        deadline = format_deadline(offer.expires_at, s)

    # 组装与候选人页面一致的《录用通知书》完整数据（固定文案 + HR 填写 + 系统带出）
    from app.services.offer_letter import build_letter

    letter = build_letter(offer)

    subject, html = mailer.build_offer_invite_mail(
        candidate_name=letter["candidate_name"] or offer.candidate_name or "",
        job_title=letter["job_title"] or offer.job_title or "",
        store_name=offer.store_name or "",
        report_date=offer.report_date,
        link=link,
        deadline=deadline or letter["deadline"],
        report_time=letter["report_time"],
        work_location=letter["work_location"],
        contact_name=letter["contact_name"],
        contact_phone=letter["contact_phone"],
        probation_phrase=letter["probation_phrase"],
        probation_suffix=letter["probation_suffix"],
        probation_salary=letter["probation_salary"],
        regular_salary=letter["regular_salary"],
        materials=letter["materials"],
        legal_note=letter["legal_note"],
        company_name=letter["company_name"],
        letter_date=letter["letter_date"],
        settings=s,
    )
    ok, err = mailer.send_mail(to, subject, html, settings=s)
    if ok:
        logger.info("候选人 Offer 邮件已发送 to=%s offer_id=%s", to, offer.id)
        return {"status": "sent", "to": to, "reason": ""}
    logger.warning("候选人 Offer 邮件发送失败 to=%s offer_id=%s: %s", to, offer.id, err)
    return {"status": "failed", "to": to, "reason": err}


def mail_offer_accepted(
    offer: Offer,
    *,
    candidate_email: str = "",
    settings: Settings | None = None,
) -> dict:
    """候选人接受后发第二封邮件（录用要点 + 报到指引）。"""
    from app.services import mailer

    s = settings or get_settings()
    to = (candidate_email or "").strip()
    if not mailer.is_mail_ready(s):
        return {"status": "skipped", "to": to, "reason": "邮件通道未启用"}
    if not to:
        return {"status": "skipped", "to": "", "reason": "候选人未留邮箱"}

    subject, html = mailer.build_offer_accepted_mail(
        candidate_name=offer.candidate_name or "",
        job_title=offer.job_title or "",
        store_name=offer.store_name or "",
        report_date=offer.report_date,
        report_time=getattr(offer, "report_time", "") or "",
        work_location=getattr(offer, "work_location", "") or "",
        contact_name=getattr(offer, "contact_name", "") or "",
        contact_phone=getattr(offer, "contact_phone", "") or "",
        settings=s,
    )
    ok, err = mailer.send_mail(to, subject, html, settings=s)
    if ok:
        logger.info("候选人接受确认邮件已发送 to=%s offer_id=%s", to, offer.id)
        return {"status": "sent", "to": to, "reason": ""}
    logger.warning("候选人接受邮件发送失败 to=%s offer_id=%s: %s", to, offer.id, err)
    return {"status": "failed", "to": to, "reason": err}


def offer_candidate_email(db, offer: Offer) -> str:
    """取该 Offer 对应候选人的邮箱（取不到返回空串）。"""
    try:
        from app.db.models import Candidate

        cand = db.get(Candidate, offer.candidate_id) if offer.candidate_id else None
        return (cand.email or "").strip() if cand else ""
    except Exception as exc:  # noqa: BLE001 - 取邮箱失败不阻断主流程
        logger.warning("读取候选人邮箱失败 offer_id=%s: %s", getattr(offer, "id", ""), exc)
        return ""


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
    task_creator=None,
    scores: dict | None = None,
    candidate: dict | None = None,
) -> dict:
    """推送 Offer 事件卡片（尽力而为，返回结果摘要）。

    Returns:
        ``{"status": "sent"|"failed"|"skipped", "event": ..., "recipients": [...], "reason": ...}``
    """
    s = settings or get_settings()
    if not s.feishu_offer_notify_enabled:
        return {"status": "skipped", "event": event, "recipients": [], "reason": "推送总开关关闭"}

    card = build_offer_card(
        event,
        offer,
        link=link,
        copy_text=copy_text,
        settings=s,
        actor=actor,
        scores=scores,
        candidate=candidate,
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
    result = {
        "status": "sent" if ok else "failed",
        "event": event,
        "recipients": targets,
        "reason": err,
    }
    # 关键事件同时建飞书待办（与卡片同批收件人：待审批→审批人，已批准→HR，已接受→HR∪店长）
    task_result = _create_offer_task(
        event, offer, targets, s, link=link, creator=task_creator
    )
    if task_result is not None:
        result["task"] = task_result
    return result
