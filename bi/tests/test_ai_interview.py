"""BI「AI 面试记录」单测（不依赖数据库的部分）。

接口行为（列表 / 详情）在本地用真实库冒烟过，这里只锁住两件容易被改坏的小事：
1. 非法 session_id 必须被拦在 SQL 之前（否则会变成 500 而不是 404）；
2. 状态文案固定，前端直接用 ``status_label`` 渲染。
"""
from __future__ import annotations

import importlib.util
import pathlib

BI_DIR = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("bi_app_ai_test", BI_DIR / "app.py")
assert _spec and _spec.loader
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)


def test_invalid_session_id_is_rejected_before_query():
    # 没有 conn 也不会炸 → 证明 uuid 校验发生在连库之前
    assert bi._ai_interview_detail(None, "abc") is None
    assert bi._ai_interview_detail(None, "") is None
    assert bi._ai_interview_detail(None, "0000") is None


def test_status_labels_are_stable():
    assert bi.AI_STATUS_LABELS == {
        "pending": "待开始",
        "running": "面试中",
        "finished": "已完成",
    }
