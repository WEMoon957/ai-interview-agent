"""Offer 接口层单测：参数解析 / 统一响应 / 状态校验 / 推送调用。

覆盖管理端 10 个端点与候选人端 3 个端点的关键分支（状态机细节见 test_offer_state.py）。
飞书推送在本层被替换为记录器，避免真实网络调用。
"""
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import app.api.offers as offers_mod
from app.api.schemas import (
    AcceptOffer,
    OfferCreate,
    OfferDecline,
    OfferOnboard,
    OfferReject,
    OfferUpdate,
)
from app.config import Settings
from app.db.models.job import JobPosting
from app.db.models.offline import OfflineInterview
from app.db.models.offer import Offer
from app.db.models.summary import RecruitmentSummary
from app.exceptions import BizError
from app.services import offer as svc


# ── 测试桩 ───────────────────────────────────────────────────
class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, *, offers=None, summary=None, job=None, offline=None):
        self.offers = list(offers or [])
        self.summary = summary
        self.job = job
        self.offline = offline
        self.commits = 0
        self.added = []

    def get(self, model, _pk):
        if model is Offer:
            return self.offers[0] if self.offers else None
        if model is RecruitmentSummary:
            return self.summary
        if model is JobPosting:
            return self.job
        return None

    def query(self, *entities):
        model = entities[0] if entities else None
        if model is Offer:
            return _FakeQuery(self.offers)
        if model is OfflineInterview:
            return _FakeQuery([self.offline] if self.offline is not None else [])
        return _FakeQuery([])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        pass


@pytest.fixture(autouse=True)
def _no_expire(monkeypatch):
    """默认把惰性过期置为空操作。

    测试桩会忽略 ``filter`` 条件，若不隔离，``expire_due_offers`` 会把所有 Offer
    误置为 expired，并对每条记录触发一次真实的飞书推送（网络调用 → 测试变慢）。
    """
    monkeypatch.setattr(svc, "expire_due_offers", lambda db, notify=True: [])


def _list_offers(db, **kw):
    """调用列表路由。

    直接调用路由函数时，未传的参数取的是 FastAPI 的 ``Query(None)`` 对象（真值），
    因此必须显式补齐全部 Query 参数。
    """
    params = dict(
        status=None,
        job_id=None,
        store=None,
        keyword=None,
        source=None,
        date_from=None,
        date_to=None,
        limit=200,
    )
    params.update(kw)
    return offers_mod.list_offers(db=db, **params)


def _stats(db, **kw):
    """调用统计路由（同上，显式补齐 Query 参数）。"""
    params = dict(month=None, job_id=None)
    params.update(kw)
    return offers_mod.offer_stats(db=db, **params)


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
        status=svc.STATUS_DRAFT,
        approver_open_id="",
        approval_note="",
        approval_message_id="",
        withdraw_reason="",
        decline_reason_text="",
        onboard_store="",
        no_show_note="",
    )
    base.update(kw)
    return Offer(**base)


def _summary(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        candidate_name="张三",
        final_score=80.0,
        is_complete=True,
        # 终试闸门：none=历史数据（未进入终试流程），允许直接发起 Offer
        final_interview_status="none",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _job(**kw) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), title="前厅服务员", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


class _Notifier:
    """替换 notify_offer_event，记录事件。"""

    def __init__(self):
        self.events = []

    def __call__(self, event, offer, **kw):
        self.events.append((event, kw))
        return {"status": "sent", "event": event, "recipients": ["ou_x"], "reason": ""}


@pytest.fixture
def notifier(monkeypatch):
    rec = _Notifier()
    monkeypatch.setattr(offers_mod, "notify_offer_event", rec)
    return rec


@pytest.fixture
def settings_md(monkeypatch):
    """替换服务层配置：审批人已配置、部门为空（避免真实网络解析）。"""
    s = Settings(offer_approver_open_ids="ou_boss", feishu_hr_department_ids="")
    monkeypatch.setattr(svc, "get_settings", lambda: s)
    return s


