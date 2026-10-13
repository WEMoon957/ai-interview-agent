"""飞书 HR 通知单元测试。

覆盖：阈值判定、消息构造、幂等/未达标/未启用分支、多收件人部分成功、
lark-cli 输出契约解析（ok==true 才成功）、cli 路径解析。
"""
import json

import pytest

import app.services.feishu_notify as fn
from app.config import Settings


def make_settings(**overrides) -> Settings:
    """构造仅含飞书通知字段的测试配置。

    显式传 ``_env_file=None`` 隔离真实 .env，避免本地配置渗入单测
    （否则会读取到真实收件人、甚至真的发起 lark-cli 调用）。
    """
    base = dict(
        feishu_notify_enabled=True,
        feishu_notify_threshold=65.0,
        feishu_notify_transport="cli",
        feishu_hr_open_ids="ou_test_hr",
        feishu_hr_department_ids="",
        feishu_lark_cli_path="",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_notify_timeout=5.0,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


class FakeReport:
    """SessionReport 的最小替身。"""

    def __init__(self, total_score=78.5, verdict="推荐", summary="整体匹配度较高", dim_scores=None):
        self.total_score = total_score
        self.verdict = verdict
        self.summary = summary
        self.dim_scores = dim_scores if dim_scores is not None else [
            {"key": "tech_depth", "name": "技术深度", "weight": 0.4, "score": 8.5, "weighted_score": 3.4},
        ]
        self.notified_at = None


# ────────────────────────────── 阈值判定 ──────────────────────────────


@pytest.mark.parametrize(
    "score,threshold,expected",
    [
        (65.0, 65.0, True),
        (65.01, 65.0, True),
        (64.99, 65.0, False),
        (100, 65.0, True),
        ("70", 65.0, True),  # 字符串数字可解析
        (None, 65.0, False),
        ("abc", 65.0, False),
    ],
)
def test_should_notify(score, threshold, expected):
    assert fn.should_notify(score, threshold) is expected


# ────────────────────────────── 消息构造 ──────────────────────────────


def test_build_notification_text_contains_key_fields():
    report = {
        "total_score": 78.5,
        "verdict": "推荐",
        "summary": "整体匹配度较高，可按流程推进后续环节。",
        "dim_scores": [{"key": "tech_depth", "name": "技术深度", "score": 8.5}],
    }
    text = fn.build_notification_text("张三", "sess-123", report, 65.0)

    assert "张三" in text
    assert "78.5" in text
    assert "推荐" in text
    assert "sess-123" in text
    assert "技术深度" in text
    assert "≥ 65" in text
    assert "下一轮面试" in text


def test_build_notification_text_handles_missing_name_and_dims():
    report = {"total_score": 70, "verdict": "推荐", "summary": "", "dim_scores": []}
    text = fn.build_notification_text("", "sess-1", report, 65.0)
    assert "未填写姓名" in text
    assert "维度得分" not in text  # 无维度时不渲染该段


# ────────────────────────────── 编排：达标 -> 发送 -> 置位 ──────────────────────────────


def test_notify_skipped_when_disabled():
    row = FakeReport()
    result = fn.notify_report_qualified(
        row, session_id="s1", settings=make_settings(feishu_notify_enabled=False)
    )
    assert result["status"] == "skipped"
    assert row.notified_at is None


def test_notify_skipped_when_below_threshold():
    row = FakeReport(total_score=64.9)
    result = fn.notify_report_qualified(row, session_id="s1", settings=make_settings())
    assert result["status"] == "skipped"
    assert "未达阈值" in result["reason"]
    assert row.notified_at is None


def test_notify_skipped_when_already_notified():
    row = FakeReport()
    row.notified_at = object()  # 非空即视为已推送
    called = {"n": 0}

    def sender(open_id, text, settings):
        called["n"] += 1
        return True, ""

    result = fn.notify_report_qualified(row, session_id="s1", settings=make_settings(), sender=sender)
    assert result["status"] == "skipped"
    assert called["n"] == 0


def test_notify_sent_sets_notified_at():
    row = FakeReport(total_score=78.5)
    captured = {}

    def sender(open_id, text, settings):
        captured["open_id"] = open_id
        captured["text"] = text
        return True, ""

    result = fn.notify_report_qualified(
        row, session_id="s1", candidate_name="李四", settings=make_settings(), sender=sender
    )
    assert result["status"] == "sent"
    assert result["recipients"] == ["ou_test_hr"]
    assert row.notified_at is not None
    assert captured["open_id"] == "ou_test_hr"
    assert "李四" in captured["text"]


def test_notify_failed_keeps_notified_at_none():
    row = FakeReport(total_score=80.0)
    result = fn.notify_report_qualified(
        row, session_id="s1", settings=make_settings(), sender=lambda *_: (False, "230013 无可用范围")
    )
    assert result["status"] == "failed"
    assert "230013" in result["reason"]
    assert row.notified_at is None  # 失败不置位，允许后续重试


def test_notify_sender_exception_is_isolated():
    row = FakeReport(total_score=80.0)

    def boom(*_):
        raise RuntimeError("网络中断")

    result = fn.notify_report_qualified(row, session_id="s1", settings=make_settings(), sender=boom)
    assert result["status"] == "failed"
    assert row.notified_at is None


# ────────────────────────────── 多收件人 ──────────────────────────────


def test_send_notification_multiple_partial_success():
    settings = make_settings(feishu_hr_open_ids="ou_a, ou_b")

    def sender(open_id, text, settings):
        return (open_id == "ou_b"), ("boom" if open_id == "ou_a" else "")

    ok, recipients, err = fn.send_notification("hi", settings=settings, sender=sender)
    assert ok is True  # 任一人成功即成功
    assert recipients == ["ou_a", "ou_b"]
    assert "ou_a" in err


def test_send_notification_without_recipients():
    settings = make_settings(feishu_hr_open_ids="")
    ok, recipients, err = fn.send_notification("hi", settings=settings, sender=lambda *_: (True, ""))
    assert ok is False
    assert recipients == []
    assert "未解析到任何 HR 收件人" in err


# ────────────────────────────── 部门解析（按部门收件） ──────────────────────────────


@pytest.mark.parametrize(
    "status,expected",
    [
        ({"is_activated": True}, True),
        ({}, True),  # 字段缺失按可用处理
        ({"is_activated": True, "is_resigned": True}, False),
        ({"is_activated": True, "is_exited": True}, False),
        ({"is_activated": True, "is_frozen": True}, False),
        ({"is_activated": False}, False),
    ],
)
def test_is_active_member(status, expected):
    assert fn.is_active_member({"status": status}) is expected


def test_resolve_department_member_ids_filters_and_paginates(monkeypatch):
    """两页数据：过滤离职成员，翻页正确累加。"""
    pages = [
        {
            "items": [
                {"open_id": "ou_1", "status": {"is_activated": True}},
                {"open_id": "ou_gone", "status": {"is_activated": True, "is_resigned": True}},
            ],
            "has_more": True,
            "page_token": "t2",
        },
        {
            "items": [{"open_id": "ou_2", "status": {"is_activated": True}}],
            "has_more": False,
        },
    ]
    calls = {"n": 0}

    def fake_run(path, params, settings):
        page = pages[calls["n"]]
        calls["n"] += 1
        return True, page

    monkeypatch.setattr(fn, "_run_cli_api", fake_run)
    ids, err = fn.resolve_department_member_ids(["od_x"], make_settings())
    assert ids == ["ou_1", "ou_2"]
    assert err == ""
    assert calls["n"] == 2  # 翻了两页


def test_resolve_department_member_ids_isolates_failure(monkeypatch):
    monkeypatch.setattr(fn, "_run_cli_api", lambda *a, **k: (False, "权限不足"))
    ids, err = fn.resolve_department_member_ids(["od_x"], make_settings())
    assert ids == []
    assert "权限不足" in err


def test_resolve_recipients_merges_and_dedups(monkeypatch):
    settings = make_settings(
        feishu_hr_open_ids="ou_a,ou_dup",
        feishu_hr_department_ids="od_x",
    )
    monkeypatch.setattr(
        fn, "resolve_department_member_ids", lambda ids, s: (["ou_dup", "ou_b"], "")
    )
    recipients, err = fn.resolve_recipients(settings)
    # 显式 open_id 在前、部门成员在后，且去重保序
    assert recipients == ["ou_a", "ou_dup", "ou_b"]
    assert err == ""


def test_resolve_recipients_skips_department_when_unset(monkeypatch):
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        return [], ""

    monkeypatch.setattr(fn, "resolve_department_member_ids", boom)
    recipients, err = fn.resolve_recipients(make_settings(feishu_hr_open_ids="ou_a"))
    assert recipients == ["ou_a"]
    assert called["n"] == 0


def test_send_notification_uses_resolved_recipients(monkeypatch):
    settings = make_settings(feishu_hr_department_ids="od_x")
    monkeypatch.setattr(fn, "resolve_recipients", lambda s: (["ou_dept_1"], ""))

    sent = []

    def sender(open_id, text, settings):
        sent.append(open_id)
        return True, ""

    ok, recipients, err = fn.send_notification("hi", settings=settings, sender=sender)
    assert ok is True
    assert sent == ["ou_dept_1"]
    assert recipients == ["ou_dept_1"]


# ────────────────────────────── lark-cli 传输 ──────────────────────────────


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_send_via_cli_success(monkeypatch):
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        fn.subprocess, "run", lambda *a, **k: _Proc(stdout=json.dumps({"ok": True}))
    )
    ok, err = fn.send_via_cli("ou_x", "hello", make_settings())
    assert ok is True and err == ""


