"""LLM 配置单例：统一走 OpenAI 兼容协议，禁止各节点自行 ChatOpenAI。"""
from functools import lru_cache
from typing import Type

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import get_settings
from app.models.schemas import AnswerScore


def get_llm(temperature: float | None = None, max_tokens: int | None = None) -> ChatOpenAI:
    """返回配置化的 ChatOpenAI 实例（OpenAI 兼容协议，可切换多提供商）。

    Args:
        temperature: 可选，覆盖全局 llm_temperature。
        max_tokens: 可选，覆盖全局 llm_max_tokens；默认取 settings.llm_max_tokens。
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature if temperature is None else temperature,
        max_tokens=settings.llm_max_tokens if max_tokens is None else max_tokens,
    )


@lru_cache
def get_structured_llm(schema: Type[BaseModel] = AnswerScore) -> Runnable:
    """返回绑定结构化输出的 Runnable（评分用低温 0.2）。

    结构化输出走 ``method="json_schema"``（而非默认的 function-calling / tool_choice）：
    DeepSeek V4.1 Flash（deepseek-flash）等「thinking mode」模型不支持 tool_choice，
    但支持 JSON Schema 约束输出，故统一用 json_schema 模式以保证多提供商兼容
    （共享知识 #8）。
    """
    llm = get_llm(temperature=get_settings().scoring_temperature)
    return llm.with_structured_output(schema, method="json_schema")
