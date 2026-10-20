"""候选人邮件发送（SMTP，标准库实现，零新增依赖）。

用途：把 Offer 相关通知直接发到候选人邮箱，候选人不必自己保存页面。

设计原则（与 ``feishu_notify`` 保持一致）：
- **未启用或未配置 SMTP 时静默跳过**，返回 ``(False, 原因)`` 供日志记录；
- **绝不抛异常**打断主流程 —— 发邮件失败不能让审批通过或候选人接受动作失败；
- 邮件正文用**内联样式**（邮件客户端对 ``<style>`` 支持不一致，内联最稳）；
- 不记录收件人之外的隐私内容到日志。

邮件流程：
1. Offer 审批通过 → :func:`build_offer_invite_mail` + :func:`send_mail`（含「查看并接受」按钮）
2. 候选人确认接受 → :func:`build_offer_accepted_mail` + :func:`send_mail`（通知书要点 + 报到指引）
"""
from __future__ import annotations

import smtplib
import ssl
from datetime import date, datetime, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

from app.config import Settings, get_settings

# 报到携带材料（与候选人《录用通知书》页保持一致的 5 项）
REPORT_MATERIALS = [
    "身份证原件",
    "毕业证原件",
    "本人中国银行银行卡",
    "近三个月内体检报告一份（需包含常规内外科检查、肝功能、血常规、尿常规、胸部X光等）",
    "原单位离职证明",
]


def is_mail_ready(settings: Settings | None = None) -> bool:
    """邮件通道是否可用（开关打开 + SMTP 账号密码齐全）。"""
    s = settings or get_settings()
    return bool(
        s.candidate_mail_enabled
        and (s.smtp_username or "").strip()
        and (s.smtp_password or "").strip()
        and (s.smtp_host or "").strip()
    )


def _from_address(s: Settings) -> str:
    """发件地址：固定取 SMTP 登录名（企业邮要求 From 与登录账号同域）。"""
    return (s.smtp_username or "").strip()


def _reply_to(s: Settings) -> str:
    """回信地址：未单独配置时回落到发件地址，保证「直接回复」不落空。"""
    return (getattr(s, "mail_reply_to", "") or _from_address(s) or "").strip()


def _company_name(s: Settings) -> str:
    """主题/落款用的公司名：显式配置优先，其次从旧格式显示名（「公司 · 部门」）截取。"""
    explicit = (getattr(s, "mail_company_name", "") or "").strip()
    if explicit:
        return explicit
    return (s.mail_from_name or "").split("·")[0].strip() or "公司"


def send_mail(
    to: str,
    subject: str,
    html: str,
    *,
    settings: Settings | None = None,
    text: str = "",
) -> tuple[bool, str]:
    """发送一封 HTML 邮件；**绝不抛异常**。

    Returns:
        (是否成功, 失败原因)；未配置或未启用时返回 (False, "邮件通道未启用")。
    """
    s = settings or get_settings()
    if not is_mail_ready(s):
        return False, "邮件通道未启用（candidate_mail_enabled / SMTP 未配置）"

    recipient = (to or "").strip()
    if not recipient or "@" not in recipient:
        return False, f"收件邮箱无效：{to!r}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    # 显示名 + 发件地址：显示名用 Header 编码以正确显示中文。
    # 发件地址必须是 SMTP 登录名（飞书等企业邮会拒绝伪造同域发件人）。
    from_addr = _from_address(s)
    msg["From"] = f"{Header(s.mail_from_name, 'utf-8').encode()} <{from_addr}>"
    msg["To"] = recipient
    # Reply-To：候选人点「回复」时回到公共邮箱（默认与发件地址相同）
    reply_to = _reply_to(s)
    if reply_to:
        msg["Reply-To"] = reply_to

    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if s.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=s.smtp_timeout, context=context
            ) as server:
                server.login(s.smtp_username, s.smtp_password)
                server.sendmail(s.smtp_username, [recipient], msg.as_string())
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(s.smtp_username, s.smtp_password)
                server.sendmail(s.smtp_username, [recipient], msg.as_string())
    except Exception as exc:  # noqa: BLE001 - 发送失败一律降级为失败说明
        return False, f"发送失败：{exc}"
    return True, ""


# ────────────────────────── 内部：文案与版式 ──────────────────────────


