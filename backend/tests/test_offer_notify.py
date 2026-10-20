"""Offer 飞书卡片单测：卡片构造（含审批按钮）/ 收件人分流 / 推送降级。

验收要点（设计方案 v2）：
- 审批卡片带「同意 / 驳回」按钮，value 携带 offer_id 与动作（回调接口依赖此结构）；
- 收件人分流：待审批 → 审批人；已接受 → HR ∪ 店长；其余 → HR；
- 推送失败/未配置收件人/开关关闭时均不抛异常，返回明确状态；
- 现有纯文本卡片行为零回归（``build_card`` 不传 actions 时结构不变）。
"""
import json
import uuid
from datetime import date, datetime, timezone

import pytest

from app.config import Settings
from app.db.models.offer import Offer
from app.services import offer_notify as on
from app.services.feishu_notify import build_button, build_card, card_to_markdown


def _offer(**kw) -> Offer:
    base = dict(
        id=uuid.uuid4(),
        summary_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        candidate_name="张三",
        job_title="前厅服务员",
        store_name="昆明南屏店",
        source="online",
        report_date=date(2026, 10, 20),
        probation_days=30,
        note="",
        status="sent",
        approver_open_id="",
        approval_note="",
        approval_message_id="",
        withdraw_reason="",
        decline_reason_text="",
        onboard_store="",
        no_show_note="",
        expires_at=datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return Offer(**base)


def _settings(**kw) -> Settings:
    """构造隔离的测试配置。

    注意：必须显式清空 ``feishu_hr_department_ids``，否则 Settings 会从项目 .env
    读取真实部门 id，导致 resolve_recipients 解析真实成员并发起网络请求。
    """
    base = dict(
        feishu_offer_notify_enabled=True,
        # 默认关闭待办，避免单测真的调飞书创建任务；待办用例单独传 feishu_task_enabled=True + 假 creator
        feishu_task_enabled=False,
        feishu_hr_open_ids="ou_hr1,ou_hr2",
        feishu_hr_department_ids="",
        feishu_observer_open_ids="",
        offer_approver_open_ids="ou_boss",
        feishu_store_manager_open_ids="ou_mgr,ou_hr1",
    )
    base.update(kw)
    return Settings(**base)


class _Recorder:
    """记录发送调用（open_id, card）的假 sender。"""

    def __init__(self, ok=True, raise_exc=False):
        self.calls = []
        self.ok = ok
        self.raise_exc = raise_exc

    def __call__(self, open_id, card, settings):
        self.calls.append((open_id, card))
        if self.raise_exc:
            raise RuntimeError("boom")
        return self.ok, "" if self.ok else "模拟失败"


# ── build_card 按钮扩展（不破坏既有行为）─────────────────────
def test_build_card_without_actions_unchanged():
    """不传 actions：卡片仅含 div 与 hr，与历史结构一致。"""
    card = build_card("标题", "blue", ["段落一", "段落二"])
    tags = [el["tag"] for el in card["elements"]]
    assert tags == ["div", "hr", "div"]
    assert card["header"]["template"] == "blue"


def test_build_card_with_actions_appends_action_element():
    card = build_card(
        "审批", "orange", ["内容"], actions=[build_button("同意", {"offer_id": "x", "action": "approve"})]
    )
    last = card["elements"][-1]
    assert last["tag"] == "action"
    assert last["actions"][0]["text"]["content"] == "同意"
    assert last["actions"][0]["value"] == {"offer_id": "x", "action": "approve"}


def test_card_to_markdown_renders_buttons():
    """降级文本要能看到按钮文案（dry-run 预览可核对）。"""
    card = build_card(
        "审批",
        "orange",
        ["内容"],
        actions=[
            build_button("同意", {"offer_id": "x", "action": "approve"}, button_type="primary"),
            build_button("驳回", {"offer_id": "x", "action": "reject"}, button_type="danger"),
        ],
    )
    text = card_to_markdown(card)
    assert "【按钮】同意 / 驳回" in text


# ── 审批卡片 ─────────────────────────────────────────────────
def test_approval_card_has_two_buttons_with_offer_id():
    offer = _offer(status="pending_approval")
    card = on.build_approval_card(offer)
    actions = card["elements"][-1]["actions"]
    assert len(actions) == 2
    values = [a["value"] for a in actions]
    assert {"offer_id": str(offer.id), "action": "approve"} in values
    assert {"offer_id": str(offer.id), "action": "reject"} in values
    # 改版后标题栏改深灰「档案」配色（见 APPROVAL_HEADER_TEMPLATE）
    assert card["header"]["template"] == on.APPROVAL_HEADER_TEMPLATE


def test_approval_card_contains_offer_summary():
    offer = _offer(status="pending_approval", note="包吃住", source="walk_in")
    text = card_to_markdown(on.build_approval_card(offer))
    assert "张三" in text
    assert "前厅服务员" in text
    assert "昆明南屏店" in text
    assert "包吃住" in text
    assert "到店直招" in text


def test_approval_card_shows_salary_package():
    """改版核心：审批卡片必须出现试用期 / 转正后两档薪资。"""
    offer = _offer(
        status="pending_approval",
        probation_salary="4,800 元/月",
        regular_salary="5,500 元/月",
    )
    text = card_to_markdown(on.build_approval_card(offer))
    assert "薪酬" in text
    assert "试用期薪资" in text and "4,800 元/月" in text
    assert "转正后薪资" in text and "5,500 元/月" in text


def test_approval_card_marks_missing_salary_as_unfilled():
    """薪资未填时要显式写「未填写」，不能静默消失。"""
    offer = _offer(status="pending_approval")
    text = card_to_markdown(on.build_approval_card(offer))
    assert "未填写" in text


def test_approval_card_sections_and_verdict_color():
    """三段式档案结构 + 录用建议着色（lark_md 富文本）。"""
    offer = _offer(status="pending_approval")
    card = on.build_approval_card(
        offer,
        scores={"screening_score": 91, "ai_interview_score": 82.5,
                "offline_score": 86, "final_score": 86.4, "final_verdict": "强烈推荐"},
        candidate={"phone": "13800002210", "email": "a@b.com", "source_channel": "BOSS直聘"},
    )
    text = card_to_markdown(card)
    assert "💰" in text and "📅" in text and "📊" in text
    assert "BOSS直聘" in text
    assert "13800002210" in text
    raw = json.dumps(card, ensure_ascii=False)
    assert "<font color='green'>强烈推荐</font>" in raw
    # 字段块是飞书原生 fields 结构，不能被写成一段纯文本
    assert any(el.get("fields") for el in card["elements"] if el.get("tag") == "div")


# ── 各事件卡片 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "event,expect_title_keyword,template",
    [
        (on.EVENT_SENT, "已批准", "blue"),
        (on.EVENT_REJECTED, "驳回", "red"),
        (on.EVENT_ACCEPTED, "已接受", "green"),
        (on.EVENT_DECLINED, "婉拒", "grey"),
        (on.EVENT_EXPIRED, "过期", "orange"),
        (on.EVENT_NO_SHOW, "未报到", "red"),
    ],
)
def test_event_cards(event, expect_title_keyword, template):
    card = on.build_offer_card(event, _offer())
    assert card is not None
    assert expect_title_keyword in card["header"]["title"]["content"]
    assert card["header"]["template"] == template


