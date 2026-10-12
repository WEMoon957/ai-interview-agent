"""builder.py checkpointer 创建逻辑的回归测试（第 3 轮：连接池化）。

本轮修复：_create_checkpointer 由「单个 psycopg 长连接」改为
    psycopg_pool.ConnectionPool -> PostgresSaver(pool) -> setup()
本文件验证该创建路径与释放路径正确（用 mock，无需真实 PostgreSQL）。
"""
import inspect

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def test_postgres_saver_constructor_and_setup_api():
    """静态/签名级回归：PostgresSaver(conn) 构造器签名匹配、setup() 存在。"""
    from langgraph.checkpoint.postgres import PostgresSaver

    params = list(inspect.signature(PostgresSaver.__init__).parameters.keys())
    assert "conn" in params  # 首位位置参数：接受 Connection 或 ConnectionPool
    assert hasattr(PostgresSaver, "setup")


def test_connection_pool_accepts_required_kwargs():
    """静态回归：ConnectionPool 接受 conninfo / min_size / max_size / kwargs。"""
    params = list(inspect.signature(ConnectionPool.__init__).parameters.keys())
    for name in ("conninfo", "min_size", "max_size", "kwargs"):
        assert name in params


def test_create_checkpointer_uses_pool(monkeypatch):
    """回归：_create_checkpointer 应走 ConnectionPool + PostgresSaver(pool) + setup。"""
    import app.graph.builder as builder_mod
    from app.config import get_settings

    calls = {}

    def fake_pool(**kwargs):
        calls.update(kwargs)
        return "FAKE_POOL"

    class FakeSaver:
        def __init__(self, conn):
            calls["saver_conn"] = conn

        def setup(self):
            calls["setup"] = True

    monkeypatch.setattr(builder_mod, "ConnectionPool", fake_pool)
    monkeypatch.setattr(builder_mod, "PostgresSaver", FakeSaver)
    monkeypatch.setattr(builder_mod, "_checkpointer_pool", None)

    cp = builder_mod._create_checkpointer()
    settings = get_settings()

    assert isinstance(cp, FakeSaver)
    assert calls["saver_conn"] == "FAKE_POOL"
    # PostgresSaver 要求：autocommit=True + row_factory=dict_row，经池 kwargs 透传
    assert calls["kwargs"] == {"autocommit": True, "row_factory": dict_row}
    assert calls["min_size"] == settings.checkpointer_pool_min_size
    assert calls["max_size"] == settings.checkpointer_pool_max_size
    assert calls["open"] is True  # 显式打开（psycopg_pool 未来默认值将变为 False）
    assert calls["setup"] is True
    # 池需登记到模块级，供 close_interview_graph 释放
    assert builder_mod._checkpointer_pool == "FAKE_POOL"


def test_close_interview_graph_closes_pool(monkeypatch):
    """close_interview_graph 关闭池并重置单例；未建池时调用幂等不报错。"""
    import app.graph.builder as builder_mod

    closed = []

    class FakePool:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(builder_mod, "_checkpointer_pool", FakePool())
    monkeypatch.setattr(builder_mod, "_compiled_graph", object())

    builder_mod.close_interview_graph()

    assert closed == [True]
    assert builder_mod._checkpointer_pool is None
    assert builder_mod._compiled_graph is None

    # 幂等：再次调用无池可关，不抛异常
    builder_mod.close_interview_graph()
    assert closed == [True]


def test_pool_size_configurable_via_env(monkeypatch):
    """池大小可通过环境变量配置（不写死代码）。"""
    from app.config import Settings

    monkeypatch.setenv("CHECKPOINTER_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("CHECKPOINTER_POOL_MAX_SIZE", "7")

    settings = Settings(_env_file=None)

    assert settings.checkpointer_pool_min_size == 2
    assert settings.checkpointer_pool_max_size == 7