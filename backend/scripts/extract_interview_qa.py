"""从面试逐字稿抽取结构化问答并落库（供 BI 数据看板点击查看）。

抽取结果写入 ``offline_interviews.qa_extract / qa_extracted_at``；
业务系统序列化不返回该字段，故**系统侧不做展示**，只在 BI 看板消费。

用法（在 backend/ 目录执行）::

    .venv/bin/python scripts/extract_interview_qa.py --code 270615
    .venv/bin/python scripts/extract_interview_qa.py --id <offline_interview_id>
    .venv/bin/python scripts/extract_interview_qa.py --all          # 所有已转写、未抽取的记录
    .venv/bin/python scripts/extract_interview_qa.py --all --force  # 忽略已有结果，全部重抽
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import OfflineInterview  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.qa_extraction import extract_interview_qa  # noqa: E402


def _resolve_targets(db, args) -> list[OfflineInterview]:
    """按 code / id / all 选出待抽取的记录。"""
    if args.all:
        query = db.query(OfflineInterview).filter(OfflineInterview.transcript_text.isnot(None))
        if not args.force:
            query = query.filter(
                (OfflineInterview.qa_extracted_at.is_(None))
                | (OfflineInterview.qa_extract == {})
            )
        return query.order_by(OfflineInterview.created_at.desc()).all()

    if args.id:
        row = db.get(OfflineInterview, uuid.UUID(args.id))
        return [row] if row else []

    if args.code:
        row = (
            db.query(OfflineInterview)
            .filter(OfflineInterview.walk_in_code == args.code)
            .first()
        )
        return [row] if row else []

    return []


def _run_one(db, row: OfflineInterview) -> dict:
    """对单条记录跑抽取并落库，返回抽取结果。"""
    transcript = row.transcript_text or ""
    if not transcript.strip():
        print(f"  跳过 {row.id}：无逐字稿")
        return {}

    job_title = row.job.title if row.job else ""
    job_desc = row.job.jd_text if row.job else ""
    qa = extract_interview_qa(transcript, job_title=job_title, job_desc=job_desc)

    row.qa_extract = qa
    row.qa_extracted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)

    n = len(qa.get("questions") or [])
    print(f"  已抽取 {row.id}（{job_title or '—'}）：{n} 组问答")
    for i, item in enumerate(qa.get("questions") or [], 1):
        print(f"    {i}. 问：{item.get('question')}")
        print(f"       答：{item.get('answer_summary')}")
        if item.get("highlights"):
            print(f"       亮点：{'；'.join(item['highlights'])}")
    if qa.get("overall"):
        print(f"    总述：{qa['overall']}")
    return qa


def main() -> int:
    parser = argparse.ArgumentParser(description="面试逐字稿 → 结构化问答（落库供 BI 看板）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--code", help="到店直招 6 位短码")
    group.add_argument("--id", help="offline_interviews 记录 id")
    group.add_argument("--all", action="store_true", help="处理所有已转写的记录")
    parser.add_argument("--force", action="store_true", help="忽略已有结果，强制重抽（配合 --all）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = _resolve_targets(db, args)
        rows = [r for r in rows if r is not None]
        if not rows:
            print("未找到符合条件的记录。")
            return 1

        print(f"共 {len(rows)} 条记录待抽取：")
        for row in rows:
            qa = _run_one(db, row)
            if args.force and qa:
                pass
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
