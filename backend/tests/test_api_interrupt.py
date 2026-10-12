"""API 层 interrupt 提取的回归测试（第 2 轮，langgraph 0.6.x）。

验证：
1. 失效的 _get_interrupt_value 已从 app.api.interview 移除；
2. _get_pending_interrupt 能从 graph.get_state(config).tasks[].interrupts[].value
   正确取出首问载荷（Bug 2 修复后的唯一提取路径）。
"""
import importlib
import json
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

import app.api.interview as interview_mod
from app.models.schemas import AnswerScore


def _build_graph():
    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")
    # evaluate 走「get_llm + 纯文本 JSON 解析」，mock 必须打到 get_llm 并返回
    # 带 .content 的对象（旧 mock 打在 get_structured_llm 上是失效的）。
    def _fake_get_llm(*_args, **_kwargs):
        def _invoke(_prompt):
            score = AnswerScore(
                scores={"tech_depth": 8},
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


def _state():
    return {
        "thread_id": "t1",
        "candidate": {"name": "z", "resume_text": "r", "job_desc": "j"},
        "questions": [{"id": "q1", "text": "Q1"}, {"id": "q2", "text": "Q2"}],
        "rubric": [{"key": "tech_depth", "name": "技术深度", "weight": 1.0, "max_score": 10}],
        "current_idx": 0,
        "max_follow_ups": 2,
        "follow_up_count": 0,
        "messages": [],
        "evaluations": [],
        "status": "pending",
        "report": {},
    }


def test_get_interrupt_value_removed():
    """Bug 2 修复：失效的 _get_interrupt_value 应已删除。"""
    assert not hasattr(interview_mod, "_get_interrupt_value")


def test_get_pending_interrupt_returns_first_question():
    """修复后唯一提取函数 _get_pending_interrupt 应正确返回首题载荷。"""
    graph = _build_graph()
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke(_state(), config)
    snapshot = graph.get_state(config)
    value = interview_mod._get_pending_interrupt(snapshot)
    assert value is not None
    assert value["question"] == "Q1"
    assert value["type"] == "question"
