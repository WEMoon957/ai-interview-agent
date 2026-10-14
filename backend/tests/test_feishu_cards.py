"""飞书卡片推送单测：卡片构造、初筛推荐 / 汇总复核编排、卡片双传输。

全部用注入 sender / monkeypatch，不触达真实飞书，也不会打扰真实 HR。
"""
import json
from datetime import datetime, timezone

import pytest

import app.services.feishu_notify as fn
from app.config import Settings


def make_settings(**overrides) -> Settings:
    """仅含飞书通知字段的测试配置（_env_file=None 隔离真实 .env）。"""
    base = dict(
        feishu_notify_enabled=True,
        feishu_notify_threshold=65.0,
        feishu_screening_notify_enabled=True,
        feishu_screening_min_score=65.0,
        feishu_summary_notify_enabled=True,
        feishu_notify_max_items=20,
        feishu_notify_transport="cli",
        feishu_hr_open_ids="ou_test_hr",
        feishu_hr_department_ids="",
        feishu_lark_cli_path="",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_notify_timeout=5.0,
        # 待办相关（默认在测试里关闭，避免真建任务；单独用例再打开）
        feishu_task_enabled=False,
        feishu_task_assignee_open_ids="",
        feishu_task_identity="bot",
        feishu_task_due_days=2,
        public_base_url="http://localhost:3000",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _capture_sender():
    """返回 (sender, 调用记录)。记录每次发送的 (open_id, card)。"""
    calls = []

    def sender(open_id, card, settings):
        calls.append((open_id, card))
        return True, ""

    return sender, calls


def _screening_items():
    return [
        {"candidate_name": "张三", "total_score": 91.0, "recommendation": "强烈推荐", "highlights": ["5 年砧板经验", "无食安问题"]},
        {"candidate_name": "李四", "total_score": 55.0, "recommendation": "待定", "highlights": []},
        {"candidate_name": "王五", "total_score": 88.0, "recommendation": "强烈推荐", "highlights": ["可独立备菜"]},
    ]


def _summary_items():
    return [
        {"candidate_name": "张三", "final_score": 86.4, "final_verdict": "强烈推荐", "screening_score": 91.0, "ai_interview_score": 82.0, "offline_score": 88.0},
        {"candidate_name": "小李", "final_score": 58.2, "final_verdict": "待定", "screening_score": 78.0, "ai_interview_score": 52.0, "offline_score": 55.0},
        {"candidate_name": "小王", "final_score": 40.1, "final_verdict": "不推荐", "screening_score": 60.0, "ai_interview_score": 30.0, "offline_score": 35.0},
    ]


# ────────────────────────────── 卡片构造 ──────────────────────────────


def test_build_card_structure():
    card = fn.build_card("标题", "blue", ["段一", "段二"])
    assert card["config"]["wide_screen_mode"] is True
    assert card["header"]["template"] == "blue"
    assert card["header"]["title"]["content"] == "标题"
    # 两段之间插入一条 hr 分隔线
    tags = [el["tag"] for el in card["elements"]]
    assert tags == ["div", "hr", "div"]
    assert card["elements"][0]["text"]["tag"] == "lark_md"


def test_card_to_markdown_preview():
    card = fn.build_card("标题", "blue", ["段一", "段二"])
    md = fn.card_to_markdown(card)
    assert "【标题】" in md and "段一" in md and "段二" in md


def test_build_screening_card_contains_ranked_names_and_hint():
    card = fn.build_screening_card(
        job_title="后厨帮厨",
        folder_name="候选人简历",
        total=3,
        succeeded=3,
        recommended=_screening_items()[:1],
        min_score=65.0,
    )
    md = fn.card_to_markdown(card)
    assert "后厨帮厨" in md
    assert "张三" in md and "91.0" in md
    assert "强烈推荐" in md
    assert "≥ 65" in md
    assert "批量发起 AI 面试链接" in md


def test_build_screening_card_caps_items():
    many = [
        {"candidate_name": f"候选人{i}", "total_score": 90 - i, "recommendation": "推荐", "highlights": []}
        for i in range(30)
    ]
    card = fn.build_screening_card(
        job_title="岗位", folder_name="", total=30, succeeded=30, recommended=many, min_score=65, max_items=5
    )
    md = fn.card_to_markdown(card)
    assert "候选人0" in md
    assert "候选人9" not in md
    assert "另有 25 人未列出" in md


def test_build_summary_card_splits_offer_and_review():
    card = fn.build_summary_card(
        job_title="店员",
        offer_items=[_summary_items()[0]],
        review_items=_summary_items()[1:],
        snapshot_at="2026-10-13 17:00",
    )
    md = fn.card_to_markdown(card)
    assert "建议发放 offer" in md
    assert "建议复核后决定" in md
    assert "offer 发放" in md and "入职手续" in md
    assert "张三" in md and "小李" in md and "小王" in md


# ────────────────────────────── 初筛推荐编排 ──────────────────────────────


def test_screening_notify_skipped_when_disabled():
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="", items=_screening_items(),
        settings=make_settings(feishu_screening_notify_enabled=False),
        sender=lambda *_: (True, ""),
    )
    assert result["status"] == "skipped"
    assert "未启用" in result["reason"]


