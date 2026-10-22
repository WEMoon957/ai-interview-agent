"""整场面试评分：内容有效性闸门 + 汇总口径。

背景（2026-10-22）：线上出现「录音里什么都没有，却打了 55 分」。
根因是候选人在整场里只说了「嗯/可以/都行」、或录音内容其实是店内派活，
逐字稿「有字」所以照常送评，LLM 只能落到「5-6 分：信息有限但态度端正」
这一档 → 5.5×10=55。本文件锁死三道防线：

1. ``assess_transcript``：没内容的逐字稿不送评（确定性规则，不花 LLM）；
2. ``_aggregate``：未打分的维度按比例分摊权重，不再当 0 分（与 AI 面试同口径）；
3. ``minutes_poller``：不可评时清空 ai_score 并把状态置 ``insufficient``，
   避免旧分被「采纳 AI 分」写进人工结论。
"""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import app.services.minutes_poller as mp
import app.services.whole_interview_scoring as wis
from app.services.whole_interview_scoring import (
    DEFAULT_RUBRIC,
    _aggregate,
    assess_transcript,
    score_whole_interview,
)

JOB = "隆江猪脚饭店员：负责点单、出餐、桌面清洁，能吃苦耐劳，有餐饮经验优先。"

# 一段正常候选人的回答（约 100 个有效字），用于拼长对话
_LONG_ANSWER = (
    "我之前在火锅店做了两年服务员，主要负责点单上菜和收银，"
    "最忙的时候一个人看八张台，客人投诉我先道歉再看具体是什么问题，"
    "比如说菜上慢了我会去后厨催一下同时跟客人说明还要等多久，"
    "住的地方离这边走路十几分钟上班挺方便的"
)


def _dialog(rounds: int) -> str:
    """构造 rounds 轮问答的逐字稿，每轮都是同一段长回答。"""
    lines: list[str] = []
    for i in range(rounds):
        lines.append(f"说话人{1 if i % 2 == 0 else 2} 00:{i:02d}:00")
        lines.append(_LONG_ANSWER)
    return "\n".join(lines)


# ── 1. 有效性闸门 ────────────────────────────────────────────
def test_empty_transcript_is_not_scorable():
    for text in ("", "   \n\t  \n", None):
        r = assess_transcript(text)
        assert r["scorable"] is False
        assert r["reliability"] == "insufficient"
        assert "为空" in r["reason"]


def test_speaker_labels_only_is_not_scorable():
    """只有「说话人1 00:00:01」标签、没有任何内容。"""
    text = "说话人1 00:00:01\n说话人2 00:00:02\n说话人1 00:00:03\n"
    r = assess_transcript(text)
    assert r["scorable"] is False
    assert r["chars"] == 0


def test_one_sided_transcript_is_not_scorable():
    """只有店长一个人说了很多 —— 没有问答往返，不构成面试。"""
    text = "说话人1 00:00:01\n" + _LONG_ANSWER * 6
    r = assess_transcript(text)
    assert r["scorable"] is False
    assert "只有一方" in r["reason"]


def test_too_short_is_not_scorable():
    """连一句话都没有（<40 有效字）—— 规则直接拦，不浪费 LLM。"""
    text = "说话人1 00:00:01\n做了吗？\n说话人2 00:00:03\n嗯。"
    r = assess_transcript(text)
    assert r["scorable"] is False
    assert r["chars"] < 40


# 以下两段是线上真实形态：字够多，但读完找不到候选人的实质回答。
# 规则层放行（无法凭字数区分「答得短」和「没答」），由 LLM 判 scorable=false。
_FILLER_DIALOG = "\n".join(
    [
        "说话人1 00:00:02",
        "先坐。之前做过餐饮吗？",
        "说话人2 00:00:06",
        "嗯。",
        "说话人1 00:00:08",
        "在哪家做的，做了多久？",
        "说话人2 00:00:11",
        "就是……饭店。",
        "说话人1 00:00:14",
        "大概多久呢？",
        "说话人2 00:00:16",
        "不记得了。",
        "说话人1 00:00:19",
        "我们这边一天要站八九个小时，能接受吗？",
        "说话人2 00:00:24",
        "可以。",
        "说话人1 00:00:26",
        "期望薪资多少？什么时候能来上班？",
        "说话人2 00:00:31",
        "都行。",
    ]
)

