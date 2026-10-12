"""评分查询接口（/api/scores/candidate/{candidate_id}）单元测试。

验证：按候选人 id 查询，能正确返回候选人信息 + 综合分 + 等级 + 维度得分；
覆盖「有报告 / 无会话 / 无报告 / 候选人不存在」四种分支。
"""
import uuid

import pytest

import app.api.scores as scores_mod
from app.exceptions import BizError


class FakeCandidate:
    def __init__(self, name="张三", email="a@b.com", phone="13800000000"):
        self.id = uuid.uuid4()
        self.name = name
        self.email = email
        self.phone = phone


class FakeSession:
    def __init__(self, candidate_id, status="finished"):
        self.id = uuid.uuid4()
        self.candidate_id = candidate_id
        self.status = status
        self.created_at = None


class FakeReport:
    def __init__(self, total_score=85.5, verdict="推荐"):
        self.total_score = total_score
        self.verdict = verdict
        self.summary = "总体评价"
        self.dim_scores = [
            {"key": "tech_depth", "name": "技术深度", "weight": 0.4, "score": 8.5, "weighted_score": 3.4},
            {"key": "experience", "name": "项目经验", "weight": 0.3, "score": 8.0, "weighted_score": 2.4},
        ]


class FakeDB:
    """极简 mock：按 get / query 返回预设对象。"""

    def __init__(self, candidate=None, session=None, report=None):
        self._candidate = candidate
        self._session = session
        self._report = report

    def get(self, model, key):
        if model.__name__ == "Candidate":
            return self._candidate
        return None

    def query(self, model):
        return _FakeQuery(model, self._session, self._report)


class _FakeQuery:
    def __init__(self, model, session, report):
        self._model = model
        self._session = session
        self._report = report

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        if self._model.__name__ == "InterviewSession":
            return self._session
        if self._model.__name__ == "SessionReport":
            return self._report
        return None


def test_get_score_with_report(monkeypatch):
    """有报告：返回候选人信息 + 综合分 + 等级 + 维度分。"""
    candidate = FakeCandidate()
    session = FakeSession(candidate.id, status="finished")
    report = FakeReport()

    db = FakeDB(candidate=candidate, session=session, report=report)
    monkeypatch.setattr(scores_mod, "get_db", lambda: iter([db]))

    result = scores_mod.get_score_by_candidate(str(candidate.id), db)
    data = result["data"]

    assert data["candidate"]["name"] == "张三"
    assert data["candidate"]["email"] == "a@b.com"
    assert data["session_id"] == str(session.id)
    assert data["status"] == "finished"
    assert data["score"]["total_score"] == 85.5
    assert data["score"]["verdict"] == "推荐"
    assert len(data["score"]["dim_scores"]) == 2
    assert data["score"]["dim_scores"][0]["key"] == "tech_depth"


def test_get_score_no_session(monkeypatch):
    """候选人存在但无会话：返回候选人信息，score 为 null。"""
    candidate = FakeCandidate()
    db = FakeDB(candidate=candidate, session=None, report=None)
    monkeypatch.setattr(scores_mod, "get_db", lambda: iter([db]))

    result = scores_mod.get_score_by_candidate(str(candidate.id), db)
    data = result["data"]

    assert data["candidate"]["name"] == "张三"
    assert data["session_id"] is None
    assert data["score"] is None


def test_get_score_session_without_report(monkeypatch):
    """有会话但未出报告（如面试进行中）：score 各字段为 null。"""
    candidate = FakeCandidate()
    session = FakeSession(candidate.id, status="running")
    db = FakeDB(candidate=candidate, session=session, report=None)
    monkeypatch.setattr(scores_mod, "get_db", lambda: iter([db]))

    result = scores_mod.get_score_by_candidate(str(candidate.id), db)
    data = result["data"]

    assert data["status"] == "running"
    assert data["score"]["total_score"] is None
    assert data["score"]["verdict"] is None
    assert data["score"]["dim_scores"] == []


def test_get_score_candidate_not_found(monkeypatch):
    """候选人不存在：抛 404。"""
    db = FakeDB(candidate=None, session=None, report=None)
    monkeypatch.setattr(scores_mod, "get_db", lambda: iter([db]))

    with pytest.raises(BizError) as exc:
        scores_mod.get_score_by_candidate(str(uuid.uuid4()), db)
    assert exc.value.code == 404


def test_get_score_invalid_uuid(monkeypatch):
    """非法 UUID：抛 422。"""
    db = FakeDB(candidate=None, session=None, report=None)
    monkeypatch.setattr(scores_mod, "get_db", lambda: iter([db]))

    with pytest.raises(BizError) as exc:
        scores_mod.get_score_by_candidate("not-a-uuid", db)
    assert exc.value.code == 422
