"""把历史 AI 面试会话的「提问 + 候选人原话」从 LangGraph checkpoint 回填到 interview_turns。

背景：原话此前只存在 checkpoint 的 ``messages`` 里（msgpack 序列化），业务库没有。
0018 迁移上线后，新面试会实时落库；**上线前已完成的面试**需要跑本脚本补一份，
否则报告页只有分数、没有原话。

用法（在 backend/ 目录执行）::

    .venv/bin/python scripts/backfill_interview_turns.py --all
    .venv/bin/python scripts/backfill_interview_turns.py --all --force   # 先清空已有原话再回填
    .venv/bin/python scripts/backfill_interview_turns.py --session <session_id>
    .venv/bin/python scripts/backfill_interview_turns.py --token <面试token>

幂等：``interview_turns`` 有 ``(session_id, turn_index)`` 唯一约束，重复执行不会产生重复记录。
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import InterviewSession, InterviewTurn  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.graph.builder import close_interview_graph, get_interview_graph  # noqa: E402
from app.services.interview_turns import extract_qa_pairs, record_turn  # noqa: E402


def _resolve_sessions(db, args) -> list[InterviewSession]:
    """按参数解析要回填的会话。"""
    query = db.query(InterviewSession)
    if args.session:
        query = query.filter(InterviewSession.id == uuid.UUID(args.session))
    elif args.token:
        query = query.filter(InterviewSession.token == args.token)
    elif not args.all:
        raise SystemExit("请指定 --all / --session <id> / --token <token>")
    return query.order_by(InterviewSession.created_at.asc()).all()


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 AI 面试逐轮原话")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="回填全部会话")
    group.add_argument("--session", help="只回填指定 session_id")
    group.add_argument("--token", help="只回填指定面试 token")
    parser.add_argument("--force", action="store_true", help="先清空该会话已有原话再回填")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        sessions = _resolve_sessions(db, args)
        print(f"待处理会话：{len(sessions)} 个")

        graph = get_interview_graph()
        total_turns = 0
        skipped = 0

        for sess in sessions:
            config = {"configurable": {"thread_id": sess.thread_id}}
            try:
                snapshot = graph.get_state(config)
                messages = (snapshot.values or {}).get("messages") or []
            except Exception as exc:  # noqa: BLE001 - 单个会话失败不影响其余
                print(f"  [跳过] {sess.id} 读取 checkpoint 失败：{exc}")
                skipped += 1
                continue

            pairs = extract_qa_pairs(messages)
            if not pairs:
                skipped += 1
                continue

            if args.force and not args.dry_run:
                db.query(InterviewTurn).filter(
                    InterviewTurn.session_id == sess.id
                ).delete(synchronize_session=False)
                db.commit()

            label = f"{sess.candidate.name if sess.candidate else '候选人'}({sess.token[:8]})"
            print(f"  [回填] {label} status={sess.status} 轮次={len(pairs)}")
            if args.dry_run:
                total_turns += len(pairs)
                continue

            # question_id / question_type 无法从 checkpoint 反推（messages 里没有元信息），
            # 留空并默认主观题；报告页按顺序对齐评分，不依赖 question_id。
            for idx, (question, answer) in enumerate(pairs, start=1):
                record_turn(
                    session_id=sess.id,
                    turn_index=idx,
                    question_id="",
                    question_type="subjective",
                    is_follow_up=False,
                    question_text=question,
                    answer_text=answer,
                )
            total_turns += len(pairs)

        print(f"完成：写入 {total_turns} 轮原话，跳过 {skipped} 个会话")
        return 0
    finally:
        db.close()
        close_interview_graph()


if __name__ == "__main__":
    raise SystemExit(main())
