"""面试原话留存：逐轮配对提取 + 评分对齐 的单测。

对应需求「AI 面试要拿到候选人所有原话，再基于原话分析」：
- 原话必须逐字完整（不摘要、不改写），且不依赖评分是否成功；
- 历史会话可从 checkpoint 的 messages 反推配对，开场白不能被当成提问；
- 报告页的「原话 ↔ 评分」按轮次对齐，追问（同一 question_id 多条）不能错位。
"""
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from app.services.interview_turns import align_evaluations, extract_qa_pairs

OPENING = "张三，你好！欢迎参加本次 AI 面试。接下来我会围绕岗位要求逐题向你提问……"


def _turn(turn_index: int, question_id: str = "q1"):
    """构造一条原话记录桩（只需要 align_evaluations 用到的字段）。"""
    return SimpleNamespace(turn_index=turn_index, question_id=question_id)


def _eval(question_id: str, comment: str = ""):
    """构造一条评分记录桩。"""
    return SimpleNamespace(
        question_id=question_id,
        scores={"service": 8.0},
        comment=comment,
        follow_up_needed=False,
        follow_up_question="",
        created_at=None,
    )


class TestExtractPairs:
    """从 checkpoint messages 反推「提问 → 回答」配对。"""

    def test_skips_opening_and_keeps_every_answer(self):
        messages = [
            AIMessage(content=OPENING),
            AIMessage(content="请介绍一下你在后厨的工作经历。"),
            HumanMessage(content="我在后厨做了三年，主要切配。"),
            AIMessage(content="高峰期你一般怎么安排先后？"),
            HumanMessage(content="先做容易凉的那几单。"),
        ]

        pairs = extract_qa_pairs(messages)

        assert pairs == [
            ("请介绍一下你在后厨的工作经历。", "我在后厨做了三年，主要切配。"),
            ("高峰期你一般怎么安排先后？", "先做容易凉的那几单。"),
        ]

    def test_follow_up_is_a_separate_pair(self):
        """追问是独立一轮，原话不能被合并进上一轮。"""
        messages = [
            AIMessage(content=OPENING),
            AIMessage(content="你会做什么菜？"),
            HumanMessage(content="家常菜都能做。"),
            AIMessage(content="能举个最近做的例子吗？"),
            HumanMessage(content="上周做了红烧肉和清蒸鱼。"),
        ]

        pairs = extract_qa_pairs(messages)

        assert len(pairs) == 2
        assert pairs[1] == ("能举个最近做的例子吗？", "上周做了红烧肉和清蒸鱼。")

    def test_orphan_answer_is_dropped(self):
        """没有前置提问的孤立回答直接丢弃，不产生错配。"""
        pairs = extract_qa_pairs([HumanMessage(content="我先说两句")])
        assert pairs == []

    def test_empty_messages(self):
        assert extract_qa_pairs([]) == []
        assert extract_qa_pairs(None) == []


class TestAlignEvaluations:
    """原话与评分的对齐口径。"""

    def test_same_question_follow_ups_are_consumed_in_order(self):
        """同一道题的多次追问（question_id 相同）必须按顺序一对一，不能都挂到第一条。"""
        turns = [_turn(1, "q1"), _turn(2, "q1"), _turn(3, "q2")]
        evaluations = [_eval("q1", "首答"), _eval("q1", "追问"), _eval("q2", "第二题")]

        aligned = align_evaluations(turns, evaluations)

        assert aligned[1]["comment"] == "首答"
        assert aligned[2]["comment"] == "追问"
        assert aligned[3]["comment"] == "第二题"

    def test_fewer_evaluations_than_turns_keeps_turn_without_evaluation(self):
        """评分少于轮次时（评分失败），原话仍保留，只是没有 evaluation。"""
        turns = [_turn(1, "q1"), _turn(2, "q1")]
        evaluations = [_eval("q1", "首答")]

        aligned = align_evaluations(turns, evaluations)

        assert aligned[1]["comment"] == "首答"
        assert 2 not in aligned

    def test_empty_evaluations(self):
        assert align_evaluations([_turn(1)], []) == {}

    def test_legacy_turns_without_question_id_fall_back_to_order(self):
        """回填的历史原话没有 question_id，退化为顺序对齐。"""
        turns = [_turn(1, ""), _turn(2, "")]
        evaluations = [_eval("q1", "第一"), _eval("q2", "第二")]

        aligned = align_evaluations(turns, evaluations)

        assert aligned[1]["comment"] == "第一"
        assert aligned[2]["comment"] == "第二"
