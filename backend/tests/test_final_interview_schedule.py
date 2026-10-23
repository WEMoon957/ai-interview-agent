"""终试链路：终面时间确认 + 老板推送单测。

覆盖：
- 老板确认/改期终面时间（仅 pending_final 可改；passed/failed 拒绝；可清空）；
- 决策时顺带记录终面时间；
- 序列化带出终面时间；
- 进入待终试时推送老板（收件人为 boss，不抄送 HR）；
- 未配置老板收件人 / 开关关闭时静默跳过，不报错；
- 卡片内容包含候选人、岗位、各段分与「待安排」。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.feishu_notify as fn
from app.exceptions import BizError
from app.services.final_interview import (
    FINAL_FAILED,
    FINAL_NONE,
    FINAL_PASSED,
    FINAL_PENDING,
    decide_final_interview,
    schedule_final_interview,
    serialize_for_boss,
)


def _summary(**kw):
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        job_id="00000000-0000-0000-0000-000000000002",
        candidate_id="00000000-0000-0000-0000-000000000003",
        candidate_name="王芳",
        screening_score=None,
        ai_interview_score=None,
        offline_score=80.0,
        final_score=78.5,
        final_verdict="推荐",
        is_complete=True,
        detail={},
        final_interview_status=FINAL_PENDING,
        final_interview_note="",
        final_interview_scheduled_at=None,
        final_interview_decided_by="",
        final_interview_decided_at=None,
        snapshot_at=datetime.now(timezone.utc),
        candidate=None,
        job=SimpleNamespace(title="店员"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── 终面时间 ────────────────────────────────────────────────
def test_schedule_sets_time_on_pending():
    row = _summary()
    when = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)
    schedule_final_interview(None, row, scheduled_at=when, operator_username="boss")
    assert row.final_interview_scheduled_at == when
    assert row.final_interview_decided_by == "boss"
    assert row.final_interview_status == FINAL_PENDING  # 只定时间，不改状态


def test_schedule_naive_time_assumed_utc():
    row = _summary()
    schedule_final_interview(None, row, scheduled_at=datetime(2026, 10, 25, 14, 0))
    assert row.final_interview_scheduled_at.tzinfo is not None


def test_schedule_can_clear():
    row = _summary(final_interview_scheduled_at=datetime.now(timezone.utc))
    schedule_final_interview(None, row, scheduled_at=None)
    assert row.final_interview_scheduled_at is None


@pytest.mark.parametrize("status", [FINAL_PASSED, FINAL_FAILED])
def test_schedule_rejected_after_decision(status):
    row = _summary(final_interview_status=status)
    with pytest.raises(BizError) as exc:
        schedule_final_interview(None, row, scheduled_at=datetime.now(timezone.utc))
    assert exc.value.http_status == 409


def test_schedule_rejected_when_not_pending():
    row = _summary(final_interview_status=FINAL_NONE)
    with pytest.raises(BizError):
        schedule_final_interview(None, row, scheduled_at=datetime.now(timezone.utc))


def test_decide_records_time_when_given():
    row = _summary()
    when = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    decide_final_interview(
        None, row, decision="pass", operator_username="boss", scheduled_at=when
    )
    assert row.final_interview_status == FINAL_PASSED
    assert row.final_interview_scheduled_at == when


def test_decide_keeps_existing_time_when_omitted():
    when = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    row = _summary(final_interview_scheduled_at=when)
    decide_final_interview(None, row, decision="pass", operator_username="boss")
    assert row.final_interview_scheduled_at == when


def test_serialize_includes_scheduled_at():
    when = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    data = serialize_for_boss(_summary(final_interview_scheduled_at=when))
    assert data["final_interview_scheduled_at"] == when.isoformat()


def test_serialize_scheduled_at_none():
    assert serialize_for_boss(_summary())["final_interview_scheduled_at"] is None


# ── 老板推送 ────────────────────────────────────────────────
def _notify_settings(**kw):
    base = dict(
        feishu_final_pending_notify_enabled=True,
        feishu_final_scheduled_notify_enabled=True,
        feishu_final_passed_notify_enabled=True,
        feishu_notify_ready=True,
        feishu_task_enabled=False,
        feishu_notify_max_items=20,
        public_base_url_normalized="http://example.com",
        boss_open_id_list=["ou_boss"],
        offer_confirm_tz_offset=8,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_build_card_shows_pending_and_scores():
    card = fn.build_final_pending_card(
        items=[
            {
                "candidate_name": "王芳",
                "job_title": "店员",
                "final_score": 78.5,
                "screening_score": 70,
                "ai_interview_score": 72,
                "offline_score": 80,
                "final_interview_scheduled_at": "",
            }
        ],
        base_url="http://example.com",
    )
    body = "\n".join(
        el.get("text", {}).get("content", "") for el in card["elements"] if el.get("tag") == "div"
    )
    assert "王芳" in body and "店员" in body
    assert "待安排" in body
    assert card["header"]["title"]["content"].startswith("待终试")


def test_notify_sends_to_boss_only():
    captured = {}

    def fake_send(card, **kw):
        captured["card"] = card
        captured["recipients"] = kw.get("recipients")
        return True, kw.get("recipients") or [], ""

    with patch.object(fn, "send_card", side_effect=fake_send):
        res = fn.notify_final_pending(
            items=[{"candidate_name": "王芳", "job_title": "店员"}],
            settings=_notify_settings(),
        )

    assert res["status"] == "sent"
    assert captured["recipients"] == ["ou_boss"]  # 只发老板，不抄送 HR


def test_notify_skipped_without_boss_ids():
    res = fn.notify_final_pending(
        items=[{"candidate_name": "王芳"}],
        settings=_notify_settings(boss_open_id_list=[]),
    )
    assert res["status"] == "skipped"
    assert "FEISHU_BOSS_OPEN_IDS" in res["reason"]


def test_notify_skipped_when_disabled():
    res = fn.notify_final_pending(
        items=[{"candidate_name": "王芳"}],
        settings=_notify_settings(feishu_final_pending_notify_enabled=False),
    )
    assert res["status"] == "skipped"


def test_notify_creates_task_for_boss():
    with (
        patch.object(fn, "send_card", return_value=(True, ["ou_boss"], "")),
        patch.object(
            fn, "create_tasks", return_value=(True, ["ou_boss"], "")
        ) as create,
    ):
        res = fn.notify_final_pending(
            items=[{"candidate_name": "王芳", "job_title": "店员"}],
            settings=_notify_settings(feishu_task_enabled=True),
        )

    assert res["task"]["status"] == "created"
    assert create.call_args.kwargs["assignees"] == ["ou_boss"]


# ── 终面时间推 HR ───────────────────────────────────────────
def _card_body(card):
    return "\n".join(
        el.get("text", {}).get("content", "")
        for el in card["elements"]
        if el.get("tag") == "div"
    )


def test_fmt_local_time_converts_utc_to_beijing():
    assert fn._fmt_local_time("2026-10-25T06:00:00+00:00", 8) == "2026-10-25 14:00"
    assert fn._fmt_local_time(None) == ""
    assert fn._fmt_local_time("not-a-time") == "not-a-time"


def test_build_final_scheduled_card_set():
    card = fn.build_final_scheduled_card(
        action=fn.FINAL_SCHEDULE_SET,
        candidate_name="王芳",
        job_title="店员",
        scheduled_at=datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc),
        note="门店二楼",
        final_score=78.5,
        screening_score=70,
        ai_interview_score=72,
        offline_score=80,
        operator="boss",
        base_url="http://example.com",
    )
    body = _card_body(card)
    assert card["header"]["title"]["content"] == "终面时间已确认"
    assert "王芳" in body and "店员" in body
    assert "2026-10-25 14:00" in body  # UTC 06:00 → 北京时间 14:00
    assert "门店二楼" in body and "boss" in body
    assert "通知候选人" in body
    assert card["elements"][-1]["tag"] == "action"  # 带跳转按钮


def test_build_final_scheduled_card_update_and_clear():
    updated = fn.build_final_scheduled_card(
        action=fn.FINAL_SCHEDULE_UPDATE,
        candidate_name="王芳",
        scheduled_at=datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc),
    )
    assert updated["header"]["title"]["content"] == "终面时间已改期"
    assert "2026-10-26 10:00" in _card_body(updated)

    cleared = fn.build_final_scheduled_card(
        action=fn.FINAL_SCHEDULE_CLEAR, candidate_name="王芳", job_title="店员"
    )
    assert cleared["header"]["title"]["content"] == "终面时间已取消"
    assert "已取消" in _card_body(cleared)


def test_notify_final_scheduled_sends_to_hr_by_default():
    captured = {}

    def fake_send(card, **kw):
        captured["card"] = card
        captured["recipients"] = kw.get("recipients")
        return True, ["ou_hr"], ""

    row = _summary(
        final_interview_scheduled_at=datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
    )
    with patch.object(fn, "send_card", side_effect=fake_send):
        res = fn.notify_final_scheduled(
            summary=row,
            action=fn.FINAL_SCHEDULE_SET,
            operator="boss",
            settings=_notify_settings(),
        )

    assert res["status"] == "sent"
    assert captured["recipients"] is None  # 走默认 HR 收件人
    assert "终面时间已确认" == captured["card"]["header"]["title"]["content"]


def test_notify_final_scheduled_skipped_when_disabled():
    res = fn.notify_final_scheduled(
        summary=_summary(),
        settings=_notify_settings(feishu_final_scheduled_notify_enabled=False),
    )
    assert res["status"] == "skipped"


def test_notify_final_scheduled_failed_keeps_reason():
    with patch.object(fn, "send_card", return_value=(False, ["ou_hr"], "boom")):
        res = fn.notify_final_scheduled(
            summary=_summary(), settings=_notify_settings()
        )
    assert res["status"] == "failed"
    assert "boom" in res["reason"]


# ── 路由层：只在时间真的变化时推送 ──────────────────────────
@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (None, datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc), fn.FINAL_SCHEDULE_SET),
        (
            datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc),
            fn.FINAL_SCHEDULE_UPDATE,
        ),
        (datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc), None, fn.FINAL_SCHEDULE_CLEAR),
    ],
)
def test_route_maps_schedule_change_to_action(previous, current, expected):
    from app.api.final_interview import _notify_schedule_change

    row = _summary(final_interview_scheduled_at=current)
    with patch(
        "app.api.final_interview.notify_final_scheduled",
        return_value={"status": "sent"},
    ) as notify:
        _notify_schedule_change(row, previous, "boss")
    assert notify.call_args.kwargs["action"] == expected


def test_route_skips_push_when_time_unchanged():
    from app.api.final_interview import _notify_schedule_change

    when = datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
    row = _summary(final_interview_scheduled_at=when)
    with patch("app.api.final_interview.notify_final_scheduled") as notify:
        _notify_schedule_change(row, when, "boss")
    notify.assert_not_called()


def test_route_swallows_notify_exception():
    from app.api.final_interview import _notify_schedule_change

    row = _summary(
        final_interview_scheduled_at=datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc)
    )
    with patch(
        "app.api.final_interview.notify_final_scheduled",
        side_effect=RuntimeError("boom"),
    ):
        _notify_schedule_change(row, None, "boss")  # 不应抛出


# ── 终试通过推 HR（含全局抄送）────────────────────────────────
def test_notify_final_passed_sends_to_hr_by_default():
    captured = {}

    def fake_send(card, **kw):
        captured["card"] = card
        captured["recipients"] = kw.get("recipients")
        return True, ["ou_hr", "ou_observer"], ""

    row = _summary(
        final_interview_status=FINAL_PASSED,
        final_interview_scheduled_at=datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc),
    )
    with patch.object(fn, "send_card", side_effect=fake_send):
        res = fn.notify_final_passed(
            summary=row, operator="boss", settings=_notify_settings()
        )

    assert res["status"] == "sent"
    # recipients 留空才会并入 FEISHU_OBSERVER_OPEN_IDS，定向发送会绕过全局抄送
    assert captured["recipients"] is None
    assert captured["card"]["header"]["title"]["content"] == fn.FINAL_PASSED_TITLE


def test_final_passed_card_carries_candidate_and_next_step():
    card = fn.build_final_passed_card(
        candidate_name="王芳",
        job_title="店员",
        final_score=78.5,
        offline_score=80.0,
        operator="boss",
        base_url="http://example.com",
    )
    text = str(card)
    assert "王芳" in text and "店员" in text
    assert "78.5" in text
    assert "/console/summary" in text  # 一键去创建 Offer


def test_notify_final_passed_skipped_when_disabled():
    res = fn.notify_final_passed(
        summary=_summary(),
        settings=_notify_settings(feishu_final_passed_notify_enabled=False),
    )
    assert res["status"] == "skipped"


def test_notify_final_passed_failed_keeps_reason():
    with patch.object(fn, "send_card", return_value=(False, ["ou_hr"], "boom")):
        res = fn.notify_final_passed(summary=_summary(), settings=_notify_settings())
    assert res["status"] == "failed"
    assert "boom" in res["reason"]


class _FakeDB:
    """路由层测试用的最小 Session 替身（只用到 get / commit / refresh）。"""

    def __init__(self, row):
        self.row = row
        self.committed = False

    def get(self, _model, _pk):
        return self.row

    def commit(self):
        self.committed = True

    def refresh(self, _obj):
        return None


def test_route_final_decision_pass_notifies_hr():
    from app.api.final_interview import decide_final

    row = _summary()
    db = _FakeDB(row)
    sent = {}

    def fake_notify(**kw):
        sent.update(kw)
        return {"status": "sent"}

    with patch("app.api.final_interview.notify_final_passed", side_effect=fake_notify):
        decide_final(
            "00000000-0000-0000-0000-000000000001",
            SimpleNamespace(decision="pass", note="", scheduled_at=None),
            user=SimpleNamespace(username="boss"),
            db=db,
        )

    assert db.committed is True
    assert sent["operator"] == "boss"
    assert sent["summary"] is row


def test_route_final_decision_fail_does_not_notify():
    from app.api.final_interview import decide_final

    row = _summary()
    with patch("app.api.final_interview.notify_final_passed") as notify:
        decide_final(
            "00000000-0000-0000-0000-000000000001",
            SimpleNamespace(decision="fail", note="不合适", scheduled_at=None),
            user=SimpleNamespace(username="boss"),
            db=_FakeDB(row),
        )
    notify.assert_not_called()


def test_route_final_decision_swallows_notify_exception():
    from app.api.final_interview import decide_final

    row = _summary()
    with patch(
        "app.api.final_interview.notify_final_passed",
        side_effect=RuntimeError("boom"),
    ):
        decide_final(
            "00000000-0000-0000-0000-000000000001",
            SimpleNamespace(decision="pass", note="", scheduled_at=None),
            user=SimpleNamespace(username="boss"),
            db=_FakeDB(row),
        )
    assert row.final_interview_status == FINAL_PASSED  # 决策已落库，通知异常不外抛
