"""飞书 HR 通知通道自检脚本（不依赖面试流程，用于联调与排障）。

用途：验证「面试达标 -> 推送 HR 飞书」这条外部通道是否打通，尤其是
HR 收件人 open_id、lark-cli 登录态、机器人可用范围（230013）等易踩的坑。

用法（在 backend/ 目录下执行）：
    # 1) 干跑：只打印将发送的文案与解析到的配置，不真正发送
    python scripts/send_feishu_test.py --dry-run

    # 2) 正式发送一条样例通知给配置里的 HR
    python scripts/send_feishu_test.py

    # 3) 临时指定收件人（覆盖 .env，便于验证）
    python scripts/send_feishu_test.py --open-id ou_xxx

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
    default_sender,
    resolve_cli_path,
    resolve_recipients,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书 HR 通知通道自检")
    parser.add_argument("--open-id", default="", help="临时收件人 open_id（覆盖 .env）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    args = parser.parse_args()

    settings = get_settings()

    print("=== 当前配置 ===")
    print(f"  启用开关        : {settings.feishu_notify_enabled}")
    print(f"  触发阈值        : ≥ {settings.feishu_notify_threshold}")
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

    text = build_notification_text("（自检候选人）", "self-test-session", SAMPLE_REPORT, settings.feishu_notify_threshold)
    print("\n=== 将发送的文案 ===")
    print(text)

    if args.dry_run:
        print("\n[干跑] 未实际发送。")
        return 0

    if args.open_id:
        # 临时收件人：绕过 settings 列表，直接逐个发送
        ok = False
        for open_id in recipients:
            sent, err = default_sender(open_id, text, settings)
            print(f"  -> {open_id}: {'成功' if sent else '失败：' + err}")
            ok = ok or sent
        return 0 if ok else 1

    ok, used, err = send_notification(text, settings=settings)
    print(f"\n发送结果：{'成功' if ok else '失败'}，收件人={used}")
    if not ok:
        print(f"失败详情：{err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
