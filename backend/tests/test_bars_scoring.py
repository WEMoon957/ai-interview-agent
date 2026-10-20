"""行为锚定评分（BARS）单测：等级夹取 / 原话校验 / 截断抢救。"""
import json

from app.graph.nodes.evaluate import _salvage_truncated_json
from app.services.bars_scoring import (
    BARS_RANGES,
    enforce_level_ranges,
    scores_from_evidence,
    verify_evidence,
)


class TestEnforceLevelRanges:
    """分数必须落在证据等级的区间内。"""

    def test_high_score_with_weak_evidence_is_clamped_down(self):
        """L3 证据（5.5-7.5）却给了 9.5 分 → 压到 7.5。"""
        scores = {"dim_0": 9.5}
        evidence = [{"dimension": "dim_0", "level": "L3", "quote": "做过两年"}]
        assert enforce_level_ranges(scores, evidence) == {"dim_0": 7.5}

    def test_low_score_with_strong_evidence_is_lifted(self):
        """L4 证据（7.5-8.5）却给了 5 分 → 抬到 7.5（证据说话，不靠模型心情）。"""
        scores = {"dim_1": 5.0}
        evidence = [{"dimension": "dim_1", "level": "L4", "quote": "每月到岗 18 人"}]
        assert enforce_level_ranges(scores, evidence) == {"dim_1": 7.5}

    def test_multiple_evidence_uses_highest_level(self):
        scores = {"dim_0": 7.0}
        evidence = [
            {"dimension": "dim_0", "level": "L2", "quote": "做过"},
            {"dimension": "dim_0", "level": "L5", "quote": "留存率 80%"},
        ]
        # L5 区间 8.5-10，7.0 被抬到 8.5
        assert enforce_level_ranges(scores, evidence) == {"dim_0": 8.5}

    def test_dimension_without_evidence_is_untouched(self):
        """没有证据的维度不夹取（是否给分由维度相关性原则决定）。"""
        assert enforce_level_ranges({"dim_9": 3.3}, []) == {"dim_9": 3.3}

    def test_non_ten_max_score_is_scaled(self):
        """满分 5 的维度按比例缩放（L3 变成 2.75-3.75）。"""
        scores = {"dim_0": 4.8}
        evidence = [{"dimension": "dim_0", "level": "L3"}]
        assert enforce_level_ranges(scores, evidence, {"dim_0": 5}) == {"dim_0": 3.75}

    def test_range_table_is_sane(self):
        assert BARS_RANGES["L1"][0] == 0.0
        assert BARS_RANGES["L5"][1] == 10.0


class TestVerifyEvidence:
    """引用的「原话」必须真的出现在回答里。"""

    def test_exact_quote_passes(self):
        evidence = [{"quote": "我在后厨做了三年，主要切配。"}]
        assert verify_evidence("我在后厨做了三年，主要切配。还行", evidence) == 1
        assert evidence[0]["verified"] is True

    def test_quote_ignoring_punctuation_and_space_passes(self):
        """模型漏个标点、多个空格也算通过（归一化后比对）。"""
        evidence = [{"quote": "我在后厨做了三年 主要切配"}]
        assert verify_evidence("我在后厨做了三年，主要切配。", evidence) == 1

    def test_fabricated_quote_fails(self):
        evidence = [{"quote": "我管过二十人的团队并做到零流失"}]
        assert verify_evidence("我在后厨做了三年，主要切配。", evidence) == 0
        assert evidence[0]["verified"] is False

    def test_too_short_quote_is_rejected(self):
        """「我」「行」这类 1-3 字片段没有证据价值，直接判无效。"""
        evidence = [{"quote": "我"}]
        assert verify_evidence("我在后厨做了三年", evidence) == 0

    def test_mixed_evidence_counts_only_passed(self):
        evidence = [
            {"quote": "我在后厨做了三年"},
            {"quote": "这段根本不存在"},
        ]
        assert verify_evidence("我在后厨做了三年，主要切配。", evidence) == 1


class TestScoresFromEvidence:
    """输出被截断、只剩 evidence 时按等级代表分兜底。"""

    def test_picks_highest_level_per_dimension(self):
        evidence = [
            {"dimension": "dim_0", "level": "L2"},
            {"dimension": "dim_0", "level": "L4"},
            {"dimension": "dim_1", "level": "L1"},
        ]
        assert scores_from_evidence(evidence) == {"dim_0": 8.0, "dim_1": 2.0}

    def test_ignores_unknown_level_and_empty_dimension(self):
        evidence = [{"dimension": "", "level": "L3"}, {"dimension": "dim_0", "level": "L9"}]
        assert scores_from_evidence(evidence) == {}


class TestSalvageTruncatedJson:
    """模型输出被截断时尽力捞回字段。"""

    def test_salvages_complete_object(self):
        assert _salvage_truncated_json('{"scores": {"a": 7}}') == {"scores": {"a": 7}}

    def test_salvages_truncated_tail(self):
        text = '{"evidence": [{"dimension": "dim_0", "level": "L3"}], "scores": {"dim_0": 7}, "com'
        parsed = _salvage_truncated_json(text)
        assert parsed["scores"] == {"dim_0": 7}

    def test_falls_back_to_evidence_array(self):
        text = '{"evidence": [{"dimension": "dim_0", "level": "L4", "quote": "每月 18 人"}], "sco'
        parsed = _salvage_truncated_json(text)
        assert parsed["evidence"][0]["level"] == "L4"

    def test_returns_empty_for_garbage(self):
        assert _salvage_truncated_json("抱歉，我无法完成这个任务") == {}
