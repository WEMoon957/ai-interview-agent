"""面试图构建：StateGraph + PostgresSaver checkpointer。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.nodes import ask_question, evaluate, final_report, load_context
from app.graph.route import route
from app.graph.state import InterviewState

if TYPE_CHECKING:
    # 仅用于类型标注，避免不同 langgraph 版本导出路径差异带来的运行时导入风险
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

_compiled_graph: Optional[CompiledStateGraph] = None


def _create_checkpointer() -> PostgresSaver:
    """创建并初始化 PostgresSaver（复用业务库，首次调用前 setup 建检查点表）。

    langgraph-checkpoint-postgres 的 ``from_conn_string`` 返回的是 contextmanager
    （其内部使用 autocommit=True + dict_row 连接），退出 with 块即关闭连接，
    不适合作为应用级单例复用。这里手动创建一个长生命周期 psycopg 连接，
    再传入 ``PostgresSaver(conn)``（官方文档明确要求 autocommit=True 与
    row_factory=dict_row）。生产高并发可升级为 psycopg_pool.ConnectionPool。
    """
    conn = psycopg.connect(
        get_settings().checkpointer_dsn,
        autocommit=True,
        row_factory=dict_row,
    )
    checkpointer = PostgresSaver(conn)
    # 创建 checkpoints / checkpoint_blobs / checkpoint_writes（幂等）
    checkpointer.setup()
    return checkpointer


def build_interview_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> CompiledStateGraph:
    """构建并缓存编译后的面试图。

    边结构：START → load_context → ask_question → evaluate
            evaluate → route → {ask_question | final_report}
            final_report → END
    """
    global _compiled_graph

    if _compiled_graph is not None:
        return _compiled_graph

    if checkpointer is None:
        checkpointer = _create_checkpointer()

    graph = StateGraph(InterviewState)

    graph.add_node("load_context", load_context)
    graph.add_node("ask_question", ask_question)
    graph.add_node("evaluate", evaluate)
    graph.add_node("final_report", final_report)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "ask_question")
    graph.add_edge("ask_question", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        route,
        {
            "ask_question": "ask_question",
            "final_report": "final_report",
        },
    )
    graph.add_edge("final_report", END)

    _compiled_graph = graph.compile(checkpointer=checkpointer)
    return _compiled_graph


def get_interview_graph() -> CompiledStateGraph:
    """获取全局唯一的编译图实例（懒加载，首次调用时才连接数据库）。"""
    return build_interview_graph()
