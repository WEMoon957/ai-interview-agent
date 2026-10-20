"""scoring.py 纯函数单元测试。

覆盖：维度均分 -> 加权 -> 百分制 -> 等级阈值；边界（0 分 / 满分 / 权重不为 1）。
"""
import pytest

from app.models.schemas import DimensionScore, EvaluationRecord, Report
from app.services.scoring import aggregate_report, verdict_from_score


def _rubric():
    """标准 4 维 rubric（权重合计 1.0，各维度满分 10）。"""
    return [
        {"key": "tech_depth", "name": "技术深度", "description": "", "weight": 0.40, "max_score": 10},
        {"key": "experience", "name": "项目经验", "description": "", "weight": 0.30, "max_score": 10},
        {"key": "communication", "name": "沟通表达", "description": "", "weight": 0.15, "max_score": 10},
        {"key": "job_fit", "name": "岗位匹配", "description": "", "weight": 0.15, "max_score": 10},
    ]


def _evals():
    """两条评分记录（固定样例）。"""
    return [
        EvaluationRecord(
            question_id="q1",
            scores={"tech_depth": 8, "experience": 7, "communication": 9, "job_fit": 8},
            comment="c1",
            follow_up_needed=False,
        ),
        EvaluationRecord(
            question_id="q2",
            scores={"tech_depth": 6, "experience": 8, "communication": 7, "job_fit": 9},
            comment="c2",
            follow_up_needed=False,
        ),
    ]


# ── verdict_from_score 阈值（80/65/50，面向基层岗位放宽）────
@pytest.mark.parametrize(
    "score,expected",
    [
        (100, "强烈推荐"),
        (80, "强烈推荐"),
        (79.99, "推荐"),
        (65, "推荐"),
        (64.99, "待定"),
        (50, "待定"),
        (49.99, "不推荐"),
        (0, "不推荐"),
    ],
)
def test_verdict_thresholds(score, expected):
    assert verdict_from_score(score) == expected


# ── aggregate_report 固定样例 ─────────────────────────────
def test_aggregate_report_fixed_sample():
    report = aggregate_report(_evals(), _rubric())

    assert isinstance(report, Report)
    # 维度均分
    by_key = {d.key: d for d in report.dim_scores}
    assert by_key["tech_depth"].score == 7.0       # (8+6)/2
    assert by_key["experience"].score == 7.5       # (7+8)/2
    assert by_key["communication"].score == 8.0    # (9+7)/2
    assert by_key["job_fit"].score == 8.5          # (8+9)/2

    # weighted = avg * weight（源码对 weighted_score 做了 round(..., 2)，故 1.275 -> 1.27）
    assert by_key["tech_depth"].weighted_score == 2.8      # 7.0 * 0.40
    assert by_key["experience"].weighted_score == 2.25     # 7.5 * 0.30
    assert by_key["communication"].weighted_score == 1.2   # 8.0 * 0.15
    assert by_key["job_fit"].weighted_score == 1.27        # round(8.5*0.15, 2)

    # total = Σ round(weighted,2) * 10 = (2.8+2.25+1.2+1.27)*10 = 75.2
    assert report.total_score == 75.2
    assert report.verdict == "推荐"
    assert report.summary  # 非空


def test_aggregate_report_full_score():
    """满分：各维度均 10 -> total 100 -> 强烈推荐。"""
    evals = [
        EvaluationRecord(
            question_id="q1",
            scores={"tech_depth": 10, "experience": 10, "communication": 10, "job_fit": 10},
            comment="perfect",
            follow_up_needed=False,
        )
    ]
    report = aggregate_report(evals, _rubric())
    assert report.total_score == 100.0
    assert report.verdict == "强烈推荐"


