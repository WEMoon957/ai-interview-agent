"""汇总自动落库单测：``rebuild_job_summary`` / ``auto_rebuild_summary``。

背景：BI 看板「候选人流程」表与后台「数据汇总」同源读取 ``recruitment_summaries``，
以前只有手动点「生成 / 更新汇总落库」才会刷新，导致新候选人迟迟不出现。
改造后业务事件（初筛上传 / AI 报告 / 线下打分 / 采纳 AI 分 / 转写）会自动重建快照，
且**任何异常都不得影响主流程**。

覆盖点：
- 无快照时新增、有快照时覆盖（幂等）；
- ``notify=False`` 不推 HR；``notify=True`` 仅对「新完成且未提醒过」的候选人推送一次；
- 自动落库失败时吞掉异常并 rollback（不向上抛）。
"""
import uuid
from types import SimpleNamespace

import pytest

from app.db.models.job import JobPosting
from app.db.models.summary import RecruitmentSummary
from app.services import summary as svc


# ── 测试桩 ───────────────────────────────────────────────────
class _FakeQuery:
    """极简 query 桩：链式调用返回自身，取数返回预设行。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeDB:
    """极简 DB 桩（不落库）。"""

    def __init__(self, *, job=None, existing=None):
        self.job = job
        self.existing = existing
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def get(self, model, _pk):
        return self.job if model is JobPosting else None

    def query(self, *_entities):
        return _FakeQuery([self.existing] if self.existing is not None else [])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _job(title="店员"):
    return SimpleNamespace(id=uuid.uuid4(), title=title, skip_screening=False)


def _row(candidate_id, **overrides):
    """构造 ``build_summary_rows`` 的返回行（字段与生产实现保持一致）。"""
    row = {
        "candidate_id": str(candidate_id),
        "candidate_name": "张三",
        "screening_score": 80.0,
        "ai_interview_score": 70.0,
        "offline_score": 90.0,
        "final_score": 80.0,
        "stage": "offline_done",
        "final_verdict": "推荐",
        "is_complete": True,
        "summary": "简历初筛 80 分；AI面试 70 分；线下复试 90 分。",
        "screening_id": None,
        "session_id": None,
        "session_token": None,
        "offline_id": None,
        "offline_status": "done",
        "offline_scheduled_at": None,
        "interviewer": "店长",
        "screening_status": "screened",
        "session_status": "finished",
        "ai_interview_verdict": "推荐",
        "ai_interview_summary": "不错",
        "offline_source": "walk_in",
        "is_walk_in": True,
    }
    row.update(overrides)
    return row


# ── rebuild_job_summary ────────────────────────────────────
def test_rebuild_creates_snapshot_when_absent(monkeypatch):
    """首次落库：新增快照行并写入聚合结果。"""
    job = _job()
    db = _FakeDB(job=job)
    cid = uuid.uuid4()
    monkeypatch.setattr(svc, "build_summary_rows", lambda _db, _jid: [_row(cid)])

    result = svc.rebuild_job_summary(db, job.id, notify=False)

    assert result["saved"] == 1
    assert result["notify"]["status"] == "skipped"
    assert db.commits == 1
    assert len(db.added) == 1
    saved = db.added[0]
    assert isinstance(saved, RecruitmentSummary)
    assert saved.candidate_id == cid
    assert saved.final_score == 80.0
    assert saved.is_complete is True
    assert saved.snapshot_at is not None
    assert saved.detail["is_walk_in"] is True


def test_rebuild_updates_existing_snapshot_idempotently(monkeypatch):
    """已有快照：原地覆盖，不新增行（幂等）。"""
    job = _job()
    cid = uuid.uuid4()
    existing = SimpleNamespace(
        notified_at=None,
        job_id=job.id,
        candidate_id=cid,
        # 终试闸门字段：历史快照默认为 none（未进入终试流程）
        final_interview_status="none",
        is_complete=True,
    )
    db = _FakeDB(job=job, existing=existing)
    monkeypatch.setattr(svc, "build_summary_rows", lambda _db, _jid: [_row(cid, final_score=88.0)])

    result = svc.rebuild_job_summary(db, job.id, notify=False)

    assert result["saved"] == 1
    assert db.added == []
    assert existing.final_score == 88.0
    assert existing.snapshot_at is not None


def test_rebuild_notify_sends_once_for_new_completed(monkeypatch):
    """新完成且未提醒：推送一次，成功后写 notified_at。"""
    job = _job()
    cid = uuid.uuid4()
    existing = SimpleNamespace(
        notified_at=None,
        job_id=job.id,
        candidate_id=cid,
        # 终试闸门字段：历史快照默认为 none（未进入终试流程）
        final_interview_status="none",
        is_complete=True,
    )
    db = _FakeDB(job=job, existing=existing)
    calls = []

    monkeypatch.setattr(svc, "build_summary_rows", lambda _db, _jid: [_row(cid)])
    monkeypatch.setattr(
        svc,
        "notify_summary_review",
        lambda **kw: calls.append(kw) or {"status": "sent", "channel": "api"},
    )

    result = svc.rebuild_job_summary(db, job.id, notify=True)

    assert result["notify"]["status"] == "sent"
    assert len(calls) == 1
    assert calls[0]["job_title"] == "店员"
    assert calls[0]["items"][0]["candidate_name"] == "张三"
    assert existing.notified_at is not None


def test_rebuild_notify_skipped_when_already_notified(monkeypatch):
    """已提醒过：不再重复推送（幂等），避免重复刷屏。"""
    job = _job()
    cid = uuid.uuid4()
    existing = SimpleNamespace(
        notified_at=object(),
        job_id=job.id,
        candidate_id=cid,
        final_interview_status="none",
        is_complete=True,
    )
    db = _FakeDB(job=job, existing=existing)
    monkeypatch.setattr(svc, "build_summary_rows", lambda _db, _jid: [_row(cid)])
    monkeypatch.setattr(svc, "notify_summary_review", lambda **_kw: pytest.fail("不应重复推送"))

    result = svc.rebuild_job_summary(db, job.id, notify=True)

    assert result["notify"]["status"] == "skipped"


def test_rebuild_notify_failure_does_not_break_persist(monkeypatch):
    """飞书推送异常：落库照常成功，异常不外抛。"""
    job = _job()
    cid = uuid.uuid4()
    existing = SimpleNamespace(
        notified_at=None,
        job_id=job.id,
        candidate_id=cid,
        # 终试闸门字段：历史快照默认为 none（未进入终试流程）
        final_interview_status="none",
        is_complete=True,
    )
    db = _FakeDB(job=job, existing=existing)
    monkeypatch.setattr(svc, "build_summary_rows", lambda _db, _jid: [_row(cid)])

    def _boom(**_kw):
        raise RuntimeError("feishu down")

    monkeypatch.setattr(svc, "notify_summary_review", _boom)

    result = svc.rebuild_job_summary(db, job.id, notify=True)

    assert result["saved"] == 1
    assert result["notify"]["status"] == "failed"
    assert existing.notified_at is None


def test_rebuild_returns_zero_for_missing_job(monkeypatch):
    """岗位不存在：返回 saved=0，不抛异常（自动触发时可能遇到已删岗位）。"""
    db = _FakeDB(job=None)
    monkeypatch.setattr(svc, "build_summary_rows", lambda _db, _jid: pytest.fail("不应聚合"))

    result = svc.rebuild_job_summary(db, uuid.uuid4(), notify=True)

    assert result["saved"] == 0
    assert result["notify"]["status"] == "skipped"


# ── auto_rebuild_summary（自愈：绝不影响主流程）────────────────
def test_auto_rebuild_swallows_exception(monkeypatch):
    """自动落库失败：吞异常 + rollback + close，不向上抛。"""
    import app.db.session as dbsession

    fake = _FakeDB()

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dbsession, "SessionLocal", lambda: fake)
    monkeypatch.setattr(svc, "rebuild_job_summary", _boom)

    svc.auto_rebuild_summary(uuid.uuid4(), notify=True)

    assert fake.rollbacks == 1
    assert fake.closed is True


def test_auto_rebuild_uses_own_session_and_closes(monkeypatch):
    """自动落库使用独立会话（读得到已提交数据），完成后必然 close。"""
    import app.db.session as dbsession

    fake = _FakeDB()
    seen = {}

    def _fake_rebuild(db, job_id, *, notify=True, now=None):
        seen["db"] = db
        seen["job_id"] = job_id
        seen["notify"] = notify
        return {"saved": 1, "notify": {"status": "skipped"}}

    monkeypatch.setattr(dbsession, "SessionLocal", lambda: fake)
    monkeypatch.setattr(svc, "rebuild_job_summary", _fake_rebuild)

    jid = uuid.uuid4()
    svc.auto_rebuild_summary(jid, notify=False)

    assert seen["db"] is fake
    assert seen["job_id"] == jid
    assert seen["notify"] is False
    assert fake.closed is True


def test_auto_rebuild_ignores_empty_job_id(monkeypatch):
    """无 job_id（数据异常）：直接返回，不建会话。"""
    import app.db.session as dbsession

    monkeypatch.setattr(dbsession, "SessionLocal", lambda: pytest.fail("不应建会话"))
    svc.auto_rebuild_summary(None)
