"""简历初筛：依据岗位 JD 对单份简历做结构化打分（纯函数 + LLM 调用）。

与 ``app/graph/nodes/evaluate.py`` 保持同一套健壮性约定：
- 走「普通 LLM + 纯文本 JSON 输出 + 手动解析」而非 structured_output，
  兼容 DeepSeek V4.1 Flash（thinking mode）等不支持 tool_choice 的模型；
- 任何异常（LLM 抖动 / JSON 解析失败）均降级为中性结果，绝不打断批量上传；
- 总分由代码按维度分归一化重算，不直接信任 LLM 给出的总分，保证可排序、可复现。
"""
import json
import logging
import re

from app.config import get_settings
from app.models.schemas import ScreeningDimension, ScreeningDimensionScore, ScreeningResult
from app.services.llm import get_llm

logger = logging.getLogger(__name__)

# 简历初筛默认维度（可按岗位覆盖；权重之和会被归一化）
DEFAULT_SCREENING_DIMENSIONS: list[ScreeningDimension] = [
    ScreeningDimension(
        key="skill_match",
        name="技能匹配",
        description="简历所列技能/证书与岗位要求的匹配程度",
        weight=0.30,
        max_score=10,
    ),
    ScreeningDimension(
        key="experience",
        name="经验相关",
        description="过往工作经历与目标岗位的相关性与年限",
        weight=0.30,
        max_score=10,
    ),
    ScreeningDimension(
        key="stability",
        name="稳定性",
        description="过往任职时长、跳槽频率体现的稳定性",
        weight=0.20,
        max_score=10,
    ),
    ScreeningDimension(
        key="attitude",
        name="素质与态度",
        description="简历呈现的服务意识、责任心、学习能力等软素质",
        weight=0.20,
        max_score=10,
    ),
]

SCREENING_SYSTEM_PROMPT = """你是一名务实的招聘初筛专员。请依据岗位 JD，对候选人的简历做初步筛选打分。

岗位名称: {job_title}

岗位 JD:
{jd}

候选人姓名: {candidate_name}

候选人简历:
{resume}

评分维度（JSON 数组，含 key/name/description/weight/max_score）:
{dimensions}

【岗位层级校准】
- 一线基层岗位（餐饮店员、服务员、收银、后厨等）以「能胜任岗位基本职责」为基准，不要用管理层或资深专家的标准要求候选人。
- 只要简历体现了相关经验、技能或端正态度，即给中上分；不要因为「简历写得简单、篇幅短」而大幅扣分。

【打分锚点（以满分 10 为例）】
- 0-2 分：完全无关或存在明显硬伤（如信息造假迹象）
- 3-4 分：与岗位要求差距较大
- 5-6 分：基本可接受，但存在明显短板
- 7-8 分：匹配度良好，是认真投递的正常落点
- 9-10 分：高度匹配，亮点突出

【输出格式】只输出一个 JSON 对象，不要任何其他文字或 Markdown 代码块标记。
字段如下：
{{"dim_scores": [{{"key": "维度key（必须与给定维度一一对应）", "score": 分值}}], "summary": "综合评价（引用简历中的具体经历/技能佐证）", "highlights": ["亮点1", "亮点2"], "risks": ["风险或待确认项1"]}}
要求：dim_scores 覆盖全部给定维度；highlights / risks 各不超过 3 条，无则给空数组。"""


def recommendation_from_score(total_score: float) -> str:
    """按百分制初筛分返回建议档位（与面试报告阈值 80/65/50 对齐）。"""
    if total_score >= 80:
        return "强烈推荐"
    if total_score >= 65:
        return "推荐"
    if total_score >= 50:
        return "待定"
    return "不推荐"


def _parse_json_response(content: str) -> dict:
    """从 LLM 返回文本中稳健解析 JSON 对象（兼容 ```json 包裹与前后杂讯）。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从 LLM 输出中解析 JSON: {text[:200]}")


def _normalize_weights(dims: list[ScreeningDimension]) -> list[float]:
    """将维度权重归一化为和为 1；全为 0 时按等权处理。"""
    total = sum(max(0.0, float(d.weight or 0)) for d in dims)
    if total <= 0:
        n = len(dims) or 1
        return [1.0 / n for _ in dims]
    return [max(0.0, float(d.weight or 0)) / total for d in dims]


def _coerce_result(
    parsed: dict,
    dims: list[ScreeningDimension],
) -> ScreeningResult:
    """把 LLM 输出对齐到预设维度，重算加权与总分（不信任 LLM 的总分）。"""
    raw_scores: dict[str, float] = {}
    for item in parsed.get("dim_scores") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        try:
            raw_scores[key] = float(item.get("score"))
        except (TypeError, ValueError):
            continue

    weights = _normalize_weights(dims)
    dim_scores: list[ScreeningDimensionScore] = []
    total = 0.0
    for dim, w in zip(dims, weights):
        max_score = float(dim.max_score or 10) or 10
        score = raw_scores.get(dim.key, 0.0)
        # 夹取到 [0, max_score]，避免 LLM 越界
        score = min(max(score, 0.0), max_score)
        ratio = score / max_score if max_score else 0.0
        weighted = round(ratio * w * 100, 2)
        total += weighted
        dim_scores.append(
            ScreeningDimensionScore(
                key=dim.key,
                name=dim.name,
                score=round(score, 2),
                max_score=max_score,
                weight=round(w, 4),
                weighted_score=weighted,
            )
        )

    def _str_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:5]

    return ScreeningResult(
        dim_scores=dim_scores,
        total_score=round(total, 2),
        summary=str(parsed.get("summary") or "").strip(),
        highlights=_str_list(parsed.get("highlights")),
        risks=_str_list(parsed.get("risks")),
    )


def screen_resume(
    job_title: str,
    jd_text: str,
    candidate_name: str,
    resume_text: str,
    dimensions: list[ScreeningDimension] | None = None,
) -> ScreeningResult:
    """对单份简历执行 AI 初筛，返回结构化结果（失败降级不抛异常）。

    Args:
        job_title: 岗位名称（用于 prompt 与层级判断）。
        jd_text: 岗位 JD 文本。
        candidate_name: 候选人姓名。
        resume_text: 简历解析后的纯文本。
        dimensions: 评分维度；默认使用 ``DEFAULT_SCREENING_DIMENSIONS``。

    Returns:
        ScreeningResult：维度得分 + 百分制总分 + 评价 + 亮点/风险。
    """
    dims = dimensions or DEFAULT_SCREENING_DIMENSIONS
    dims_json = json.dumps([d.model_dump() for d in dims], ensure_ascii=False)

    try:
        prompt = SCREENING_SYSTEM_PROMPT.format(
            job_title=job_title or "",
            jd=(jd_text or "")[:6000],
            candidate_name=candidate_name or "",
            resume=(resume_text or "")[:12000],
            dimensions=dims_json,
        )
        llm = get_llm(temperature=get_settings().scoring_temperature)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        parsed = _parse_json_response(content)
        return _coerce_result(parsed, dims)
    except Exception:
        logger.exception("简历初筛失败，降级为中性结果（candidate=%s）", candidate_name)
        return _coerce_result(
            {
                "dim_scores": [],
                "summary": "（AI 初筛暂不可用，请人工复核该简历）",
                "highlights": [],
                "risks": ["AI 初筛失败，需人工审核"],
            },
            dims,
        )