# ── 管理端：列表 / 详情 / 统计 ────────────────────────────────
def test_list_offers_ok():
    db = _FakeDB(offers=[_offer()])
    result = _list_offers(db)
    assert result["code"] == 0
    assert len(result["data"]) == 1
    assert result["data"][0]["candidate_name"] == "张三"


def test_list_offers_invalid_date_422():
    db = _FakeDB()
    with pytest.raises(BizError) as exc:
        _list_offers(db, date_from="2026/01/01")
    assert exc.value.http_status == 422


def test_get_offer_not_found_404():
    db = _FakeDB(offers=[])
    with pytest.raises(BizError) as exc:
        offers_mod.get_offer(str(uuid.uuid4()), db=db)
    assert exc.value.http_status == 404


def test_get_offer_invalid_uuid_422():
    db = _FakeDB()
    with pytest.raises(BizError) as exc:
        offers_mod.get_offer("not-a-uuid", db=db)
    assert exc.value.http_status == 422


def test_get_offer_ok_includes_status_label():
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    db = _FakeDB(offers=[offer])
    result = offers_mod.get_offer(str(offer.id), db=db)
    assert result["data"]["status"] == "sent"
    assert result["data"]["status_label"] == "已发出"


# ── 管理端：起草 / 修改 ──────────────────────────────────────
def test_create_offer_ok_commits():
    summary = _summary()
    db = _FakeDB(summary=summary, job=_job())
    payload = OfferCreate(
        summary_id=str(summary.id),
        store_name="昆明南屏店",
        report_date="2026-10-20",
    )
    result = offers_mod.create_offer(payload, db)
    assert result["code"] == 0
    assert result["data"]["status"] == "draft"
    assert result["data"]["report_date"] == "2026-10-20"
    assert db.commits == 1


def test_create_offer_invalid_summary_uuid_422():
    db = _FakeDB()
    payload = OfferCreate(summary_id="bad", store_name="店", report_date="2026-10-20")
    with pytest.raises(BizError) as exc:
        offers_mod.create_offer(payload, db)
    assert exc.value.http_status == 422


def test_create_offer_invalid_date_422():
    summary = _summary()
    db = _FakeDB(summary=summary, job=_job())
    payload = OfferCreate(summary_id=str(summary.id), store_name="店", report_date="2026/10/20")
    with pytest.raises(BizError) as exc:
        offers_mod.create_offer(payload, db)
    assert exc.value.http_status == 422


def test_create_offer_inactive_job_422():
    db = _FakeDB(summary=_summary(), job=_job(is_active=False))
    payload = OfferCreate(
        summary_id=str(uuid.uuid4()), store_name="店", report_date="2026-10-20"
    )
    with pytest.raises(BizError) as exc:
        offers_mod.create_offer(payload, db)
    assert exc.value.http_status == 422
    assert "停招" in exc.value.message


def test_update_offer_ok():
    offer = _offer(status=svc.STATUS_DRAFT)
    db = _FakeDB(offers=[offer])
    result = offers_mod.update_offer(
        str(offer.id), OfferUpdate(store_name="新门店", probation_days=15), db
    )
    assert result["data"]["probation_days"] == 15
    assert db.commits == 1


def test_update_offer_conflict_when_sent_409():
    offer = _offer(status=svc.STATUS_SENT)
    db = _FakeDB(offers=[offer])
    with pytest.raises(BizError) as exc:
        offers_mod.update_offer(str(offer.id), OfferUpdate(store_name="新门店"), db)
    assert exc.value.http_status == 409


# ── 管理端：审批 ─────────────────────────────────────────────
def test_submit_approval_pushes_card(settings_md, notifier):
    offer = _offer(status=svc.STATUS_DRAFT)
    db = _FakeDB(offers=[offer])
    result = offers_mod.submit_approval(str(offer.id), db=db)
    assert result["data"]["offer"]["status"] == "pending_approval"
    assert result["data"]["notify"]["status"] == "sent"
    assert notifier.events[0][0] == "pending_approval"


