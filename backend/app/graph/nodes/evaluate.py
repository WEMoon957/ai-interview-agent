"""evaluate 节点：结构化评分（AnswerScore），并落库逐题评分。"""
import json
import logging
import re
import time
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from app.db.models import AnswerEvaluation
from app.db.session import SessionLocal
from app.graph.state import InterviewState
from app.models.schemas import AnswerScore, EvaluationRecord
from app.services.bars_scoring import (
    BARS_ANCHORS_TEXT,
    enforce_level_ranges,
    scores_from_evidence,
    verify_evidence,
)
from app.services.llm import get_llm

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """你是一名务实的招聘面试官。请用「行为锚定」的方式给候选人这一题的回答定级，再根据等级给分。

岗位 JD:
{job}

候选人简历:
{resume}

评分维度（JSON 数组，含 key/name/description/weight/max_score）:
{rubric}

当前问题:
{question}

候选人回答:
{answer}

【第一步：摘原话证据（必做，先于打分）】
从候选人回答里摘出能支撑评分的**原话片段**，作为 evidence。规则：
- quote 必须是候选人原话里**连续的一段**，逐字照抄：不改写、不概括、不把两段话拼在一起
- **每个维度最多摘 1 条**最有代表性的原话（总条数不超过维度数），不要堆砌
- 每条 evidence 绑定一个维度，并判断它达到哪一级（L1-L5）
- reason 用一句话（不超过 20 字）说明这条原话为什么支撑该等级
- 某个维度找不到任何原话支撑，就把该维度从 scores 里**省略**，不要凭空给分，也不要打 0

【第二步：按等级给分（行为锚定，满分 10 分制）】
{anchors}
- 如果维度的 description 里写了更具体的分级标准，以 description 为准（但仍映射到 L1-L5）

【第三步：硬约束（必须遵守）】
- 每个维度的分数必须落在它最高证据等级对应的区间内
- 没有 L4 证据的维度不得给 7.5 分以上；没有 L5 证据的维度不得给 8.5 分以上
- 分数段的含义：**5 分左右 = 合格能上岗**（L2 上限，不是扣分结果）；**6.5-7 分 = 有具体事实但说不清做法**（L3）；**8 分左右 = 有量化结果或能说清做法**（L4）；**9 分以上 = 既有量化结果、又能讲清怎么做到的**（L5）
- 重要：不要因为「基层岗位要求不高」就把好回答压到 L4。只要回答里**同时出现了具体数字和做法**，就应当给 L5 —— 上限是用来区分优秀候选人的，不是摆设

【评分基准：与岗位层级匹配】
- 先根据 JD 判断岗位层级：一线基层岗位（餐饮店员、服务员、普工、收银等）以「能胜任岗位基本职责」为基准，不要以管理层、专业技术人员或资深专家的标准要求候选人
- 基层岗位口语化、句子短是常态：**答到点上的简短回答给 L2-L3**，不因简短压到 L1；但也**不因「态度好、愿意干」自动抬到 L4** —— 7.5 分以上要有量化或做法支撑
- 未提及个别细节（具体分工、某一步流程）不单独扣分；只有关键信息完全缺失才降到 L1

【维度相关性原则】
- 只对与当前问题相关的维度打分；无法从本问题考察的维度（例如问期望薪资时的「工作经历」）从 scores 中省略，不要打 0 分

【第四步：评语与追问】
- comment 用 1-2 句话说明给分理由，不超过 80 字，必须引用 evidence 里的原话
- 仅当回答明显敷衍（单字应付、答非所问）或关键信息完全缺失且确实值得深挖时，才置 follow_up_needed=true 并给出追问；口语化但答到点上的简短回答不追问{follow_up_policy}

【追问的关键约束】follow_up_question 必须是**单一的一个问题**：
- 只提一个问题，不要罗列 1）2）3）或多个问题
- 聚焦在候选人回答中最值得深挖的那一个点，问题要简洁，不超过 50 字

【输出格式】只输出一个 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块标记。
字段缺一不可，即使不需要追问也必须输出 "follow_up_needed": false 和 "follow_up_question": ""。
{{"evidence": [{{"dimension": "维度key", "level": "L1|L2|L3|L4|L5", "quote": "候选人原话的连续片段", "reason": "为什么支撑这一级"}}], "scores": {{"维度key": 分值}}, "comment": "给分理由（引用原话）", "follow_up_needed": true/false, "follow_up_question": "单一追问问题（不需要追问时为空字符串）"}}"""

