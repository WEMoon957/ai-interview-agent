"""终试面试档案：把候选人此前各环节的评分维度、优点、风险、待确认问题聚合成
一份「老板终试前速览」。

设计原则：

- **确定性聚合，不调 LLM**：所有素材都已由上游环节产出并落库（简历初筛 /
  AI 面试报告 + 逐题评分 / 线下复试），本模块只做读取与归一化，同一份数据
  每次算出的档案完全一致，便于老板与 HR 对齐口径。
- **维度分统一到「满分 10」口径**（``norm``）后再横向对比 —— 简历初筛与人工
  复试可能是 100 分制，AI 报告与 AI 参考分本身已是 10 分制。
- **每条结论都带 ``source``**（出处），老板能判断该信哪条、终试该追问什么。
- 线下复试的 ``qa_extract``（逐字稿问答）按既有约定只归数据看板消费，这里
  **仅取派生结论**（问答亮点 → 优点、未答清的问题 → 待确认），不返回问答全文。

数据取用顺序：汇总快照里的 ``detail.*_id`` → 实时查那条记录；查不到再按
``(job_id, candidate_id)`` 兜底取最近一条，避免快照字段为空时档案整块缺失。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    AnswerEvaluation,
    InterviewSession,
    InterviewTemplate,
    OfflineInterview,
    RecruitmentSummary,
    ResumeScreening,
    SessionReport,
)
from app.services.final_interview import final_label

# ── 阶段标识（维度矩阵的列）────────────────────────────────────
STAGE_SCREENING = "screening"
STAGE_AI = "ai_interview"
STAGE_OFFLINE = "offline"
STAGE_OFFLINE_AI = "offline_ai"

STAGE_LABELS: dict[str, str] = {
    STAGE_SCREENING: "简历初筛",
    STAGE_AI: "AI 面试",
    STAGE_OFFLINE: "HR/店长复试",
    STAGE_OFFLINE_AI: "复试录音 AI 参考",
}

# 短板 / 亮点阈值（10 分制口径）
DIM_LOW_NORM = 6.0
DIM_STRONG_NORM = 8.0

# 每类结论最多返回条数（避免档案变成流水账）
MAX_SECTION_ITEMS = 8
MAX_DIM_ROWS = 16
MAX_QA_ITEMS = 20

OFFLINE_DECISION_LABELS: dict[str, str] = {
    "pass": "建议录用",
    "hold": "待定",
    "reject": "不建议录用",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_uuid(value: Any) -> uuid.UUID | None:
    """尽力把快照里的 id 转成 UUID，脏值返回 None（不抛异常）。"""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _num(value: Any) -> float | None:
    """转 float，失败返回 None。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_list(value: Any, limit: int = 3) -> list[str]:
    """字符串数组清洗：去空、去重、截断。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ── 取数 ────────────────────────────────────────────────────────
def _load_screening(db: Session, summary: RecruitmentSummary) -> ResumeScreening | None:
    sid = _as_uuid((summary.detail or {}).get("screening_id"))
    row = db.get(ResumeScreening, sid) if sid else None
    if row is not None:
        return row
    return (
        db.query(ResumeScreening)
        .filter(
            ResumeScreening.job_id == summary.job_id,
            ResumeScreening.candidate_id == summary.candidate_id,
        )
        .order_by(ResumeScreening.created_at.desc())
        .first()
    )


def _load_session_bundle(
    db: Session, summary: RecruitmentSummary
) -> tuple[InterviewSession | None, SessionReport | None, list[AnswerEvaluation]]:
    """取 AI 面试会话 + 报告 + 逐题评分（三者可能缺任意一项）。"""
    sid = _as_uuid((summary.detail or {}).get("session_id"))
    session = db.get(InterviewSession, sid) if sid else None
    if session is None:
        # 兜底：该候选人在本岗位模板下的最近一次会话
        session = (
            db.query(InterviewSession)
            .join(InterviewTemplate, InterviewSession.template_id == InterviewTemplate.id)
            .filter(
                InterviewSession.candidate_id == summary.candidate_id,
                InterviewTemplate.job_id == summary.job_id,
            )
            .order_by(InterviewSession.created_at.desc())
            .first()
        )
    if session is None:
        return None, None, []

    report = db.query(SessionReport).filter(SessionReport.session_id == session.id).first()
    evaluations = (
        db.query(AnswerEvaluation)
        .filter(AnswerEvaluation.session_id == session.id)
        .order_by(AnswerEvaluation.created_at.asc())
        .all()
    )
    return session, report, evaluations


def _load_offline(db: Session, summary: RecruitmentSummary) -> OfflineInterview | None:
    oid = _as_uuid((summary.detail or {}).get("offline_id"))
    row = db.get(OfflineInterview, oid) if oid else None
    if row is not None:
        return row
    return (
        db.query(OfflineInterview)
        .filter(
            OfflineInterview.job_id == summary.job_id,
            OfflineInterview.candidate_id == summary.candidate_id,
        )
        .order_by(OfflineInterview.created_at.desc())
        .first()
    )


# ── 维度归一化 ──────────────────────────────────────────────────
def _dims_from_raw(dims: Any, *, normalized: bool = False) -> list[dict[str, Any]]:
    """把各来源的 dim_scores 统一成 ``{key,name,score,max_score,norm}``。

    ``normalized=True`` 表示传入的 ``score`` 已经是 10 分制（AI 报告 / AI 参考分），
    不再按 ``max_score`` 折算 —— 避免 rubric 满分非 10 时被二次缩放。
    """
    out: list[dict[str, Any]] = []
    if not isinstance(dims, list):
        return out
    for item in dims:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("key") or "").strip()
        if not name:
            continue
        score = _num(item.get("score"))
        max_score = _num(item.get("max_score")) or 10.0
        if score is None:
            norm = None
        elif normalized:
            norm = round(score, 2)
        else:
            norm = round(score / max_score * 10.0, 2) if max_score > 0 else None
        out.append(
            {
                "key": str(item.get("key") or name),
                "name": name,
                "score": score,
                "max_score": max_score,
                "norm": norm,
            }
        )
    return out


def _merge_dims(
    rows: dict[str, dict[str, Any]],
    dims: list[dict[str, Any]],
    stage: str,
) -> None:
    """把某阶段的维度分写进矩阵行。

    按 ``key`` 合并（key 缺失时退化为名称）—— 同一套 rubric 在不同环节会用
    同一 key，才能落到同一行做横向对比；展示名取首次出现的 name。
    """
    for dim in dims:
        row_key = dim.get("key") or dim["name"]
        row = rows.get(row_key)
        if row is None:
            row = {"name": dim["name"], "cells": {}}
            rows[row_key] = row
        row["cells"][stage] = dim["norm"]


def _dedupe_items(items: list[dict[str, Any]], *, limit: int = MAX_SECTION_ITEMS) -> list[dict[str, Any]]:
    """按 text/question 去重并截断（保留首次出现的出处）。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("text") or item.get("question") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def derive_dim_insights(
    dim_rows: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """从维度矩阵派生终试材料：亮点 → 优点，短板 → 风险 + 待确认问题。

    同名维度只取一次（取最极端的那个阶段的分数），避免同一短板在四个环节里
    重复刷屏。阈值见 ``DIM_LOW_NORM`` / ``DIM_STRONG_NORM``。
    """
    strengths: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    weak: list[dict[str, Any]] = []
    strong: list[dict[str, Any]] = []
    for row in dim_rows.values():
        for stage, norm in (row.get("cells") or {}).items():
            if norm is None:
                continue
            entry = {"name": row["name"], "norm": norm, "stage": stage}
            if norm < DIM_LOW_NORM:
                weak.append(entry)
            elif norm >= DIM_STRONG_NORM:
                strong.append(entry)
    weak.sort(key=lambda d: d["norm"])
    strong.sort(key=lambda d: d["norm"], reverse=True)

    seen_weak: set[str] = set()
    for item in weak:
        if item["name"] in seen_weak:
            continue
        seen_weak.add(item["name"])
        label = STAGE_LABELS.get(item["stage"], item["stage"])
        risks.append(
            {"text": f"{item['name']} 偏弱：{item['norm']}/10（{label}）", "source": label}
        )
        pending.append(
            {
                "question": f"重点确认「{item['name']}」：{label}只给了 {item['norm']}/10",
                "source": "维度短板",
                "hint": "建议让候选人举一个具体事例再判断",
            }
        )
        if len(seen_weak) >= 4:
            break

    seen_strong: set[str] = set()
    for item in strong:
        if item["name"] in seen_strong:
            continue
        seen_strong.add(item["name"])
        label = STAGE_LABELS.get(item["stage"], item["stage"])
        strengths.append(
            {"text": f"{item['name']} 突出：{item['norm']}/10（{label}）", "source": label}
        )
        if len(seen_strong) >= 3:
            break

    # 兜底：整份档案没有 8 分以上维度时，标出「相对最强项」，
    # 避免中等水平候选人（基层岗位 7-8 分是正常落点）的优点栏永远空白
    if not strengths and dim_rows:
        best: dict[str, Any] | None = None
        for row in dim_rows.values():
            for stage, norm in (row.get("cells") or {}).items():
                if norm is None:
                    continue
                if best is None or norm > best["norm"]:
                    best = {"name": row["name"], "norm": norm, "stage": stage}
        if best is not None and best["norm"] >= 7.0:
            label = STAGE_LABELS.get(best["stage"], best["stage"])
            strengths.append(
                {
                    "text": f"{best['name']} 是各环节最强项：{best['norm']}/10（{label}）",
                    "source": label,
                }
            )

    # 兜底：没有绝对短板（<6 分）时，把「各环节最低维度」作为终试关注点 ——
    # 老板终试总要有得问，否则档案退化成一张成绩单
    if not pending and dim_rows:
        worst: dict[str, Any] | None = None
        for row in dim_rows.values():
            for stage, norm in (row.get("cells") or {}).items():
                if norm is None:
                    continue
                if worst is None or norm < worst["norm"]:
                    worst = {"name": row["name"], "norm": norm, "stage": stage}
        if worst is not None and worst["norm"] < DIM_STRONG_NORM:
            label = STAGE_LABELS.get(worst["stage"], worst["stage"])
            pending.append(
                {
                    "question": f"再确认「{worst['name']}」：各环节最低 {worst['norm']}/10（{label}）",
                    "source": "相对短板",
                    "hint": "整体达标，让候选人举一个具体例子说明该维度的实际表现",
                }
            )

    return strengths, risks, pending


# ── 主入口 ──────────────────────────────────────────────────────
def build_interview_brief(db: Session, summary: RecruitmentSummary) -> dict[str, Any]:
    """聚合一候选人终试前速览：各环节评分 + 维度矩阵 + 优点 / 风险 / 待确认问题。"""
    screening = _load_screening(db, summary)
    session, report, evaluations = _load_session_bundle(db, summary)
    offline = _load_offline(db, summary)

    stages: list[dict[str, Any]] = []
    columns: list[dict[str, str]] = []
    dim_rows: dict[str, dict[str, Any]] = {}

    strengths: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    # 1) 简历初筛（免初筛岗位的「导入待面试」记录既无分数也无维度，不占一个空阶段）
    screen_dims = _dims_from_raw(screening.dim_scores) if screening is not None else []
    if screening is not None and (screening.total_score is not None or screen_dims):
        dims = screen_dims
        if dims:
            columns.append({"key": STAGE_SCREENING, "label": STAGE_LABELS[STAGE_SCREENING]})
            _merge_dims(dim_rows, dims, STAGE_SCREENING)
        stages.append(
            {
                "key": STAGE_SCREENING,
                "label": STAGE_LABELS[STAGE_SCREENING],
                "score": screening.total_score,
                "score_scale": 100,
                "conclusion": screening.recommendation or "",
                "comment": screening.summary or "",
                "interviewer": "AI 初筛",
                "happened_at": _iso(screening.created_at),
                "dims": dims,
                "extra": {},
            }
        )
        for text in _clean_list(screening.highlights)[:3]:
            strengths.append({"text": text, "source": STAGE_LABELS[STAGE_SCREENING]})
        for text in _clean_list(screening.risks)[:3]:
            risks.append({"text": text, "source": STAGE_LABELS[STAGE_SCREENING]})

    # 2) AI 面试（报告维度分已是 10 分制）
    if report is not None:
        dims = _dims_from_raw(report.dim_scores, normalized=True)
        if dims:
            columns.append({"key": STAGE_AI, "label": STAGE_LABELS[STAGE_AI]})
            _merge_dims(dim_rows, dims, STAGE_AI)
        stages.append(
            {
                "key": STAGE_AI,
                "label": STAGE_LABELS[STAGE_AI],
                "score": report.total_score,
                "score_scale": 100,
                "conclusion": report.verdict or "",
                "comment": report.summary or "",
                "interviewer": "AI 面试官",
                "happened_at": _iso(session.started_at if session else None),
                "dims": dims,
                "extra": {},
            }
        )

    # 3) 线下复试（人工打分）
    if offline is not None:
        manual_dims = _dims_from_raw(offline.dim_scores)
        if manual_dims:
            columns.append({"key": STAGE_OFFLINE, "label": STAGE_LABELS[STAGE_OFFLINE]})
            _merge_dims(dim_rows, manual_dims, STAGE_OFFLINE)
        stages.append(
            {
                "key": STAGE_OFFLINE,
                "label": STAGE_LABELS[STAGE_OFFLINE],
                "score": offline.score,
                "score_scale": 100,
                "conclusion": OFFLINE_DECISION_LABELS.get(offline.decision, offline.decision or ""),
                "comment": offline.comment or "",
                "interviewer": offline.interviewer or "",
                "happened_at": _iso(offline.scored_at or offline.scheduled_at),
                "dims": manual_dims,
                "extra": {"ai_score": offline.ai_score} if offline.ai_score is not None else {},
            }
        )
        # 复试结论为待定 / 不建议时，直接作为风险项
        if offline.decision in {"hold", "reject"}:
            risks.append(
                {
                    "text": f"复试结论：{OFFLINE_DECISION_LABELS.get(offline.decision, offline.decision)}"
                    + (f"（{offline.comment.strip()[:60]}）" if offline.comment.strip() else ""),
                    "source": STAGE_LABELS[STAGE_OFFLINE],
                }
            )
        # 复试录音 AI 参考分（与人工分并存，不覆盖）
        ai_dims = _dims_from_raw(offline.ai_dim_scores, normalized=True)
        if ai_dims or offline.ai_score is not None:
            if ai_dims:
                columns.append({"key": STAGE_OFFLINE_AI, "label": STAGE_LABELS[STAGE_OFFLINE_AI]})
                _merge_dims(dim_rows, ai_dims, STAGE_OFFLINE_AI)
            stages.append(
                {
                    "key": STAGE_OFFLINE_AI,
                    "label": STAGE_LABELS[STAGE_OFFLINE_AI],
                    "score": offline.ai_score,
                    "score_scale": 100,
                    "conclusion": "",
                    "comment": offline.ai_comment or "",
                    "interviewer": "AI（整场逐字稿）",
                    "happened_at": _iso(offline.ai_scored_at),
                    "dims": ai_dims,
                    "extra": {},
                }
            )
        # 逐字稿问答派生结论（不返回问答全文）
        qa = offline.qa_extract if isinstance(offline.qa_extract, dict) else {}
        for item in (qa.get("questions") or [])[:MAX_QA_ITEMS]:
            if not isinstance(item, dict):
                continue
            for text in _clean_list(item.get("highlights"), limit=2):
                strengths.append({"text": text, "source": "复试问答"})
            question = str(item.get("question") or "").strip()
            answered = str(item.get("answer_summary") or "").strip()
            if question and not answered:
                pending.append(
                    {
                        "question": question,
                        "source": "复试未答清",
                        "hint": "面试官问了，但逐字稿里没有明确回答",
                    }
                )

    # 4) AI 面试里标记「需追问」的题目 → 终试待确认问题
    for ev in evaluations:
        if not ev.follow_up_needed:
            continue
        question = (ev.follow_up_question or "").strip()
        if not question:
            continue
        pending.append(
            {
                "question": question,
                "source": "AI 面试追问点",
                "hint": (ev.comment or "").strip()[:80],
            }
        )

    # 5) 维度短板 / 亮点 → 风险 / 待确认 / 优点
    dim_strengths, dim_risks, dim_pending = derive_dim_insights(dim_rows)
    strengths.extend(dim_strengths)
    risks.extend(dim_risks)
    pending.extend(dim_pending)

    # 6) 汇总层面的整体结论
    if summary.final_verdict in {"待定", "不推荐"}:
        risks.append(
            {
                "text": f"综合结论为「{summary.final_verdict}」（终分 {summary.final_score}）",
                "source": "综合汇总",
            }
        )

    # 保持「初筛 → AI 面试 → 复试」的维度出现顺序，老板按流程读更顺
    matrix_rows = [
        {
            "name": row["name"],
            "cells": row["cells"],
            "lowest": min((v for v in row["cells"].values() if v is not None), default=None),
        }
        for row in dim_rows.values()
    ][:MAX_DIM_ROWS]

    return {
        "summary_id": str(summary.id),
        "candidate_id": str(summary.candidate_id),
        "candidate_name": summary.candidate_name,
        "job_id": str(summary.job_id),
        "job_title": summary.job.title if summary.job else "",
        "source": (
            "walk_in"
            if (summary.detail or {}).get("is_walk_in")
            else (summary.detail or {}).get("offline_source") or "online"
        ),
        "final_score": summary.final_score,
        "final_verdict": summary.final_verdict,
        "final_interview_status": summary.final_interview_status,
        "final_interview_label": final_label(summary.final_interview_status),
        "dimension_matrix": {"columns": columns, "rows": matrix_rows},
        "stages": stages,
        "strengths": _dedupe_items(strengths),
        "risks": _dedupe_items(risks),
        "pending_questions": _dedupe_items(pending),
        "generated_at": _utcnow().isoformat(),
    }
