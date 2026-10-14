"""整场面试整体评分：店长面试逐字稿 → 维度分 + 总评 + 录用建议。

与逐题 ``evaluate`` 的区别：店长面试是「店长问 + 候选人答」的自由对话，非逐题回答，
故一次性把整场逐字稿喂给 LLM，产出维度分 + 总体评价；总分与等级建议由代码按既有口径
（``scoring.verdict_from_score``）统一计算，作为「AI 参考分」与店长人工打分并存、不覆盖。

同既有评分约定：纯文本 JSON 输出 + 手动解析 + 降级兜底（thinking mode 模型不支持 tool_choice）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.config import get_settings
from app.services.llm import get_llm
from app.services.scoring import verdict_from_score

logger = logging.getLogger(__name__)

# 到店直招无关联面试模板时的默认评分维度（餐饮门店一线岗位通用）
DEFAULT_RUBRIC: list[dict] = [
    {
        "key": "attitude",
        "name": "工作态度",
        "description": "积极性、配合度、服务意识、对岗位的认可",
        "weight": 0.30,
        "max_score": 10,
    },
    {
        "key": "communication",
        "name": "沟通表达",
        "description": "能否听懂问题、把意思说清楚",
        "weight": 0.25,
        "max_score": 10,
    },
    {
        "key": "job_match",
        "name": "岗位匹配",
        "description": "经验、技能与岗位要求的契合程度",
        "weight": 0.25,
        "max_score": 10,
    },
    {
        "key": "stability",
        "name": "稳定性",
        "description": "过往工作稳定性、长期任职意愿",
        "weight": 0.20,
        "max_score": 10,
    },
]

WHOLE_INTERVIEW_SCORING_PROMPT = """你是一名务实的招聘面试官。下面是一段**到店直招**的整场面试逐字稿（含店长提问与候选人回答）。请根据评分维度，对候选人的**整体表现**进行客观评分。

岗位 JD:
{job}

评分维度（JSON 数组，含 key/name/description/weight/max_score）:
{rubric}

整场面试逐字稿:
{transcript}

【评分基准：与岗位层级匹配】
- 先根据 JD 判断岗位层级：一线基层岗位（如餐饮店员、服务员、普工、收银等）以「能胜任岗位基本职责」为基准评分，不要以管理层、专业技术人员或资深专家的标准要求候选人。
- 基层岗位面试多为口语化的简短问答，回答直接、态度端正就是正常表现；不要因为「回答简短、信息量少、没有展开论述」而大幅扣分。

【评分锚点（以满分 10 为例，其他 max_score 等比换算）】
- 0-2 分：拒绝回答、答非所问，或存在明显诚信问题
- 3-4 分：明显回避问题，或关键信息完全缺失
- 5-6 分：回答过于简短、信息有限，但态度端正
- 7-8 分：正面回答了问题、内容具体、态度积极——认真作答的正常落点
- 9-10 分：回答全面、亮点突出，显著超出岗位要求

【给分校准（重要）】
- 一场正常认真作答的面试，综合分应落在 70-80 分区间；如果你准备给出的分数换算后明显低于该区间，说明打分过严，应上调。
- 候选人未提及个别细节不构成扣分理由；只有关键信息完全缺失才降一档。
- 口语化表达、偶有口误、语句直白，不影响沟通表达维度得分。

【维度相关性原则】
- 只对能从这段逐字稿考察到的维度打分；无法考察的维度从 scores 中省略（不是打 0，而是不出现）。

评分要求：
1. 逐相关维度打分，分值在 0 到该维度 max_score 之间，可保留小数；认真作答从 7 分起评。
2. comment 用 2-4 句话概括整体表现，引用逐字稿中的关键原话佐证，并给出录用建议。

【输出格式】你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块标记。
JSON 对象字段如下：
{{"scores": {{"维度key": 分值, ...}}, "comment": "整体评价（含关键原话佐证与录用建议）"}}"""


def _format_rubric(rubric: list) -> str:
    return json.dumps(rubric or [], ensure_ascii=False, default=str)


def _parse_json_response(content: str) -> dict:
    """稳健解析 LLM 返回的 JSON（兼容 ```json 包裹 / 前后多余文字）。"""
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


def _coerce_scores(parsed: Any) -> dict[str, float]:
    """把 LLM 的 scores 兜底成 {key: float}，丢弃脏值。"""
    if not isinstance(parsed, dict):
        return {}
    scores_raw = parsed.get("scores")
    if not isinstance(scores_raw, dict):
        return {}
    scores: dict[str, float] = {}
    for k, v in scores_raw.items():
        try:
            scores[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return scores


def _aggregate(scores: dict[str, float], rubric: list) -> tuple[list[dict], float]:
    """按 rubric 汇总维度得分与百分制总分（口径对齐 ``scoring.aggregate_report``）。

    整场评分 LLM 直接给维度分（0~max_score），这里归一化到 0~10 再加权，
    total = Σ(归一化分 × weight) × 10。
    """
    dim_scores: list[dict] = []
    for dim in rubric or []:
        if not isinstance(dim, dict):
            continue
        key = dim.get("key") or ""
        name = dim.get("name") or key
        weight = float(dim.get("weight") or 0)
        max_score = float(dim.get("max_score") or 10) or 10

        raw = scores.get(key)
        norm = (raw / max_score * 10.0) if raw is not None else 0.0
        weighted = norm * weight
        dim_scores.append(
            {
                "key": key,
                "name": name,
                "score": round(norm, 2),
                "max_score": max_score,
                "weight": weight,
                "weighted_score": round(weighted, 2),
            }
        )

    total = round(sum(d.get("weighted_score", 0.0) for d in dim_scores) * 10.0, 2)
    return dim_scores, total


def score_whole_interview(
    transcript: str,
    job_desc: str,
    rubric: list,
) -> dict:
    """对整场面试逐字稿做整体评分，返回：

    ``{"scores": {key: 原始分}, "dim_scores": [...], "total_score": float, "comment": str, "verdict": str}``

    任何 LLM 失败均降级为中性结果（scores={}、total_score=None），不抛异常，
    由调用方决定是否重试。
    """
    try:
        prompt = ChatPromptTemplate.from_messages([("system", WHOLE_INTERVIEW_SCORING_PROMPT)])
        llm = get_llm(temperature=get_settings().scoring_temperature)
        chain = prompt | llm
        response = chain.invoke(
            {
                "job": job_desc or "",
                "rubric": _format_rubric(rubric),
                "transcript": transcript or "",
            }
        )
        content = response.content if hasattr(response, "content") else str(response)
        parsed = _parse_json_response(content)
        scores = _coerce_scores(parsed)
        comment = str(parsed.get("comment") or "") if isinstance(parsed, dict) else ""
    except Exception:
        logger.exception("整场面试评分失败，降级为中性结果")
        return {
            "scores": {},
            "dim_scores": [],
            "total_score": None,
            "comment": "（整场评分暂不可用，已跳过 AI 参考分）",
            "verdict": "",
        }

    dim_scores, total = _aggregate(scores, rubric)
    return {
        "scores": scores,
        "dim_scores": dim_scores,
        "total_score": total,
        "comment": comment,
        "verdict": verdict_from_score(total) if total is not None else "",
    }