def test_screening_notify_skipped_when_nobody_qualified():
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="",
        items=[{"candidate_name": "低分", "total_score": 40.0}],
        settings=make_settings(), sender=lambda *_: (True, ""),
    )
    assert result["status"] == "skipped"
    assert "无初筛分" in result["reason"]


def test_screening_notify_filters_sorts_and_sends_card():
    sender, calls = _capture_sender()
    result = fn.notify_screening_recommendations(
        job_title="后厨帮厨", folder_name="候选人简历",
        items=_screening_items(), total=3, succeeded=3,
        settings=make_settings(), sender=sender,
    )
    assert result["status"] == "sent"
    assert result["recommended"] == 2  # 55 分被过滤
    assert result["recipients"] == ["ou_test_hr"]
    assert len(calls) == 1
    open_id, card = calls[0]
    assert open_id == "ou_test_hr"
    assert card["header"]["template"] == "blue"
    # 卡片内按分数降序：张三(91) 在 王五(88) 之前
    content = fn.card_to_markdown(card)
    assert content.index("张三") < content.index("王五")
    assert "李四" not in content


def test_screening_notify_failure_reports_reason():
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="", items=_screening_items(),
        settings=make_settings(),
        sender=lambda *_: (False, "230013 机器人可用范围不足"),
    )
    assert result["status"] == "failed"
    assert "230013" in result["reason"]


def test_screening_notify_custom_min_score_overrides_settings():
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="", items=_screening_items(),
        settings=make_settings(), sender=lambda *_: (True, ""), min_score=90.0,
    )
    assert result["status"] == "sent"
    assert result["recommended"] == 1  # 仅 91 分达标


# ────────────────────────────── 汇总复核编排 ──────────────────────────────


def test_summary_notify_skipped_when_disabled():
    result = fn.notify_summary_review(
        job_title="岗位", items=_summary_items(),
        settings=make_settings(feishu_summary_notify_enabled=False),
        sender=lambda *_: (True, ""),
    )
    assert result["status"] == "skipped"
    assert "未启用" in result["reason"]


def test_summary_notify_skipped_when_no_items():
    result = fn.notify_summary_review(
        job_title="岗位", items=[], settings=make_settings(), sender=lambda *_: (True, "")
    )
    assert result["status"] == "skipped"
    assert "无可复核候选人" in result["reason"]


def test_summary_notify_sends_orange_card_with_offer_and_review_sections():
    sender, calls = _capture_sender()
    result = fn.notify_summary_review(
        job_title="店员", items=_summary_items(), settings=make_settings(),
        sender=sender, snapshot_at="2026-10-13 17:00",
    )
    assert result["status"] == "sent"
    assert result["reviewed"] == 3
    _, card = calls[0]
    assert card["header"]["template"] == "orange"
    md = fn.card_to_markdown(card)
    assert "建议发放 offer" in md
    assert "suggest" not in md
    assert "张三" in md
    assert "86.4" in md
    assert "入职手续" in md


