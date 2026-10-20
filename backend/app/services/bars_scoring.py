"""行为锚定评分（BARS）：等级定义、原话证据校验、分数区间约束。

为什么要有这一层：
原先 LLM 直接给「整体印象分」，prompt 又把 7 分钉成默认值，结果是所有候选人都落在
6-8 分、标准差不到 0.8，分数失去区分度。这里把判分拆成两步 —— **先摘原话证据并定级，
再按等级给分** —— 分数由「命中哪一级行为」决定，而不是由模型的整体感觉决定。

三个约束（都在代码里兜底，不只靠 prompt）：
1. ``verify_evidence``：quote 必须真的出现在候选人原话里（归一化后子串匹配），
   防止模型编造或改写「原话」；
2. ``enforce_level_ranges``：分数被夹取到所定等级的区间内，没有 L4 证据拿不到 8 分以上；
3. 无证据的维度不参与夹取，由 prompt 的「维度相关性原则」决定是否省略。
"""
from __future__ import annotations

import re

# 通用五级行为锚点（10 分制）：(等级, 区间下界, 区间上界, 说明)
#
# 区间的划分目标不是「均匀」，而是「把上限打开」：
# 旧口径的毛病是所有人挤在 6-8，8 分以上极难出现。这里让
# 「有量化、有方法」的回答真正落到 8.5-10，而「做过但说不清」停在 5.5-7.5，
# 「只有结论」停在 3.5-5.5 —— 三档之间有实际间隔，分数才有排序价值。
BARS_LEVELS: list[tuple[str, float, float, str]] = [
    ("L1", 0.0, 3.5, "答非所问，或关键信息完全缺失"),
    ("L2", 3.5, 5.5, "只有结论式回答（「做过」「能吃苦」），没有任何可验证的具体信息"),
    ("L3", 5.5, 7.5, "有具体事实（做什么、多久、什么岗位、大概量级），但说不清做法"),
    ("L4", 7.5, 8.5, "有量化结果，或能说清具体做法（二者有其一）"),
    ("L5", 8.5, 10.0, "量化结果 + 可复用做法，能讲清「怎么做到的、效果如何」"),
]

BARS_RANGES: dict[str, tuple[float, float]] = {
    level: (low, high) for level, low, high, _ in BARS_LEVELS
}

# 供 prompt 直接插入的锚点文案
BARS_ANCHORS_TEXT = "\n".join(
    f"- {level}（{low:g}-{high:g} 分）：{desc}" for level, low, high, desc in BARS_LEVELS
)

# 标点与空白：校验原话时忽略，容忍模型漏字/换行差异
_NOISE = re.compile(r"[\s，。！？、；：\"'“”‘’（）()【】\[\]…—\-·,.!?;:`~]+")


def _normalize(text: str) -> str:
    """归一化文本：去掉空白与标点并转小写，用于原话子串比对。"""
    return _NOISE.sub("", (text or "").lower())


def verify_evidence(answer: str, evidence: list[dict]) -> int:
    """逐条校验 evidence 的 quote 是否为候选人原话片段，就地写入 ``verified``。

    判定口径：归一化（去空白/标点）后做子串匹配；短于 4 个字的引用视为无效
    （「我」「行」这类片段没有证据价值，且极易误命中）。

    Returns:
        通过校验的条数。
    """
    normalized_answer = _normalize(answer)
    passed = 0
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        quote = _normalize(str(item.get("quote") or ""))
        ok = len(quote) >= 4 and quote in normalized_answer
        item["verified"] = ok
        if ok:
            passed += 1
    return passed


# 各等级的代表分（用于 LLM 输出被截断、只剩 evidence 时兜底补分）
LEVEL_MIDPOINTS: dict[str, float] = {
    "L1": 2.0,
    "L2": 4.5,
    "L3": 6.5,
    "L4": 8.0,
    "L5": 9.3,
}


def scores_from_evidence(
    evidence: list[dict],
    max_scores: dict[str, float] | None = None,
) -> dict[str, float]:
    """按证据等级的代表分兜底出分（仅用于 LLM 输出被截断、scores 缺失的场景）。

    同一维度多条证据取最高等级；维度满分不是 10 时按比例缩放。
    """
    result: dict[str, float] = {}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "")
        level = str(item.get("level") or "").upper()
        if not dim or level not in LEVEL_MIDPOINTS:
            continue
        scale = (float((max_scores or {}).get(dim, 10.0)) or 10.0) / 10.0
        value = round(LEVEL_MIDPOINTS[level] * scale, 2)
        result[dim] = max(result.get(dim, 0.0), value)
    return result


def enforce_level_ranges(
    scores: dict[str, float],
    evidence: list[dict],
    max_scores: dict[str, float] | None = None,
) -> dict[str, float]:
    """把每个维度的分数夹取到其最高证据等级对应的区间内。

    - 同一维度多条证据时，取**等级最高**的那条决定区间；
    - 没有证据的维度原样返回（是否给分由 prompt 的维度相关性原则决定）；
    - 各维度满分不同时按 ``max_scores[dim] / 10`` 缩放区间（缺省按 10 分制）。

    纯函数：返回夹取后的新 dict，不修改入参。
    """
    best_level: dict[str, str] = {}
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "")
        level = str(item.get("level") or "").upper()
        if dim not in scores or level not in BARS_RANGES:
            continue
        if dim not in best_level or BARS_RANGES[level][0] > BARS_RANGES[best_level[dim]][0]:
            best_level[dim] = level

    result: dict[str, float] = {}
    for dim, raw in scores.items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        level = best_level.get(dim)
        if level is None:
            result[dim] = value
            continue
        scale = (float((max_scores or {}).get(dim, 10.0)) or 10.0) / 10.0
        low, high = BARS_RANGES[level]
        result[dim] = round(min(max(value, low * scale), high * scale), 2)
    return result