def test_send_via_cli_error_envelope(monkeypatch):
    payload = {"ok": False, "error": {"code": 230013, "message": "Bot has NO availability to this user."}}
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(fn.subprocess, "run", lambda *a, **k: _Proc(stdout=json.dumps(payload)))
    ok, err = fn.send_via_cli("ou_x", "hello", make_settings())
    assert ok is False
    assert "230013" in err


def test_send_via_cli_missing_binary(monkeypatch):
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: None)
    ok, err = fn.send_via_cli("ou_x", "hello", make_settings())
    assert ok is False
    assert "lark-cli" in err


def test_send_via_cli_nonzero_exit(monkeypatch):
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        fn.subprocess, "run", lambda *a, **k: _Proc(stderr="segfault", returncode=1)
    )
    ok, err = fn.send_via_cli("ou_x", "hello", make_settings())
    assert ok is False
    assert "退出码" in err


def test_resolve_cli_path_prefers_configured(tmp_path):
    fake = tmp_path / "lark-cli"
    fake.write_text("#!/bin/sh\n")
    assert fn.resolve_cli_path(str(fake)) == str(fake)


def test_resolve_cli_path_nonexistent_falls_back(monkeypatch):
    monkeypatch.setattr(fn.shutil, "which", lambda _n: None)
    monkeypatch.setattr(fn.Path, "home", classmethod(lambda cls: __import__("pathlib").Path("/nonexistent-home-xyz")))
    assert fn.resolve_cli_path("/no/such/binary") is None


