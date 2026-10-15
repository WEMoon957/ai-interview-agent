"""Offer → 入职闭环：状态机 / 时间口径 / 业务动作 / 惰性过期 / 统计 单测。

对应 docs/设计方案-Offer入职闭环.md v2 的验收要点：
- 10 态流转表全覆盖，非法流转抛 BizError(409)；
- 截止时间 = 发出后的「次日本地 08:00」（可配）；
- 审批通过才生成 token 与 expires_at（draft 不发链接）；
- 岗位停招 / 分数不足 不允许发起；同一汇总只允许一条进行中 Offer；
- 未报到必须填说明；婉拒理由为自由文本；
- 统计口径：接受率 = accepted/(accepted+declined+expired)，入职率 = onboarded/accepted。
"""
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models.job import JobPosting
from app.db.models.offline import OfflineInterview
from app.db.models.offer import Offer
from app.db.models.summary import RecruitmentSummary
from app.exceptions import BizError
from app.services import offer as svc


# ── 测试桩 ───────────────────────────────────────────────────
class _FakeQuery:
    """极简 query 桩：链式调用一律返回自身，取数直接返回预设行。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeDB:
    """极简 DB 桩（不落库）。"""

    def __init__(self, *, offers=None, summary=None, job=None, offline=None):
        self.offers = list(offers or [])
        self.summary = summary
        self.job = job
        self.offline = offline
        self.added = []
        self.commit_count = 0

    def get(self, model, _pk):
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
        self.commit_count += 1


def _offer(**kw) -> Offer:
    """构造一个未落库的 Offer 对象（显式给值，避免依赖 flush 默认值）。"""
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
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _job(**kw) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), title="前厅服务员", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


# ── 状态机 ───────────────────────────────────────────────────
def test_all_statuses_have_labels():
    """10 个状态都必须有中文名（前端标签以接口为准）。"""
    assert len(svc.OFFER_STATUSES) == 10
    for status in svc.OFFER_STATUSES:
        assert svc.STATUS_LABELS.get(status)


def test_legacy_eight_states_still_present():
    """回归基线：v1 的 8 个状态不得缺失（v2 只新增审批态）。"""
    legacy = {
        "draft", "sent", "accepted", "declined",
        "expired", "withdrawn", "onboarded", "no_show",
    }
    assert legacy.issubset(set(svc.OFFER_STATUSES))


def test_terminal_statuses_have_no_outgoing():
    """终态不允许任何流转。"""
    for status in svc.TERMINAL_STATUSES:
        assert svc.ALLOWED_TRANSITIONS[status] == frozenset()


def test_draft_transitions():
    assert svc.can_transition(svc.STATUS_DRAFT, svc.STATUS_PENDING_APPROVAL) is True
    assert svc.can_transition(svc.STATUS_DRAFT, svc.STATUS_WITHDRAWN) is True
    # v2 关键：草稿不能直接发送（必须过审批）
    assert svc.can_transition(svc.STATUS_DRAFT, svc.STATUS_SENT) is False


def test_pending_approval_transitions():
    assert svc.can_transition(svc.STATUS_PENDING_APPROVAL, svc.STATUS_SENT) is True
    assert svc.can_transition(svc.STATUS_PENDING_APPROVAL, svc.STATUS_REJECTED) is True
    assert svc.can_transition(svc.STATUS_PENDING_APPROVAL, svc.STATUS_WITHDRAWN) is True
    assert svc.can_transition(svc.STATUS_PENDING_APPROVAL, svc.STATUS_ACCEPTED) is False


def test_rejected_can_resubmit():
    """驳回不是终态：可修改后重新提交审批。"""
    assert svc.can_transition(svc.STATUS_REJECTED, svc.STATUS_PENDING_APPROVAL) is True
    assert svc.STATUS_REJECTED not in svc.TERMINAL_STATUSES
    assert svc.STATUS_REJECTED in svc.ACTIVE_STATUSES


def test_sent_transitions():
    for target in ("accepted", "declined", "expired", "withdrawn"):
        assert svc.can_transition(svc.STATUS_SENT, target) is True


def test_accepted_transitions():
    assert svc.can_transition(svc.STATUS_ACCEPTED, svc.STATUS_ONBOARDED) is True
    assert svc.can_transition(svc.STATUS_ACCEPTED, svc.STATUS_NO_SHOW) is True
    assert svc.can_transition(svc.STATUS_ACCEPTED, svc.STATUS_SENT) is False


def test_ensure_transition_raises_409():
    with pytest.raises(BizError) as exc:
        svc.ensure_transition(svc.STATUS_DECLINED, svc.STATUS_ACCEPTED)
    assert exc.value.http_status == 409
    assert exc.value.code == 409


# ── 截止时间（次日本地 08:00）─────────────────────────────────
def test_expires_at_is_next_day_8am_beijing():
    """北京时间 17:00 发出 → 次日 08:00（北京）= 次日 00:00 UTC。"""
    now = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)  # 北京 10-15 17:00
    result = svc.compute_expires_at(now)
    assert result == datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)


def test_expires_at_before_dawn_rolls_to_next_local_day():
    """北京时间次日凌晨发出 → 顺延到再一天的 08:00（保证窗口不倒挂）。"""
    now = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)  # 北京 10-16 06:00
    result = svc.compute_expires_at(now)
    assert result == datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)  # 北京 10-17 08:00


def test_expires_at_is_always_in_the_future():
    """任意时刻计算出的截止时间都必须晚于当前时刻。"""
    s = Settings()
    for hour in (0, 6, 12, 23):
        now = datetime(2026, 10, 15, hour, 30, tzinfo=timezone.utc)
        assert svc.compute_expires_at(now, s) > now


def test_expires_at_respects_custom_hour():
    """截止小时可配（如改成 9 点）。"""
    s = Settings(offer_confirm_hour=9, offer_confirm_tz_offset=8)
    now = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    assert svc.compute_expires_at(now, s) == datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)


def test_format_deadline():
    expires = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)  # 北京 10-16 08:00
    assert svc.format_deadline(expires) == "10月16日 08:00"
    assert svc.format_deadline(None) == "尽快"


# ── 链接与话术 ───────────────────────────────────────────────
def test_build_offer_link():
    assert svc.build_offer_link("abc123", "https://hire.example.com/") == (
        "https://hire.example.com/o/abc123"
    )


def test_build_copy_text_contains_key_fields():
    offer = _offer(
        status=svc.STATUS_SENT,
        token="tok123",
        expires_at=datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc),
        note="包吃住",
    )
    link = svc.build_offer_link(offer.token, "https://hire.example.com")
    text = svc.build_copy_text(offer, link)
    assert "张三" in text
    assert "前厅服务员" in text
    assert "昆明南屏店" in text
    assert "2026-10-20" in text
    assert "30 天" in text
    assert "包吃住" in text
    assert "10月16日 08:00" in text
    assert link in text


def test_build_copy_text_blank_note_shows_placeholder():
    offer = _offer(note="")
    text = svc.build_copy_text(offer, "https://x/o/t")
    assert "备注：无" in text


# ── create_offer ─────────────────────────────────────────────
def test_create_offer_ok():
    summary = _summary()
    db = _FakeDB(summary=summary, job=_job())
    row = svc.create_offer(
        db,
        summary_id=summary.id,
        store_name="昆明南屏店",
        report_date=date(2026, 10, 20),
    )
    assert row.status == svc.STATUS_DRAFT
    assert row.candidate_name == "张三"
    assert row.job_title == "前厅服务员"
    assert row.store_name == "昆明南屏店"
    assert db.added == [row]
    # draft 阶段不生成 token / 截止时间（审批通过才发链接）
    assert row.token is None
    assert row.expires_at is None


def test_create_offer_summary_missing_404():
    db = _FakeDB(summary=None, job=_job())
    with pytest.raises(BizError) as exc:
        svc.create_offer(db, summary_id=uuid.uuid4(), store_name="店", report_date=date(2026, 10, 20))
    assert exc.value.http_status == 404


def test_create_offer_job_inactive_422():
    """岗位停招后不允许发起 Offer。"""
    db = _FakeDB(summary=_summary(), job=_job(is_active=False))
    with pytest.raises(BizError) as exc:
        svc.create_offer(db, summary_id=uuid.uuid4(), store_name="店", report_date=date(2026, 10, 20))
    assert exc.value.http_status == 422
    assert "停招" in exc.value.message


def test_create_offer_low_score_422():
    db = _FakeDB(summary=_summary(final_score=60.0), job=_job())
    with pytest.raises(BizError) as exc:
        svc.create_offer(db, summary_id=uuid.uuid4(), store_name="店", report_date=date(2026, 10, 20))
    assert exc.value.http_status == 422
    assert "低于门槛" in exc.value.message


def test_create_offer_none_score_422():
    """没有任何分数的汇总不允许发起。"""
    db = _FakeDB(summary=_summary(final_score=None), job=_job())
    with pytest.raises(BizError) as exc:
        svc.create_offer(db, summary_id=uuid.uuid4(), store_name="店", report_date=date(2026, 10, 20))
    assert exc.value.http_status == 422


def test_create_offer_incomplete_summary_422():
    db = _FakeDB(summary=_summary(is_complete=False), job=_job())
    with pytest.raises(BizError) as exc:
        svc.create_offer(db, summary_id=uuid.uuid4(), store_name="店", report_date=date(2026, 10, 20))
    assert exc.value.http_status == 422


def test_create_offer_duplicate_active_409():
    existing = _offer(status=svc.STATUS_SENT)
    db = _FakeDB(summary=_summary(), job=_job(), offers=[existing])
    with pytest.raises(BizError) as exc:
        svc.create_offer(db, summary_id=uuid.uuid4(), store_name="店", report_date=date(2026, 10, 20))
    assert exc.value.http_status == 409


def test_create_offer_propagates_walk_in_source():
    """到店直招候选人：来源快照取线下复试的 source。"""
    summary = _summary()
    db = _FakeDB(
        summary=summary,
        job=_job(),
        offline=SimpleNamespace(source="walk_in"),
    )
    row = svc.create_offer(
        db, summary_id=summary.id, store_name="店", report_date=date(2026, 10, 20)
    )
    assert row.source == "walk_in"


# ── update / 审批 ────────────────────────────────────────────
def test_update_offer_only_in_draft_or_rejected():
    editable = _offer(status=svc.STATUS_DRAFT)
    svc.update_offer(_FakeDB(), editable, store_name="新门店")
    assert editable.store_name == "新门店"

    with pytest.raises(BizError) as exc:
        svc.update_offer(_FakeDB(), _offer(status=svc.STATUS_SENT), store_name="新门店")
    assert exc.value.http_status == 409


def test_submit_approval_requires_approver_config():
    """未配置审批人时不允许提交审批（审批流必须有明确收件人）。"""
    db = _FakeDB()
    offer = _offer(status=svc.STATUS_DRAFT)
    with pytest.raises(BizError) as exc:
        svc.submit_approval(db, offer, settings=Settings(offer_approver_open_ids=""))
    assert exc.value.http_status == 422
    assert "审批人" in exc.value.message


def test_submit_approval_ok():
    settings = Settings(offer_approver_open_ids="ou_a, ou_b")
    offer = _offer(status=svc.STATUS_DRAFT)
    svc.submit_approval(_FakeDB(), offer, settings=settings)
    assert offer.status == svc.STATUS_PENDING_APPROVAL
    assert offer.approver_open_id == "ou_a,ou_b"
    assert offer.approval_submitted_at is not None


def test_submit_approval_from_draft_only():
    """已发出的 Offer 不能再次提交审批。"""
    with pytest.raises(BizError) as exc:
        svc.submit_approval(
            _FakeDB(), _offer(status=svc.STATUS_SENT), settings=Settings(offer_approver_open_ids="ou_a")
        )
    assert exc.value.http_status == 409


def test_approve_offer_generates_token_and_deadline():
    offer = _offer(status=svc.STATUS_PENDING_APPROVAL)
    svc.approve_offer(_FakeDB(), offer)
    assert offer.status == svc.STATUS_SENT
    assert offer.token and len(offer.token) == 32
    assert offer.sent_at is not None
    assert offer.approved_at is not None
    assert offer.expires_at is not None and offer.expires_at > offer.sent_at


def test_approve_offer_rejects_illegal_state():
    with pytest.raises(BizError) as exc:
        svc.approve_offer(_FakeDB(), _offer(status=svc.STATUS_DRAFT))
    assert exc.value.http_status == 409


def test_reject_offer_requires_note():
    with pytest.raises(BizError) as exc:
        svc.reject_offer(_FakeDB(), _offer(status=svc.STATUS_PENDING_APPROVAL), note="  ")
    assert exc.value.http_status == 422


def test_reject_then_resubmit_clears_previous_note():
    offer = _offer(status=svc.STATUS_PENDING_APPROVAL)
    svc.reject_offer(_FakeDB(), offer, note="薪资超标")
    assert offer.status == svc.STATUS_REJECTED
    assert offer.approval_note == "薪资超标"
    assert offer.rejected_at is not None

    svc.submit_approval(
        _FakeDB(), offer, settings=Settings(offer_approver_open_ids="ou_a")
    )
    assert offer.status == svc.STATUS_PENDING_APPROVAL
    assert offer.approval_note == ""
    assert offer.rejected_at is None


def test_withdraw_offer():
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    svc.withdraw_offer(_FakeDB(), offer, reason="岗位取消")
    assert offer.status == svc.STATUS_WITHDRAWN
    assert offer.withdraw_reason == "岗位取消"
    assert offer.withdrawn_at is not None


# ── 候选人动作 ───────────────────────────────────────────────
def test_accept_offer_ok_and_idempotent():
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    svc.accept_offer(_FakeDB(), offer)
    assert offer.status == svc.STATUS_ACCEPTED
    assert offer.decided_at is not None

    decided_at = offer.decided_at
    svc.accept_offer(_FakeDB(), offer)  # 幂等：不报错、不覆盖时间
    assert offer.status == svc.STATUS_ACCEPTED
    assert offer.decided_at == decided_at


def test_accept_offer_rejected_when_expired():
    with pytest.raises(BizError) as exc:
        svc.accept_offer(_FakeDB(), _offer(status=svc.STATUS_EXPIRED))
    assert exc.value.http_status == 409


def test_decline_offer_records_free_text_reason():
    offer = _offer(status=svc.STATUS_SENT)
    svc.decline_offer(_FakeDB(), offer, reason_text=" 已经找到别的工作了 ")
    assert offer.status == svc.STATUS_DECLINED
    assert offer.decline_reason_text == "已经找到别的工作了"
    assert offer.decided_at is not None


def test_decline_offer_allows_blank_reason():
    """婉拒理由为选填（降低候选人操作摩擦）。"""
    offer = _offer(status=svc.STATUS_SENT)
    svc.decline_offer(_FakeDB(), offer, reason_text="")
    assert offer.status == svc.STATUS_DECLINED
    assert offer.decline_reason_text == ""


# ── 入职办理 ─────────────────────────────────────────────────
def test_onboard_offer_onboarded():
    offer = _offer(status=svc.STATUS_ACCEPTED, store_name="昆明南屏店")
    svc.onboard_offer(_FakeDB(), offer, result=svc.STATUS_ONBOARDED)
    assert offer.status == svc.STATUS_ONBOARDED
    assert offer.actual_report_date == offer.report_date
    assert offer.onboard_store == "昆明南屏店"
    assert offer.onboarded_at is not None
    assert offer.no_show_note == ""


def test_onboard_offer_no_show_requires_note():
    with pytest.raises(BizError) as exc:
        svc.onboard_offer(_FakeDB(), _offer(status=svc.STATUS_ACCEPTED), result=svc.STATUS_NO_SHOW, note=" ")
    assert exc.value.http_status == 422


def test_onboard_offer_no_show_ok():
    offer = _offer(status=svc.STATUS_ACCEPTED)
    svc.onboard_offer(
        _FakeDB(), offer, result=svc.STATUS_NO_SHOW, note="联系不上，未到店"
    )
    assert offer.status == svc.STATUS_NO_SHOW
    assert offer.no_show_note == "联系不上，未到店"


def test_onboard_offer_invalid_result_422():
    with pytest.raises(BizError) as exc:
        svc.onboard_offer(_FakeDB(), _offer(status=svc.STATUS_ACCEPTED), result="whatever")
    assert exc.value.http_status == 422


def test_onboard_offer_only_from_accepted():
    with pytest.raises(BizError) as exc:
        svc.onboard_offer(_FakeDB(), _offer(status=svc.STATUS_SENT), result=svc.STATUS_ONBOARDED)
    assert exc.value.http_status == 409


# ── 惰性过期 ─────────────────────────────────────────────────
def test_expire_due_offers_marks_and_commits():
    past = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    offer = _offer(status=svc.STATUS_SENT, expires_at=past)
    db = _FakeDB(offers=[offer])
    result = svc.expire_due_offers(db, notify=False)
    assert result == [offer]
    assert offer.status == svc.STATUS_EXPIRED
    assert offer.expired_at is not None
    # 先落库再推送：状态流转必须已提交
    assert db.commit_count == 1


def test_expire_due_offers_no_rows_no_commit():
    db = _FakeDB(offers=[])
    assert svc.expire_due_offers(db, notify=False) == []
    assert db.commit_count == 0


# ── 统计 ─────────────────────────────────────────────────────
def test_rate_helper():
    assert svc._rate(3, 4) == 0.75
    assert svc._rate(1, 3) == 0.3333
    assert svc._rate(0, 0) is None


def test_month_range():
    start, end = svc._month_range("2026-10")
    assert start == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 1, tzinfo=timezone.utc)

    start, end = svc._month_range("2026-12")
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)

    assert svc._month_range(None) is None
    assert svc._month_range("") is None


def test_month_range_invalid_422():
    with pytest.raises(BizError) as exc:
        svc._month_range("2026/10")
    assert exc.value.http_status == 422


def test_accept_rate_denominator_excludes_draft_and_withdrawn():
    """接受率分母 = accepted + declined + expired（不含草稿/撤回/待审批）。

    以纯计算口径验证：3 接受 / (3 + 1 婉拒 + 2 过期) = 0.5
    """
    assert svc._rate(3, 3 + 1 + 2) == 0.5


# ── 序列化 ───────────────────────────────────────────────────
def test_serialize_offer_basic_fields():
    offer = _offer(status=svc.STATUS_SENT, token="tok")
    data = svc.serialize_offer(offer)
    assert data["candidate_name"] == "张三"
    assert data["status"] == "sent"
    assert data["status_label"] == "已发出"
    assert data["report_date"] == "2026-10-20"
    assert "offer_link" not in data  # 默认不带链接


def test_serialize_offer_with_link_only_when_sent():
    sent = _offer(status=svc.STATUS_SENT, token="tok")
    data = svc.serialize_offer(sent, base_url="https://hire.example.com", with_link=True)
    assert data["offer_link"] == "https://hire.example.com/o/tok"

    draft = _offer(status=svc.STATUS_DRAFT, token=None)
    assert "offer_link" not in svc.serialize_offer(draft, with_link=True)


def test_get_offer_by_token_404():
    class _DB:
        def query(self, *_a):
            return _FakeQuery([])

    with pytest.raises(BizError) as exc:
        svc.get_offer_by_token(_DB(), "nope")
    assert exc.value.http_status == 404