# ────────────────────────────── 卡片双传输 ──────────────────────────────


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_send_card_via_cli_uses_interactive_msg_type(monkeypatch):
    captured = {}
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(stdout=json.dumps({"ok": True}))

    monkeypatch.setattr(fn.subprocess, "run", fake_run)

    card = fn.build_card("标题", "blue", ["内容"])
    ok, err = fn.send_card_via_cli("ou_x", card, make_settings())
    assert ok is True and err == ""

    cmd = captured["cmd"]
    assert "--msg-type" in cmd and cmd[cmd.index("--msg-type") + 1] == "interactive"
    payload = json.loads(cmd[cmd.index("--content") + 1])
    assert payload["header"]["title"]["content"] == "标题"


def test_send_card_via_api_posts_interactive_card(monkeypatch):
    import httpx

    captured = {}

    class _Resp:
        def json(self):
            return {"code": 0, "data": {"message_id": "om_2"}}

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        captured.update(url=url, params=params, headers=headers, body=json)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(fn, "get_tenant_access_token", lambda s: "t-x")

    card = fn.build_card("招聘汇总完成", "orange", ["内容"])
    ok, err = fn.send_card_via_api(
        "ou_x", card,
        make_settings(feishu_notify_transport="api", feishu_app_id="cli_x", feishu_app_secret="sec"),
    )
    assert ok is True and err == ""
    assert captured["body"]["msg_type"] == "interactive"
    assert json.loads(captured["body"]["content"])["header"]["template"] == "orange"


def test_send_card_without_recipients():
    ok, recipients, err = fn.send_card(
        fn.build_card("t", "blue", ["b"]),
        settings=make_settings(feishu_hr_open_ids=""),
        sender=lambda *_: (True, ""),
    )
    assert ok is False
    assert recipients == []
    assert "未解析到任何 HR 收件人" in err


def test_send_card_isolates_single_recipient_failure():
    settings = make_settings(feishu_hr_open_ids="ou_a,ou_b")

    def sender(open_id, card, settings):
        return (open_id == "ou_b"), ("boom" if open_id == "ou_a" else "")

    ok, recipients, err = fn.send_card(fn.build_card("t", "blue", ["b"]), settings=settings, sender=sender)
    assert ok is True  # 任一人成功即成功
    assert recipients == ["ou_a", "ou_b"]
    assert "ou_a" in err


def test_send_card_sender_exception_is_isolated():
    def boom(*_):
        raise RuntimeError("网络中断")

    ok, _, err = fn.send_card(
        fn.build_card("t", "blue", ["b"]), settings=make_settings(), sender=boom
    )
    assert ok is False
    assert "网络中断" in err


# ────────────────────────────── 降级与边界 ──────────────────────────────


@pytest.mark.parametrize("value,expected", [(None, "—"), ("abc", "—"), (88, "88.0"), (86.44, "86.4")])
def test_fmt_score(value, expected):
    assert fn._fmt_score(value) == expected


def test_screening_card_handles_missing_name_and_highlights():
    card = fn.build_screening_card(
        job_title="岗位", folder_name="", total=1, succeeded=1,
        recommended=[{"candidate_name": "", "total_score": 70, "recommendation": "", "highlights": []}],
        min_score=65,
    )
    md = fn.card_to_markdown(card)
    assert "未填写姓名" in md
    assert "亮点" not in md


# ────────────────────────────── 面试链接进卡片 ──────────────────────────────


def test_screening_card_includes_absolute_interview_links():
    items = [
        {"candidate_name": "张三", "total_score": 91.0, "recommendation": "强烈推荐",
         "highlights": [], "interview_path": "/i/tok-a"},
        {"candidate_name": "王五", "total_score": 88.0, "recommendation": "强烈推荐",
         "highlights": [], "interview_path": "/i/tok-b"},
    ]
    card = fn.build_screening_card(
        job_title="后厨帮厨", folder_name="", total=2, succeeded=2,
        recommended=items, min_score=65, base_url="http://host:3000",
    )
    md = fn.card_to_markdown(card)
    assert "http://host:3000/i/tok-a" in md
    assert "http://host:3000/i/tok-b" in md
    assert "可直接转发候选人答题" in md


