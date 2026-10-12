"""API 路由层 interrupt 链路的端到端回归测试（第 2 轮，Bug 2 修复验证）。

用 mock DB session + MemorySaver 图 + fake LLM，直接调用 get_context / submit_answer，
证明：
- get_context 首次能返回首题（不再为空）；
- submit_answer 能正确分流 follow_up / next_question / finished（不再恒 finished）。
"""
import importlib
import uuid

import pytest
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import app.api.interview as interview_mod
from app.api.schemas import AnswerSubmit
from app.models.schemas import AnswerScore


class FakeSession:
    def __init__(self, thread_id, status="pending"):
        self.id = uuid.uuid4()
        self.thread_id = thread_id
        self.status = status
        self.started_at = None


class FakeDB:
    def commit(self):
        pass


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


def _make_graph(follow_up_on_first=True):
    """构建 MemorySaver 图；第 1 次评分触发一次追问，其余不追问。"""
    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")
    state = {"n": 0}

    # evaluate 走「get_llm + 纯文本 JSON 解析」，mock 必须打到 get_llm
    # 并返回带 .content 的对象（打在 get_structured_llm 上是失效的）。
    def fake_llm(*_args, **_kwargs):
        def _invoke(_prompt):
            import json
            from types import SimpleNamespace

            state["n"] += 1
            if state["n"] == 1 and follow_up_on_first:
                score = AnswerScore(
                    scores={"tech_depth": 8, "experience": 7, "communication": 9, "job_fit": 8},
                    comment="可再展开",
                    follow_up_needed=True,
                    follow_up_question="请展开说明你的具体职责。",
                )
            else:
                score = AnswerScore(
                    scores={"tech_depth": 7, "experience": 8, "communication": 7, "job_fit": 8},
                    comment="完整",
                    follow_up_needed=False,
                    follow_up_question="",
                )
            return SimpleNamespace(
                content=json.dumps(score.model_dump(mode="json"), ensure_ascii=False)
            )

        return RunnableLambda(_invoke)

    evaluate_mod.get_llm = fake_llm

    from app.graph.builder import build_interview_graph

    return build_interview_graph(checkpointer=MemorySaver())


def test_get_context_first_run_returns_question(monkeypatch):
    """Bug 2 修复验证：get_context 首次调用应返回首题（不再为空）。"""
    thread_id = "ctx-1"
    graph = _make_graph()

    monkeypatch.setattr(interview_mod, "get_interview_graph", lambda: graph)
    monkeypatch.setattr(
        interview_mod,
        "_load_session",
        lambda db, token: FakeSession(thread_id, status="pending"),
    )
    monkeypatch.setattr(
        interview_mod,
        "_build_initial_state",
        lambda db, sess: _initial_state(thread_id),
    )

    result = interview_mod.get_context("tok", FakeDB())
    data = result["data"]
    assert data["question"] == "问题一"
    assert data["question_id"] == "q1"
    assert data["status"] == "running"
    assert data["opening"]  # 开场白非空
    assert data["progress"] == {"current": 0, "total": 2}


def test_submit_answer_routes_follow_up_then_next_then_finished(monkeypatch):
    """Bug 2 修复验证：submit_answer 应正确分流 follow_up / next_question / finished。"""
    thread_id = "ans-1"
    graph = _make_graph(follow_up_on_first=True)
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(_initial_state(thread_id), config)  # 预跑到首问 interrupt

    monkeypatch.setattr(interview_mod, "get_interview_graph", lambda: graph)
    monkeypatch.setattr(
        interview_mod,
        "_load_session",
        lambda db, token: FakeSession(thread_id, status="running"),
    )

    # 1) 首答 -> 应触发追问
    r1 = interview_mod.submit_answer("tok", AnswerSubmit(answer="我的回答一"), FakeDB())
    assert r1["data"]["status"] == "follow_up"
    assert "具体职责" in r1["data"]["question"]

    # 2) 追问答 -> 追问结束进入下一题
    r2 = interview_mod.submit_answer("tok", AnswerSubmit(answer="追问回答"), FakeDB())
    assert r2["data"]["status"] == "next_question"
    assert r2["data"]["question"] == "问题二"
    assert r2["data"]["progress"] == {"current": 1, "total": 2}

    # 3) 末题答 -> 收尾 finished
    r3 = interview_mod.submit_answer("tok", AnswerSubmit(answer="我的回答二"), FakeDB())
    assert r3["data"]["status"] == "finished"
    assert "已完成" in r3["data"]["message"]


def test_submit_answer_finished_when_session_already_finished(monkeypatch):
    """已完成会话重复提交：应直接返回 finished，不再 resume。"""
    thread_id = "ans-2"
    graph = _make_graph(follow_up_on_first=False)
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(_initial_state(thread_id), config)

    monkeypatch.setattr(interview_mod, "get_interview_graph", lambda: graph)
    monkeypatch.setattr(
        interview_mod,
        "_load_session",
        lambda db, token: FakeSession(thread_id, status="finished"),
    )

    r = interview_mod.submit_answer("tok", AnswerSubmit(answer="再答一次"), FakeDB())
    assert r["data"]["status"] == "finished"
