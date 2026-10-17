#!/usr/bin/env python
"""候选人邮件通道自检（配好 SMTP 后运行）。

作用：不经过真实业务，直接渲染并发送两封候选人邮件，快速验证
「SMTP 配置是否正确、邮件长什么样、能不能送达」。

用法：
    cd backend
    .venv/bin/python scripts/send_mail_test.py --to you@example.com
    .venv/bin/python scripts/send_mail_test.py --to you@example.com --dry-run  # 只渲染不发送

前置：在 .env 中配置
    CANDIDATE_MAIL_ENABLED=true
    SMTP_HOST=smtp.exmail.qq.com
    SMTP_PORT=465
    SMTP_USERNAME=hr@your-domain.com
    SMTP_PASSWORD=<客户端专用密码>
    MAIL_FROM_NAME=昆明笨二娘餐饮管理有限公司 · 招聘组
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.services import mailer  # noqa: E402

SAMPLE_LINK = "https://example.com/o/DEMO_TOKEN_FOR_PREVIEW"
SAMPLE_DATE = date(2026, 10, 20)


def main() -> int:
    parser = argparse.ArgumentParser(description="候选人邮件通道自检")
    parser.add_argument("--to", required=True, help="收件邮箱（建议先发给自己）")
    parser.add_argument("--dry-run", action="store_true", help="只渲染模板，不实际发送")
    args = parser.parse_args()

    settings = get_settings()
    print("=" * 62)
    print("候选人邮件通道自检")
    print("=" * 62)
    print("通道状态：", mailer.mail_configured_summary(settings))
    print("收件人  ：", args.to)
    print()

    subject1, html1 = mailer.build_offer_invite_mail(
        candidate_name="张三",
        job_title="前厅服务员",
        store_name="昆明南屏店",
        report_date=SAMPLE_DATE,
        link=SAMPLE_LINK,
        deadline="2026年10月23日 20:00",
        report_time="上午 9:00",
        work_location="昆明市西山区万达北塔2507",
        contact_name="方玺",
        contact_phone="17702348593",
        probation_phrase="试用期为一个月",
        probation_suffix="，可视情况提前转正",
        probation_salary="4000 元/月",
        regular_salary="4500 元/月",
        materials=mailer.REPORT_MATERIALS,
        legal_note="本通知为录用确认，具体权利义务以正式劳动合同为准。",
        company_name="昆明笨二娘餐饮管理有限公司",
        letter_date="2026年10月17日",
        settings=settings,
    )
    subject2, html2 = mailer.build_offer_accepted_mail(
        candidate_name="张三",
        job_title="前厅服务员",
        store_name="昆明南屏店",
        report_date=SAMPLE_DATE,
        report_time="上午 9:00",
        work_location="昆明市西山区万达北塔2507",
        contact_name="方玺",
        contact_phone="17702348593",
        settings=settings,
    )

    print("【第一封】Offer 通知（含接受按钮）")
    print("  主题：", subject1)
    print("  正文长度：", len(html1), "字符")
    print()
    print("【第二封】入职指引（接受后发送）")
    print("  主题：", subject2)
    print("  正文长度：", len(html2), "字符")
    print()

    if args.dry_run:
        print("--dry-run 已指定：仅渲染，未发送。")
        print("（模板内容正常。去掉 --dry-run 即可真实发送）")
        return 0

    if not mailer.is_mail_ready(settings):
        print("邮件通道未就绪：请检查 CANDIDATE_MAIL_ENABLED 与 SMTP_* 配置。")
        return 1

    for label, subject, html in (("第一封", subject1, html1), ("第二封", subject2, html2)):
        ok, err = mailer.send_mail(args.to, subject, html, settings=settings)
        print(f"{label} -> {'成功' if ok else '失败'} {err}")
        if not ok:
            return 1

    print()
    print("两封邮件均已发送，请查收（含垃圾箱）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
