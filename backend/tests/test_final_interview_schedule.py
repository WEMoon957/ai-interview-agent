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
        feishu_notify_ready=True,
        feishu_task_enabled=False,
        feishu_notify_max_items=20,
        public_base_url_normalized="http://example.com",
        boss_open_id_list=["ou_boss"],
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
