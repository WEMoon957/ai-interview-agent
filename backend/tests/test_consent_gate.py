"""候选人端《安全知情协议》门禁测试（P1-7 合规声明）。

覆盖：
1. 未同意时 get_context 仅返回 consent_required，且不触发跑图（不消耗 LLM）；
2. agree_consent 记录同意时间，且幂等（重复调用保留首次时间、不重复写库）；
3. 未同意时 submit_answer 抛 403，无法绕过前端门禁直接答题；
4. 同意后 get_context 正常返回首题（面试可正常进行）。
"""
import importlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

import app.api.interview as interview_mod
from app.api.schemas import AnswerSubmit
from app.exceptions import BizError
from app.models.schemas import AnswerScore


class FakeSession:
    def __init__(self, thread_id, status="pending", consent_agreed_at=None):
        self.id = uuid.uuid4()
        self.thread_id = thread_id
        self.status = status
        self.started_at = None
        self.consent_agreed_at = consent_agreed_at


class FakeDB:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _rubric():
    return [
        {"key": "tech_depth", "name": "技术深度", "weight": 0.40, "max_score": 10},
        {"key": "experience", "name": "项目经验", "weight": 0.30, "max_score": 10},
        {"key": "communication", "name": "沟通表达", "weight": 0.15, "max_score": 10},
        {"key": "job_fit", "name": "岗位匹配", "weight": 0.15, "max_score": 10},
    ]


def _initial_state(thread_id):
    return {
        "thread_id": thread_id,
        "candidate": {"name": "张三", "resume_text": "resume", "job_desc": "jd"},
        "questions": [{"id": "q1", "text": "问题一"}, {"id": "q2", "text": "问题二"}],
        "rubric": _rubric(),
        "current_idx": 0,
        "max_follow_ups": 2,
        "follow_up_count": 0,
        "messages": [],
        "evaluations": [],
        "status": "pending",
        "report": {},
    }


def _build_graph():
    """构建 MemorySaver 图（evaluate 打桩，不追击）。"""
    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")

    def _fake_get_llm(*_args, **_kwargs):
        def _invoke(_prompt):
            score = AnswerScore(
                scores={"tech_depth": 7, "experience": 8, "communication": 7, "job_fit": 8},
                comment="ok",
                follow_up_needed=False,
                follow_up_question="",
            )
            return SimpleNamespace(
                content=json.dumps(score.model_dump(mode="json"), ensure_ascii=False)
            )

        return RunnableLambda(_invoke)

    evaluate_mod.get_llm = _fake_get_llm
    from app.graph.builder import build_interview_graph

    return build_interview_graph(checkpointer=MemorySaver())


def _forbid_graph_call(monkeypatch):
    """断言未同意协议时绝不触发跑图。"""
    def _must_not_be_called():
        raise AssertionError("未同意协议时不应调用 get_interview_graph")

    monkeypatch.setattr(interview_mod, "get_interview_graph", _must_not_be_called)


def test_get_context_requires_consent_without_running_graph(monkeypatch):
    """未同意协议：返回 consent_required，且不跑图、不返回题目。"""
    _forbid_graph_call(monkeypatch)
    monkeypatch.setattr(
        interview_mod, "_load_session", lambda db, token: FakeSession("consent-1")
    )

    result = interview_mod.get_context("tok", FakeDB())
    data = result["data"]
    assert data["status"] == "consent_required"
    assert data["question"] == ""
    assert data["opening"] == ""
    assert data["progress"] == {"current": 0, "total": 0}
    assert data["consent"] == {"required": True, "agreed": False, "agreed_at": None}


def test_agree_consent_records_time_and_is_idempotent(monkeypatch):
    """同意接口：记录同意时间；重复调用保留首次时间且不重复写库。"""
    sess = FakeSession("consent-2")
    db = FakeDB()
    monkeypatch.setattr(interview_mod, "_load_session", lambda _db, token: sess)

    first = interview_mod.agree_consent("tok", db)
    assert first["data"]["agreed"] is True
    assert first["data"]["agreed_at"] is not None
    assert db.commits == 1

    second = interview_mod.agree_consent("tok", db)
    assert second["data"]["agreed_at"] == first["data"]["agreed_at"]
    assert db.commits == 1  # 幂等：未重复写库


def test_submit_answer_blocked_before_consent(monkeypatch):
    """未同意协议：直接提交回答应被 403 拒绝（防绕过前端门禁）。"""
    _forbid_graph_call(monkeypatch)
    monkeypatch.setattr(
        interview_mod, "_load_session", lambda db, token: FakeSession("consent-3")
    )

    with pytest.raises(BizError) as exc:
        interview_mod.submit_answer("tok", AnswerSubmit(answer="我的回答"), FakeDB())
    assert exc.value.http_status == 403
    assert exc.value.code == 403
    assert "安全知情协议" in exc.value.message


def test_get_context_returns_question_after_consent(monkeypatch):
    """同意后：get_context 正常返回首题，且带已同意标记。"""
    thread_id = "consent-4"
    agreed_at = datetime.now(timezone.utc)
    graph = _build_graph()

    monkeypatch.setattr(interview_mod, "get_interview_graph", lambda: graph)
    monkeypatch.setattr(
        interview_mod,
        "_load_session",
        lambda db, token: FakeSession(thread_id, consent_agreed_at=agreed_at),
    )
    monkeypatch.setattr(
        interview_mod,
        "_build_initial_state",
        lambda db, sess: _initial_state(thread_id),
    )

    result = interview_mod.get_context("tok", FakeDB())
    data = result["data"]
    assert data["status"] == "running"
    assert data["question"] == "问题一"
    assert data["question_id"] == "q1"
    assert data["consent"]["agreed"] is True
    assert data["consent"]["agreed_at"] == agreed_at.isoformat()