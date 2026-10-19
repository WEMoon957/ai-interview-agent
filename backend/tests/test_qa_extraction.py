"""qa_extraction / minutes_poller 问答抽取单元测试。

覆盖：
- ``answer_quotes``（候选人关键原话）的解析与截断上限；
- 历史 LLM 输出缺该字段时的向后兼容（返回空数组而非报错）；
- 脏值过滤（非列表 / 空串 / 无问题的条目）；
- 空逐字稿短路（不打 LLM）、LLM 异常降级为空结果；
- 轮询器：转写成功后自动抽取并落库，抽取失败不影响转写结果。
"""
from app.services import minutes_poller as mp
from app.services import qa_extraction as qe


def _coerce(json_text: str) -> dict:
    """按真实链路解析 + 兜底（跳过 LLM 调用）。"""
    return qe._coerce_qa(qe._parse_json_response(json_text))


# ── 关键原话 ────────────────────────────────────────────────
def test_answer_quotes_parsed_and_capped_at_three():
    """LLM 给多条原话时最多保留 3 条，保持原始顺序。"""
    raw = """{"questions": [{"question": "之前做过餐饮吗？",
        "answer_summary": "做过三年服务员。",
        "answer_quotes": ["我在西贝干了三年", "当过小组长", "带过四个人", "还会收银", "能上夜班"],
        "highlights": ["有带人经验"]}], "overall": "稳定"}"""
    qa = _coerce(raw)
    item = qa["questions"][0]
    assert item["answer_quotes"] == ["我在西贝干了三年", "当过小组长", "带过四个人"]


def test_missing_answer_quotes_is_backward_compatible():
    """老版本 prompt 的输出没有 answer_quotes，仍应正常解析。"""
    raw = '{"questions": [{"question": "为什么离职？", "answer_summary": "想换环境", "highlights": []}], "overall": ""}'
    qa = _coerce(raw)
    assert qa["questions"][0]["answer_quotes"] == []
    assert qa["questions"][0]["answer_summary"] == "想换环境"


def test_dirty_values_are_dropped():
    """非列表原话 / 空串 / 无问题的条目全部丢弃，不污染看板。"""
    raw = """{"questions": [
        {"question": "   ", "answer_quotes": ["丢掉"]},
        {"question": "能接受排班吗？", "answer_summary": "", "answer_quotes": "不是列表", "highlights": ["  ", "能"]},
        {"question": "期望薪资？", "answer_quotes": ["  ", "5000 左右 "]}
    ], "overall": "  "}"""
    qa = _coerce(raw)
    assert len(qa["questions"]) == 2
    assert qa["questions"][0]["answer_quotes"] == []
    assert qa["questions"][0]["highlights"] == ["能"]
    assert qa["questions"][1]["answer_quotes"] == ["5000 左右"]
    assert qa["overall"] == ""


def test_prompt_asks_for_quotes():
    """prompt 必须明确要求关键原话，否则模型不会稳定返回该字段。"""
    assert "answer_quotes" in qe.QA_EXTRACTION_PROMPT
    assert "原话" in qe.QA_EXTRACTION_PROMPT


