"""终试面试档案单测：维度归一化 / 短板派生 / 去重（纯函数，不依赖 DB）。"""
from app.services.interview_brief import (
    STAGE_AI,
    STAGE_OFFLINE,
    STAGE_SCREENING,
    _dedupe_items,
    _dims_from_raw,
    _merge_dims,
    derive_dim_insights,
)


# ── 维度归一化：统一到 10 分制 ──────────────────────────────
def test_dims_from_raw_scales_hundred_scale_to_ten():
    """100 分制的维度分折算成 10 分制，便于跨环节对比。"""
    dims = _dims_from_raw([{"key": "skill", "name": "岗位技能", "score": 85, "max_score": 100}])
    assert dims == [
        {"key": "skill", "name": "岗位技能", "score": 85.0, "max_score": 100.0, "norm": 8.5}
    ]


def test_dims_from_raw_keeps_already_normalized_score():
    """AI 报告 / AI 参考分已是 10 分制，不再按 max_score 二次缩放。"""
    dims = _dims_from_raw(
        [{"key": "attitude", "name": "工作态度", "score": 7.5, "max_score": 5}],
        normalized=True,
    )
    assert dims[0]["norm"] == 7.5


def test_dims_from_raw_drops_dirty_rows():
    """缺名 / 非 dict 的维度直接丢弃，不污染矩阵。"""
    dims = _dims_from_raw(
        [
            "not-a-dict",
            {"score": 8},
            {"key": "comm", "name": "沟通表达", "score": 8, "max_score": 10},
        ]
    )
    assert [d["name"] for d in dims] == ["沟通表达"]


# ── 维度矩阵：同名维度跨环节合并 ────────────────────────────
def test_merge_dims_shares_row_by_name():
    rows: dict = {}
    _merge_dims(rows, [{"name": "稳定性", "norm": 8.0}], STAGE_SCREENING)
    _merge_dims(rows, [{"name": "稳定性", "norm": 5.0}], STAGE_AI)
    assert rows["稳定性"]["cells"] == {STAGE_SCREENING: 8.0, STAGE_AI: 5.0}


# ── 短板 / 亮点派生 ─────────────────────────────────────────
def test_derive_dim_insights_low_score_becomes_risk_and_question():
    """低于 6 分：进风险，同时生成一条终试要当面确认的问题。"""
    rows = {"稳定性": {"name": "稳定性", "cells": {STAGE_AI: 5.0}}}
    strengths, risks, pending = derive_dim_insights(rows)
    assert strengths == []
    assert [r["text"] for r in risks] == ["稳定性 偏弱：5.0/10（AI 面试）"]
    assert pending[0]["source"] == "维度短板"
    assert "稳定性" in pending[0]["question"]


def test_derive_dim_insights_high_score_becomes_strength():
    rows = {"服务意识": {"name": "服务意识", "cells": {STAGE_OFFLINE: 9.0}}}
    strengths, risks, pending = derive_dim_insights(rows)
    assert [s["text"] for s in strengths] == ["服务意识 突出：9.0/10（HR/店长复试）"]
    assert risks == [] and pending == []


def test_derive_dim_insights_dedupes_same_dim_across_stages():
    """同一维度在多个环节都低分，只出一条风险（取最低的那个环节）。"""
    rows = {
        "稳定性": {
            "name": "稳定性",
            "cells": {STAGE_SCREENING: 4.0, STAGE_AI: 5.0, STAGE_OFFLINE: 5.5},
        }
    }
    _, risks, pending = derive_dim_insights(rows)
    assert len([r for r in risks if "稳定性" in r["text"]]) == 1
    assert len([p for p in pending if "稳定性" in p["question"]]) == 1
    assert "4.0" in risks[0]["text"]


def test_derive_dim_insights_ignores_boundary():
    """刚好 6 分不算短板、刚好 8 分算亮点（阈值口径固定）。"""
    rows = {
        "边界低": {"name": "边界低", "cells": {STAGE_AI: 6.0}},
        "边界高": {"name": "边界高", "cells": {STAGE_AI: 8.0}},
    }
    strengths, risks, pending = derive_dim_insights(rows)
    assert not any("边界低" in r["text"] for r in risks)
    assert any("边界高" in s["text"] for s in strengths)


# ── 去重 ────────────────────────────────────────────────────
def test_dedupe_items_keeps_first_source_and_limit():
    items = [
        {"text": "A", "source": "简历初筛"},
        {"text": "A", "source": "复试问答"},
        {"question": "B", "source": "复试未答清"},
    ]
    out = _dedupe_items(items)
    assert [i.get("text") or i.get("question") for i in out] == ["A", "B"]
    assert out[0]["source"] == "简历初筛"
    assert len(_dedupe_items(items, limit=1)) == 1
