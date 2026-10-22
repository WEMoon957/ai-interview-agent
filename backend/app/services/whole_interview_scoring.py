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

【第一步：先判断这段逐字稿能不能评（必须最先做）】
出现以下任一情况，判定为**不可评**，直接返回 scorable=false 且 scores 为空，不要打分：
- 逐字稿为空，或只有说话人标签、时间戳、语气词（嗯、啊、哦、好、可以、都行、随便）
- 只有店长（或第三方）在说话，候选人没有任何针对面试问题的实质回答
- 内容与面试无关：派活、闲聊、环境音、其他会议、其他候选人的面试
- 候选人自称没听清、拒答、或全程答非所问，没有可供评估的信息

不可评时**禁止**做以下任何事：为了凑出一个分数而推测、脑补、补全候选人表现；
把店长或第三方说的话当成候选人的回答；把「配合应答」当成「工作态度好」。
「什么都没说」不等于「态度端正」，也不等于「信息有限但可给中间分」——它就是不可评。

反过来，**不要过度拒绝**：候选人哪怕只简短回答了一两个关键问题（做过什么、做了多久、
能不能接受排班、为什么来），就属于「可评但信息有限」，应正常打分并在 comment 里
注明信息不足，而不是判不可评。判定标准是「有没有实质回答」，不是「回答得够不够长」。

只有双方都有实质问答时，才继续往下打分。

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
- 以下校准**仅适用于双方都有实质问答的面试**，不适用于不可评的逐字稿。
- 一场双方都有实质问答、认真作答的面试，综合分应落在 70-80 分区间；如果你准备给出的分数换算后明显低于该区间，说明打分过严，应上调。
- 候选人未提及个别细节不构成扣分理由；只有关键信息完全缺失才降一档。
- 口语化表达、偶有口误、语句直白，不影响沟通表达维度得分。

【维度相关性原则】
- 只对能从这段逐字稿考察到的维度打分；无法考察的维度从 scores 中省略（不是打 0，而是不出现）。

评分要求：
1. 逐相关维度打分，分值在 0 到该维度 max_score 之间，可保留小数；在候选人确有实质回答的前提下，认真作答从 7 分起评。
2. comment 用 2-4 句话概括整体表现，引用逐字稿中的关键原话佐证，并给出录用建议。

【输出格式】你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块标记。
JSON 对象字段如下：
- scorable：布尔值。逐字稿能不能评（按第一步的判定）
- scores：{{"维度key": 分值, ...}}；不可评时必须为空对象 {{}}
- comment：字符串。可评时是整体评价（含关键原话佐证与录用建议）；不可评时用 1-2 句话说明为什么评不了

