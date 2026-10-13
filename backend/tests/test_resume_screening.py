"""简历初筛服务单测：维度归一化 / 总分重算 / 降级兜底（fake LLM，不依赖外部服务）。"""
import importlib
import json
from types import SimpleNamespace

from app.models.schemas import ScreeningDimension
from app.services.resume_screening import recommendation_from_score


def _dims():
    return [
        ScreeningDimension(key="skill_match", name="技能匹配", weight=0.30, max_score=10),
        ScreeningDimension(key="experience", name="经验相关", weight=0.30, max_score=10),
        ScreeningDimension(key="stability", name="稳定性", weight=0.20, max_score=10),
        ScreeningDimension(key="attitude", name="素质与态度", weight=0.20, max_score=10),
    ]


# ── recommendation 阈值 ────────────────────────────────────
def test_recommendation_thresholds():
    assert recommendation_from_score(80) == "强烈推荐"
    assert recommendation_from_score(79.9) == "推荐"
    assert recommendation_from_score(65) == "推荐"
    assert recommendation_from_score(64.9) == "待定"
    assert recommendation_from_score(50) == "待定"
    assert recommendation_from_score(49.9) == "不推荐"


# ── 结果对齐与总分重算 ─────────────────────────────────────
def test_coerce_result_recomputes_total_and_normalizes_weights():
    mod = importlib.import_module("app.services.resume_screening")
    parsed = {
        "dim_scores": [
            {"key": "skill_match", "score": 8},
            {"key": "experience", "score": 6},
            {"key": "stability", "score": 10},
            {"key": "attitude", "score": 7},
        ],
        "summary": "整体尚可",
        "highlights": ["有 3 年后厨经验"],
        "risks": ["近两年换过 3 家店"],
    }
    result = mod._coerce_result(parsed, _dims())

    assert len(result.dim_scores) == 4
    # 权重已归一化为 1
    assert round(sum(d.weight for d in result.dim_scores), 4) == 1.0
    # 总分 = Σ(score/max * weight) * 100，代码重算而非信任 LLM
    expected = round((8 / 10 * 0.3 + 6 / 10 * 0.3 + 10 / 10 * 0.2 + 7 / 10 * 0.2) * 100, 2)
    assert result.total_score == expected
    assert result.highlights == ["有 3 年后厨经验"]


def test_coerce_result_clamps_out_of_range_and_fills_missing():
    mod = importlib.import_module("app.services.resume_screening")
    parsed = {"dim_scores": [{"key": "skill_match", "score": 99}, {"key": "experience", "score": -5}]}
    result = mod._coerce_result(parsed, _dims())

    by_key = {d.key: d.score for d in result.dim_scores}
    assert by_key["skill_match"] == 10  # 上越界夹到满分
    assert by_key["experience"] == 0  # 下越界夹到 0
    assert by_key["stability"] == 0  # 缺失维度按 0 计，不丢维度


def test_coerce_result_handles_weight_sum_zero():
    """权重全 0 时按等权回退，不出现除零。"""
    mod = importlib.import_module("app.services.resume_screening")
    dims = [
        ScreeningDimension(key="a", name="A", weight=0, max_score=10),
        ScreeningDimension(key="b", name="B", weight=0, max_score=10),
    ]
    result = mod._coerce_result({"dim_scores": [{"key": "a", "score": 10}, {"key": "b", "score": 0}]}, dims)
    assert result.dim_scores[0].weight == 0.5
    assert result.total_score == 50.0


# ── screen_resume（fake LLM）──────────────────────────────
def _fake_llm(content_or_raise):
    def _invoke(_prompt):
        if isinstance(content_or_raise, Exception):
            raise content_or_raise
        return SimpleNamespace(content=content_or_raise)

    return lambda **_kwargs: SimpleNamespace(invoke=_invoke)


def test_screen_resume_with_fake_llm(monkeypatch):
    mod = importlib.import_module("app.services.resume_screening")

    payload = json.dumps(
        {
            "dim_scores": [
                {"key": "skill_match", "score": 9},
                {"key": "experience", "score": 8},
                {"key": "stability", "score": 7},
                {"key": "attitude", "score": 8},
            ],
            "summary": "刀工扎实，符合后厨岗位要求",
            "highlights": ["5 年砧板经验"],
            "risks": [],
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(mod, "get_llm", _fake_llm(payload))

    result = mod.screen_resume("后厨", "要求 2 年经验", "张三", "5 年砧板经验")
    assert result.total_score > 70
    assert "刀工" in result.summary
    assert result.highlights == ["5 年砧板经验"]


def test_screen_resume_degrades_on_garbage_output(monkeypatch):
    """LLM 输出无法解析时降级为中性结果，不抛异常。"""
    mod = importlib.import_module("app.services.resume_screening")
    monkeypatch.setattr(mod, "get_llm", _fake_llm("我拒绝输出 JSON"))

    result = mod.screen_resume("后厨", "jd", "李四", "简历文本")
    assert result.total_score == 0.0
    assert result.risks  # 标记需人工复核
    assert len(result.dim_scores) == 4  # 维度结构完整，便于表格展示


def test_screen_resume_degrades_on_llm_exception(monkeypatch):
    """LLM 调用异常时同样降级，不打断批量上传。"""
    mod = importlib.import_module("app.services.resume_screening")
    monkeypatch.setattr(mod, "get_llm", _fake_llm(RuntimeError("timeout")))

    result = mod.screen_resume("后厨", "jd", "王五", "简历")
    assert result.total_score == 0.0