# ────────────────────────────── 配置派生 ──────────────────────────────


def test_settings_hr_open_id_list_parsing():
    s = make_settings(feishu_hr_open_ids=" ou_a ,ou_b ,, ou_c ")
    assert s.hr_open_id_list == ["ou_a", "ou_b", "ou_c"]


def test_settings_notify_ready_cli_and_api():
    assert make_settings().feishu_notify_ready is True
    # 未启用 -> False
    assert make_settings(feishu_notify_enabled=False).feishu_notify_ready is False
    # 无收件人 -> False
    assert make_settings(feishu_hr_open_ids="").feishu_notify_ready is False
    # 仅按部门收件（无显式 open_id）也应就绪
    assert (
        make_settings(feishu_hr_open_ids="", feishu_hr_department_ids="od_x").feishu_notify_ready
        is True
    )
    # api 传输缺凭据 -> False
    assert make_settings(feishu_notify_transport="api").feishu_notify_ready is False
    # api 传输凭据齐备 -> True
    assert (
        make_settings(
            feishu_notify_transport="api", feishu_app_id="cli_x", feishu_app_secret="sec"
        ).feishu_notify_ready
        is True
    )


def test_settings_hr_department_id_list_parsing():
    s = make_settings(feishu_hr_department_ids=" od_a ,, od_b ")
    assert s.hr_department_id_list == ["od_a", "od_b"]
