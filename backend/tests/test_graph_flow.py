"""route.py 纯函数单元测试 + 状态机核心流程（Mock LLM + MemorySaver）。

route 测试无需外部服务；状态机测试用 MemorySaver 替代 PostgresSaver，
用 fake LLM 替代真实结构化输出，session_id 留空以跳过 DB 落库。
"""
import importlib
import json
from types import SimpleNamespace

from app.graph.route import route, should_follow_up
from app.services.scoring import verdict_from_score


def _state(**overrides):
    base = {
        "current_idx": 0,
        "questions": [{"id": "q1", "text": "Q1"}, {"id": "q2", "text": "Q2"}],
        "max_follow_ups": 2,
        "follow_up_count": 0,
        "evaluations": [],
    }
    base.update(overrides)
    return base


def _eval(follow_up_needed):
    return {
        "question_id": "q1",
        "scores": {"tech_depth": 8},
        "comment": "",
        "follow_up_needed": follow_up_needed,
        "follow_up_question": "请再展开" if follow_up_needed else "",
    }


# ── should_follow_up ───────────────────────────────────────
def test_should_follow_up_true():
    s = _state(evaluations=[_eval(True)], follow_up_count=0, max_follow_ups=2)
    assert should_follow_up(s) is True


def test_should_follow_up_false_when_not_needed():
    s = _state(evaluations=[_eval(False)])
    assert should_follow_up(s) is False


def test_should_follow_up_false_when_at_limit():
    s = _state(evaluations=[_eval(True)], follow_up_count=2, max_follow_ups=2)
    assert should_follow_up(s) is False


def test_should_follow_up_false_when_no_evaluations():
    s = _state()
    assert should_follow_up(s) is False


def test_should_follow_up_false_when_disabled():
    """max_follow_ups=0：即使评分标记需要追问也彻底禁止（追问取消需求）。"""
    s = _state(evaluations=[_eval(True)], follow_up_count=0, max_follow_ups=0)
    assert should_follow_up(s) is False


def test_should_follow_up_default_when_missing():
    """state 缺 max_follow_ups 键：回落默认 2（允许追问）。"""
    s = _state(evaluations=[_eval(True)], follow_up_count=0)
    del s["max_follow_ups"]
    assert should_follow_up(s) is True


# ── verdict 阈值（80/65/50）────────────────────────────────
def test_verdict_thresholds():
    assert verdict_from_score(80) == "强烈推荐"
    assert verdict_from_score(79.9) == "推荐"
    assert verdict_from_score(65) == "推荐"
    assert verdict_from_score(64.9) == "待定"
    assert verdict_from_score(50) == "待定"
    assert verdict_from_score(49.9) == "不推荐"
    assert verdict_from_score(0) == "不推荐"


# ── route 分流 ─────────────────────────────────────────────
def test_route_follow_up():
    s = _state(evaluations=[_eval(True)], follow_up_count=0, max_follow_ups=2)
    assert route(s) == "ask_question"


def test_route_next_question():
    s = _state(evaluations=[_eval(False)], current_idx=0)
    assert route(s) == "ask_question"  # 仍有下一题


def test_route_final_when_last_question():
    s = _state(evaluations=[_eval(False)], current_idx=1)
    assert route(s) == "final_report"


def test_route_final_when_followup_exhausted_last_question():
    # 最后一题、追问已达上限 -> 收尾
    s = _state(
        evaluations=[_eval(True)],
        current_idx=1,
        follow_up_count=2,
        max_follow_ups=2,
    )
    assert route(s) == "final_report"


def test_route_next_question_when_follow_up_disabled():
    """max_follow_ups=0：评分标记追问也直接进下一题，不追问。"""
    s = _state(evaluations=[_eval(True)], current_idx=0, max_follow_ups=0)
    assert route(s) == "ask_question"
    # 语义校验：走 ask_question 是因为「下一题」而非「追问」
    assert should_follow_up(s) is False


# ── 状态机核心流程（Mock LLM + MemorySaver）───────────────
def _interrupt_value(graph, config):
    """从 StateSnapshot.tasks 中提取 interrupt 载荷（langgraph 0.2.x 正确姿势）。"""
    snapshot = graph.get_state(config)
    tasks = getattr(snapshot, "tasks", None) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or []
        if interrupts:
            return getattr(interrupts[0], "value", interrupts[0])
    return None


