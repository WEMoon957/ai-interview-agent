"""飞书 HR 通知通道自检脚本（不依赖业务流程，用于联调与排障）。

三类消息都能单独自检：
  ① interview —— AI 面试达标通知（Markdown 文本）
  ② screening —— 简历初筛推荐名单（交互卡片）
  ③ summary   —— 招聘汇总复核提醒（交互卡片，含 offer / 入职提示）

用途：验证外部通道是否打通，尤其是 HR 收件人 open_id、lark-cli 登录态、
机器人可用范围（230013）、卡片消息是否被客户端正常渲染等易踩的坑。

用法（在 backend/ 目录下执行）：
    # 1) 干跑：只打印将发送的内容与解析到的配置，不真正发送（默认 interview）
    python scripts/send_feishu_test.py --dry-run

    # 2) 预览 / 真发「简历初筛推荐」卡片
    python scripts/send_feishu_test.py --kind screening --dry-run
    python scripts/send_feishu_test.py --kind screening

    # 3) 预览 / 真发「汇总复核」卡片
    python scripts/send_feishu_test.py --kind summary --dry-run

    # 4) 临时指定收件人（覆盖 .env，便于验证）
    python scripts/send_feishu_test.py --kind screening --open-id ou_xxx

依赖 .env 中的 FEISHU_* 配置（见项目根 .env.example）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services.feishu_notify import (  # noqa: E402
    build_notification_text,
    build_screening_card,
    build_summary_task,
    build_screening_task,
    build_summary_card,
    card_to_markdown,
    create_tasks,
    default_card_sender,
    default_sender,
    default_task_creator,
    resolve_cli_path,
    resolve_recipients,
    send_card,
    send_notification,
)

SAMPLE_REPORT = {
    "total_score": 78.5,
    "verdict": "推荐",
    "summary": "综合评分 78.5 分（满分 100），录用建议：推荐。整体匹配度较高，可按流程推进后续环节。",
    "dim_scores": [
        {"key": "service_awareness", "name": "服务意识", "weight": 0.4, "score": 8.5, "weighted_score": 3.4},
        {"key": "stability", "name": "稳定性", "weight": 0.3, "score": 7.5, "weighted_score": 2.25},
    ],
}

SAMPLE_SCREENING_ITEMS = [
    {"candidate_name": "（自检候选人A）", "total_score": 91.0, "recommendation": "强烈推荐", "highlights": ["5 年后厨经验", "连续 3 年无食安问题"], "interview_path": "/i/self-test-token-a"},
    {"candidate_name": "（自检候选人B）", "total_score": 78.0, "recommendation": "推荐", "highlights": ["刀工扎实"], "interview_path": "/i/self-test-token-b"},
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


def _build_payload(kind: str, settings) -> tuple[str, object, bool]:
    """构造待发送内容。返回 (预览文本, 发送载荷, 是否为卡片)。"""
    if kind == "screening":
        card = build_screening_card(
            job_title="（自检岗位）后厨帮厨",
            folder_name="候选人简历",
            total=5,
            succeeded=5,
            recommended=SAMPLE_SCREENING_ITEMS,
            min_score=settings.feishu_screening_min_score,
            max_items=settings.feishu_notify_max_items,
            base_url=settings.public_base_url_normalized,
        )
        return card_to_markdown(card), card, True

    if kind == "summary":
        card = build_summary_card(
            job_title="（自检岗位）后厨帮厨",
            offer_items=[SAMPLE_SUMMARY_ITEMS[0]],
            review_items=[SAMPLE_SUMMARY_ITEMS[1]],
            snapshot_at="2026-01-01 10:00",
            max_items=settings.feishu_notify_max_items,
        )
        return card_to_markdown(card), card, True

    text = build_notification_text(
        "（自检候选人）", "self-test-session", SAMPLE_REPORT, settings.feishu_notify_threshold
    )
    return text, text, False


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书 HR 通知通道自检")
    parser.add_argument("--open-id", default="", help="临时收件人 open_id（覆盖 .env）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    parser.add_argument(
        "--kind",
        default="interview",
        choices=["interview", "screening", "summary"],
        help="消息类型：interview=面试达标 | screening=初筛推荐卡片 | summary=汇总复核卡片",
    )
    parser.add_argument(
        "--with-task",
        action="store_true",
        help="同时创建对应的飞书待办（screening / summary 均支持）；配合 --open-id 可只给自己建",
    )
    args = parser.parse_args()

    settings = get_settings()

    print("=== 当前配置 ===")
    print(f"  启用开关        : {settings.feishu_notify_enabled}")
    print(f"  AI 面试阈值     : ≥ {settings.feishu_notify_threshold}")
    print(f"  初筛推送        : {settings.feishu_screening_notify_enabled}（门槛 ≥ {settings.feishu_screening_min_score:g}）")
    print(f"  汇总推送        : {settings.feishu_summary_notify_enabled}")
    print(f"  传输方式        : {settings.feishu_notify_transport}")
    print(f"  显式收件人      : {settings.hr_open_id_list or '（无）'}")
    print(f"  收件部门        : {settings.hr_department_id_list or '（无）'}")
    print(f"  lark-cli 路径   : {resolve_cli_path(settings.feishu_lark_cli_path) or '（未找到）'}")
    print(f"  配置是否就绪    : {settings.feishu_notify_ready}")

    resolved, resolve_err = resolve_recipients(settings)
    print(f"  解析后收件人    : {resolved or '（无）'}")
    if resolve_err:
        print(f"  解析告警        : {resolve_err}")

    recipients = [args.open_id] if args.open_id else resolved
    if not recipients:
        print("\n[×] 无收件人：请设置 FEISHU_HR_OPEN_IDS / FEISHU_HR_DEPARTMENT_IDS，或用 --open-id 指定。")
        return 2

    preview, payload, is_card = _build_payload(args.kind, settings)
    print(f"\n=== 将发送的内容（{args.kind}{' · 卡片' if is_card else ' · 文本'}）===")
    print(preview)

    task_title = task_desc = ""
    if args.kind == "screening":
        task_title, task_desc = build_screening_task(
            job_title="（自检岗位）后厨帮厨",
            recommended=SAMPLE_SCREENING_ITEMS,
            min_score=settings.feishu_screening_min_score,
            base_url=settings.public_base_url_normalized,
            max_items=settings.feishu_notify_max_items,
        )
    elif args.kind == "summary":
        task_title, task_desc = build_summary_task(
            job_title="（自检岗位）后厨帮厨",
            offer_items=[SAMPLE_SUMMARY_ITEMS[0]],
            review_items=[SAMPLE_SUMMARY_ITEMS[1]],
            snapshot_at="2026-01-01 10:00",
            max_items=settings.feishu_notify_max_items,
        )

    if task_title:
        print("\n=== 对应的飞书待办（--with-task 时创建）===")
        print(f"标题：{task_title}")
        print(f"截止：+{settings.feishu_task_due_days} 天")
        print(task_desc)

    if args.dry_run:
        print("\n[干跑] 未实际发送。")
        return 0

    sent_ok = True
    if args.open_id:
        # 临时收件人：绕过 settings 列表，直接逐个发送
        ok = False
        sender = default_card_sender if is_card else default_sender
        for open_id in recipients:
            sent, err = sender(open_id, payload, settings)
            print(f"  -> {open_id}: {'成功' if sent else '失败：' + err}")
            ok = ok or sent
        sent_ok = ok
    elif is_card:
        ok, used, err = send_card(payload, settings=settings)
        print(f"\n发送结果：{'成功' if ok else '失败'}，收件人={used}")
        if not ok:
            print(f"失败详情：{err}")
        sent_ok = ok
    else:
        ok, used, err = send_notification(payload, settings=settings)
        print(f"\n发送结果：{'成功' if ok else '失败'}，收件人={used}")
        if not ok:
            print(f"失败详情：{err}")
        sent_ok = ok

    if args.with_task and task_title:
        if args.open_id:
            t_ok, t_err = default_task_creator(args.open_id, task_title, task_desc, settings)
            print(f"待办 -> {args.open_id}: {'创建成功' if t_ok else '创建失败：' + t_err}")
        else:
            t_ok, assignees, t_err = create_tasks(task_title, task_desc, settings=settings)
            print(f"待办 -> {assignees}: {'创建成功' if t_ok else '创建失败：' + t_err}")
        sent_ok = sent_ok and t_ok

    return 0 if sent_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