def test_sent_card_includes_link_and_copy_text():
    offer = _offer(status="sent", token="tok", approved_at=datetime.now(timezone.utc))
    card = on.build_offer_card(
        on.EVENT_SENT, offer, link="https://hire.example.com/o/tok", copy_text="【品牌】张三您好"
    )
    text = card_to_markdown(card)
    assert "可直接转发的话术" in text
    assert "【品牌】张三您好" in text
    assert "10月16日 08:00" in text  # 截止时间按本地时区展示


def test_declined_card_includes_free_text_reason():
    card = on.build_offer_card(on.EVENT_DECLINED, _offer(decline_reason_text="已找到别的工作"))
    assert "已找到别的工作" in card_to_markdown(card)


def test_declined_card_blank_reason_placeholder():
    card = on.build_offer_card(on.EVENT_DECLINED, _offer(decline_reason_text=""))
    assert "（未填写）" in card_to_markdown(card)


def test_no_show_card_includes_note():
    card = on.build_offer_card(on.EVENT_NO_SHOW, _offer(no_show_note="电话联系不上"))
    assert "电话联系不上" in card_to_markdown(card)


def test_unknown_event_returns_none():
    assert on.build_offer_card("whatever", _offer()) is None


# ── 收件人分流 ───────────────────────────────────────────────
def test_targets_pending_approval_goes_to_approver():
    targets, err = on.resolve_targets(on.EVENT_PENDING_APPROVAL, _offer(), _settings())
    assert targets == ["ou_boss"]
    assert err == ""