def _make_fake_llm_factory(score_sequence):
    """构造 fake get_llm 工厂：按调用次序返回 AnswerScore JSON 文本。

    evaluate 节点走「纯文本 JSON 输出 + 手动解析」方案（多提供商兼容），
    fake 需返回带 .content 属性的对象（content 为 JSON 字符串）。
    """
    from langchain_core.runnables import RunnableLambda

    calls = {"n": 0}

    def _invoke(_prompt):
        idx = min(calls["n"], len(score_sequence) - 1)
        calls["n"] += 1
        payload = score_sequence[idx].model_dump(mode="json")
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    def _factory(*_args, **_kwargs):
        return RunnableLambda(_invoke)

    return _factory


def _make_raw_llm_factory(raw_contents):
    """构造 fake get_llm 工厂：按调用次序返回原始文本（模拟 LLM 偶发脏输出）。"""
    from langchain_core.runnables import RunnableLambda

    calls = {"n": 0}

    def _invoke(_prompt):
        idx = min(calls["n"], len(raw_contents) - 1)
        calls["n"] += 1
        return SimpleNamespace(content=raw_contents[idx])

    def _factory(*_args, **_kwargs):
        return RunnableLambda(_invoke)

    return _factory


# ── evaluate 健壮性（LLM 偶发缺字段 / 脏输出降级）─────────
def test_evaluate_coerces_missing_fields():
    """LLM 漏掉 follow_up_needed / follow_up_question 时兜底填充，不抛异常。"""
    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")

    raw = json.dumps(
        {"scores": {"dim_0": 7.0}, "comment": "只评相关维度"},
        ensure_ascii=False,
    )
    result = evaluate_mod._coerce_answer_score(json.loads(raw))
    assert result.scores == {"dim_0": 7.0}
    assert result.follow_up_needed is False
    assert result.follow_up_question == ""


def test_evaluate_coerces_dirty_scores_and_types():
    """scores 非 dict / 脏值 / 字符串数字均被安全清洗。"""
    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")

    result = evaluate_mod._coerce_answer_score(
        {
            "scores": {"a": "7.5", "b": "not-a-number", "c": None},
            "comment": None,
            "follow_up_needed": "true",
        }
    )
    assert result.scores == {"a": 7.5}  # 仅保留可转数值的项
    assert result.comment == ""
    assert result.follow_up_needed is True


def test_graph_survives_llm_garbage_output(monkeypatch):
    """LLM 输出完全无法解析时降级为中性评分，面试流程不中断。"""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")
    from app.graph.builder import build_interview_graph

    monkeypatch.setattr(
        evaluate_mod,
        "get_llm",
        _make_raw_llm_factory(["这不是 JSON，我拒绝配合。"]),
    )

    graph = build_interview_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-garbage"}}

    graph.invoke(_initial_state("t-garbage", 0), config)
    first_q = _interrupt_value(graph, config)
    assert first_q is not None and first_q["question"] == "问题一"

    # 垃圾输出 -> 降级中性评分 -> 直接下一题（不追问、不崩）
    graph.invoke(Command(resume="回答一"), config)
    nq = _interrupt_value(graph, config)
    assert nq is not None and nq["type"] == "question" and nq["question"] == "问题二"

    graph.invoke(Command(resume="回答二"), config)
    assert _interrupt_value(graph, config) is None

    values = graph.get_state(config).values or {}
    assert values.get("status") == "finished"
    evals = values.get("evaluations") or []
    assert len(evals) == 2
    # 降级记录：scores 为空 dict、不追问
    assert all(e["scores"] == {} and e["follow_up_needed"] is False for e in evals)
    assert values.get("report", {}).get("verdict") is not None


def _initial_state(thread_id: str, max_follow_ups: int) -> dict:
    return {
        "thread_id": thread_id,
        # session_id 留空 -> 跳过 DB 落库
        "candidate": {"name": "张三", "resume_text": "resume", "job_desc": "jd"},
        "questions": [
            {"id": "q1", "text": "问题一"},
            {"id": "q2", "text": "问题二"},
        ],
        "rubric": [
            {"key": "tech_depth", "name": "技术深度", "weight": 0.40, "max_score": 10},
            {"key": "experience", "name": "项目经验", "weight": 0.30, "max_score": 10},
            {"key": "communication", "name": "沟通表达", "weight": 0.15, "max_score": 10},
            {"key": "job_fit", "name": "岗位匹配", "weight": 0.15, "max_score": 10},
        ],
        "current_idx": 0,
        "max_follow_ups": max_follow_ups,
        "follow_up_count": 0,
        "messages": [],
        "evaluations": [],
        "status": "pending",
        "report": {},
    }


