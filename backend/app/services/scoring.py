"""评分汇总：维度加权 -> 百分制 -> 等级建议（纯函数，与 LLM 解耦、可单测）。"""
from typing import Any, Sequence

from app.models.schemas import DimensionScore, Report


def verdict_from_score(total_score: float) -> str:
    """按百分制综合分返回录用等级建议（阈值 80/65/50，共享知识 #5）。

    面向基层岗位（餐饮门店等一线招聘）放宽的判定基准：
    >=80 强烈推荐 / >=65 推荐 / >=50 待定 / <50 不推荐。
    """
    if total_score >= 80:
        return "强烈推荐"
    if total_score >= 65:
        return "推荐"
    if total_score >= 50:
        return "待定"
    return "不推荐"


def _to_dict(obj: Any) -> dict:
    """兼容 Pydantic 对象与 dict 输入。"""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj or {}


def aggregate_report(
    evaluations: Sequence[Any],
    rubric: Sequence[Any],
) -> Report:
    """汇总逐题评分为最终报告。

    公式（共享知识 #5）：
      - 维度得分 = 该维度在所有已评分题上的均分；
      - weighted_score = 维度均分 × weight；
      - total_score = Σ weighted_score × 10（各维度满分 10 时即百分制）。

    **未考察维度不参与计分**（BARS 改造后尤为关键）：行为锚定要求「没有原话支撑的
    维度不给分」，因此一场面试里某些维度可能一次都没被考察到。这类维度标记
    ``covered=False``，其权重按比例分摊到已考察维度上 —— 否则「没考到」会被当成
    「得了 0 分」，凭空把这场的总分压低。全部维度都覆盖时，公式与旧版完全一致。

    Args:
        evaluations: 逐题评分记录（EvaluationRecord 或等价 dict）。
        rubric: 评分维度列表（RubricDimension 或等价 dict）。

    Returns:
        Report：维度得分 + 百分制综合分 + 等级建议 + 总体评价。
    """
    evals = [_to_dict(e) for e in (evaluations or [])]
    dims = [_to_dict(d) for d in (rubric or [])]

    # 本场实际被考察到的维度（任意一题的 scores 里出现过即算）
    covered_keys = {
        str(k)
        for ev in evals
        for k in ((ev.get("scores") or {}).keys())
    }

    dim_scores: list[DimensionScore] = []
    for dim in dims:
        key = dim.get("key") or ""
        name = dim.get("name") or key
        weight = float(dim.get("weight") or 0)
        max_score = float(dim.get("max_score") or 10) or 10

        values: list[float] = []
        for ev in evals:
            scores = ev.get("scores") or {}
            if key in scores:
                try:
                    values.append(float(scores[key]))
                except (TypeError, ValueError):
                    continue

        covered = str(key) in covered_keys
        avg = sum(values) / len(values) if values else 0.0
        weighted = avg * weight

        dim_scores.append(
            DimensionScore(
                key=key,
                name=name,
                weight=weight,
                score=round(avg, 2),
                weighted_score=round(weighted, 2),
                covered=covered,
            )
        )

    total_weight = sum(d.weight for d in dim_scores)
    covered_weight = sum(d.weight for d in dim_scores if d.covered)
    covered_sum = sum(d.weighted_score for d in dim_scores if d.covered)

    if 0 < covered_weight < total_weight:
        # 有维度没考察到：把缺失权重按比例分摊给已考察维度，保持总分口径不缩水
        total_score = round(covered_sum / covered_weight * total_weight * 10.0, 2)
    else:
        total_score = round(covered_sum * 10.0, 2)

    verdict = verdict_from_score(total_score)
    summary = _build_summary(dim_scores, total_score, verdict)

    return Report(
        dim_scores=dim_scores,
        total_score=total_score,
        verdict=verdict,
        summary=summary,
    )


def _build_summary(
    dim_scores: list[DimensionScore],
    total_score: float,
    verdict: str,
) -> str:
    """生成确定性的总体评价与录用风险提示（不依赖 LLM，保证纯函数可复现）。"""
    # 只从「本场真正考察过」的维度里挑强弱项，避免把未考察维度说成短板
    ranked = sorted(
        (d for d in dim_scores if d.covered),
        key=lambda d: d.weighted_score,
        reverse=True,
    )
    if not ranked:
        return f"本次面试无有效评分数据，综合分 {total_score}，录用建议：{verdict}。"

    strongest = ranked[0]
    weakest = ranked[-1]

    parts = [
        f"综合评分 {total_score} 分（满分 100），录用建议：{verdict}。",
        f"表现最佳维度：{strongest.name}（均分 {strongest.score}）。",
        f"需重点关注维度：{weakest.name}（均分 {weakest.score}）。",
    ]

    if total_score < 50:
        parts.append("风险提示：整体表现与岗位要求存在明显差距，建议谨慎评估。")
    elif total_score < 65:
        parts.append("风险提示：部分维度存在短板，建议针对性补充考察后再做决策。")
    else:
        parts.append("整体匹配度较高，可按流程推进后续环节。")

    return "".join(parts)
