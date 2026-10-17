#!/usr/bin/env python
"""把所有飞书推送卡片各发一份到指定账号（验收/联调用）。

覆盖 11 类消息：
  1) AI 面试达标（文本）
  2) 简历初筛推荐（卡片）
  3) 招聘汇总复核（卡片）
  4) AI 面试已发起（卡片）
  5) Offer 待审批（卡片，带「同意 / 驳回」按钮）
  6) Offer 已批准
  7) Offer 被驳回
  8) Offer 已接受
  9) Offer 婉拒
 10) Offer 已过期
 11) Offer 未报到

设计：**只发给指定人**——清空 HR 部门、覆盖所有收件人列表、关闭飞书待办，
因此不会打扰真实 HR/店长。默认收件人取 FEISHU_OBSERVER_OPEN_IDS 的第一个，
未配置则取 FEISHU_HR_OPEN_IDS 的第一个。

用法（在 backend/ 目录下执行）：
    .venv/bin/python scripts/send_feishu_all_cards.py --dry-run
    .venv/bin/python scripts/send_feishu_all_cards.py
    .venv/bin/python scripts/send_feishu_all_cards.py --open-id ou_xxx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.models import Offer  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services import offer_notify as on  # noqa: E402
from app.services.feishu_notify import (  # noqa: E402
    notify_interview_created,
    notify_report_qualified,
    notify_screening_recommendations,
    notify_summary_review,
)

SAMPLE_REPORT = SimpleNamespace(
    total_score=78.5,
    verdict="推荐",
    summary="综合评分 78.5 分（满分 100），整体匹配度较高，可按流程推进后续环节。",
    dim_scores=[
        {"key": "service_awareness", "name": "服务意识", "weight": 0.4, "score": 8.5},
        {"key": "stability", "name": "稳定性", "weight": 0.3, "score": 7.5},
    ],
    notified_at=None,
)

SAMPLE_SCREENING_ITEMS = [
    {
        "candidate_name": "（自检候选人A）",
        "total_score": 91.0,
        "recommendation": "强烈推荐",
        "highlights": ["5 年后厨经验", "连续 3 年无食安问题"],
        "interview_path": "/i/self-test-token-a",
    },
    {
        "candidate_name": "（自检候选人B）",
        "total_score": 78.0,
        "recommendation": "推荐",
        "highlights": ["刀工扎实"],
        "interview_path": "/i/self-test-token-b",
    },
]

SAMPLE_SUMMARY_ITEMS = [
    {
        "candidate_name": "（自检候选人A）",
        "final_score": 86.4,
        "final_verdict": "强烈推荐",
        "screening_score": 91.0,
        "ai_interview_score": 82.0,
        "offline_score": 88.0,
    },
    {
        "candidate_name": "（自检候选人B）",
        "final_score": 58.2,
        "final_verdict": "待定",
        "screening_score": 78.0,
        "ai_interview_score": 52.0,
        "offline_score": 55.0,
    },
]

SAMPLE_CREATED_ITEMS = [
    {"candidate_name": "（自检候选人A）", "interview_path": "/i/self-test-token-a"},
    {"candidate_name": "（自检候选人B）", "interview_path": "/i/self-test-token-b"},
]

SAMPLE_SCORES = {
    "screening_score": 91.0,
    "ai_interview_score": 82.0,
    "offline_score": 88.0,
    "final_score": 86.4,
    "final_verdict": "强烈推荐",
}
SAMPLE_CANDIDATE = {
    "name": "（自检候选人A）",
    "phone": "13800000000",
    "email": "self-test@example.com",
    "source_channel": "线上投递",
}


def _only_me_settings(open_id: str):
    """生成「只发给我」的配置：所有收件人列表都指向 open_id，关闭部门与待办。"""
    base = get_settings()
    return base.model_copy(
        update={
            "feishu_notify_enabled": True,
            "feishu_hr_open_ids": open_id,
            "feishu_hr_department_ids": "",
            "feishu_observer_open_ids": open_id,
            "offer_approver_open_ids": open_id,
            "feishu_store_manager_open_ids": open_id,
            "feishu_task_enabled": False,
            "feishu_screening_notify_enabled": True,
            "feishu_summary_notify_enabled": True,
            "feishu_interview_created_notify_enabled": True,
            "feishu_offer_notify_enabled": True,
        }
    )


def _latest_offer(db) -> Offer | None:
    """取最近一条 Offer 用于渲染 Offer 卡片（没有则返回 None）。"""
    return db.query(Offer).order_by(Offer.created_at.desc()).first()


def main() -> int:
    parser = argparse.ArgumentParser(description="把全部飞书推送卡片各发一份到指定账号")
    parser.add_argument("--open-id", default="", help="收件人 open_id（默认取全局抄送人/HR 第一个）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将发送的项目，不实际发送")
    args = parser.parse_args()

    base = get_settings()
    open_id = args.open_id or (
        (base.observer_open_id_list or base.hr_open_id_list or [""])[0]
    )
    if not open_id:
        print("[×] 未指定收件人：请传 --open-id，或配置 FEISHU_OBSERVER_OPEN_IDS。")
        return 2

    s = _only_me_settings(open_id)
    print("=" * 62)
    print("飞书全卡片自检")
    print("=" * 62)
    print("收件人  ：", open_id, "（只发给你，不会打扰 HR/店长）")
    print("传输方式：", s.feishu_notify_transport)
    print("配置就绪：", s.feishu_notify_ready)
    print()

    if args.dry_run:
        print("将发送 11 类消息：面试达标 / 初筛推荐 / 汇总复核 / 面试已发起 /")
        print("Offer 待审批 / 已批准 / 被驳回 / 已接受 / 婉拒 / 已过期 / 未报到")
        print("\n[干跑] 未实际发送。")
        return 0

    results: list[tuple[str, dict]] = []

    def run(label: str, fn):
        try:
            results.append((label, fn()))
        except Exception as exc:  # noqa: BLE001 - 单类失败不阻断其余
            results.append((label, {"status": "failed", "reason": repr(exc)}))

    run(
        "1. AI 面试达标",
        lambda: notify_report_qualified(
            SAMPLE_REPORT, session_id="self-test-session", candidate_name="（自检候选人）", settings=s
        ),
    )
    run(
        "2. 简历初筛推荐",
        lambda: notify_screening_recommendations(
            job_title="（自检岗位）后厨帮厨",
            folder_name="候选人简历",
            items=SAMPLE_SCREENING_ITEMS,
            total=2,
            succeeded=2,
            settings=s,
        ),
    )
    run(
        "3. 招聘汇总复核",
        lambda: notify_summary_review(
            job_title="（自检岗位）后厨帮厨",
            items=SAMPLE_SUMMARY_ITEMS,
            snapshot_at="2026-01-01 10:00",
            settings=s,
        ),
    )
    run(
        "4. AI 面试已发起",
        lambda: notify_interview_created(
            job_title="（自检岗位）后厨帮厨",
            items=SAMPLE_CREATED_ITEMS,
            source="批量发起",
            settings=s,
        ),
    )

    # Offer 7 类事件：用最近一条真实 Offer 渲染（保证字段完整）
    db = SessionLocal()
    try:
        offer = _latest_offer(db)
    finally:
        db.close()

    if offer is None:
        print("[!] 库中没有 Offer，跳过 Offer 的 7 类卡片。")
    else:
        link = f"{s.public_base_url_normalized}/o/{offer.token}" if offer.token else ""
        offer_events = [
            ("5. Offer 待审批", on.EVENT_PENDING_APPROVAL, {"scores": SAMPLE_SCORES, "candidate": SAMPLE_CANDIDATE}),
            ("6. Offer 已批准", on.EVENT_SENT, {"link": link, "copy_text": "你好，这是你的 Offer 链接：" + link}),
            ("7. Offer 被驳回", on.EVENT_REJECTED, {}),
            ("8. Offer 已接受", on.EVENT_ACCEPTED, {}),
            ("9. Offer 婉拒", on.EVENT_DECLINED, {}),
            ("10. Offer 已过期", on.EVENT_EXPIRED, {}),
            ("11. Offer 未报到", on.EVENT_NO_SHOW, {}),
        ]
        for label, event, extra in offer_events:
            run(
                label,
                lambda e=event, kw=extra: on.notify_offer_event(e, offer, settings=s, **kw),
            )

    print()
    print("发送结果：")
    ok_count = 0
    for label, res in results:
        status = res.get("status", "?")
        if status == "sent":
            ok_count += 1
        print(f"  {label:<20} {status:<8} {res.get('reason', '')}")
    print()
    print(f"完成：{ok_count}/{len(results)} 类发送成功。请到飞书查看。")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
