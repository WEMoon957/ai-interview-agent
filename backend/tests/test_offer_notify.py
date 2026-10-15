"""Offer 飞书卡片单测：卡片构造（含审批按钮）/ 收件人分流 / 推送降级。

验收要点（设计方案 v2）：
- 审批卡片带「同意 / 驳回」按钮，value 携带 offer_id 与动作（回调接口依赖此结构）；
- 收件人分流：待审批 → 审批人；已接受 → HR ∪ 店长；其余 → HR；
- 推送失败/未配置收件人/开关关闭时均不抛异常，返回明确状态；
- 现有纯文本卡片行为零回归（``build_card`` 不传 actions 时结构不变）。
"""
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
        feishu_hr_open_ids="ou_hr1,ou_hr2",
        feishu_hr_department_ids="",
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
    assert card["header"]["template"] == "orange"


def test_approval_card_contains_offer_summary():
    offer = _offer(status="pending_approval", note="包吃住", source="walk_in")
    text = card_to_markdown(on.build_approval_card(offer))
    assert "张三" in text
    assert "前厅服务员" in text
    assert "昆明南屏店" in text
    assert "包吃住" in text
    assert "到店直招" in text


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
