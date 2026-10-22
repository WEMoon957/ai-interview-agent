"""妙记「按候选人姓名匹配标题」单测。

覆盖：
- 姓名归一化（全角/空格/分隔符/大小写）与单字姓名保护；
- 妙记链接解析（网页链接 / App 分享链接 / 裸 token / 整段分享文案）；
- 搜索时间窗（无约面时间时用最近 N 天；有约面时间时覆盖前后 2 天）；
- 轮询匹配：姓名唯一命中 → 绑定；姓名命中多条 → 不绑定（防串场）；短码兜底。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import app.services.feishu_minutes as fm
import app.services.minutes_poller as mp


def _settings(**kw):
    base = dict(feishu_minutes_search_window_days=7)
    base.update(kw)
    return SimpleNamespace(**base)


# ── 姓名归一化与匹配 ────────────────────────────────────────
def test_normalize_name_strips_spaces_and_separators():
    assert fm.normalize_name(" 王 芳 ") == "王芳"
    assert fm.normalize_name("王芳-复试") == "王芳复试"
    assert fm.normalize_name("王芳（店长面试）") == "王芳店长面试"
    assert fm.normalize_name("Ｊｏｈｎ　Ｓｍｉｔｈ") == "johnsmith"


def test_match_candidate_name_variants():
    assert fm.match_candidate_name("王芳 店长面试", "王芳")
    assert fm.match_candidate_name("店长面试-王芳", "王芳")
    assert fm.match_candidate_name("王 芳 复试录音", "王芳")
    assert fm.match_candidate_name("王芳（服务员）", "王芳")
    assert fm.match_candidate_name("John Smith interview", "john smith")
    assert not fm.match_candidate_name("李四 店长面试", "王芳")


def test_match_candidate_name_rejects_single_char_name():
    """单字姓名会命中任意标题，必须拒绝自动匹配。"""
    assert not fm.match_candidate_name("王芳 店长面试", "王")
    assert not fm.match_candidate_name("  ", "王")


def test_match_candidate_name_empty_inputs():
    assert not fm.match_candidate_name("", "王芳")
    assert not fm.match_candidate_name("王芳 面试", "")


# ── 妙记链接解析 ────────────────────────────────────────────
def test_extract_token_from_web_url():
    assert (
        fm.extract_minute_token("https://xxx.feishu.cn/minutes/obcn6qGKMiZy0eNFSKl6e8")
        == "obcn6qGKMiZy0eNFSKl6e8"
    )


def test_extract_token_from_url_with_query_and_fragment():
    assert (
        fm.extract_minute_token(
            "https://xxx.feishu.cn/minutes/obcnabc123?from=share#detail"
        )
        == "obcnabc123"
    )


def test_extract_token_from_app_share_link():
    """飞书 App 分享出来的是 ?minuteToken= 形态，路径里没有 /minutes/<token>。"""
    assert (
        fm.extract_minute_token(
            "https://applink.feishu.cn/client/minutes/open?minuteToken=obcnshare999"
        )
        == "obcnshare999"
    )


def test_extract_token_from_share_text_with_title():
    """整段分享文案（带标题正文）也能取出 token。"""
    text = "张三的面试录音 https://xxx.feishu.cn/minutes/obcnfulltext 点击查看详情"
    assert fm.extract_minute_token(text) == "obcnfulltext"


def test_extract_token_from_bare_obcn():
    assert fm.extract_minute_token("obcnbare123456789") == "obcnbare123456789"


def test_extract_token_from_raw_token():
    assert fm.extract_minute_token("a" * 24) == "a" * 24


def test_extract_token_invalid_returns_none():
    assert fm.extract_minute_token("") is None
    assert fm.extract_minute_token("随便写点什么") is None
    assert fm.extract_minute_token("https://xxx.feishu.cn/drive/folder/xxx") is None


# ── 搜索时间窗 ──────────────────────────────────────────────
def test_search_window_without_scheduled_uses_recent_days():
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(scheduled_at=None)
    start, end = mp._search_window(row, now, _settings(feishu_minutes_search_window_days=7))
    assert start == now - timedelta(days=7)
    assert end == now


def test_search_window_covers_scheduled_day():
    now = datetime.now(timezone.utc)
    scheduled = now - timedelta(days=20)  # 远早于「最近 7 天」窗口
    row = SimpleNamespace(scheduled_at=scheduled)
    start, end = mp._search_window(row, now, _settings(feishu_minutes_search_window_days=7))
    assert start <= scheduled - timedelta(days=2)
    assert end >= scheduled


def test_search_window_handles_naive_scheduled():
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(scheduled_at=datetime(2026, 10, 1, 14, 0))  # naive
    start, end = mp._search_window(row, now, _settings())
    assert start.tzinfo is not None and end.tzinfo is not None


def test_search_window_never_exceeds_api_limit():
    """飞书 search 单次跨度上限 30 天，窗口配置过大时必须收缩。"""
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(scheduled_at=None)
    start, end = mp._search_window(row, now, _settings(feishu_minutes_search_window_days=90))
    assert end - start == timedelta(days=30)


# ── 轮询匹配（防串场 + 短码兜底）────────────────────────────
class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """第一次 query 返回待匹配记录，第二次返回待转写记录（本项目轮询的真实顺序）。"""

    def __init__(self, pending, to_transcribe=None):
        self._responses = [list(pending), list(to_transcribe or [])]
        self._calls = 0
        self.commits = 0

    def query(self, _model):
        rows = self._responses[self._calls] if self._calls < len(self._responses) else []
        self._calls += 1
        return _FakeQuery(rows)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _row(name="王芳", code="270615", scheduled_at=None):
    return SimpleNamespace(
        id="offline-1",
        candidate=SimpleNamespace(name=name),
        walk_in_code=code,
        minutes_token=None,
        minutes_url=None,
        minutes_title="",
        transcript_status="pending",
        interviewer_open_id="ou_a",
        scheduled_at=scheduled_at,
    )


def _run_poll(db, search_impl):
    with (
        patch.object(mp, "SessionLocal", return_value=db),
        patch.object(mp, "_resolve_owner_token", return_value=("ou_a", "u-1")),
        patch.object(mp, "search_minutes", side_effect=search_impl),
    ):
        return mp.poll_minutes_once(_settings())


def test_poll_binds_when_name_hits_once():
    row = _row()
    db = _FakeDB([row])
    items = [{"title": "王芳 店长面试", "token": "obcn1", "owner_id": "ou_a"}]

    def search(owner, start, end, **kw):
        return True, items, ""

    stats = _run_poll(db, search)
    assert stats["matched"] == 1
    assert row.minutes_token == "obcn1"
    assert row.transcript_status == "matched"


def test_poll_skips_when_name_hits_multiple():
    """同名歧义：两条都叫王芳，绝不自动绑定（错绑是静默的）。"""
    row = _row()
    db = _FakeDB([row])
    items = [
        {"title": "王芳 店长面试", "token": "obcnA", "owner_id": "ou_a"},
        {"title": "王芳 复试", "token": "obcnB", "owner_id": "ou_a"},
    ]

    def search(owner, start, end, **kw):
        # 姓名搜到 2 条；短码兜底那一次也搜不到（标题里没短码）
        if kw.get("query") == row.walk_in_code:
            return True, [], ""
        return True, items, ""

    stats = _run_poll(db, search)
    assert stats["matched"] == 0
    assert stats["ambiguous"] == 1
    assert row.minutes_token is None
    assert row.transcript_status == "pending"


def test_poll_falls_back_to_code_when_name_misses():
    """老流程「标题写短码」的记录仍能认出来。"""
    row = _row()
    db = _FakeDB([row])

    def search(owner, start, end, **kw):
        if kw.get("query") == "王芳":
            return True, [], ""
        return True, [{"title": "270615 店长面试", "token": "obcnCode", "owner_id": "ou_a"}], ""

    stats = _run_poll(db, search)
    assert stats["matched"] == 1
    assert row.minutes_token == "obcnCode"


def test_poll_ignores_unrelated_title():
    row = _row()
    db = _FakeDB([row])

    def search(owner, start, end, **kw):
        return True, [{"title": "李四 店长面试", "token": "obcnX", "owner_id": "ou_a"}], ""

    stats = _run_poll(db, search)
    assert stats["matched"] == 0
    assert row.minutes_token is None
