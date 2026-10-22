#!/usr/bin/env python
"""个人访问令牌（PAT）管理脚本。

用途：给程序化客户端（WorkBuddy 桌面 Agent、CLI 工具）签发长期凭据，
让它们不必持有 HR 的账号密码。

用法::

    # 给 hr01 签发一个可写令牌（默认 90 天）
    python scripts/create_pat.py create --username hr01 --name "MacBook-招聘助手" --scopes read,write

    # 只读令牌（Agent 只查数据，不能改）
    python scripts/create_pat.py create --username boss --name "老板看板" --scopes read

    # 长期有效（0 = 不过期，谨慎使用）
    python scripts/create_pat.py create --username hr01 --name "常驻" --days 0

    # 查看某人名下的令牌（不含原文）
    python scripts/create_pat.py list --username hr01

    # 仓库操作（令牌泄露、员工离职、换电脑）
    python scripts/create_pat.py revoke --token-id 3f2a...

令牌原文**只在 create 时打印一次**，系统只保存 sha256 摘要，丢失只能重新签发。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.admin_user import AdminUser  # noqa: E402
from app.db.models.personal_access_token import PersonalAccessToken  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.exceptions import BizError  # noqa: E402
from app.services import pat as pat_service  # noqa: E402


def _fmt(value: datetime | None) -> str:
    """UTC 时间格式化，空值显示 '-'。"""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _find_user(db: Session, username: str) -> AdminUser:
    normalized = (username or "").strip().lower()
    user = db.query(AdminUser).filter(AdminUser.username == normalized).first()
    if user is None:
        raise ValueError(f"账号不存在：{normalized}")
    return user


def cmd_create(db: Session, args: argparse.Namespace) -> int:
    """签发令牌并打印原文（仅此一次）。"""
    user = _find_user(db, args.username)
    if not user.is_active:
        raise ValueError(f"账号已停用：{user.username}，停用账号的令牌会立即失效")

    scopes = [s.strip() for s in (args.scopes or "read").split(",") if s.strip()]
    row, raw = pat_service.create_token(
        db,
        user,
        name=args.name,
        scopes=scopes,
        expires_days=args.days,
        created_by="cli",
    )

    print()
    print(f"已为 {user.username}（{user.role}）签发访问令牌")
    print(f"  名称：{row.name}")
    print(f"  范围：{row.scopes}")
    print(f"  有效：{'长期' if args.days == 0 else f'{args.days} 天'}（至 {_fmt(row.expires_at)}）")
    print()
    print("  令牌原文（只显示这一次，请立即复制）：")
    print()
    print(f"    {raw}")
    print()
    print("  交给本人的方式：粘贴到 ~/.recruit-mcp/token 文件，或设置环境变量 IA_TOKEN。")
    print("  不要通过聊天工具明文发送，不要提交到代码仓库。")
    print()
    return 0


def cmd_list(db: Session, args: argparse.Namespace) -> int:
    """列出某用户名下的令牌。"""
    user = _find_user(db, args.username)
    rows = pat_service.list_tokens(db, user.id)
    if not rows:
        print(f"{user.username} 名下没有令牌")
        return 0

    print(f"{user.username} 名下的令牌（共 {len(rows)} 个）")
    print("-" * 92)
    print(f"{'id':<38}{'名称':<18}{'范围':<12}{'状态':<10}{'最后使用':<18}")
    print("-" * 92)
    for row in rows:
        if row.revoked_at is not None:
            state = "已撤销"
        elif pat_service.is_expired(row):
            state = "已过期"
        else:
            state = "有效"
        print(f"{str(row.id):<38}{(row.name or '-'):<18}{row.scopes:<12}{state:<10}{_fmt(row.last_used_at):<18}")
    print("-" * 92)
    print("提示：撤销用 create_pat.py revoke --token-id <id>")
    return 0


def cmd_revoke(db: Session, args: argparse.Namespace) -> int:
    """按 id 撤销令牌（幂等）。"""
    import uuid as _uuid

    try:
        tid = _uuid.UUID(args.token_id)
    except (ValueError, TypeError):
        raise ValueError("token-id 不是合法的 UUID") from None

    row = db.get(PersonalAccessToken, tid)
    if row is None:
        raise ValueError(f"令牌不存在：{args.token_id}")

    already = row.revoked_at is not None
    pat_service.revoke_token(db, row)
    print(f"令牌 {tid}（{row.name}）{'此前已撤销' if already else '已撤销'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="个人访问令牌（PAT）管理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="签发令牌")
    p_create.add_argument("--username", required=True, help="令牌归属账号")
    p_create.add_argument("--name", default="", help="设备/用途标识，如「MacBook-招聘助手」")
    p_create.add_argument(
        "--scopes", default="read", help="权限范围：read / read,write（默认 read）"
    )
    p_create.add_argument(
        "--days", type=int, default=pat_service.DEFAULT_EXPIRES_DAYS,
        help=f"有效天数，0 表示长期（默认 {pat_service.DEFAULT_EXPIRES_DAYS}）",
    )
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="列出令牌")
    p_list.add_argument("--username", required=True, help="账号")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", help="撤销令牌")
    p_revoke.add_argument("--token-id", required=True, help="令牌 id（list 可见）")
    p_revoke.set_defaults(func=cmd_revoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = SessionLocal()
    try:
        return args.func(db, args)
    except BizError as exc:
        print(f"失败：{exc.message}")
        return 1
    except ValueError as exc:
        print(f"失败：{exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