def test_submit_approval_requires_approver(monkeypatch):
    """未配置审批人：422（审批流必须有明确收件人）。"""
    monkeypatch.setattr(svc, "get_settings", lambda: Settings(offer_approver_open_ids=""))
    offer = _offer(status=svc.STATUS_DRAFT)
    db = _FakeDB(offers=[offer])
    with pytest.raises(BizError) as exc:
        offers_mod.submit_approval(str(offer.id), db=db)
    assert exc.value.http_status == 422


def test_approve_returns_link_and_copy_text(settings_md, notifier):
    offer = _offer(status=svc.STATUS_PENDING_APPROVAL)
    db = _FakeDB(offers=[offer])
    result = offers_mod.approve_offer(str(offer.id), None, db)
    data = result["data"]
    assert data["offer"]["status"] == "sent"
    assert offer.token and offer.token in data["link"]
    assert "/o/" in data["link"]
    assert "张三" in data["copy_text"]
    assert notifier.events[0][0] == "sent"


def test_reject_requires_note_from_service(settings_md, notifier):
    """驳回意见由服务层校验（空串 → 422）。"""
    offer = _offer(status=svc.STATUS_PENDING_APPROVAL)
    db = _FakeDB(offers=[offer])
    with pytest.raises(BizError) as exc:
        offers_mod.reject_offer(str(offer.id), OfferReject(note=" "), db)
    assert exc.value.http_status == 422


def test_reject_ok_pushes_card(settings_md, notifier):
    offer = _offer(status=svc.STATUS_PENDING_APPROVAL)
    db = _FakeDB(offers=[offer])
    result = offers_mod.reject_offer(str(offer.id), OfferReject(note="薪资超标"), db)
    assert result["data"]["offer"]["status"] == "rejected"
    assert notifier.events[0][0] == "rejected"


def test_withdraw_ok():
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    db = _FakeDB(offers=[offer])
    result = offers_mod.withdraw_offer(str(offer.id), None, db)
    assert result["data"]["status"] == "withdrawn"


# ── 管理端：办理入职 ─────────────────────────────────────────
def test_onboard_onboarded_ok(notifier):
    offer = _offer(status=svc.STATUS_ACCEPTED)
    db = _FakeDB(offers=[offer])
    result = offers_mod.onboard_offer(
        str(offer.id), OfferOnboard(result="onboarded"), db
    )
    assert result["data"]["offer"]["status"] == "onboarded"
    assert result["data"]["notify"] is None  # 已报到不推送
    assert notifier.events == []


def test_onboard_no_show_requires_note():
    offer = _offer(status=svc.STATUS_ACCEPTED)
    db = _FakeDB(offers=[offer])
    with pytest.raises(BizError) as exc:
        offers_mod.onboard_offer(str(offer.id), OfferOnboard(result="no_show", note=""), db)
    assert exc.value.http_status == 422


def test_onboard_no_show_pushes_card(notifier):
    offer = _offer(status=svc.STATUS_ACCEPTED)
    db = _FakeDB(offers=[offer])
    result = offers_mod.onboard_offer(
        str(offer.id), OfferOnboard(result="no_show", note="联系不上"), db
    )
    assert result["data"]["offer"]["status"] == "no_show"
    assert notifier.events[0][0] == "no_show"


def test_onboard_invalid_date_422():
    offer = _offer(status=svc.STATUS_ACCEPTED)
    db = _FakeDB(offers=[offer])
    with pytest.raises(BizError) as exc:
        offers_mod.onboard_offer(
            str(offer.id),
            OfferOnboard(result="onboarded", actual_report_date="2026/10/20"),
            db,
        )
    assert exc.value.http_status == 422


