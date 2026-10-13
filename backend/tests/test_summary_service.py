"""招聘汇总聚合单测：加权重归一化 / 阶段推导（纯函数，不依赖 DB）。"""
from app.services.summary import (
    STAGE_AI_DONE,
    STAGE_AI_INTERVIEW,
    STAGE_IMPORTED,
    STAGE_LABELS,
    STAGE_OFFLINE_DONE,
    STAGE_OFFLINE_SCHEDULED,
    STAGE_SCREENED,
    compute_final_score,
    _resolve_stage,
)


# ── compute_final_score ────────────────────────────────────
def test_final_score_all_stages():
    """三阶段齐全：按 0.2 / 0.4 / 0.4 加权。"""
    score = compute_final_score(80, 70, 90, w_screening=0.2, w_ai=0.4, w_offline=0.4)
    assert score == round(80 * 0.2 + 70 * 0.4 + 90 * 0.4, 2)
    assert score == 80.0


def test_final_score_renormalizes_when_missing_stage():
    """缺线下复试时，仅对初筛 + AI 两阶段重新归一化（缺环节不惩罚）。"""
    score = compute_final_score(60, 80, None, w_screening=0.2, w_ai=0.4, w_offline=0.4)
    # (60*0.2 + 80*0.4) / (0.2+0.4) = 44 / 0.6 = 73.33
    assert score == round((60 * 0.2 + 80 * 0.4) / 0.6, 2)


def test_final_score_only_screening():
    assert compute_final_score(75, None, None) == 75.0


def test_final_score_skips_none_screening_renormalizing_ai_offline():
    """免初筛岗位：初筛分为 None，最终分只由 AI 面试 + 线下复试按 0.4/0.4 归一化。"""
    score = compute_final_score(None, 80, 90, w_screening=0.2, w_ai=0.4, w_offline=0.4)
    assert score == round((80 * 0.4 + 90 * 0.4) / 0.8, 2)
    assert score == 85.0


def test_stage_labels_cover_imported():
    """仅导入未初筛单独成阶段，避免误标为「已初筛」。"""
    assert STAGE_LABELS[STAGE_IMPORTED] == "已导入待面试"


def test_final_score_none_when_no_data():
    assert compute_final_score(None, None, None) is None
    assert compute_final_score(None, None, None, w_screening=0, w_ai=0, w_offline=0) is None


# ── 阶段推导 ───────────────────────────────────────────────
def test_resolve_stage_variants():
    assert _resolve_stage(70, False, None, False, None) == STAGE_SCREENED
    assert _resolve_stage(70, True, None, False, None) == STAGE_AI_INTERVIEW
    assert _resolve_stage(70, True, 75, False, None) == STAGE_AI_DONE
    assert _resolve_stage(70, True, 75, True, None) == STAGE_OFFLINE_SCHEDULED
    assert _resolve_stage(70, True, 75, True, 88) == STAGE_OFFLINE_DONE


def test_resolve_stage_offline_done_even_without_ai_score():
    """线下已打分即视为完成阶段（即便 AI 分缺失）。"""
    assert _resolve_stage(None, False, None, True, 90) == STAGE_OFFLINE_DONE