def test_full_graph_flow(monkeypatch):
    """用 fake LLM + MemorySaver 跑通 load_context→ask_question→evaluate→route→final_report。

    覆盖：首问 interrupt、答题 resume、追问分流、下一题、收尾报告。
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    # 注意：app.graph.nodes.__init__ 会 re-export evaluate 函数，遮蔽同名子模块属性，
    # 因此用 importlib 精确拿到模块对象（而非被遮蔽的函数）。
    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")
    from app.graph.builder import build_interview_graph
    from app.models.schemas import AnswerScore

    # fake 评分：第 1 题回答触发一次追问，其余不再追问
    monkeypatch.setattr(
        evaluate_mod,
        "get_llm",
        _make_fake_llm_factory(
            [
                AnswerScore(
                    scores={"tech_depth": 8, "experience": 7, "communication": 9, "job_fit": 8},
                    comment="回答有深度，但项目细节可再展开。",
                    follow_up_needed=True,
                    follow_up_question="请展开说明你在该项目中的具体职责。",
                ),
                AnswerScore(
                    scores={"tech_depth": 7, "experience": 8, "communication": 7, "job_fit": 8},
                    comment="回答完整。",
                    follow_up_needed=False,
                    follow_up_question="",
                ),
            ]
        ),
    )

    graph = build_interview_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    # 1) 首次 invoke -> 跑到首问 interrupt
    graph.invoke(_initial_state("t1", 2), config)
    first_q = _interrupt_value(graph, config)
    assert first_q is not None, "首次 invoke 应在首问处 interrupt 挂起"
    assert first_q["type"] == "question"
    assert first_q["question"] == "问题一"
    assert first_q["current_idx"] == 0

    # 2) 提交回答 -> 评分触发追问 -> 再次挂起在追问
    graph.invoke(Command(resume="我的回答一"), config)
    fu = _interrupt_value(graph, config)
    assert fu is not None, "第 1 题回答后应触发追问并挂起"
    assert fu["type"] == "follow_up"
    assert fu["follow_up_count"] == 1
    assert "具体职责" in fu["question"]

    # 3) 回答追问 -> 追问结束进入下一题（问题二）
    graph.invoke(Command(resume="追问回答"), config)
    nq = _interrupt_value(graph, config)
    assert nq is not None, "追问后应进入下一题并挂起"
    assert nq["type"] == "question"
    assert nq["question"] == "问题二"
    assert nq["current_idx"] == 1

    # 4) 回答最后一题 -> 收尾，无 interrupt
    graph.invoke(Command(resume="我的回答二"), config)
    assert _interrupt_value(graph, config) is None, "最后一题回答后应收尾，不再挂起"

    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    assert values.get("status") == "finished"
    assert values.get("report", {}).get("verdict") in {"推荐", "待定", "不推荐", "强烈推荐"}
    # 3 条评分（首答 + 追问答 + 末题答）
    assert len(values.get("evaluations") or []) == 3


def test_graph_no_follow_up_when_disabled(monkeypatch):
    """max_follow_ups=0：即使 LLM 误报 follow_up_needed=true，也绝不追问。

    验证 route 层硬拦截（与 evaluate prompt 的禁用约束构成双保险）。
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    evaluate_mod = importlib.import_module("app.graph.nodes.evaluate")
    from app.graph.builder import build_interview_graph
    from app.models.schemas import AnswerScore

    # fake 评分恒报 follow_up_needed=true，模拟 LLM 不听话
    monkeypatch.setattr(
        evaluate_mod,
        "get_llm",
        _make_fake_llm_factory(
            [
                AnswerScore(
                    scores={"tech_depth": 6, "experience": 6, "communication": 6, "job_fit": 6},
                    comment="回答尚可。",
                    follow_up_needed=True,
                    follow_up_question="能再展开说说吗？",
                ),
            ]
        ),
    )

    graph = build_interview_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-no-fu"}}

    # 首问挂起
    graph.invoke(_initial_state("t-no-fu", 0), config)
    first_q = _interrupt_value(graph, config)
    assert first_q is not None and first_q["question"] == "问题一"

    # 回答第 1 题 -> 即使评分误报追问，也应直接进入下一题
    graph.invoke(Command(resume="回答一"), config)
    nq = _interrupt_value(graph, config)
    assert nq is not None, "应挂起在下一题"
    assert nq["type"] == "question", "max_follow_ups=0 时不得进入追问"
    assert nq["question"] == "问题二"
    assert nq["current_idx"] == 1

    # 回答第 2 题 -> 收尾
    graph.invoke(Command(resume="回答二"), config)
    assert _interrupt_value(graph, config) is None

    values = graph.get_state(config).values or {}
    assert values.get("status") == "finished"
    # 每题各评一次，无追问答
    assert len(values.get("evaluations") or []) == 2