# ── 候选人端 ─────────────────────────────────────────────────
def test_candidate_get_invalid_token_404():
    db = _FakeDB(offers=[])
    with pytest.raises(BizError) as exc:
        offers_mod.get_offer_context("nope", db=db)
    assert exc.value.http_status == 404


def test_candidate_get_hides_internal_fields():
    offer = _offer(status=svc.STATUS_SENT, token="tok", expires_at=datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc))
    db = _FakeDB(offers=[offer])
    data = offers_mod.get_offer_context("tok", db=db)["data"]
    assert data["candidate_name"] == "张三"
    assert data["deadline_text"] == "10月16日 08:00"
    # 管理端字段不得外泄
    assert "approver_open_id" not in data
    assert "summary_id" not in data


def test_candidate_get_expired_token_returns_expired_status(monkeypatch):
    """访问即过期：状态先流转为 expired，页面据此渲染结果页。"""
    past = datetime(2026, 10, 1, tzinfo=timezone.utc)
    offer = _offer(status=svc.STATUS_SENT, token="tok", expires_at=past)
    db = _FakeDB(offers=[offer])

    def _fake_expire(db_, notify=True):
        offer.status = svc.STATUS_EXPIRED
        offer.expired_at = datetime.now(timezone.utc)
        return [offer]

    monkeypatch.setattr(svc, "expire_due_offers", _fake_expire)
    data = offers_mod.get_offer_context("tok", db=db)["data"]
    assert data["status"] == "expired"


def test_candidate_accept_ok_pushes_notify(settings_md, notifier):
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    db = _FakeDB(offers=[offer])
    result = offers_mod.accept_offer("tok", AcceptOffer(email="li@example.com"), db=db)
    assert result["data"]["status"] == "accepted"
    assert notifier.events[0][0] == "accepted"
    assert db.commits >= 1


def test_candidate_accept_conflict_when_not_sent():
    offer = _offer(status=svc.STATUS_DRAFT, token="tok")
    db = _FakeDB(offers=[offer])
    with pytest.raises(BizError) as exc:
        offers_mod.accept_offer("tok", AcceptOffer(email="li@example.com"), db=db)
    assert exc.value.http_status == 409


def test_accept_offer_schema_requires_email():
    """候选人接受请求体：邮箱为必填项（缺省由 FastAPI 返回 422）。"""
    with pytest.raises(Exception):
        AcceptOffer()


def test_candidate_decline_ok_with_free_text(settings_md, notifier):
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    db = _FakeDB(offers=[offer])
    result = offers_mod.decline_offer("tok", OfferDecline(reason_text="已找到别的工作"), db)
    assert result["data"]["status"] == "declined"
    assert offer.decline_reason_text == "已找到别的工作"
    assert notifier.events[0][0] == "declined"


def test_candidate_decline_allows_blank_reason(settings_md, notifier):
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    db = _FakeDB(offers=[offer])
    result = offers_mod.decline_offer("tok", None, db)
    assert result["data"]["status"] == "declined"


# ── 统计端点 ─────────────────────────────────────────────────
def test_stats_endpoint_ok(monkeypatch):
    db = _FakeDB()

    class _StatsQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_k):
            return self

        def group_by(self, *_a, **_k):
            return self

        def with_entities(self, *_a, **_k):
            return self

        def all(self):
            return list(self._rows)

        def __iter__(self):
            return iter(self._rows)

        def scalar(self):
            return 0

    monkeypatch.setattr(db, "query", lambda *entities: _StatsQuery([]))
    result = _stats(db)
    assert result["code"] == 0
    assert result["data"]["total"] == 0
    assert result["data"]["accept_rate"] is None


def test_stats_endpoint_invalid_month_422():
    db = _FakeDB()
    with pytest.raises(BizError) as exc:
        _stats(db, month="2026/10")
    assert exc.value.http_status == 422
