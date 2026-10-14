"""公司文化档案：快照 helper / 开场白注入 / 初始状态注入 的单测。

对应 docs/PRD-企业文化融入面试题.md 的 CC-4 / CC-5 / CC-6 验收标准：
- 未配置介绍时，链路行为与改造前零差异；
- 有介绍时开场白包含介绍，且限长截断；
- 快照隔离：会话用发链接时的快照，不受事后改档案影响。
"""
import uuid
from types import SimpleNamespace

from app.db.models.company import CompanyProfile
from app.services.company_profile import (
    OPENING_INTRO_MAX_CHARS,
    intro_for_opening,
    save_profile,
    snapshot_company_profile,
)
from app.graph.nodes.load_context import load_context

# 改造前的通用开场白（回归基线：无公司介绍时必须逐字一致）
LEGACY_OPENING = (
    "张三，你好！欢迎参加本次 AI 面试。"
    "接下来我会围绕岗位要求逐题向你提问，请尽量结合真实经历具体作答。"
    "部分问题我可能会根据你的回答进行追问，请保持放松，正常发挥即可。"
)


class _FakeQuery:
    """极简 query 桩：只支持 order_by().first() 这一条调用链。"""

    def __init__(self, rows):
        self._rows = rows

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    """极简 DB 桩：供公司档案服务与 _build_initial_state 使用。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []

    def query(self, _model):
        return _FakeQuery(self.rows)

    def get(self, _model, _pk):
        return None

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def flush(self):
        pass

    def commit(self):
        pass


def _profile(content: str, updated_by: str = "") -> CompanyProfile:
    return CompanyProfile(content=content, updated_by=updated_by)


# ── snapshot_company_profile ─────────────────────────────


def test_snapshot_empty_when_no_profile():
    """未配置档案：快照为空对象，不阻断发链接。"""
    assert snapshot_company_profile(_FakeDB([])) == {}


def test_snapshot_empty_when_content_blank():
    """档案存在但内容为空白：等价于未配置。"""
    assert snapshot_company_profile(_FakeDB([_profile("   \n  ")])) == {}


def test_snapshot_returns_stripped_content():
    """有介绍：快照为 {"content": ...}（去首尾空白）。"""
    db = _FakeDB([_profile("  我们是谁：笨二娘隆江猪脚饭  ")])
    assert snapshot_company_profile(db) == {"content": "我们是谁：笨二娘隆江猪脚饭"}


# ── save_profile（单例）───────────────────────────────────


def test_save_profile_creates_when_absent():
    """首次保存：新建一行，不提交（由路由层统一 commit）。"""
    db = _FakeDB([])
    row = save_profile(db, "公司介绍 v1", "李俊颖")
    assert row.content == "公司介绍 v1"
    assert row.updated_by == "李俊颖"
    assert db.added == [row]


def test_save_profile_updates_single_row():
    """再次保存：更新同一行，不新增（保持单例）。"""
    existing = _profile("公司介绍 v1", "李俊颖")
    db = _FakeDB([existing])
    row = save_profile(db, "公司介绍 v2", "王HR")
    assert row is existing
    assert row.content == "公司介绍 v2"
    assert row.updated_by == "王HR"
    assert db.added == []
    assert len(db.rows) == 1


def test_save_profile_allows_clearing():
    """允许清空介绍（回到未配置态）。"""
    db = _FakeDB([_profile("旧内容", "李俊颖")])
    save_profile(db, "", "")
    assert snapshot_company_profile(db) == {}


# ── intro_for_opening（限长截断）──────────────────────────


def test_intro_for_opening_empty_cases():
    """空快照 / 空内容 / None → 空串。"""
    assert intro_for_opening(None) == ""
    assert intro_for_opening({}) == ""
    assert intro_for_opening({"content": "   "}) == ""


def test_intro_for_opening_keeps_short_text():
    """不超过上限时原样返回。"""
    text = "我们正处于快速开店期，节假日最忙。"
    assert intro_for_opening({"content": text}) == text


def test_intro_for_opening_truncates_long_text():
    """超过 300 字：截断并加省略号。"""
    text = "文" * (OPENING_INTRO_MAX_CHARS + 120)
    result = intro_for_opening({"content": text})
    assert result.endswith("……")
    assert len(result) == OPENING_INTRO_MAX_CHARS + 2
    assert result.startswith("文" * 10)


# ── load_context 开场白 ───────────────────────────────────


def test_opening_without_intro_matches_legacy():
    """无公司介绍：开场白与改造前逐字一致（回归零差异）。"""
    opening = load_context({"candidate": {"name": "张三"}})["messages"][0].content
    assert opening == LEGACY_OPENING


def test_opening_without_intro_for_legacy_session_snapshot():
    """存量会话（快照为 None / 空字典）：同样走旧文案。"""
    for snapshot in (None, {}, {"content": ""}):
        state = {"candidate": {"name": "张三"}, "company_profile": snapshot}
        assert load_context(state)["messages"][0].content == LEGACY_OPENING


def test_opening_includes_intro_when_present():
    """有公司介绍：开场白包含介绍原文，且保留通用收尾。"""
    intro = "我们是笨二娘隆江猪脚饭，昆明起家，现有 12 家直营门店。"
    state = {"candidate": {"name": "李四"}, "company_profile": {"content": intro}}
    opening = load_context(state)["messages"][0].content
    assert intro in opening
    assert opening.startswith("李四，你好！欢迎参加本次 AI 面试。")
    assert "先花一分钟了解我们：" in opening
    assert "接下来我会围绕岗位要求逐题向你提问" in opening


def test_opening_truncates_long_intro():
    """超长介绍只进前 300 字，避免开场白过长。"""
    state = {
        "candidate": {"name": "李四"},
        "company_profile": {"content": "店" * 800},
    }
    opening = load_context(state)["messages"][0].content
    assert "……" in opening
    assert "店" * (OPENING_INTRO_MAX_CHARS + 1) not in opening


# ── _build_initial_state 注入快照（CC-5）───────────────────


def _fake_session(snapshot):
    return SimpleNamespace(
        id=uuid.uuid4(),
        thread_id="thread-x",
        template_id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        company_profile_snapshot=snapshot,
    )


def test_build_initial_state_injects_snapshot():
    """state.company_profile 取自会话快照。"""
    from app.api.interview import _build_initial_state

    state = _build_initial_state(
        _FakeDB([]), _fake_session({"content": "介绍内容"})
    )
    assert state["company_profile"] == {"content": "介绍内容"}


def test_build_initial_state_empty_for_legacy_session():
    """存量会话快照为 NULL：注入空字典，不影响既有行为。"""
    from app.api.interview import _build_initial_state

    state = _build_initial_state(_FakeDB([]), _fake_session(None))
    assert state["company_profile"] == {}
