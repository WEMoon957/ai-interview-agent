"""候选人邮件模块单测。

覆盖：
- 通道判定（开关 + SMTP 账号密码的组合）
- 未配置 / 未启用时**静默跳过**且**不抛异常**（不能拖垮审批与接受主流程）
- 非法收件邮箱的保护
- 两封邮件的模板内容（候选人姓名、接受链接、5 项报到材料）
- offer_notify 两个入口函数在「无邮箱」时的降级
"""

from email import message_from_string
from email.header import decode_header, make_header
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.services.mailer as mailer
from app.services.offer_notify import mail_offer_accepted, mail_offer_invite


def _settings(**kw):
    base = dict(
        candidate_mail_enabled=True,
        smtp_host="smtp.feishu.cn",
        smtp_port=465,
        smtp_use_ssl=True,
        smtp_username="hr@example.com",
        smtp_password="client-secret",
        smtp_timeout=5,
        mail_from_name="人事行政",
        mail_company_name="昆明笨二娘餐饮管理有限公司",
        mail_reply_to="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _offer(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        candidate_name="李俊颖",
        job_title="AI落地工程师",
        store_name="南屏店",
        report_date=None,
        report_time="上午 9:00",
        work_location="昆明市西山区万达北塔2507",
        contact_name="方玺",
        contact_phone="17702348593",
        expires_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── 通道判定 ────────────────────────────────────────────────
def test_is_mail_ready_requires_switch_and_credentials():
    assert mailer.is_mail_ready(_settings()) is True
    assert mailer.is_mail_ready(_settings(candidate_mail_enabled=False)) is False
    assert mailer.is_mail_ready(_settings(smtp_username="")) is False
    assert mailer.is_mail_ready(_settings(smtp_password="")) is False
    assert mailer.is_mail_ready(_settings(smtp_host="")) is False


# ── send_mail 的降级与保护 ──────────────────────────────────
def test_send_mail_skips_when_not_ready():
    ok, err = mailer.send_mail("a@b.com", "t", "<p>x</p>", settings=_settings(candidate_mail_enabled=False))
    assert ok is False
    assert "未启用" in err


def test_send_mail_rejects_invalid_recipient():
    ok, err = mailer.send_mail("not-an-email", "t", "<p>x</p>", settings=_settings())
    assert ok is False
    assert "收件邮箱无效" in err


def test_send_mail_never_raises_on_smtp_error():
    """SMTP 抛异常时必须降级为 (False, 原因)，不得向上抛。"""
    with patch("smtplib.SMTP_SSL", side_effect=OSError("connection refused")):
        ok, err = mailer.send_mail("a@b.com", "t", "<p>x</p>", settings=_settings())
    assert ok is False
    assert "发送失败" in err


def test_send_mail_success_path():
    """成功路径：登录 + 发送被调用，返回 (True, "")。"""
    server = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = server
    with patch("smtplib.SMTP_SSL", return_value=cm):
        ok, err = mailer.send_mail(
            "candidate@example.com", "主题", "<p>正文</p>", settings=_settings()
        )
    assert ok is True
    assert err == ""
    server.login.assert_called_once_with("hr@example.com", "client-secret")
    assert server.sendmail.call_count == 1
    # 收件人是候选人
    assert server.sendmail.call_args[0][1] == ["candidate@example.com"]


def test_send_mail_builds_from_and_reply_to():
    """From = 显示名 + 登录账号（同域），Reply-To 默认回落到登录账号。"""
    server = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = server
    with patch("smtplib.SMTP_SSL", return_value=cm):
        ok, _ = mailer.send_mail(
            "candidate@example.com", "主题", "<p>正文</p>", settings=_settings()
        )
    assert ok is True
    msg = message_from_string(server.sendmail.call_args[0][2])
    from_display = str(make_header(decode_header(msg["From"])))
    assert from_display == "人事行政 <hr@example.com>"
    assert msg["Reply-To"] == "hr@example.com"


def test_send_mail_reply_to_override():
    """显式配置 MAIL_REPLY_TO 时优先于发件地址。"""
    server = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = server
    with patch("smtplib.SMTP_SSL", return_value=cm):
        mailer.send_mail(
            "candidate@example.com",
            "主题",
            "<p>正文</p>",
            settings=_settings(mail_reply_to="hr@benerniang.com"),
        )
    msg = message_from_string(server.sendmail.call_args[0][2])
    assert msg["Reply-To"] == "hr@benerniang.com"
    assert "hr@example.com" in msg["From"]  # 发件地址不被 Reply-To 改写


def test_company_name_is_independent_from_display_name():
    """显示名只是「人事行政」，主题里仍应是公司全称。"""
    subject, _ = mailer.build_offer_accepted_mail(
        candidate_name="李俊颖",
        job_title="AI落地工程师",
        store_name="南屏店",
        report_date=None,
        settings=_settings(),
    )
    assert "昆明笨二娘餐饮管理有限公司" in subject
    assert "人事行政" not in subject


def test_company_name_falls_back_to_legacy_display_name():
    """未配 MAIL_COMPANY_NAME 时，兼容旧格式「公司 · 部门」显示名。"""
    s = _settings(mail_company_name="", mail_from_name="某某餐饮 · 招聘组")
    assert mailer._company_name(s) == "某某餐饮"


# ── 模板内容 ────────────────────────────────────────────────
def test_invite_mail_contains_link_and_deadline():
    subject, html = mailer.build_offer_invite_mail(
        candidate_name="李俊颖",
        job_title="AI落地工程师",
        store_name="南屏店",
        report_date=None,
        link="https://hire.example.com/o/tok123",
        deadline="9月23日20:00",
        settings=_settings(),
    )
    assert "录用" in subject
    assert "李俊颖" in html
    assert "AI落地工程师" in html
    assert "南屏店" in html
    assert "https://hire.example.com/o/tok123" in html
    assert "9月23日20:00" in html
    assert "查看并接受 Offer" in html


def test_invite_mail_contains_full_offer_letter():
    """第一封邮件必须包含完整《录用通知书》信息，供候选人保存。"""
    subject, html = mailer.build_offer_invite_mail(
        candidate_name="李俊颖",
        job_title="AI落地工程师",
        store_name="南屏店",
        report_date=None,
        link="https://hire.example.com/o/tok123",
        deadline="9月23日20:00",
        report_time="上午 9:00",
        work_location="昆明市西山区万达北塔2507",
        contact_name="方玺",
        contact_phone="17702348593",
        probation_phrase="试用期为一个月",
        probation_suffix="，可视情况提前转正",
        probation_salary="4000 元/月",
        regular_salary="4500 元/月",
        materials=mailer.REPORT_MATERIALS,
        legal_note="本通知为录用确认，具体权利义务以正式劳动合同为准。",
        company_name="昆明笨二娘餐饮管理有限公司",
        letter_date="2026年10月17日",
        settings=_settings(),
    )
    assert "录用" in subject
    for text in [
        "工作地点",
        "昆明市西山区万达北塔2507",
        "联系人",
        "方玺",
        "17702348593",
        "试用期为一个月",
        "4000 元/月",
        "4500 元/月",
        "报到时间",
        "上午 9:00",
        "报到时请携带以下资料",
        "本通知为录用确认",
        "昆明笨二娘餐饮管理有限公司",
        "2026年10月17日",
    ]:
        assert text in html
    for material in mailer.REPORT_MATERIALS:
        assert material in html


def test_invite_mail_escapes_html_in_fields():
    """动态字段需转义，避免姓名/岗位里的 HTML 注入邮件正文。"""
    _, html = mailer.build_offer_invite_mail(
        candidate_name="<script>alert(1)</script>",
        job_title="A & B",
        store_name="门店",
        report_date=None,
        link="https://x/o/t",
        settings=_settings(),
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html


def test_accepted_mail_lists_all_materials():
    subject, html = mailer.build_offer_accepted_mail(
        candidate_name="李俊颖",
        job_title="AI落地工程师",
        store_name="南屏店",
        report_date=None,
        report_time="上午 9:00",
        work_location="昆明市西山区万达北塔2507",
        contact_name="方玺",
        contact_phone="17702348593",
        settings=_settings(),
    )
    assert "入职" in subject
    for material in mailer.REPORT_MATERIALS:
        assert material in html
    assert "报到时请携带以下材料" in html
    assert "17702348593" in html


def test_mail_shell_has_no_external_assets():
    """邮件外壳必须内联样式、不引外链资源（部分客户端会拦截外链）。"""
    _, html = mailer.build_offer_invite_mail(
        candidate_name="张三",
        job_title="服务员",
        store_name="门店",
        report_date=None,
        link="https://x/o/t",
        settings=_settings(),
    )
    assert "<style" not in html
    assert "<img" not in html
    assert "http" in html  # 仅允许 CTA 链接本身


# ── offer_notify 入口函数的降级 ─────────────────────────────
def test_mail_offer_invite_skips_without_email():
    out = mail_offer_invite(_offer(), link="https://x/o/t", candidate_email="", settings=_settings())
    assert out["status"] == "skipped"
    assert "未留邮箱" in out["reason"]


def test_mail_offer_invite_skips_when_channel_disabled():
    out = mail_offer_invite(
        _offer(),
        link="https://x/o/t",
        candidate_email="a@b.com",
        settings=_settings(candidate_mail_enabled=False),
    )
    assert out["status"] == "skipped"
    assert "未启用" in out["reason"]


def test_mail_offer_invite_sends_when_ready():
    with patch.object(mailer, "send_mail", return_value=(True, "")) as mocked:
        out = mail_offer_invite(
            _offer(), link="https://x/o/t", candidate_email="a@b.com", settings=_settings()
        )
    assert out["status"] == "sent"
    assert out["to"] == "a@b.com"
    assert mocked.call_count == 1


def test_mail_offer_accepted_reports_failure():
    with patch.object(mailer, "send_mail", return_value=(False, "550 rejected")):
        out = mail_offer_accepted(_offer(), candidate_email="a@b.com", settings=_settings())
    assert out["status"] == "failed"
    assert "550" in out["reason"]