def test_screening_card_keeps_absolute_url_as_is():
    card = fn.build_screening_card(
        job_title="岗位", folder_name="", total=1, succeeded=1,
        recommended=[{"candidate_name": "张三", "total_score": 80, "interview_path": "https://a.com/i/x"}],
        min_score=65, base_url="http://host:3000",
    )
    md = fn.card_to_markdown(card)
    assert "https://a.com/i/x" in md
    assert "http://host:3000/https" not in md


def test_screening_card_without_links_keeps_manual_hint():
    card = fn.build_screening_card(
        job_title="岗位", folder_name="", total=1, succeeded=1,
        recommended=[{"candidate_name": "张三", "total_score": 80, "highlights": []}],
        min_score=65, base_url="http://host:3000",
    )
    md = fn.card_to_markdown(card)
    assert "批量发起 AI 面试链接" in md
    assert "AI 面试链接：" not in md


# ────────────────────────────── 飞书待办（任务） ──────────────────────────────


def test_build_screening_task_title_and_desc():
    items = [
        {"candidate_name": "张三", "total_score": 91.0, "recommendation": "强烈推荐", "interview_path": "/i/tok-a"},
        {"candidate_name": "王五", "total_score": 88.0, "recommendation": "推荐", "interview_path": "/i/tok-b"},
    ]
    title, desc = fn.build_screening_task(
        job_title="后厨帮厨", recommended=items, min_score=65, base_url="http://host:3000"
    )
    assert "后厨帮厨" in title and "2 位" in title
    assert "初筛通过：2 人" in desc
    assert "http://host:3000/i/tok-a" in desc
    assert "约线下复试" in desc


def test_resolve_task_assignees_prefers_explicit_config():
    s = make_settings(feishu_task_assignee_open_ids="ou_a, ou_b")
    assert fn.resolve_task_assignees(s) == ["ou_a", "ou_b"]


def test_resolve_task_assignees_falls_back_to_recipients(monkeypatch):
    s = make_settings(feishu_task_assignee_open_ids="")
    monkeypatch.setattr(fn, "resolve_recipients", lambda st: (["ou_r1", "ou_r2"], ""))
    assert fn.resolve_task_assignees(s) == ["ou_r1", "ou_r2"]


def test_create_tasks_partial_success_and_isolation():
    s = make_settings(feishu_task_assignee_open_ids="ou_a,ou_b")
    seen = []

    def creator(assignee, summary, description, settings):
        seen.append(assignee)
        if assignee == "ou_a":
            raise RuntimeError("boom")  # 异常必须被隔离
        return True, ""

    ok, assignees, err = fn.create_tasks("S", "D", settings=s, creator=creator)
    assert ok is True  # 任一人成功即成功
    assert assignees == ["ou_a", "ou_b"]
    assert seen == ["ou_a", "ou_b"]
    assert "ou_a" in err


def test_create_tasks_without_assignees(monkeypatch):
    s = make_settings(feishu_task_assignee_open_ids="")
    monkeypatch.setattr(fn, "resolve_recipients", lambda st: ([], ""))
    ok, assignees, err = fn.create_tasks("S", "D", settings=s, creator=lambda *_: (True, ""))
    assert ok is False and assignees == []
    assert "未解析到任何待办负责人" in err


def test_create_task_via_cli_builds_expected_command(monkeypatch):
    captured = {}
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(stdout=json.dumps({"ok": True}))

    monkeypatch.setattr(fn.subprocess, "run", fake_run)

    ok, err = fn.create_task_via_cli("ou_x", "标题", "描述", make_settings())
    assert ok is True and err == ""
    cmd = captured["cmd"]
    assert cmd[:3] == ["/usr/local/bin/lark-cli", "task", "+create"]
    assert cmd[cmd.index("--as") + 1] == "bot"  # 默认应用身份
    assert cmd[cmd.index("--assignee") + 1] == "ou_x"
    assert cmd[cmd.index("--due") + 1] == "+2d"
    assert cmd[cmd.index("--summary") + 1] == "标题"