可评示例：{{"scorable": true, "scores": {{"attitude": 7.5}}, "comment": "……"}}
不可评示例：{{"scorable": false, "scores": {{}}, "comment": "逐字稿中只有店内派活与应答，没有面试问答内容，无法评估候选人。"}}"""


def _format_rubric(rubric: list) -> str:
    return json.dumps(rubric or [], ensure_ascii=False, default=str)


# ── 逐字稿有效性判定（确定性规则，不依赖 LLM） ─────────────────────
#
# 背景：录音为空、只有环境音、或录的是店内派活时，逐字稿仍然是「有字」的。
# 这种文本送进 LLM 会被当成「回答过于简短、信息有限但态度端正」→ 5-6 分档，
# 换算 50-60 分，落进「待定」，还会生成一段像模像样的评语。这里先按字数和
# 对话轮次拦一道，把「没内容」和「答得不好」区分开 —— 后者交给 LLM 打分，
# 前者根本不该出分。

# 妙记逐字稿的说话人行形如「说话人1 00:00:03」，也兼容「店长：」这类前缀。
# 注意：说话人身份只取时间戳**前面**的部分 —— 每个段落的起始时间都不同，
# 若把整行当 key，同一个人会被拆成十几个说话人。
_SPEAKER_STAMP = re.compile(r"^\s*(\S{1,16})\s+\d{1,2}:\d{2}(?::\d{2})?\s*$")
_SPEAKER_PREFIX = re.compile(r"^\s*([^\s：:]{1,12})\s*[：:]")

_MIN_CHARS_TO_SCORE = 40  # 有效字数低于此：连一句话都没有，不必送评
_MIN_CHARS_CONFIDENT = 200  # 有效字数低于此：可评但参考性低


def _meaningful_len(text: str) -> int:
    """有效字数：去掉空白、标点、符号后的字符数。"""
    return len(re.sub(r"[\s\W_]+", "", text or ""))


def _split_by_speaker(transcript: str) -> dict[str, int]:
    """按说话人累计有效字数；识别不出说话人时全部记到 ``__unknown__``。"""
    buckets: dict[str, int] = {}
    current = "__unknown__"
    for raw in (transcript or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        stamp = _SPEAKER_STAMP.match(line)
        if stamp:
            current = stamp.group(1)
            buckets.setdefault(current, 0)
            continue
        m = _SPEAKER_PREFIX.match(line)
        if m and len(line) <= 40:  # 短行才当标签，避免误切长句里的冒号
            current = m.group(1)
            buckets.setdefault(current, 0)
            line = line[m.end() :].strip()
            if not line:
                continue
        buckets[current] = buckets.get(current, 0) + _meaningful_len(line)
    return buckets


def assess_transcript(transcript: str) -> dict:
    """判断逐字稿够不够格送评。只看「有没有人真的在回答」，不判断回答质量。

    Returns:
        ``{"scorable": bool, "reliability": "ok"|"low"|"insufficient",
        "chars": int, "speakers": int, "reason": str}``
    """
    text = (transcript or "").strip()
    if not text:
        return {
            "scorable": False,
            "reliability": "insufficient",
            "chars": 0,
            "speakers": 0,
            "reason": "逐字稿为空（录音里没有识别出任何语音）",
        }

    buckets = _split_by_speaker(text)
    speakers = len(buckets)
    chars = sum(buckets.values())

    # 只有一方在说话 —— 没有问答往返，不构成面试
    if speakers == 1 and "__unknown__" not in buckets:
        only = next(iter(buckets))
        return {
            "scorable": False,
            "reliability": "insufficient",
            "chars": chars,
            "speakers": speakers,
            "reason": f"逐字稿只有一方（{only}）在说话，没有问答往返，不构成面试",
        }

    if chars < _MIN_CHARS_TO_SCORE:
        return {
            "scorable": False,
            "reliability": "insufficient",
            "chars": chars,
            "speakers": speakers,
            "reason": (
                f"逐字稿只有 {chars} 个有效字（不足 {_MIN_CHARS_TO_SCORE}），"
                "录音里几乎没有语音内容"
            ),
        }

    return {
        "scorable": True,
        "reliability": "ok" if chars >= _MIN_CHARS_CONFIDENT else "low",
        "chars": chars,
        "speakers": speakers,
        "reason": "",
    }


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


def _aggregate(
    scores: dict[str, float], rubric: list
) -> tuple[list[dict], float | None]:
    """按 rubric 汇总维度得分与百分制总分（口径对齐 ``scoring.aggregate_report``）。

    整场评分 LLM 直接给维度分（0~max_score），这里归一化到 0~10 再加权，
    total = Σ(归一化分 × weight) / Σ(weight) × 10。

    未被 LLM 打分的维度**不按 0 分计入**：那是「没考察到」，不是「得了 0 分」。
    缺失维度的权重按比例分摊给已打分的维度，否则一场只答了 2 个维度的正常面试
    会被未答维度凭空拉低（旧逻辑实测 8 分两维被拉成 44 分）。全部维度都覆盖时，
    公式与旧版完全一致。

    一个维度都没打时返回 ``None`` —— 表示这场评不了，而不是考了 0 分。
    """
    dim_scores: list[dict] = []
    total_weight = 0.0
    covered_weight = 0.0
    covered_sum = 0.0

    for dim in rubric or []:
        if not isinstance(dim, dict):
            continue
        key = dim.get("key") or ""
        name = dim.get("name") or key
        weight = float(dim.get("weight") or 0)
        max_score = float(dim.get("max_score") or 10) or 10

        raw = scores.get(key)
        covered = raw is not None
        norm = (raw / max_score * 10.0) if covered else 0.0

        total_weight += weight
        if covered:
            covered_weight += weight
            covered_sum += norm * weight

        dim_scores.append(
            {
                "key": key,
                "name": name,
                "score": round(norm, 2),
                "max_score": max_score,
                "weight": weight,
                "weighted_score": round(norm * weight, 2),
                "covered": covered,
            }
        )

    if covered_weight <= 0:
        return dim_scores, None

    total = round(covered_sum / covered_weight * total_weight * 10.0, 2)
    return dim_scores, total


def _coerce_scorable(value: Any) -> bool:
    """把 LLM 的 scorable 兜底成 bool（兼容字符串 / 缺字段）。"""
    if value is None:
        return True  # 旧模型不返回该字段时按可评处理，最终由 scores 是否为空兜底
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "no", "0", "否", "不可评"}
    return bool(value)


def score_whole_interview(
    transcript: str,
    job_desc: str,
    rubric: list,
) -> dict:
    """对整场面试逐字稿做整体评分，返回：

    ``{"scores": {key: 原始分}, "dim_scores": [...], "total_score": float|None,
    "comment": str, "verdict": str, "scorable": bool|None, "reliability": str,
    "reason": str}``

    ``total_score`` 为 ``None`` 表示**这场评不了**（逐字稿没内容 / LLM 判不可评 /
    LLM 故障），调用方不得把它当成 0 分落库。``scorable=False`` 时 ``reason``
    给出可读原因；``scorable=None`` 表示评分链路本身出错。

    任何 LLM 失败均降级为中性结果，不抛异常，由调用方决定是否重试。
    """
    # 先做确定性闸门：空录音、只有一方说话、字数不足的直接不出分
    gate = assess_transcript(transcript)
    if not gate["scorable"]:
        logger.info(
            "整场评分跳过 minute_transcript chars=%s speakers=%s 原因=%s",
            gate["chars"],
            gate["speakers"],
            gate["reason"],
        )
        return {
            "scores": {},
            "dim_scores": [],
            "total_score": None,
            "comment": gate["reason"],
            "verdict": "",
            "scorable": False,
            "reliability": "insufficient",
            "reason": gate["reason"],
        }

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
        scorable = _coerce_scorable(parsed.get("scorable") if isinstance(parsed, dict) else None)
        scores = _coerce_scores(parsed) if scorable else {}
        comment = str(parsed.get("comment") or "") if isinstance(parsed, dict) else ""
    except Exception:
        logger.exception("整场面试评分失败，降级为中性结果")
        return {
            "scores": {},
            "dim_scores": [],
            "total_score": None,
            "comment": "（整场评分暂不可用，已跳过 AI 参考分）",
            "verdict": "",
            "scorable": None,
            "reliability": "error",
            "reason": "评分链路异常",
        }

    # LLM 判不可评：尊重它的判定，不落分
    if not scorable:
        return {
            "scores": {},
            "dim_scores": [],
            "total_score": None,
            "comment": comment or "逐字稿中没有可评估的面试内容",
            "verdict": "",
            "scorable": False,
            "reliability": "insufficient",
            "reason": comment or "逐字稿中没有可评估的面试内容",
        }

    dim_scores, total = _aggregate(scores, rubric)
    if total is None:
        return {
            "scores": {},
            "dim_scores": dim_scores,
            "total_score": None,
            "comment": comment or "逐字稿中没有可评估的面试内容",
            "verdict": "",
            "scorable": False,
            "reliability": "insufficient",
            "reason": "LLM 未对任何维度给出分数",
        }

    return {
        "scores": scores,
        "dim_scores": dim_scores,
        "total_score": total,
        "comment": comment,
        "verdict": verdict_from_score(total) if total is not None else "",
        "scorable": True,
        "reliability": gate["reliability"],
        "reason": "",
    }