def test_targets_pending_approval_without_approver_returns_error():
    targets, err = on.resolve_targets(
        on.EVENT_PENDING_APPROVAL, _offer(), _settings(offer_approver_open_ids="")
    )
    assert targets == []
    assert "审批人" in err


def test_targets_accepted_merges_hr_and_store_manager_dedup():
    """已接受：HR ∪ 店长，去重保序（ou_hr1 同时是店长，只出现一次）。"""
    targets, _ = on.resolve_targets(on.EVENT_ACCEPTED, _offer(), _settings())
    assert targets == ["ou_hr1", "ou_hr2", "ou_mgr"]


def test_targets_declined_goes_to_hr_only():
    targets, _ = on.resolve_targets(on.EVENT_DECLINED, _offer(), _settings())
    assert targets == ["ou_hr1", "ou_hr2"]


def test_targets_pending_approval_includes_observer():
    """审批卡片原收件人是审批人，全局抄送人也要额外收到一份。"""
    targets, _ = on.resolve_targets(
        on.EVENT_PENDING_APPROVAL,
        _offer(),
        _settings(feishu_observer_open_ids="ou_obs"),
    )
    assert targets == ["ou_boss", "ou_obs"]


def test_targets_accepted_includes_observer_dedup():
    targets, _ = on.resolve_targets(
        on.EVENT_ACCEPTED,
        _offer(),
        _settings(feishu_observer_open_ids="ou_obs,ou_hr1"),
    )
    assert targets == ["ou_hr1", "ou_hr2", "ou_mgr", "ou_obs"]


# ── 推送编排 ─────────────────────────────────────────────────
def test_notify_pending_approval_sends_to_approver():
    rec = _Recorder()
    res = on.notify_offer_event(
        on.EVENT_PENDING_APPROVAL, _offer(status="pending_approval"), settings=_settings(), sender=rec
    )
    assert res["status"] == "sent"
    assert res["recipients"] == ["ou_boss"]
    assert len(rec.calls) == 1
    assert rec.calls[0][1]["elements"][-1]["tag"] == "action"


def test_notify_accepted_reaches_hr_and_manager():
    rec = _Recorder()
    res = on.notify_offer_event(
        on.EVENT_ACCEPTED, _offer(status="accepted"), settings=_settings(), sender=rec
    )
    assert res["status"] == "sent"
    assert res["recipients"] == ["ou_hr1", "ou_hr2", "ou_mgr"]
    assert len(rec.calls) == 3


def test_notify_skipped_when_switch_off():
    rec = _Recorder()
    res = on.notify_offer_event(
        on.EVENT_ACCEPTED, _offer(), settings=_settings(feishu_offer_notify_enabled=False), sender=rec
    )
    assert res["status"] == "skipped"
    assert rec.calls == []


def test_notify_skipped_when_no_recipients():
    rec = _Recorder()
    res = on.notify_offer_event(
        on.EVENT_DECLINED,
        _offer(),
        settings=_settings(feishu_hr_open_ids="", feishu_store_manager_open_ids=""),
        sender=rec,
    )
    assert res["status"] == "skipped"
    assert rec.calls == []


def test_notify_failed_but_not_raising():
    """发送失败只返回 failed，不向调用方抛异常（业务主流程不受影响）。"""
    rec = _Recorder(ok=False)
    res = on.notify_offer_event(
        on.EVENT_DECLINED, _offer(), settings=_settings(), sender=rec
    )
    assert res["status"] == "failed"
    assert "模拟失败" in res["reason"]


def test_notify_sender_exception_is_swallowed():
    rec = _Recorder(raise_exc=True)
    res = on.notify_offer_event(
        on.EVENT_DECLINED, _offer(), settings=_settings(), sender=rec
    )
    assert res["status"] == "failed"


def test_notify_dry_run_does_not_send():
    rec = _Recorder()
    res = on.notify_offer_event(
        on.EVENT_ACCEPTED, _offer(), settings=_settings(), sender=rec, dry_run=True
    )
    assert res["status"] == "dry_run"
    assert res["recipients"] == ["ou_hr1", "ou_hr2", "ou_mgr"]
    assert rec.calls == []