_SHOP_CHATTER = "\n".join(
    [
        "说话人1 00:00:05",
        "这个桌子擦一下，那边客人走了。",
        "说话人2 00:00:09",
        "好嘞。",
        "说话人1 00:00:14",
        "后厨的碗还没洗完吗？",
        "说话人2 00:00:17",
        "快了快了。",
        "说话人1 00:00:22",
        "今天中午人挺多的。",
        "说话人2 00:00:25",
        "是啊，忙得很。",
    ]
)


@pytest.mark.parametrize("text", [_FILLER_DIALOG, _SHOP_CHATTER])
def test_noise_transcript_passes_rule_gate_but_marks_low(text):
    """这两类会被规则放行，但必须标 low —— 真正的不评由 LLM 判定。"""
    r = assess_transcript(text)
    assert r["scorable"] is True
    assert r["reliability"] == "low"


@pytest.mark.parametrize("text", [_FILLER_DIALOG, _SHOP_CHATTER])
def test_noise_transcript_is_rejected_by_llm(monkeypatch, text):
    """LLM 必须把「只有语气词/派活对话」判为不可评，不能给中间分。"""
    payload = '{"scorable": false, "scores": {}, "comment": "没有候选人的实质回答"}'
    monkeypatch.setattr(
        wis, "get_llm", lambda **kw: RunnableLambda(lambda _: AIMessage(content=payload))
    )
    r = score_whole_interview(text, JOB, DEFAULT_RUBRIC)
    assert r["scorable"] is False
    assert r["total_score"] is None


def test_brief_but_real_dialog_is_scorable():
    """简短但确有问答（实测 47 字）：不能因为短就判死。"""
    text = "\n".join(
        [
            "说话人1 00:00:01",
            "先坐。之前做过餐饮吗？",
            "说话人2 00:00:05",
            "做过，在火锅店做了两年服务员。",
            "说话人1 00:00:09",
            "主要负责什么？",
            "说话人2 00:00:12",
            "点单、上菜、收银都做，忙的时候一个人看八张台。",
        ]
    )
    r = assess_transcript(text)
    assert r["scorable"] is True
    assert r["reliability"] == "low"  # 短，但该评


def test_normal_dialog_is_scorable():
    r = assess_transcript(_dialog(6))
    assert r["scorable"] is True
    assert r["reliability"] == "ok"
    assert r["speakers"] == 2


# ── 2. 汇总口径：未打分维度按比例分摊，不再当 0 分 ─────────────
def test_aggregate_renormalizes_missing_dims():
    """只答了 2 个维度时不该被未答维度拉低（旧逻辑 8 分两维 → 44 分）。"""
    dims, total = _aggregate({"attitude": 8.0, "communication": 8.0}, DEFAULT_RUBRIC)
    assert total == 80.0
    assert next(d for d in dims if d["key"] == "job_match")["covered"] is False


def test_aggregate_full_coverage_matches_old_formula():
    scores = {"attitude": 8.0, "communication": 7.0, "job_match": 6.0, "stability": 5.0}
    _, total = _aggregate(scores, DEFAULT_RUBRIC)
    # 8*.3 + 7*.25 + 6*.25 + 5*.2 = 6.65
    assert total == 66.5


def test_aggregate_no_scores_returns_none():
    """一个维度都没打分 = 这场评不了，不是考了 0 分。"""
    _, total = _aggregate({}, DEFAULT_RUBRIC)
    assert total is None