def _fmt_date(value: Any) -> str:
    """日期格式化为「2026年10月20日」；非日期返回空串。"""
    if isinstance(value, datetime):
        return f"{value.year}年{value.month}月{value.day}日"
    if isinstance(value, date):
        return f"{value.year}年{value.month}月{value.day}日"
    return str(value) if value else ""


def _shell(title: str, body_html: str) -> str:
    """统一邮件外壳（内联样式，兼容各家邮件客户端）。"""
    return (
        '<div style="margin:0;padding:24px 16px;background:#f5f6f8;'
        'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;">'
        '<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:10px;'
        'padding:28px 24px;color:#1f2329;font-size:15px;line-height:1.75;">'
        f'<div style="font-size:19px;font-weight:700;margin-bottom:16px;color:#1f2329;">{title}</div>'
        f"{body_html}"
        '<div style="margin-top:28px;padding-top:16px;border-top:1px solid #e5e6eb;'
        'font-size:12px;color:#8a919f;">本邮件由招聘系统自动发送，'
        "如对内容有疑问，直接回复本邮件即可联系 HR。</div>"
        "</div></div>"
    )


def _info_rows(rows: list[tuple[str, str]]) -> str:
    """把 (标签, 值) 列表渲染成表格式信息块（跳过空值）。"""
    items = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#8a919f;white-space:nowrap;'
        f'vertical-align:top;">{escape(str(k))}</td>'
        f'<td style="padding:6px 0;color:#1f2329;font-weight:600;">{escape(str(v))}</td></tr>'
        for k, v in rows
        if v
    )
    return f'<table style="border-collapse:collapse;margin:12px 0;">{items}</table>'


def _button(text: str, link: str) -> str:
    """行动按钮（使用 table 承载，兼容不支持 padding 的客户端）。"""
    href = escape(link or "", quote=True)
    return (
        '<div style="margin:24px 0;text-align:center;">'
        f'<a href="{href}" style="display:inline-block;padding:12px 32px;background:#3370ff;'
        'color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;border-radius:6px;">'
        f"{escape(text)}</a></div>"
        f'<div style="font-size:12px;color:#8a919f;word-break:break-all;">若按钮无法点击，'
        f"请复制此链接到浏览器打开：<br/>{escape(link or '')}</div>"
    )


# ────────────────────────── 两封邮件模板 ──────────────────────────