def test_notify_unknown_event_skipped():
    rec = _Recorder()
    res = on.notify_offer_event("nope", _offer(), settings=_settings(), sender=rec)
    assert res["status"] == "skipped"
    assert rec.calls == []


# ── 飞书待办（审批 + HR 跟进）────────────────────────────────
class _TaskRecorder:
    """记录待办创建调用的假 creator（签名对齐 TaskCreator）。"""

    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, assignee, summary, description, settings):
        self.calls.append((assignee, summary, description))
        return self.ok, "" if self.ok else "模拟失败"


def test_build_task_for_pending_approval():
    title, desc = on.build_offer_task(
        on.EVENT_PENDING_APPROVAL, _offer(status="pending_approval")
    )
    assert "待审批" in title
    assert "张三" in title
    assert "同意 / 驳回" in desc


def test_build_task_for_sent_includes_link():
    title, desc = on.build_offer_task(
        on.EVENT_SENT, _offer(status="sent"), link="https://x/o/tok"
    )
    assert "待发送" in title
    assert "https://x/o/tok" in desc


def test_build_task_for_accepted_includes_report_date():
    title, desc = on.build_offer_task(on.EVENT_ACCEPTED, _offer(status="accepted"))
    assert "待入职" in title
    assert "2026-10-20" in title
    assert "已报到 / 未报到" in desc


def test_build_task_none_for_events_without_task():
    for event in (on.EVENT_DECLINED, on.EVENT_EXPIRED, on.EVENT_NO_SHOW, on.EVENT_REJECTED):
        assert on.build_offer_task(event, _offer()) is None


def test_notify_creates_task_for_approver():
    """待审批：卡片与待办都只发给审批人。"""
    rec = _Recorder()
    task = _TaskRecorder()
    res = on.notify_offer_event(
        on.EVENT_PENDING_APPROVAL,
        _offer(status="pending_approval"),
        settings=_settings(feishu_task_enabled=True),
        sender=rec,
        task_creator=task,
    )
    assert res["status"] == "sent"
    assert res["task"]["status"] == "created"
    assert [c[0] for c in task.calls] == ["ou_boss"]
    assert "待审批" in task.calls[0][1]


def test_notify_creates_tasks_for_hr_and_manager_on_accept():
    rec = _Recorder()
    task = _TaskRecorder()
    res = on.notify_offer_event(
        on.EVENT_ACCEPTED,
        _offer(status="accepted"),
        settings=_settings(feishu_task_enabled=True),
        sender=rec,
        task_creator=task,
    )
    assert [c[0] for c in task.calls] == ["ou_hr1", "ou_hr2", "ou_mgr"]
    assert res["task"]["status"] == "created"


def test_notify_creates_task_for_hr_on_sent():
    rec = _Recorder()
    task = _TaskRecorder()
    on.notify_offer_event(
        on.EVENT_SENT,
        _offer(status="sent"),
        settings=_settings(feishu_task_enabled=True),
        sender=rec,
        link="https://x/o/tok",
        task_creator=task,
    )
    assert [c[0] for c in task.calls] == ["ou_hr1", "ou_hr2"]
    assert "待发送" in task.calls[0][1]


def test_notify_task_skipped_when_disabled():
    rec = _Recorder()
    task = _TaskRecorder()
    res = on.notify_offer_event(
        on.EVENT_ACCEPTED,
        _offer(status="accepted"),
        settings=_settings(feishu_task_enabled=False),
        sender=rec,
        task_creator=task,
    )
    assert res["task"]["status"] == "skipped"
    assert task.calls == []


def test_notify_no_task_key_for_declined():
    rec = _Recorder()
    task = _TaskRecorder()
    res = on.notify_offer_event(
        on.EVENT_DECLINED,
        _offer(status="declined"),
        settings=_settings(feishu_task_enabled=True),
        sender=rec,
        task_creator=task,
    )
    assert "task" not in res
    assert task.calls == []


def test_notify_task_failure_does_not_break_card_result():
    """待办创建失败不影响卡片结果（仍返回 sent）。"""
    rec = _Recorder()
    task = _TaskRecorder(ok=False)
    res = on.notify_offer_event(
        on.EVENT_ACCEPTED,
        _offer(status="accepted"),
        settings=_settings(feishu_task_enabled=True),
        sender=rec,
        task_creator=task,
    )
    assert res["status"] == "sent"
    assert res["task"]["status"] == "failed"
