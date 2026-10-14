"""evaluate 节点：结构化评分（AnswerScore），并落库逐题评分。"""
import json
import logging
import re
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate

from app.db.models import AnswerEvaluation
from app.db.session import SessionLocal
from app.graph.state import InterviewState
from app.models.schemas import AnswerScore, EvaluationRecord
from app.services.llm import get_llm

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """你是一名务实的招聘面试官。请根据评分维度，对候选人针对当前问题的回答进行客观评分。

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

【评分基准：与岗位层级匹配】
- 先根据 JD 判断岗位层级：一线基层岗位（如餐饮店员、服务员、普工、收银等）以「能胜任岗位基本职责」为基准评分，不要以管理层、专业技术人员或资深专家的标准要求候选人。
- 基层岗位面试多为口语化的简短问答，回答直接、态度端正就是正常表现；不要因为「回答简短、信息量少、没有展开论述」而大幅扣分。

【评分锚点（以满分 10 为例，其他 max_score 等比换算）】
- 0-2 分：拒绝回答、答非所问，或存在明显诚信问题
- 3-4 分：明显回避问题，或关键信息完全缺失
- 5-6 分：回答过于简短、信息有限，但态度端正（简短应付型回答的落点）
- 7-8 分：正面回答了问题、内容具体、态度积极——**认真作答的正常落点，多数维度应给 7 分左右，细节充实的给 8 分**
- 9-10 分：回答全面、亮点突出，显著超出岗位要求

【给分校准（重要）】
- 一场正常认真作答的面试，综合分应落在 70-80 分区间；如果你准备给出的分数换算后明显低于该区间，说明打分过严，应上调。
- 候选人未提及个别细节（如具体分工、某一步流程、某一种场景）**不构成扣分理由**；只有关键信息完全缺失才降一档。
- 口语化表达、偶有口误、语句直白，不影响沟通表达维度得分——基层岗位的沟通要求是「意思清楚」，不是演讲水平。
- 回答只要正面、具体、态度积极，就从 7 分起评，不要从 5-6 分起评。

【维度相关性原则（重要）】
- 只对与当前问题相关的维度打分。若某维度无法从本问题考察（例如问题只问期望薪资时，「工作经历」维度无从体现），则将该维度从 scores 中省略，**不要因「回答未涉及」而打 0 分**。
- scores 只包含与当前问题相关的维度 key，其余省略（不是打 0，而是不出现）。

评分要求：
1. 逐相关维度打分，分值在 0 到该维度 max_score 之间，可保留小数；认真作答从 7 分起评，仅明显不合格才给低分。
2. comment 引用候选人回答中的原话作为评分依据，简要说明给分理由。
3. 仅当回答明显敷衍（如单字应付、答非所问）或关键信息完全缺失且确实值得深挖时，置 follow_up_needed=true 并给出追问；口语化但态度正常的简短回答不追问。{follow_up_policy}

【追问的关键约束】follow_up_question 必须是**单一的一个问题**：
- 只提一个问题，不要罗列 1）2）3）或多个问题
- 聚焦在候选人回答中最值得深挖的那一个点，问题要简洁，不超过 50 字

【输出格式】你必须只输出一个 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块标记。
四个字段缺一不可，即使不需要追问也必须输出 "follow_up_needed": false 和 "follow_up_question": ""。
JSON 对象字段如下：
{{"scores": {{"维度key": 分值, ...}}, "comment": "评分理由（含原话佐证）", "follow_up_needed": true/false, "follow_up_question": "单一追问问题（不需要追问时为空字符串）"}}"""

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

    return AnswerScore(
        scores=scores,
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
                comment=record.comment,
                follow_up_needed=record.follow_up_needed,
                follow_up_question=record.follow_up_question,
            )
        )
        db.commit()
    finally:
        db.close()


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

    from app.config import get_settings

    # 追问策略：上限为 0 时注入禁用约束，从源头不再生成追问
    # （None 与 route 层一致地回落为允许追问）
    max_follow_ups = state.get("max_follow_ups")
    follow_up_policy = FOLLOW_UP_DISABLED_PROMPT if max_follow_ups == 0 else ""

    try:
        prompt = ChatPromptTemplate.from_messages([("system", SCORING_SYSTEM_PROMPT)])
        # 评分用低温（scoring_temperature）保证输出稳定
        llm = get_llm(temperature=get_settings().scoring_temperature)
        chain = prompt | llm

        response = chain.invoke(
            {
                "job": (state.get("candidate") or {}).get("job_desc", ""),
                "resume": (state.get("candidate") or {}).get("resume_text", ""),
                "rubric": _format_rubric(state.get("rubric") or []),
                "question": question_text,
                "answer": answer,
                "follow_up_policy": follow_up_policy,
            }
        )

        content = response.content if hasattr(response, "content") else str(response)
        parsed = _parse_json_response(content)
        result = _coerce_answer_score(parsed)
    except Exception:
        # 降级：记录异常（报告/监控可查），给中性评分继续流程
        logger.exception(
            "评分失败，降级为中性记录（question_id=%s）", question_id
        )
        result = AnswerScore(
            scores={},
            comment="（该题评分暂不可用，已跳过维度计分）",
            follow_up_needed=False,
            follow_up_question="",
        )

    # 清理追问：保证每次只提一个问题（prompt 约束 + 代码兜底双保险）
    follow_up_question = _sanitize_follow_up(result.follow_up_question)

    record = EvaluationRecord(
        question_id=question_id,
        scores=result.scores,
        comment=result.comment,
        follow_up_needed=result.follow_up_needed,
        follow_up_question=follow_up_question,
    )

    _persist_evaluation(state, record)

    # 注意：evaluations 使用 operator.add reducer，返回值必须是 list
    return {"evaluations": [record.model_dump(mode="json")]}
