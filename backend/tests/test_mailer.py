"""候选人邮件模块单测。

覆盖：
- 通道判定（开关 + SMTP 账号密码的组合）
- 未配置 / 未启用时**静默跳过**且**不抛异常**（不能拖垮审批与接受主流程）
- 非法收件邮箱的保护
- 两封邮件的模板内容（候选人姓名、接受链接、5 项报到材料）
- offer_notify 两个入口函数在「无邮箱」时的降级
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.services.mailer as mailer
from app.services.offer_notify import mail_offer_accepted, mail_offer_invite


def _settings(**kw):
    base = dict(
        candidate_mail_enabled=True,
        smtp_host="smtp.exmail.qq.com",
        smtp_port=465,
        smtp_use_ssl=True,
        smtp_username="hr@example.com",
        smtp_password="client-secret",
        smtp_timeout=5,
        mail_from_name="昆明笨二娘餐饮管理有限公司 · 招聘组",
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
