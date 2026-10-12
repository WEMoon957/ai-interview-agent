"""面试图构建：StateGraph + PostgresSaver（psycopg_pool 连接池）checkpointer。"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
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
# checkpointer 底层连接池（进程级唯一，close_interview_graph 负责释放）
_checkpointer_pool: Optional[ConnectionPool] = None
# 懒加载互斥锁：防止首个请求洪峰下多线程重复建池（重复建池会泄漏连接）
_init_lock = threading.Lock()


def _create_checkpointer() -> PostgresSaver:
    """创建并初始化 PostgresSaver（复用业务库，首次调用前 setup 建检查点表）。

    并发要点：psycopg 的 ``Connection``/``Cursor`` 不是为多线程共享设计的，
    且单条长连接一旦被数据库端断开（PG 重启 / 连接被 kill / 网络抖动）会永久
    失效，导致后续所有请求持续报错直到重启进程。这里改用
    ``psycopg_pool.ConnectionPool``：每个线程按需检出独立连接，坏连接会被
    自动丢弃并重建，池大小由配置（checkpointer_pool_min_size /
    checkpointer_pool_max_size）控制。

    连接参数必须满足 ``autocommit=True`` 与 ``row_factory=dict_row``（通过池的
    ``kwargs`` 透传给每条连接）；conn 为 ConnectionPool 时不可再传 ``pipe``。
    """
    global _checkpointer_pool

    settings = get_settings()
    pool = ConnectionPool(
        conninfo=settings.checkpointer_dsn,
        min_size=settings.checkpointer_pool_min_size,
        max_size=settings.checkpointer_pool_max_size,
        kwargs={"autocommit": True, "row_factory": dict_row},
        # 显式 open：psycopg_pool 未来版本会把默认值改为 False（现版本已发弃用告警），
        # 且必须保证交给 PostgresSaver 的池处于打开状态
        open=True,
    )
    checkpointer = PostgresSaver(pool)
    # 创建 checkpoints / checkpoint_blobs / checkpoint_writes（幂等）
    checkpointer.setup()
    _checkpointer_pool = pool
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

    with _init_lock:
        # 双重检查：并发首调时只允许一个线程完成建池 + 编译
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


def close_interview_graph() -> None:
    """释放 checkpointer 连接池并重置全局单例（应用关闭时调用，幂等）。

    不调用会残留连接，并在解释器退出时触发 "connection pool not closed" 类告警。
    """
    global _compiled_graph, _checkpointer_pool

    pool, _checkpointer_pool = _checkpointer_pool, None
    if pool is not None:
        pool.close()
    _compiled_graph = None