def build_offer_invite_mail(
    *,
    candidate_name: str,
    job_title: str,
    store_name: str,
    report_date: Any,
    link: str,
    deadline: str = "",
    report_time: str = "",
    work_location: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    probation_phrase: str = "",
    probation_suffix: str = "",
    probation_salary: str = "",
    regular_salary: str = "",
    materials: list[str] | None = None,
    legal_note: str = "",
    company_name: str = "",
    letter_date: str = "",
    settings: Settings | None = None,
) -> tuple[str, str]:
    """第一封：完整《录用通知书》+「查看并接受」入口。

    正文包含候选人保存 Offer 所需的全部信息（岗位、地点、联系人、试用期/
    转正薪资、报到时间、携带材料、法律声明、落款），与候选人确认页
    ``/o/{token}`` 保持一致，方便候选人直接保存本邮件作为录用凭证。

    Returns:
        (主题, HTML 正文)
    """
    s = settings or get_settings()
    company = (company_name or _company_name(s)).strip() or "公司"
    subject = f"【录用通知】{candidate_name} 你好，{company}诚邀你加入"

    rows = _info_rows(
        [
            ("入职岗位", job_title),
            ("报到门店", store_name),
            ("工作地点", work_location),
            ("联系人", f"{contact_name} {contact_phone}".strip()),
            ("报到日期", _fmt_date(report_date)),
            ("报到时间", report_time),
        ]
    )

    # 薪资段落：试用期 + 转正（缺字段时只渲染存在的部分）
    salary_parts: list[str] = []
    if probation_phrase or probation_salary:
        line = probation_phrase or "试用期"
        if probation_salary:
            line += f"，试用期薪资为{probation_salary}"
        line += probation_suffix or ""
        salary_parts.append(line)
    if regular_salary:
        salary_parts.append(f"转正后综合薪资{regular_salary}")
    salary_html = (
        "<p style='margin:12px 0;'>"
        + "<br/>".join(escape(x) for x in salary_parts)
        + "</p>"
        if salary_parts
        else ""
    )

    # 报到携带材料（与候选人确认页、第二封邮件共用同一份清单）
    materials_html = ""
    if materials:
        items = "".join(
            f'<li style="margin-bottom:6px;">{escape(m)}</li>' for m in materials
        )
        materials_html = (
            '<div style="margin:20px 0 0;padding:14px 16px;background:#f7f8fa;border-radius:8px;">'
            '<div style="font-weight:700;margin-bottom:8px;">报到时请携带以下资料</div>'
            f'<ol style="margin:0;padding-left:20px;color:#1f2329;">{items}</ol></div>'
        )

    legal_html = (
        f"<p style='margin:16px 0 0;color:#4e5969;'>{escape(legal_note)}</p>"
        if legal_note
        else ""
    )
    deadline_line = (
        f'<p style="margin:16px 0 0;color:#d4380d;">请在 <b>{escape(deadline)}</b> 前完成确认，'
        "逾期将视为放弃本次录用。</p>"
        if deadline
        else ""
    )
    sign_html = (
        '<div style="margin:24px 0 0;text-align:right;color:#1f2329;">'
        f"<p style='margin:0;'>{escape(company_name)}</p>"
        f"<p style='margin:4px 0 0;'>日期：{escape(letter_date)}</p></div>"
        if (company_name or letter_date)
        else ""
    )

    body = (
        f"<p style='margin:0 0 12px;'>{escape(candidate_name)} 你好：</p>"
        "<p style='margin:0 0 12px;'>很高兴通知你，经面试评估，我们决定录用你加入我们的团队。"
        "以下是你的《录用通知书》完整内容，请仔细阅读并保存本邮件。</p>"
        f"{rows}"
        f"{salary_html}"
        f"{materials_html}"
        f"{legal_html}"
        f"{deadline_line}"
        f"{sign_html}"
        f"{_button('查看并接受 Offer', link)}"
        "<p style='margin:16px 0 0;color:#8a919f;font-size:13px;'>"
        "如对录用条件有疑问，可直接联系 HR 沟通。</p>"
    )
    return subject, _shell("录用通知书", body)


def build_offer_accepted_mail(
    *,
    candidate_name: str,
    job_title: str,
    store_name: str,
    report_date: Any,
    report_time: str = "",
    work_location: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    settings: Settings | None = None,
) -> tuple[str, str]:
    """第二封：候选人接受后的确认邮件（录用要点 + 报到指引）。"""
    s = settings or get_settings()
    company = _company_name(s)
    subject = f"【入职指引】{candidate_name}，欢迎加入{company}"

    rows = _info_rows(
        [
            ("入职岗位", job_title),
            ("报到门店", store_name),
            ("报到日期", _fmt_date(report_date)),
            ("报到时间", report_time),
            ("工作地点", work_location),
            ("联系人", f"{contact_name} {contact_phone}".strip()),
        ]
    )
    materials = "".join(
        f'<li style="margin-bottom:6px;">{escape(m)}</li>' for m in REPORT_MATERIALS
    )
    body = (
        f"<p style='margin:0 0 12px;'>{escape(candidate_name)} 你好：</p>"
        "<p style='margin:0 0 12px;'>我们已经收到你对录用通知的确认，欢迎加入我们！"
        "以下是你的入职信息，请按约定时间到岗报到。</p>"
        f"{rows}"
        '<div style="margin:20px 0 0;padding:14px 16px;background:#f7f8fa;border-radius:8px;">'
        '<div style="font-weight:700;margin-bottom:8px;">报到时请携带以下材料</div>'
        f'<ol style="margin:0;padding-left:20px;color:#1f2329;">{materials}</ol></div>'
        "<p style='margin:16px 0 0;'>如有任何变动或疑问，请提前联系 HR。期待与你相见！</p>"
    )
    return subject, _shell("入职指引", body)


def mail_configured_summary(settings: Settings | None = None) -> str:
    """给日志/自检用的通道状态摘要（不含敏感信息）。"""
    s = settings or get_settings()
    if not is_mail_ready(s):
        return "未启用"
    return (
        f"{s.smtp_host}:{s.smtp_port} 发件人={_from_address(s)} "
        f"显示名={s.mail_from_name} 回信={_reply_to(s) or '未设置'}"
    )