# ── 降级 ----------------------------------------------------
def test_empty_transcript_short_circuits_without_llm(monkeypatch):
    """空逐字稿直接返回空结果，不触发 LLM 调用（省钱且避免无意义请求）。"""

    def _boom(**kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("空逐字稿不应调用 LLM")

    monkeypatch.setattr(qe, "get_llm", _boom)
    assert qe.extract_interview_qa("   ") == {"questions": [], "overall": ""}


def test_llm_failure_degrades_to_empty(monkeypatch):
    """LLM 不可用时降级为空结果，不向上抛异常。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(qe, "get_llm", _boom)
    assert qe.extract_interview_qa("面试官：你好\n候选人：你好") == {"questions": [], "overall": ""}


# ── 轮询器接线 ───────────────────────────────────────────────
class _FakeJob:
    title = "店长"
    jd_text = "负责门店日常运营"


class _FakeRow:
    def __init__(self):
        self.minutes_token = "obcnxxxx"
        self.transcript_text = None
        self.transcript_status = ""
        self.transcript_at = None
        self.ai_score = None
        self.ai_dim_scores = []
        self.ai_comment = ""
        self.ai_scored_at = None
        self.qa_extract = {}
        self.qa_extracted_at = None
        self.job_id = None
        self.job = _FakeJob()


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


QA = {
    "questions": [
        {
            "question": "之前做过餐饮吗？",
            "answer_summary": "做过三年服务员。",
            "answer_quotes": ["我在西贝干了三年"],
            "highlights": ["经验匹配"],
        }
    ],
    "overall": "表达清楚，稳定性好。",
}


def _prepare(monkeypatch, qa_result=None, qa_exc=None):
    """把转写/评分/抽取全部打桩，只验证编排与落库。"""
    monkeypatch.setattr(mp, "_resolve_owner_token", lambda db, row, settings: ("ou_test", "user-token"))
    monkeypatch.setattr(mp, "get_transcript", lambda token, **kwargs: (True, "逐字稿正文", None))
    monkeypatch.setattr(mp, "_resolve_rubric", lambda db, row: [])
    monkeypatch.setattr(mp, "auto_rebuild_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mp, "score_whole_interview", lambda text, desc, rubric: {"total_score": 80, "dim_scores": [], "comment": ""}
    )

    def _extract(*args, **kwargs):
        if qa_exc:
            raise qa_exc
        return qa_result

    monkeypatch.setattr(mp, "extract_interview_qa", _extract)
    return _FakeRow(), _FakeDB()


def test_transcribe_then_auto_extract_qa(monkeypatch):
    """转写成功 → 评分 → 自动抽取问答并落库（BI 看板即可点开）。"""
    row, db = _prepare(monkeypatch, qa_result=QA)
    assert mp.transcribe_and_score(db, row) is True
    assert row.transcript_status == "transcribed"
    assert row.qa_extract == QA
    assert row.qa_extracted_at is not None


def test_empty_extraction_is_not_persisted(monkeypatch):
    """抽不到问答时不写时间戳，留给 --all 回填重试。"""
    row, db = _prepare(monkeypatch, qa_result={"questions": [], "overall": ""})
    assert mp.transcribe_and_score(db, row) is True
    assert row.transcript_status == "transcribed"
    assert row.qa_extract == {}
    assert row.qa_extracted_at is None


def test_extraction_failure_does_not_break_transcript(monkeypatch):
    """抽取抛异常时回滚该次写入，但转写本身仍算成功（返回 True）。"""
    row, db = _prepare(monkeypatch, qa_exc=RuntimeError("boom"))
    assert mp.transcribe_and_score(db, row) is True
    assert row.transcript_status == "transcribed"
    assert row.transcript_text == "逐字稿正文"
    assert row.qa_extracted_at is None
    assert db.rollbacks == 1


def test_transcript_failure_skips_extraction(monkeypatch):
    """拉不到逐字稿时置 failed 并直接返回 False，不进评分/抽取阶段。"""
    called = {"score": 0, "extract": 0}

    monkeypatch.setattr(mp, "_resolve_owner_token", lambda db, row, settings: (None, None))
    monkeypatch.setattr(mp, "get_transcript", lambda token, **kwargs: (False, "", "2091005"))
    monkeypatch.setattr(mp, "score_whole_interview", lambda *a, **k: called.__setitem__("score", called["score"] + 1))
    monkeypatch.setattr(mp, "extract_interview_qa", lambda *a, **k: called.__setitem__("extract", called["extract"] + 1))

    row, db = _FakeRow(), _FakeDB()
    assert mp.transcribe_and_score(db, row) is False
    assert row.transcript_status == "failed"
    assert called == {"score": 0, "extract": 0}
