"""飞书长连接卡片回调单测。

覆盖：
- 开关关闭 / 缺凭据时 ``start_long_connection`` 不启动、返回 False 且不抛异常；
- 卡片「同意」→ Offer 置为 sent（并触发 HR 通知，测试中已静默）；
- 卡片「驳回」→ 先返回填写意见的卡片，不直接改状态；
- 提交驳回意见 → 落库为 rejected；意见为空 → 提示且不改状态；
- 非指定审批人 / Offer 不存在 / 状态已变更 → 明确提示且不抛异常。

**回调函数绝不能向外抛异常**（飞书会断开长连接），因此异常路径也要断言。
"""

import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models.offer import Offer
from app.services import feishu_longconn as lc


# ── 桩 ───────────────────────────────────────────────────────
class _FakeDB:
    def __init__(self, offer=None):
        self._offer = offer
        self.commits = 0
        self.closed = False

    def get(self, _model, _pk):
        return self._offer

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        pass

    def close(self):
        self.closed = True


def _offer(**kw) -> Offer:
    base = dict(
        id=uuid.uuid4(),
        summary_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        candidate_name="所测试",
        job_title="前厅服务员",
        store_name="昆明南屏店",
        source="online",
        report_date=date(2026, 10, 20),
        probation_days=30,
        note="",
        status="pending_approval",
        approver_open_id="ou_boss",
        approval_note="",
        approval_message_id="",
        withdraw_reason="",
        decline_reason_text="",
        onboard_store="",
        no_show_note="",
        expires_at=None,
    )
    base.update(kw)
    return Offer(**base)


def _card_data(*, value=None, form_value=None, open_id="ou_boss"):
    """构造与飞书回传结构一致的假数据对象。"""
    return SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(value=value or {}, form_value=form_value or {}),
            operator=SimpleNamespace(open_id=open_id),
        )
    )


def _settings(**kw) -> Settings:
    base = dict(
        feishu_longconn_enabled=True,
        feishu_app_id="cli_test_app",
        feishu_app_secret="test_secret",
    )
    base.update(kw)
    return Settings(**base)


def _resp(resp) -> dict:
    """把 SDK 响应对象转成可断言的 dict。"""
    import lark_oapi as lark

    return json.loads(lark.JSON.marshal(resp))


@pytest.fixture
def wire(monkeypatch):
    """注入假 db 与静默通知，返回安装函数（避免真实数据库与网络）。"""
    installed = {}

    def _install(offer=None):
        db = _FakeDB(offer)
        installed["db"] = db
        monkeypatch.setattr(lc, "_dbsession_factory", lambda: db)
        monkeypatch.setattr(lc, "_notify_hr_after_approve", lambda *a, **k: None)
        return db

    return _install


# ── 启动校验 ─────────────────────────────────────────────────
def test_not_enabled_returns_false():
    assert lc.start_long_connection(_settings(feishu_longconn_enabled=False)) is False


def test_missing_credentials_returns_false():
    s = _settings(feishu_app_id="", feishu_app_secret="")
    assert lc.start_long_connection(s) is False


def test_diagnose_reports_missing_credentials_and_transport():
    s = _settings(feishu_app_id="", feishu_app_secret="", feishu_notify_transport="cli")
    problems = lc.diagnose_longconn(s)
    joined = " ".join(problems)
    assert "FEISHU_APP_ID" in joined
    assert "FEISHU_NOTIFY_TRANSPORT" in joined
    assert "card.action.trigger" in joined


# ── 回调：同意 ───────────────────────────────────────────────
def test_approve_moves_offer_to_sent(wire):
    offer = _offer()
    db = wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(value={"offer_id": str(offer.id), "action": "approve"})
        )
    )

    assert offer.status == "sent"
    assert offer.token  # 生成了候选人确认 token
    assert db.commits == 1
    assert db.closed is True
    assert "已通过" in resp["toast"]["content"]


# ── 回调：驳回（先弹输入框）────────────────────────────────────
def test_reject_returns_form_card_without_changing_status(wire):
    offer = _offer()
    wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(value={"offer_id": str(offer.id), "action": "reject"})
        )
    )

    assert offer.status == "pending_approval"  # 未直接驳回
    card = resp["card"]["data"]
    assert card["header"]["title"]["content"] == "填写驳回意见"
    form = card["elements"][1]
    assert form["tag"] == "form"
    assert form["elements"][0]["name"] == "reason"
    assert form["elements"][1]["value"]["action"] == lc.ACTION_REJECT_SUBMIT


def test_reject_submit_without_note_warns_and_keeps_status(wire):
    offer = _offer()
    wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(
                value={"offer_id": str(offer.id), "action": lc.ACTION_REJECT_SUBMIT},
                form_value={},
            )
        )
    )

    assert offer.status == "pending_approval"
    assert resp["toast"]["type"] == "warning"
    assert "驳回意见" in resp["toast"]["content"]


def test_reject_submit_with_note_marks_rejected(wire):
    offer = _offer()
    db = wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(
                value={"offer_id": str(offer.id), "action": lc.ACTION_REJECT_SUBMIT},
                form_value={"reason": "岗位已招满"},
            )
        )
    )

    assert offer.status == "rejected"
    assert offer.approval_note == "岗位已招满"
    assert db.commits == 1
    assert "已驳回" in resp["toast"]["content"]


# ── 回调：异常与越权 ─────────────────────────────────────────
def test_non_approver_is_rejected(wire):
    offer = _offer(approver_open_id="ou_boss")
    wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(
                value={"offer_id": str(offer.id), "action": "approve"},
                open_id="ou_someone_else",
            )
        )
    )

    assert offer.status == "pending_approval"
    assert resp["toast"]["type"] == "error"
    assert "指定审批人" in resp["toast"]["content"]


def test_offer_missing_returns_error(wire):
    wire(None)

    resp = _resp(
        lc._handle_card_action(
            _card_data(value={"offer_id": str(uuid.uuid4()), "action": "approve"})
        )
    )

    assert resp["toast"]["type"] == "error"
    assert "未找到" in resp["toast"]["content"]


def test_status_conflict_warns(wire):
    offer = _offer(status="sent")
    wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(value={"offer_id": str(offer.id), "action": "approve"})
        )
    )

    assert resp["toast"]["type"] == "warning"
    assert "已被处理" in resp["toast"]["content"]


def test_unknown_action_warns(wire):
    offer = _offer()
    wire(offer)

    resp = _resp(
        lc._handle_card_action(
            _card_data(value={"offer_id": str(offer.id), "action": "nope"})
        )
    )

    assert resp["toast"]["type"] == "warning"


def test_invalid_offer_id_does_not_raise(wire):
    wire(None)

    resp = _resp(
        lc._handle_card_action(_card_data(value={"offer_id": "not-a-uuid", "action": "approve"}))
    )

    assert resp["toast"]["type"] == "error"


def test_handler_never_raises_on_broken_payload():
    # 完全畸形的 data 也必须返回响应而不是抛异常（否则长连接会被断开）
    resp = _resp(lc._handle_card_action(SimpleNamespace(event=None)))
    assert resp["toast"]["type"] in {"warning", "error"}