# ── 3. score_whole_interview：内容不足时不调 LLM、不出分 ────────
def test_empty_transcript_skips_llm_entirely(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(wis, "get_llm", lambda **kw: calls.append(1))
    r = score_whole_interview("", JOB, DEFAULT_RUBRIC)
    assert calls == []
    assert r["total_score"] is None
    assert r["scorable"] is False
    assert r["reliability"] == "insufficient"


def test_llm_not_scorable_returns_no_score(monkeypatch):
    """闸门放行但 LLM 判「不可评」时，同样不落分。"""
    payload = '{"scorable": false, "scores": {}, "comment": "只有派活对话，没有面试内容"}'
    monkeypatch.setattr(
        wis, "get_llm", lambda **kw: RunnableLambda(lambda _: AIMessage(content=payload))
    )
    r = score_whole_interview(_dialog(6), JOB, DEFAULT_RUBRIC)
    assert r["scorable"] is False
    assert r["total_score"] is None
    assert "派活" in r["comment"]


def test_llm_scores_are_weighted(monkeypatch):
    payload = '{"scorable": true, "scores": {"attitude": 8, "communication": 8}, "comment": "ok"}'
    monkeypatch.setattr(
        wis, "get_llm", lambda **kw: RunnableLambda(lambda _: AIMessage(content=payload))
    )
    r = score_whole_interview(_dialog(6), JOB, DEFAULT_RUBRIC)
    assert r["scorable"] is True
    assert r["total_score"] == 80.0  # 两个 8 分按覆盖权重归一化，不是 44
    assert r["verdict"] == "强烈推荐"


# ── 4. 落库：不可评时清空旧分，避免被「采纳 AI 分」写进人工结论 ──
def _fake_row() -> SimpleNamespace:
    return SimpleNamespace(
        minutes_token="tok_x",
        minutes_url="https://www.feishu.cn/minutes/tok_x",
        transcript_text=None,
        transcript_status="matched",
        transcript_at=None,
        ai_score=55.0,  # 上一轮遗留的分数
        ai_dim_scores=[{"key": "attitude", "score": 5.5}],
        ai_comment="候选人配合度尚可……",
        ai_scored_at=None,
        job_id="job-1",
        job=None,
        candidate_id="cand-1",
    )


def test_poller_clears_stale_score_when_transcript_has_no_content(monkeypatch):
    row = _fake_row()
    commit_calls: list[int] = []
    db = SimpleNamespace(commit=lambda: commit_calls.append(1))

    monkeypatch.setattr(mp, "_candidate_tokens", lambda *a, **k: [("ou_x", "user-token")])
    monkeypatch.setattr(mp, "get_transcript", lambda *a, **k: (True, "嗯。\n喂。", ""))
    monkeypatch.setattr(mp, "_resolve_rubric", lambda db, row: DEFAULT_RUBRIC)
    monkeypatch.setattr(mp, "extract_interview_qa", lambda *a, **k: {"questions": []})
    monkeypatch.setattr(mp, "auto_rebuild_summary", lambda *a, **k: None)

    ok, err = mp.transcribe_and_score_detailed(db, row)

    assert ok is True and err == ""
    assert row.transcript_text == "嗯。\n喂。"
    assert row.ai_score is None, "内容不足时必须清掉旧分"
    assert row.ai_dim_scores == []
    assert row.ai_scored_at is None
    assert row.transcript_status == "insufficient"
    assert commit_calls, "至少提交一次"


@pytest.mark.parametrize("scorable_payload", [
    '{"scorable": false, "scores": {"attitude": 5.5}, "comment": "不可评"}',
])
def test_poller_ignores_scores_when_llm_says_not_scorable(monkeypatch, scorable_payload):
    """LLM 嘴上说不可评、却仍给出分数时，分数必须被丢弃。"""
    row = _fake_row()
    db = SimpleNamespace(commit=lambda: None)

    monkeypatch.setattr(mp, "_candidate_tokens", lambda *a, **k: [("ou_x", "user-token")])
    monkeypatch.setattr(mp, "get_transcript", lambda *a, **k: (True, _dialog(6), ""))
    monkeypatch.setattr(mp, "_resolve_rubric", lambda db, row: DEFAULT_RUBRIC)
    monkeypatch.setattr(mp, "extract_interview_qa", lambda *a, **k: {"questions": []})
    monkeypatch.setattr(mp, "auto_rebuild_summary", lambda *a, **k: None)
    monkeypatch.setattr(
        wis,
        "get_llm",
        lambda **kw: RunnableLambda(lambda _: AIMessage(content=scorable_payload)),
    )

    ok, _ = mp.transcribe_and_score_detailed(db, row)

    assert ok is True
    assert row.ai_score is None
    assert row.transcript_status == "insufficient"
