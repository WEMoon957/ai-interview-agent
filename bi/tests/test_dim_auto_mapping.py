"""BI 维度自动映射单测。

覆盖需求：操作台在「面试模板」新增评分维度后，BI 看板不用手改配置也能自动上轴，
且归并不上的维度不再被丢进「其他」。

直接按文件路径加载 ``bi/app.py``（BI 是独立服务，不 import backend），
模块名刻意起得不一样，避免和 backend 的 ``app`` 抢 ``sys.modules``。
"""
from __future__ import annotations

import importlib.util
import pathlib

BI_DIR = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("bi_app_under_test", BI_DIR / "app.py")
assert _spec and _spec.loader
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)


def _cfg(**overrides) -> dict:
    cfg = {
        "canonical": [dict(c) for c in bi.DEFAULT_DIM_CONFIG["canonical"]],
        "stage_key_map": {
            k: dict(v) for k, v in bi.DEFAULT_DIM_CONFIG["stage_key_map"].items()
        },
        "name_alias": {
            k: list(v) for k, v in bi.DEFAULT_DIM_CONFIG["name_alias"].items()
        },
        "name_map": {},
        "stage_weight": dict(bi.DEFAULT_DIM_CONFIG["stage_weight"]),
    }
    cfg.update(overrides)
    return cfg


def test_new_template_dim_becomes_axis():
    """模板里新增、别名也归并不上的维度 → 自动登记为看板维度轴。"""
    cfg, axes = bi._build_dim_config(_cfg(), [("dim_4", "员工关系管理")])

    auto_key = bi._auto_dim_key("员工关系管理")
    assert {"key": auto_key, "name": "员工关系管理", "auto": True} in axes
    assert cfg["name_map"]["员工关系管理"] == auto_key
    # 位置型 key（dim_N）不能进环节映射，否则不同模板的同名序号会串味
    assert "dim_4" not in cfg["stage_key_map"]["ai"]


def test_same_dim_name_across_templates_shares_one_axis():
    """不同模板、不同序号（dim_0 / dim_5）但同名的维度 → 同一根轴。"""
    _, axes = bi._build_dim_config(
        _cfg(), [("dim_0", "卫生"), ("dim_5", "卫生"), ("dim_1", "招聘执行能力")]
    )

    names = [a["name"] for a in axes]
    assert names.count("卫生") == 1
    assert "招聘执行能力" in names


def test_alias_folds_into_standard_dim():
    """能按别名归并的维度名仍并入标准维度，不额外增加轴。"""
    cfg, axes = bi._build_dim_config(_cfg(), [("dim_1", "服务意识")])

    assert cfg["name_map"]["服务意识"] == "attitude"
    assert [a["key"] for a in axes] == ["job_match", "communication", "attitude", "stability"]


def test_unmapped_dim_score_is_counted_not_dropped():
    """新维度打分要能算进匹配度，而不是落进 other。"""
    cfg, _ = bi._build_dim_config(_cfg(), [("dim_4", "员工关系管理")])
    norm = bi._normalize_dims(
        {
            "ai": [
                {"key": "dim_4", "name": "员工关系管理", "score": 8, "max_score": 10},
                {"key": "dim_2", "name": "沟通表达", "score": 6, "max_score": 10},
            ]
        },
        cfg,
    )

    auto_key = bi._auto_dim_key("员工关系管理")
    assert norm["dims"][auto_key] == 80.0
    assert norm["dims"]["communication"] == 60.0
    assert norm["other"] == []


def test_name_map_override_wins():
    """dim_config.json 的 name_map 可人工指定归属，优先级最高。"""
    cfg, axes = bi._build_dim_config(
        _cfg(name_map={"卫生": "job_match"}), [("dim_4", "卫生")]
    )

    assert [a["key"] for a in axes] == ["job_match", "communication", "attitude", "stability"]
    norm = bi._normalize_dims(
        {"ai": [{"key": "dim_4", "name": "卫生", "score": 7, "max_score": 10}]}, cfg
    )
    assert norm["dims"]["job_match"] == 70.0
    assert norm["other"] == []


def test_history_dim_still_gets_axis_when_template_deleted():
    """模板删了但历史数据还在的维度，也要有轴，避免老候选人数据看不见。"""
    cfg, axes = bi._build_dim_config(_cfg(), [], [("数据使用能力", "ai")])

    auto_key = bi._auto_dim_key("数据使用能力")
    assert any(a["key"] == auto_key for a in axes)
    norm = bi._normalize_dims(
        {"ai": [{"key": "dim_7", "name": "数据使用能力", "score": 5, "max_score": 10}]}, cfg
    )
    assert norm["dims"][auto_key] == 50.0


def test_repeated_build_is_idempotent():
    """同一批维度重复登记不会重复加轴。"""
    _, first = bi._build_dim_config(_cfg(), [("dim_4", "卫生")])
    _, second = bi._build_dim_config(_cfg(), [("dim_4", "卫生"), ("dim_9", "卫生")])
    assert [a["key"] for a in first] == [a["key"] for a in second]


