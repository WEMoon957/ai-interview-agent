"""面试逐字稿 → 结构化问答（问题 + 回答摘要/关键原话/亮点/风险）抽取。

用途：把到店直招 / 线下复试的整场逐字稿拆成「面试官问了什么 + 候选人怎么回答的」，
落库到 ``offline_interviews.qa_extract``，供 **招聘 BI 数据看板** 点击查看；
业务系统不做展示（故 ``_offline_out`` 不返回该字段）。

v2 起每条问答额外输出 ``risks``（风险点/疑点），整场输出 ``overall_risks``；
老数据的 ``qa_extract`` 没有这两个键，看板按「无风险数据」降级展示，不报错。

同既有评分约定：纯文本 JSON 输出 + 手动解析 + 降级兜底
（DeepSeek 等 thinking mode 模型不支持 tool_choice，统一走文本 JSON）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from app.config import get_settings
from app.services.llm import get_llm

logger = logging.getLogger(__name__)

QA_EXTRACTION_PROMPT = """你是一名招聘面试复盘助手。下面是一段线下面试的整场逐字稿（通常是「面试官/店长提问 + 候选人回答」的双向对话，已带说话人标注）。请把它整理成结构化的「问答清单」，用于事后复盘与数据看板展示。

岗位: {job}

整场面试逐字稿:
{transcript}

【整理要求】
1. 只抽取**面试官（提问方）**提出的问题，忽略寒暄与纯过渡语；候选人自己反问的问题不算在内。
2. 按对话先后顺序逐条列出；同一话题的多轮追问可合并为一条，question 用凝练后的问法。
3. 每条给出候选人回答的要点摘要 answer_summary（1-2 句，概括「答了什么」）。
4. answer_quotes 摘取候选人回答里的**关键原话**（1-3 句，尽量保留说话的原本措辞，不要改写或概括），
   用于还原「候选人当时是怎么回答的」；若该条回答没有可引用的实质内容，返回空数组，不要硬编。
5. highlights 提取候选人回答里的**亮点/加分项**（如具体经验佐证、有服务意识、稳定性、主动性等），
   0-3 条；若该回答平淡无明显亮点，可返回空数组，不要硬凑。
6. risks 提取该条回答暴露出的**风险点/疑点**（供面试官复核时重点追问），0-3 条。只写从对话里能找到依据的客观观察，常见类型：
   - 经验缺口：没做过该岗位/该业态、年限不足、只做过其中某一环节；
   - 回答含糊或回避：只有结论没有细节、被追问才补充、前后说法矛盾、答非所问；
   - 稳定性：近期频繁换工作、离职原因含糊、明说只做短期/过渡、住得太远或通勤不确定；
   - 条件不匹配：期望薪资高于岗位区间、不能接受排班/夜班/长时间站立、到岗时间太晚；
   - 意识薄弱：对客诉、食品安全、卫生、带团队等问题缺乏基本认识。
   每条写清「风险是什么 + 依据（从哪句话听出来的）」，例如「近两年换过 4 份工作，稳定性存疑」「问到客诉处理只说『道歉就行』，缺具体做法」。
   **必须严格基于逐字稿**：不要臆测、不要做人身评价；「回答简短」本身不算风险；该条回答没有问题就返回空数组，不要为凑数编造。
7. overall 用 2-3 句概括整场问答中候选人的整体表现与最突出的亮点。
8. overall_risks 用 1-2 句概括整场面试**最需要复核的风险**（同类合并，通常 1-2 类）；整场没有明显风险时返回空字符串。

【输出格式】只输出一个 JSON 对象，不要任何额外文字或 Markdown 代码块标记：
{{"questions": [{{"question": "面试官的问题", "answer_summary": "候选人回答要点", "answer_quotes": ["候选人关键原话1", "候选人关键原话2"], "highlights": ["亮点1", "亮点2"], "risks": ["风险点1 + 依据"]}}], "overall": "整场表现概述", "overall_risks": "整场风险概述"}}"""


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


def _coerce_qa(parsed: Any) -> dict:
    """把 LLM 输出兜底成规范的 ``{"questions": [...], "overall": str, "overall_risks": str}``，丢弃脏值。"""
    if not isinstance(parsed, dict):
        return {"questions": [], "overall": "", "overall_risks": ""}
    questions: list[dict] = []
    raw = parsed.get("questions")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            if not q:
                continue
            quotes_raw = item.get("answer_quotes")
            answer_quotes = (
                [str(t).strip() for t in quotes_raw if str(t).strip()]
                if isinstance(quotes_raw, list)
                else []
            )
            highlights_raw = item.get("highlights")
            highlights = (
                [str(h).strip() for h in highlights_raw if str(h).strip()]
                if isinstance(highlights_raw, list)
                else []
            )
            risks_raw = item.get("risks")
            risks = (
                [str(r).strip() for r in risks_raw if str(r).strip()]
                if isinstance(risks_raw, list)
                else []
            )
            questions.append(
                {
                    "question": q,
                    "answer_summary": str(item.get("answer_summary") or "").strip(),
                    "answer_quotes": answer_quotes[:3],
                    "highlights": highlights[:3],
                    "risks": risks[:3],
                }
            )
    return {
        "questions": questions,
        "overall": str(parsed.get("overall") or "").strip(),
        "overall_risks": str(parsed.get("overall_risks") or "").strip(),
    }


def extract_interview_qa(
    transcript: str,
    job_title: str = "",
    job_desc: str = "",
) -> dict:
    """把整场逐字稿抽取为结构化问答。

    返回 ``{"questions": [{"question", "answer_summary", "answer_quotes": [...],
    "highlights": [...], "risks": [...]}], "overall": str, "overall_risks": str}``。

    任何 LLM 失败均降级为空结果（questions=[]），不抛异常，由调用方决定是否重试。
    """
    if not (transcript or "").strip():
        return {"questions": [], "overall": "", "overall_risks": ""}

    job = job_title or job_desc or ""
    try:
        prompt = ChatPromptTemplate.from_messages([("system", QA_EXTRACTION_PROMPT)])
        llm = get_llm(temperature=get_settings().scoring_temperature)
        chain = prompt | llm
        response = chain.invoke({"job": job, "transcript": transcript})
        content = response.content if hasattr(response, "content") else str(response)
        return _coerce_qa(_parse_json_response(content))
    except Exception:
        logger.exception("面试问答抽取失败，降级为空结果")
        return {"questions": [], "overall": "", "overall_risks": ""}