def test_aggregate_report_zero_score():
    """0 分：total 0 -> 不推荐。"""
    evals = [
        EvaluationRecord(
            question_id="q1",
            scores={"tech_depth": 0, "experience": 0, "communication": 0, "job_fit": 0},
            comment="empty",
            follow_up_needed=False,
        )
    ]
    report = aggregate_report(evals, _rubric())
    assert report.total_score == 0.0
    assert report.verdict == "不推荐"


def test_aggregate_report_empty_inputs():
    """空 evaluations / 空 rubric：不抛异常，产出确定性空报告。"""
    report = aggregate_report([], _rubric())
    assert report.total_score == 0.0
    assert report.verdict == "不推荐"
    assert len(report.dim_scores) == 4
    assert all(d.score == 0.0 for d in report.dim_scores)

    report2 = aggregate_report([], [])
    assert report2.total_score == 0.0
    assert report2.dim_scores == []


def test_aggregate_report_missing_dimension_key():
    """某条记录缺失某维度 key 时，该维度按仅有的分值计算，不抛异常。"""
    evals = [
        EvaluationRecord(
            question_id="q1",
            scores={"tech_depth": 8},  # 其余维度缺失
            comment="partial",
            follow_up_needed=False,
        )
    ]
    report = aggregate_report(evals, _rubric())
    by_key = {d.key: d for d in report.dim_scores}
    assert by_key["tech_depth"].score == 8.0
    assert by_key["tech_depth"].covered is True
    # 其余维度本场没考察到：分数为 0，但标记 covered=False，不被当成「得了 0 分」
    assert by_key["experience"].score == 0.0
    assert by_key["experience"].covered is False


def test_uncovered_dimension_does_not_drag_total_down():
    """未考察维度不拉低总分：权重按比例分摊给已考察维度。

    只有 tech_depth（权重 0.40）拿到 8 分，其余三个维度本场没问到。
    旧口径下 total = 8 × 0.40 × 10 = 32（把「没问到」当成「得了 0 分」）；
    新口径把 0.40 的权重摊满到 1.0，total = 8 × 10 = 80。
    """
    evals = [
        EvaluationRecord(
            question_id="q1",
            scores={"tech_depth": 8},
            comment="partial",
            follow_up_needed=False,
        )
    ]
    report = aggregate_report(evals, _rubric())
    assert report.total_score == 80.0


def test_all_dimensions_covered_keeps_legacy_formula():
    """全部维度都考察到时，总分口径与改造前逐字一致（回归基线）。"""
    report = aggregate_report(_evals(), _rubric())
    assert all(d.covered for d in report.dim_scores)
    assert report.total_score == 75.2


def test_aggregate_report_weights_not_sum_to_one():
    """权重不为 1：公式为 Σ(均分×weight)×10，不做权重归一（文档约定行为）。

    这里用两条 weight=1.0 的维度、每题满分 10 验证：total = 200。
    说明该函数假设权重已归一（架构默认权重合计 1.0），超出 100 属预期。
    """
    rubric = [
        {"key": "a", "name": "A", "description": "", "weight": 1.0, "max_score": 10},
        {"key": "b", "name": "B", "description": "", "weight": 1.0, "max_score": 10},
    ]
    evals = [
        EvaluationRecord(
            question_id="q1",
            scores={"a": 10, "b": 10},
            comment="full",
            follow_up_needed=False,
        )
    ]
    report = aggregate_report(evals, rubric)
    assert report.total_score == 200.0


def test_aggregate_report_accepts_plain_dicts():
    """接受 dict 输入（而非 Pydantic 对象）。"""
    evals = [
        {
            "question_id": "q1",
            "scores": {"tech_depth": 10, "experience": 10, "communication": 10, "job_fit": 10},
            "comment": "ok",
            "follow_up_needed": False,
        }
    ]
    report = aggregate_report(evals, _rubric())
    assert report.total_score == 100.0


def test_dimension_score_type():
    """dim_scores 元素类型为 DimensionScore 且字段类型正确。"""
    report = aggregate_report(_evals(), _rubric())
    assert all(isinstance(d, DimensionScore) for d in report.dim_scores)