# ── 匹配度口径 ────────────────────────────────────────────────
#
# 回归背景：简历初筛的「技能匹配」和「经验相关」都归并到「岗位匹配」，旧实现按
# **原始维度条数**累加 stage_weight，把 0.20 放大成 0.40，匹配度被抬高；同时
# evidence 只留最后一个原始维度，弹层「简历初筛」列与「匹配度」列对不上账。


def test_stage_weight_not_multiplied_by_dim_count():
    """同一环节挂多个同轴维度时，环节权重只能算一次（0.2，不是 0.2×2）。"""
    cfg = _cfg()
    norm = bi._normalize_dims(
        {
            "screening": [
                {"key": "skill_match", "name": "技能匹配", "score": 7.5, "max_score": 10, "weight": 0.3},
                {"key": "experience", "name": "经验相关", "score": 6.5, "max_score": 10, "weight": 0.3},
            ],
            "ai": [
                {"key": "job_fit", "name": "岗位匹配", "score": 5.97, "max_score": None, "weight": 0.15},
            ],
        },
        cfg,
    )

    # 环节内先合成：(75×0.3 + 65×0.3)/0.6 = 70.0
    assert norm["evidence"]["job_match"]["screening"] == 70.0
    assert norm["evidence"]["job_match"]["ai"] == 59.7
    # 跨环节：(70×0.2 + 59.7×0.4)/0.6 = 63.1；旧实现会算成 64.8
    assert norm["dims"]["job_match"] == 63.1


def test_match_score_reconciles_with_stage_evidence():
    """弹层里「匹配度」必须等于各环节列按 stage_weight 的加权均值。"""
    cfg = _cfg()
    norm = bi._normalize_dims(
        {
            "screening": [
                {"key": "skill_match", "name": "技能匹配", "score": 6.5, "max_score": 10},
                {"key": "experience", "name": "经验相关", "score": 5.5, "max_score": 10},
                {"key": "stability", "name": "稳定性", "score": 6.0, "max_score": 10},
            ],
            "ai": [
                {"key": "job_fit", "name": "岗位匹配", "score": 5.9, "max_score": 10},
                {"key": "stability", "name": "稳定性", "score": 5.6, "max_score": 10},
            ],
        },
        cfg,
    )

    w = cfg["stage_weight"]
    for key, ev in norm["evidence"].items():
        if not ev:
            assert norm["dims"][key] is None
            continue
        total_w = sum(w[s] for s in ev)
        expected = sum(v * w[s] for s, v in ev.items()) / total_w
        assert abs(norm["dims"][key] - round(expected, 1)) <= 0.15


def test_uncovered_dim_is_not_scored_as_zero():
    """covered=False 是「没考察到」，不能当 0 分把匹配度压低。"""
    cfg = _cfg()
    norm = bi._normalize_dims(
        {
            "ai": [
                {"key": "job_fit", "name": "岗位匹配", "score": 0, "max_score": 10,
                 "weight": 0.2, "covered": False},
                {"key": "communication", "name": "沟通表达", "score": 6.0, "max_score": 10,
                 "weight": 0.2, "covered": True},
            ],
        },
        cfg,
    )

    assert norm["dims"]["job_match"] is None
    assert norm["evidence"]["job_match"] == {}
    assert norm["dims"]["communication"] == 60.0


def test_covered_zero_still_counts():
    """真正被考察到却给了 0 分，仍然要算 0，而不是当成缺失。"""
    cfg = _cfg()
    norm = bi._normalize_dims(
        {"ai": [{"key": "job_fit", "name": "岗位匹配", "score": 0, "max_score": 10,
                 "weight": 0.2, "covered": True}]},
        cfg,
    )

    assert norm["dims"]["job_match"] == 0.0


def test_within_stage_uses_raw_weight():
    """环节内多维度合并按原始权重加权，而不是等权平均。"""
    cfg = _cfg()
    norm = bi._normalize_dims(
        {
            "ai": [
                {"key": "job_fit", "name": "岗位匹配", "score": 8.0, "max_score": 10, "weight": 0.3},
                {"key": "tech_depth", "name": "技术深度", "score": 4.0, "max_score": 10, "weight": 0.1},
            ],
        },
        cfg,
    )

    # (80×0.3 + 40×0.1)/0.4 = 70；等权平均会得到 60
    assert norm["dims"]["job_match"] == 70.0


def test_missing_raw_weight_falls_back_to_equal_average():
    """原始维度没带权重时退回等权平均，不能凭条数放大。"""
    cfg = _cfg()
    norm = bi._normalize_dims(
        {
            "ai": [
                {"key": "job_fit", "name": "岗位匹配", "score": 8.0, "max_score": 10},
                {"key": "tech_depth", "name": "技术深度", "score": 4.0, "max_score": 10},
            ],
        },
        cfg,
    )

    assert norm["dims"]["job_match"] == 60.0