# 追问禁用时的强制约束（max_follow_ups=0 注入，双保险：prompt 约束 + route 层拦截）
FOLLOW_UP_DISABLED_PROMPT = """
【追问已禁用】本次面试配置为不允许追问：follow_up_needed 必须为 false，follow_up_question 必须为空字符串。"""


def _format_rubric(rubric: list) -> str:
    """将评分维度序列化为 JSON 字符串（供 prompt 使用）。"""
    return json.dumps(rubric, ensure_ascii=False, default=str)


def _sanitize_follow_up(question: str) -> str:
    """清理追问，保证只保留一个问题。

    LLM 偶发输出「1）... 2）...」式多条追问，这里截取到第一个明显的
    多问题分隔符为止，只保留第一个问题。
    """
    q = (question or "").strip()
    if not q:
        return ""
    # 按常见多问题分隔符截断：数字编号 / 分号+问题 / 问号后接下一个编号
    # 1. 中文/英文数字编号（如 1）1. （1）2）等）
    for sep in ["\n1）", "\n2）", "\n3）", "\n1.", "\n2.", "\n3.", "\n（1）", "\n（2）", "\n（3）"]:
        if sep in q:
            q = q.split(sep)[0]
            break
    # 2. 第一个问号后若还有编号式问题，也截断
    import re as _re
    m = _re.split(r"[？?]\s*[12二３三四五六七八九]\s*[）.)]", q, maxsplit=1)
    if len(m) > 1:
        q = m[0] + "？"
    return q.strip()


def _parse_json_response(content: str) -> dict:
    """从 LLM 返回的文本中稳健地解析出 JSON 对象。

    兼容模型偶发的 Markdown 代码块包裹（```json ... ```）或前后多余文字。
    """
    text = (content or "").strip()
    # 去掉可能的 ```json / ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 兜底：提取第一个 { ... } 片段（取最后一个 } 配对，尽量覆盖完整对象）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从 LLM 输出中解析 JSON: {text[:200]}")


