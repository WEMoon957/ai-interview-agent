"""面试逐字稿 → 结构化问答（问题 + 回答亮点）抽取。

用途：把到店直招 / 线下复试的整场逐字稿拆成「面试官问了什么 + 候选人回答的要点与亮点」，
落库到 ``offline_interviews.qa_extract``，供 **招聘 BI 数据看板** 点击查看；
业务系统不做展示（故 ``_offline_out`` 不返回该字段）。

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
3. 每条给出候选人回答的要点摘要 answer_summary（1-2 句）。
4. highlights 提取候选人回答里的**亮点/加分项**（如具体经验佐证、有服务意识、稳定性、主动性等），
   0-3 条；若该回答平淡无明显亮点，可返回空数组，不要硬凑。
5. overall 用 2-3 句概括整场问答中候选人的整体表现与最突出的亮点。

【输出格式】只输出一个 JSON 对象，不要任何额外文字或 Markdown 代码块标记：
{{"questions": [{{"question": "面试官的问题", "answer_summary": "候选人回答要点", "highlights": ["亮点1", "亮点2"]}}], "overall": "整场表现概述"}}"""


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
    """把 LLM 输出兜底成规范的 ``{"questions": [...], "overall": str}``，丢弃脏值。"""
    if not isinstance(parsed, dict):
        return {"questions": [], "overall": ""}
    questions: list[dict] = []
    raw = parsed.get("questions")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            if not q:
                continue
            highlights_raw = item.get("highlights")
            highlights = (
                [str(h).strip() for h in highlights_raw if str(h).strip()]
                if isinstance(highlights_raw, list)
                else []
            )
            questions.append(
                {
                    "question": q,
                    "answer_summary": str(item.get("answer_summary") or "").strip(),
                    "highlights": highlights[:3],
                }
            )
    return {"questions": questions, "overall": str(parsed.get("overall") or "").strip()}


def extract_interview_qa(
    transcript: str,
    job_title: str = "",
    job_desc: str = "",
) -> dict:
    """把整场逐字稿抽取为结构化问答。

    返回 ``{"questions": [{"question", "answer_summary", "highlights": [...]}], "overall": str}``。

    任何 LLM 失败均降级为空结果（questions=[]），不抛异常，由调用方决定是否重试。
    """
    if not (transcript or "").strip():
        return {"questions": [], "overall": ""}

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
        return {"questions": [], "overall": ""}
