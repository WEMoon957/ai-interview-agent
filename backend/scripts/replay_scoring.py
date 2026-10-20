"""离线回放：用已落库的真实原话，对比「旧口径」与「行为锚定（BARS）+ 证据先行」的分数分布。

用途：改评分 prompt 前，先在真实数据上看效果——分布有没有铺开、证据引用是否可校验、
分数夹取触发多少。**只读库、不写库**（不覆盖任何已有评分）。

用法（在 backend/ 目录执行）::

    .venv/bin/python scripts/replay_scoring.py --all --limit 20
    .venv/bin/python scripts/replay_scoring.py --session <session_id>
    .venv/bin/python scripts/replay_scoring.py --all --out /tmp/replay.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import uuid
from collections import Counter
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (  # noqa: E402
    AnswerEvaluation,
    InterviewSession,
    InterviewTemplate,
    InterviewTurn,
    JobPosting,
    Resume,
)
from app.db.session import SessionLocal  # noqa: E402
from app.graph.nodes.evaluate import score_answer  # noqa: E402
from app.services.interview_turns import list_turns  # noqa: E402


def _old_scores_by_turn(turns: list[InterviewTurn], evaluations: list) -> dict[int, dict]:
    """把旧评分按轮次对齐（与报告接口同一口径：question_id 优先、顺序兜底）。"""
    remaining = list(evaluations)
    result: dict[int, dict] = {}
    for turn in turns:
        idx = next(
            (
                i
                for i, ev in enumerate(remaining)
                if (getattr(ev, "question_id", "") or "") == (turn.question_id or "")
            ),
            None,
        )
        if idx is None:
            idx = 0 if remaining else None
        if idx is None:
            continue
        result[turn.turn_index] = dict(remaining.pop(idx).scores or {})
    return result


def _stats(values: list[float]) -> dict:
    """均值 / 标准差 / 6-7.5 占比 / 分布计数。"""
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 2),
        "sd": round(statistics.pstdev(values), 2),
        "mid_ratio": round(
            sum(1 for v in values if 6.0 <= v <= 7.5) / len(values) * 100, 1
        ),
        "dist": dict(sorted(Counter(values).items())),
    }


def _load_bundle(db, session: InterviewSession) -> dict | None:
    """取一场面试的回放所需材料。"""
    turns = list_turns(db, session.id)
    if not turns:
        return None
    template = db.get(InterviewTemplate, session.template_id)
    if template is None:
        return None
    job = db.get(JobPosting, template.job_id)
    resume = (
        db.query(Resume)
        .filter(Resume.candidate_id == session.candidate_id)
        .order_by(Resume.created_at.desc())
        .first()
    )
    evaluations = (
        db.query(AnswerEvaluation)
        .filter(AnswerEvaluation.session_id == session.id)
        .order_by(AnswerEvaluation.created_at.asc())
        .all()
    )
    job_desc = ""
    if job is not None:
        job_desc = f"{job.title}\n{job.jd_text or ''}\n{job.requirements or ''}".strip()
    return {
        "session": session,
        "turns": turns,
        "rubric": template.rubric or [],
        "job_desc": job_desc,
        "resume_text": (resume.parsed_text if resume else "") or "",
        "old": _old_scores_by_turn(turns, evaluations),
        "old_evals": evaluations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回放对比新旧评分口径")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="回放全部有原话的会话")
    group.add_argument("--session", help="只回放指定 session_id")
    parser.add_argument("--limit", type=int, default=0, help="最多回放多少轮（0=不限）")
    parser.add_argument("--out", help="把逐轮结果写成 JSON")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(InterviewSession)
        if args.session:
            query = query.filter(InterviewSession.id == uuid.UUID(args.session))
        sessions = query.order_by(InterviewSession.created_at.asc()).all()

        old_all: list[float] = []
        new_all: list[float] = []
        rows: list[dict] = []
        evidence_total = evidence_verified = 0
        clamped = 0
        per_session: list[dict] = []

        budget = args.limit or 10**9

        for sess in sessions:
            bundle = _load_bundle(db, sess)
            if bundle is None:
                continue
            old_sess: list[float] = []
            new_sess: list[float] = []
            for turn in bundle["turns"]:
                if budget <= 0:
                    break
                budget -= 1
                new_score = score_answer(
                    question=turn.question_text,
                    answer=turn.answer_text,
                    rubric=bundle["rubric"],
                    job_desc=bundle["job_desc"],
                    resume_text=bundle["resume_text"],
                )
                old = bundle["old"].get(turn.turn_index) or {}
                new = dict(new_score.scores)
                evidence = new_score.evidence or []
                evidence_total += len(evidence)
                evidence_verified += sum(1 for e in evidence if e.get("verified"))
                clamped += sum(
                    1 for k, v in new.items() if k in old and abs(float(old[k]) - v) > 1e-6
                )
                old_sess.extend(float(v) for v in old.values())
                new_sess.extend(float(v) for v in new.values())
                rows.append(
                    {
                        "session_id": str(sess.id),
                        "turn_index": turn.turn_index,
                        "question": turn.question_text[:60],
                        "old_scores": old,
                        "new_scores": new,
                        "evidence": evidence,
                        "comment": new_score.comment,
                    }
                )
                print(
                    f"  [{sess.candidate.name if sess.candidate else '?'} #{turn.turn_index}] "
                    f"旧 {old} -> 新 {new}（证据 {len(evidence)} 条）"
                )
            old_all.extend(old_sess)
            new_all.extend(new_sess)
            if old_sess or new_sess:
                per_session.append(
                    {
                        "session_id": str(sess.id),
                        "candidate": sess.candidate.name if sess.candidate else "",
                        "old_mean": round(statistics.mean(old_sess), 2) if old_sess else None,
                        "new_mean": round(statistics.mean(new_sess), 2) if new_sess else None,
                    }
                )
            if budget <= 0:
                break

        old_stats = _stats(old_all)
        new_stats = _stats(new_all)
        summary = {
            "old": old_stats,
            "new": new_stats,
            "evidence": {
                "total": evidence_total,
                "verified": evidence_verified,
                "verified_ratio": round(evidence_verified / evidence_total * 100, 1)
                if evidence_total
                else 0.0,
            },
            "score_changed_dims": clamped,
            "sessions": per_session,
        }

        print("\n=== 汇总 ===")
        for label, st in (("旧", old_stats), ("新", new_stats)):
            if st:
                print(
                    f"{label}：n={st['n']} 均值={st['mean']} 标准差={st['sd']} "
                    f"6-7.5 占比={st['mid_ratio']}%"
                )
                print(f"    分布 {st['dist']}")
        print(
            f"证据：共 {evidence_total} 条，通过原文校验 {evidence_verified} 条"
            f"（{summary['evidence']['verified_ratio']}%）"
        )
        print(f"相对旧分发生变化的维度打分点：{clamped} 个")
        print("\n=== 每场均值 ===")
        for item in per_session:
            print(f"  {item['candidate']}: 旧 {item['old_mean']} -> 新 {item['new_mean']}")

        if args.out:
            Path(args.out).write_text(
                json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n逐轮明细已写入 {args.out}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