def test_create_task_via_cli_honors_user_identity(monkeypatch):
    """本机联调可切到 user 身份（应用未申请 task scope 时的变通）。"""
    captured = {}
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        fn.subprocess, "run", lambda cmd, **k: (captured.update(cmd=cmd), _Proc(stdout=json.dumps({"ok": True})))[1]
    )
    ok, _ = fn.create_task_via_cli("ou_x", "t", "d", make_settings(feishu_task_identity="user"))
    assert ok is True
    assert captured["cmd"][captured["cmd"].index("--as") + 1] == "user"


def test_create_task_via_api_posts_task_v2(monkeypatch):
    import httpx

    captured = {}

    class _Resp:
        def json(self):
            return {"code": 0, "data": {"guid": "t1"}}

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        captured.update(url=url, params=params, body=json)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(fn, "get_tenant_access_token", lambda s: "t-x")

    ok, err = fn.create_task_via_api(
        "ou_x", "标题", "描述",
        make_settings(feishu_notify_transport="api", feishu_app_id="cli_x", feishu_app_secret="sec"),
    )
    assert ok is True and err == ""
    assert captured["url"].endswith("/open-apis/task/v2/tasks")
    assert captured["params"] == {"user_id_type": "open_id"}
    assert captured["body"]["members"][0] == {"id": "ou_x", "type": "user", "role": "assignee"}
    assert captured["body"]["due"]["is_all_day"] is True
    assert captured["body"]["due"]["timestamp"].isdigit()


def test_due_timestamp_ms_is_in_the_future():
    ts = int(fn._due_timestamp_ms(2))
    assert ts > int(datetime.now(timezone.utc).timestamp() * 1000)


# ────────────────────────────── 初筛推送：带链接 + 建待办 ──────────────────────────────


def test_screening_notify_creates_task_when_enabled():
    sender, calls = _capture_sender()
    tasks = []

    def creator(assignee, summary, description, settings):
        tasks.append({"assignee": assignee, "summary": summary, "description": description})
        return True, ""

    items = [
        {"candidate_name": "张三", "total_score": 91.0, "recommendation": "强烈推荐",
         "highlights": [], "interview_path": "/i/tok-a"},
    ]
    result = fn.notify_screening_recommendations(
        job_title="后厨帮厨", folder_name="候选人简历", items=items, total=1, succeeded=1,
        settings=make_settings(feishu_task_enabled=True), sender=sender, creator=creator,
    )
    assert result["status"] == "sent"
    assert result["task"]["status"] == "created"
    assert result["task"]["assignees"] == ["ou_test_hr"]
    assert len(tasks) == 1
    assert "后厨帮厨" in tasks[0]["summary"]
    assert "http://localhost:3000/i/tok-a" in tasks[0]["description"]


def test_screening_notify_skips_task_when_disabled():
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="", items=_screening_items(),
        settings=make_settings(feishu_task_enabled=False), sender=lambda *_: (True, ""),
    )
    assert result["status"] == "sent"
    assert result["task"]["status"] == "skipped"


def test_screening_notify_keeps_sent_status_when_task_fails():
    """待办创建失败不影响「卡片已送达」的判定。"""
    def boom(*_):
        raise RuntimeError("task api down")

    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="", items=_screening_items(),
        settings=make_settings(feishu_task_enabled=True),
        sender=lambda *_: (True, ""), creator=boom,
    )
    assert result["status"] == "sent"
    assert result["task"]["status"] == "failed"


def test_screening_notify_uses_public_base_url_from_settings():
    sender, calls = _capture_sender()
    fn.notify_screening_recommendations(
        job_title="岗位", folder_name="",
        items=[{"candidate_name": "张三", "total_score": 90, "interview_path": "/i/tk"}],
        settings=make_settings(public_base_url="https://hire.example.com/"),
        sender=sender,
    )
    md = fn.card_to_markdown(calls[0][1])
    assert "https://hire.example.com/i/tk" in md  # 尾部斜杠已归一化


