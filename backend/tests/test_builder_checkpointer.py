"""builder.py checkpointer 创建逻辑的回归测试（第 2 轮，langgraph 0.6.x）。

Bug 1 修复后，_create_checkpointer 改用：
    psycopg.connect(dsn, autocommit=True, row_factory=dict_row) -> PostgresSaver(conn) -> setup()
本文件验证该创建路径正确（用 mock 连接，无需真实 PostgreSQL）。
"""
import psycopg
from psycopg.rows import dict_row


def test_postgres_saver_constructor_and_setup_api():
    """静态/签名级回归：PostgresSaver(conn) 构造器签名匹配、setup() 存在。"""
    import inspect

    from langgraph.checkpoint.postgres import PostgresSaver

    params = list(inspect.signature(PostgresSaver.__init__).parameters.keys())
    assert "conn" in params  # 首位位置参数
    assert hasattr(PostgresSaver, "setup")


def test_psycopg_connect_accepts_required_kwargs():
    """静态回归：psycopg.connect 接受 autocommit 与 row_factory 关键字。"""
    import inspect

    params = list(inspect.signature(psycopg.connect).parameters.keys())
    assert "autocommit" in params
    assert "row_factory" in params


def test_create_checkpointer_uses_new_api(monkeypatch):
    """回归 Bug 1：_create_checkpointer 应走 psycopg.connect + PostgresSaver(conn) + setup。"""
    import app.graph.builder as builder_mod

    calls = {}

    def fake_connect(dsn, autocommit=False, row_factory=None, **kwargs):
        calls["dsn"] = dsn
        calls["autocommit"] = autocommit
        calls["row_factory"] = row_factory
        return object()

    monkeypatch.setattr(builder_mod.psycopg, "connect", fake_connect)

    class FakeSaver:
        def __init__(self, conn):
            self.conn = conn

        def setup(self):
            calls["setup"] = True

    monkeypatch.setattr(builder_mod, "PostgresSaver", FakeSaver)

    cp = builder_mod._create_checkpointer()

    assert isinstance(cp, FakeSaver)
    assert calls.get("autocommit") is True
    assert calls.get("row_factory") is dict_row
    assert calls.get("setup") is True