def _salvage_truncated_json(text: str) -> dict:
    """从被截断的 LLM 输出里尽力恢复字段（evidence / scores / comment）。

    加行为锚定后输出变长，模型偶发在 JSON 中间被截断。整题降级成「评分不可用」
    代价太大（这题等于白答），这里按三级策略恢复：
    1) 整体能否解析（末尾多了说明文字时也能命中）；
    2) 逐个 ``}`` / ``]`` 位置截断后解析；
    3) 字段级正则提取：evidence 按大括号配对逐个捞对象，scores 取扁平对象，
       comment 取到未闭合的引号为止 —— 这一级对「截在字符串中间」也有效。
    """
    text = (text or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    for i in range(len(text) - 1, 0, -1):
        if text[i] not in "}]":
            continue
        try:
            parsed = json.loads(text[: i + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    salvaged: dict = {}

    match = re.search(r'"scores"\s*:\s*(\{[^{}]*\})', text)
    if match:
        try:
            salvaged["scores"] = json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r'"evidence"\s*:\s*\[', text)
    if match:
        arr = text[match.end() - 1 :]
        items: list[dict] = []
        depth = 0
        start = -1
        for idx, ch in enumerate(arr):
            if ch == "{":
                if depth == 0:
                    start = idx
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        item = json.loads(arr[start : idx + 1])
                    except json.JSONDecodeError:
                        item = None
                    if isinstance(item, dict):
                        items.append(item)
                    start = -1
        if items:
            salvaged["evidence"] = items

    match = re.search(r'"comment"\s*:\s*"([^"]*)', text)
    if match and match.group(1).strip():
        salvaged["comment"] = match.group(1)

    return salvaged


def _coerce_answer_score(parsed: dict) -> AnswerScore:
    """对 LLM 偶发缺字段/类型不稳的输出做兜底填充后构造 AnswerScore。

    真实场景发现：LLM 对「期望薪资」等无法考察维度的问题时，偶发只输出
    scores + comment 而漏掉 follow_up_needed / follow_up_question，直接
    model_validate 会抛 ValidationError 打崩 evaluate 节点、卡死整个面试。
    这里对缺失字段给安全默认值，保证流程不中断。
    """
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM 输出不是 JSON 对象: {str(parsed)[:200]}")

    # scores：缺失/非 dict -> {}（该题不参与维度汇总，不惩罚候选人）
    scores_raw = parsed.get("scores")
    if not isinstance(scores_raw, dict):
        scores_raw = {}
    scores: dict[str, float] = {}
    for k, v in scores_raw.items():
        try:
            scores[str(k)] = float(v)
        except (TypeError, ValueError):
            continue  # 丢弃无法转数值的脏值

    # evidence：只保留结构完整的条目，等级统一大写，verified 由后续校验写入
    evidence: list[dict] = []
    raw_evidence = parsed.get("evidence")
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if not quote:
                continue
            evidence.append(
                {
                    "dimension": str(item.get("dimension") or "").strip(),
                    "level": str(item.get("level") or "").strip().upper(),
                    "quote": quote,
                    "reason": str(item.get("reason") or "").strip(),
                    "verified": False,
                }
            )

    return AnswerScore(
        scores=scores,
        evidence=evidence,
        comment=str(parsed.get("comment") or ""),
        follow_up_needed=bool(parsed.get("follow_up_needed") or False),
        follow_up_question=str(parsed.get("follow_up_question") or ""),
    )


def _last_qa(messages: list) -> tuple[str, str]:
    """从 messages 中提取最近一轮「AI 问题 + 用户回答」。

    从尾部向前扫描：先取最近一条 HumanMessage 作为回答，再取其前最近的
    AIMessage 作为问题（自动跳过开场白等其他消息）。
    """
    question = ""
    answer = ""
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage) and not answer:
            answer = msg.content or ""
        elif isinstance(msg, AIMessage) and not question:
            question = msg.content or ""
        if question and answer:
            break
    return question, answer


def _persist_evaluation(state: InterviewState, record: EvaluationRecord) -> None:
    """将逐题评分落库 answer_evaluations。"""
    session_id = state.get("session_id")
    if not session_id:
        return

    db = SessionLocal()
    try:
        db.add(
            AnswerEvaluation(
                session_id=uuid.UUID(session_id),
                question_id=record.question_id,
                scores=record.scores,
                evidence=record.evidence,
                comment=record.comment,
                follow_up_needed=record.follow_up_needed,
                follow_up_question=record.follow_up_question,
            )
        )
        db.commit()
    finally:
        db.close()


def _max_score_map(rubric: list) -> dict[str, float]:
    """维度 key -> 满分（缺省 10），供行为锚定按比例缩放分数区间。"""
    result: dict[str, float] = {}
    for d in rubric or []:
        key = d.get("key") if isinstance(d, dict) else getattr(d, "key", None)
        if not key:
            continue
        raw = d.get("max_score") if isinstance(d, dict) else getattr(d, "max_score", None)
        try:
            result[str(key)] = float(raw or 10)
        except (TypeError, ValueError):
            result[str(key)] = 10.0
    return result


def _get_dim_max_score(rubric: list, dim_key: str) -> float:
    """从评分维度列表中读取指定维度的满分（缺省回落 10）。"""
    for d in rubric or []:
        key = d.get("key") if isinstance(d, dict) else getattr(d, "key", None)
        if key == dim_key:
            raw = d.get("max_score") if isinstance(d, dict) else getattr(d, "max_score", None)
            try:
                return float(raw or 10)
            except (TypeError, ValueError):
                return 10.0
    return 10.0


def _evaluate_choice(question: dict, answer: str, rubric: list) -> AnswerScore:
    """选择题确定性判分（不走 LLM，无追问）。

    - 题目配置了 answer（标准答案）：选中即该维度满分，选错记 0 分；
    - 未配置 answer：仅记录所选，不贡献维度分（scores={}）。
    """
    selected = (answer or "").strip()
    correct = (question.get("answer") or "").strip()
    dim_key = (question.get("dimension") or "").strip()

    scores: dict[str, float] = {}
    if correct:
        is_correct = selected == correct
        if dim_key:
            scores[dim_key] = _get_dim_max_score(rubric, dim_key) if is_correct else 0.0
        comment = (
            f"选择题作答{'正确' if is_correct else '错误'}"
            f"（选择：{selected or '未作答'}；正确答案：{correct}）"
        )
    else:
        comment = f"选择题作答（选择：{selected or '未作答'}）"

    return AnswerScore(
        scores=scores,
        comment=comment,
        follow_up_needed=False,  # 选择题一律不追问
        follow_up_question="",
    )


def score_answer(
    *,
    question: str,
    answer: str,
    rubric: list,
    job_desc: str = "",
    resume_text: str = "",
    follow_up_policy: str = "",
) -> AnswerScore:
    """单题评分：行为锚定 prompt → 解析 → 原话校验 → 等级夹取。

    独立成函数，供 ``evaluate`` 节点与离线回放脚本（``scripts/replay_scoring.py``）共用，
    保证「线上怎么打的分」和「回放怎么复算」是同一套口径。
    """
    from app.config import get_settings

    prompt = ChatPromptTemplate.from_messages([("system", SCORING_SYSTEM_PROMPT)])
    # 评分用低温（scoring_temperature）保证输出稳定
    llm = get_llm(temperature=get_settings().scoring_temperature)

    payload = {
        "job": job_desc,
        "resume": resume_text,
        "rubric": _format_rubric(rubric),
        "question": question,
        "answer": answer,
        "anchors": BARS_ANCHORS_TEXT,
        "follow_up_policy": follow_up_policy,
    }

    # 模型偶发返回空内容（服务端抖动/被截断），重试一次再降级，避免整题白答
    parsed: dict = {}
    last_content = ""
    for attempt in range(2):
        response = (prompt | llm).invoke(payload)
        last_content = response.content if hasattr(response, "content") else str(response)
        if not (last_content or "").strip():
            logger.warning("评分返回空内容（第 %s 次尝试）", attempt + 1)
            time.sleep(1)
            continue
        try:
            parsed = _parse_json_response(last_content)
        except ValueError:
            parsed = _salvage_truncated_json(last_content)
            if not parsed:
                logger.warning("评分输出不可解析（第 %s 次尝试）", attempt + 1)
                time.sleep(1)
                continue
            logger.warning("评分输出被截断，已抢救字段：%s", sorted(parsed))
        break

    if not parsed:
        raise ValueError(f"评分输出为空或不可解析：{last_content[:200]}")

    result = _coerce_answer_score(parsed)

    # 行为锚定的两道代码兜底（不只靠 prompt 自觉）：
    # 1) 校验每条「原话」是否真的出现在回答里，编造/改写的引用标记为未通过；
    # 2) 分数夹取到所定等级的区间，没有 L4 证据拿不到 8 分以上。
    verified = verify_evidence(answer, result.evidence)
    max_scores = _max_score_map(rubric)
    # 输出被截断导致 scores 缺失时，按证据等级的代表分兜底，避免整题白答
    if not result.scores and result.evidence:
        result.scores = scores_from_evidence(result.evidence, max_scores)
        result.comment = f"{result.comment}（输出截断，按证据等级补分）".strip()
    raw_scores = dict(result.scores)
    result.scores = enforce_level_ranges(result.scores, result.evidence, max_scores=max_scores)
    logger.info(
        "评分证据 question=%s 证据=%s 通过=%s 调整=%s",
        (question or "")[:20],
        len(result.evidence),
        verified,
        {k: (raw_scores.get(k), v) for k, v in result.scores.items() if raw_scores.get(k) != v},
    )
    return result


def evaluate(state: InterviewState) -> dict:
    """对最近一次回答做结构化评分，追加到 evaluations 并落库。

    选择题（type=choice）走确定性判分（正确/错误，不调用 LLM、不追问）；
    主观题走「普通 LLM + 纯文本 JSON 输出 + 手动解析」而非
    ``with_structured_output``：DeepSeek V4.1 Flash（deepseek-flash）等
    thinking mode 模型既不支持 tool_choice（function calling）也不支持
    response_format（json_schema），但支持在 prompt 中要求输出 JSON，
    故主观题统一用 JSON 文本方案以保证多提供商兼容。

    健壮性（真实环境踩坑）：LLM 偶发缺字段/JSON 异常/超时等任何评分失败，
    均降级为中性评分记录（scores={} 该题不计入维度汇总，不追问），面试
    流程不中断——不能让候选人的面试因为一次 LLM 抖动而卡死。
    """
    questions = state.get("questions") or []
    current_idx = state.get("current_idx", 0)
    current_question = questions[current_idx] if questions else {}
    question_id = current_question.get("id", "") if current_question else ""

    question_text, answer = _last_qa(state.get("messages") or [])

    # 选择题：确定性判分，跳过 LLM 分支
    question_type = (current_question.get("type") or "subjective").strip() or "subjective"
    if question_type == "choice":
        choice_score = _evaluate_choice(current_question, answer, state.get("rubric") or [])
        record = EvaluationRecord(
            question_id=question_id,
            scores=choice_score.scores,
            comment=choice_score.comment,
            follow_up_needed=False,
            follow_up_question="",
        )
        _persist_evaluation(state, record)
        return {"evaluations": [record.model_dump(mode="json")]}

    # 追问策略：上限为 0 时注入禁用约束，从源头不再生成追问
    # （None 与 route 层一致地回落为允许追问）
    max_follow_ups = state.get("max_follow_ups")
    follow_up_policy = FOLLOW_UP_DISABLED_PROMPT if max_follow_ups == 0 else ""

    try:
        result = score_answer(
            question=question_text,
            answer=answer,
            rubric=state.get("rubric") or [],
            job_desc=(state.get("candidate") or {}).get("job_desc", ""),
            resume_text=(state.get("candidate") or {}).get("resume_text", ""),
            follow_up_policy=follow_up_policy,
        )
    except Exception:
        # 降级：记录异常（报告/监控可查），给中性评分继续流程
        logger.exception(
            "评分失败，降级为中性记录（question_id=%s）", question_id
        )
        result = AnswerScore(
            scores={},
            evidence=[],
            comment="（该题评分暂不可用，已跳过维度计分）",
            follow_up_needed=False,
            follow_up_question="",
        )

    # 清理追问：保证每次只提一个问题（prompt 约束 + 代码兜底双保险）
    follow_up_question = _sanitize_follow_up(result.follow_up_question)

    record = EvaluationRecord(
        question_id=question_id,
        scores=result.scores,
        evidence=result.evidence,
        comment=result.comment,
        follow_up_needed=result.follow_up_needed,
        follow_up_question=follow_up_question,
    )

    _persist_evaluation(state, record)

    # 注意：evaluations 使用 operator.add reducer，返回值必须是 list
    return {"evaluations": [record.model_dump(mode="json")]}