# ────────────────────────────── 部署防呆：本机基址告警 ──────────────────────────────


@pytest.mark.parametrize(
    "base,expected",
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:3000", True),
        ("http://0.0.0.0:3000", True),
        ("", True),
        ("http://81.70.1.2:3000", False),
        ("https://hire.example.com", False),
    ],
)
def test_looks_like_local_base(base, expected):
    assert fn.looks_like_local_base(base) is expected


def test_screening_notify_warns_when_base_is_local():
    """服务器部署忘改 PUBLIC_BASE_URL 时，应给出告警而不是静默发坏链接。"""
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="",
        items=[{"candidate_name": "张三", "total_score": 90, "interview_path": "/i/tk"}],
        settings=make_settings(public_base_url="http://localhost:3000"),
        sender=lambda *_: (True, ""),
    )
    assert result["status"] == "sent"
    assert "本机地址" in result["warning"]


def test_screening_notify_no_warning_with_public_base():
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="",
        items=[{"candidate_name": "张三", "total_score": 90, "interview_path": "/i/tk"}],
        settings=make_settings(public_base_url="http://81.70.1.2:3000"),
        sender=lambda *_: (True, ""),
    )
    assert result["status"] == "sent"
    assert result["warning"] == ""


def test_screening_notify_no_warning_when_no_links():
    """没生成链接时不该报基址告警（此时根本没有链接）。"""
    result = fn.notify_screening_recommendations(
        job_title="岗位", folder_name="",
        items=[{"candidate_name": "张三", "total_score": 90}],
        settings=make_settings(public_base_url="http://localhost:3000"),
        sender=lambda *_: (True, ""),
    )
    assert result["warning"] == ""


# ────────────────────────────── 启动配置自检 ──────────────────────────────


def _joined(msgs: list[str]) -> str:
    return " | ".join(msgs)


def test_diagnose_silent_when_notify_disabled():
    assert fn.diagnose_notify_config(make_settings(feishu_notify_enabled=False)) == []


def test_diagnose_warns_when_no_recipients():
    msgs = fn.diagnose_notify_config(
        make_settings(feishu_hr_open_ids="", feishu_hr_department_ids="", public_base_url="http://1.2.3.4:3000")
    )
    assert "未配置收件人" in _joined(msgs)


def test_diagnose_warns_cli_without_lark_cli(monkeypatch):
    """容器/服务器场景：默认 cli 传输但找不到 lark-cli -> 必须告警并给出改法。"""
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: None)
    msgs = fn.diagnose_notify_config(make_settings(public_base_url="http://1.2.3.4:3000"))
    text = _joined(msgs)
    assert "找不到 lark-cli" in text
    assert "FEISHU_NOTIFY_TRANSPORT=api" in text


def test_diagnose_ok_when_cli_available(monkeypatch):
    monkeypatch.setattr(fn, "resolve_cli_path", lambda _p: "/usr/local/bin/lark-cli")
    msgs = fn.diagnose_notify_config(make_settings(public_base_url="http://1.2.3.4:3000"))
    assert msgs == []


def test_diagnose_warns_api_without_credentials():
    msgs = fn.diagnose_notify_config(
        make_settings(feishu_notify_transport="api", public_base_url="http://1.2.3.4:3000")
    )
    assert "缺少 FEISHU_APP_ID" in _joined(msgs)


def test_diagnose_ok_with_api_credentials():
    msgs = fn.diagnose_notify_config(
        make_settings(
            feishu_notify_transport="api",
            feishu_app_id="cli_x",
            feishu_app_secret="sec",
            public_base_url="http://1.2.3.4:3000",
        )
    )
    assert msgs == []


def test_diagnose_warns_local_public_base():
    msgs = fn.diagnose_notify_config(
        make_settings(feishu_notify_transport="api", feishu_app_id="c", feishu_app_secret="s")
    )
    assert "PUBLIC_BASE_URL 仍为本机地址" in _joined(msgs)
