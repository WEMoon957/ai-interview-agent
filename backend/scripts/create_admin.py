"""后台管理员账号管理 CLI（安全改造配套）。

系统**不提供自助注册**：这是内部招聘系统，开放注册等于把候选人隐私
（简历、面试录音、评分）向公众敞开。账号只能从两个入口创建：

1. 本脚本 —— 服务器运维用（首次初始化、密码找回、解锁）；
2. 后台 ``POST /auth/users`` —— 仅 ``admin`` 角色可用。

用法（在 backend/ 目录下执行）::

    # 1) 创建管理员：密码交互输入，不回显、不进 shell history
    python scripts/create_admin.py create --username hr01 --role hr --display-name 张三

    # 2) 从管道读密码（自动化场景；切勿写进脚本文件或 CI 明文日志）
    printf 'Kf7#mQ2vLp9x' | python scripts/create_admin.py create --username hr01 --password-stdin

    # 3) 重置密码（会撤销该账号全部登录会话）
    python scripts/create_admin.py reset-password --username hr01

    # 4) 日常维护
    python scripts/create_admin.py list
    python scripts/create_admin.py disable --username hr01
    python scripts/create_admin.py enable  --username hr01
    python scripts/create_admin.py unlock  --username hr01

密码强度要求与后台改密一致：≥10 位，含大写/小写/数字/符号中至少三类，
且不得命中常见弱口令（见 ``app.security.passwords``）。
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行（把 backend/ 加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.admin_user import (  # noqa: E402
    ADMIN_ROLE,
    HR_ROLE,
    STORE_MANAGER_ROLE,
    VALID_ROLES,
    AdminUser,
)
from app.db.session import SessionLocal  # noqa: E402
from app.exceptions import BizError  # noqa: E402
from app.services import auth as auth_service  # noqa: E402


def _read_password(args: argparse.Namespace) -> str:
    """读取密码：优先管道（自动化），否则交互式两次确认。

    任何情况下都不会把密码回显到终端或写入日志。
    """
    if args.password_stdin:
        data = sys.stdin.read().strip()
        if not data:
            raise ValueError("从标准输入读到的密码为空")
        return data

    first = getpass.getpass("请输入密码（不回显）：")
    second = getpass.getpass("请再次输入密码：")
    if first != second:
        raise ValueError("两次输入的密码不一致")
    return first


def _find_user(db: Session, username: str) -> AdminUser:
    """按用户名查账号（用户名统一小写口径）。"""
    normalized = auth_service.normalize_username(username)
    user = db.query(AdminUser).filter(AdminUser.username == normalized).first()
    if user is None:
        raise ValueError(f"账号不存在：{normalized}")
    return user


def _fmt_time(value: datetime | None) -> str:
    """时间显示：本地无关，统一 UTC ISO，空值显示为 '-'。"""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def cmd_create(db: Session, args: argparse.Namespace) -> int:
    """创建后台账号。"""
    password = _read_password(args)
    user = auth_service.create_user(
        db,
        username=args.username,
        password=password,
        role=args.role,
        email=args.email,
        display_name=args.display_name or "",
        created_by="cli",
    )
    print(f"已创建账号：{user.username}（角色 {user.role}，id {user.id}）")
    print("请立即把初始密码交付本人，并提醒首次登录后修改。")
    return 0


def cmd_reset_password(db: Session, args: argparse.Namespace) -> int:
    """重置指定账号的密码，并撤销其全部登录会话。"""
    user = _find_user(db, args.username)
    password = _read_password(args)
    revoked = auth_service.reset_password(db, user, new_password=password, actor=None)
    print(f"已重置 {user.username} 的密码。")
    print(f"已撤销该账号全部登录会话 {revoked} 个（含可能被他人持有的会话）。")
    return 0


def cmd_list(db: Session, args: argparse.Namespace) -> int:
    """列出全部后台账号及其状态。"""
    users = db.query(AdminUser).order_by(AdminUser.created_at.asc()).all()
    if not users:
        print("当前没有任何后台账号，请先执行：")
        print("  python scripts/create_admin.py create --username <名字> --role admin")
        return 0

    print(f"{'用户名':<20}{'角色':<8}{'状态':<6}{'锁定至':<20}{'最后登录':<20}{'创建人'}")
    print("-" * 90)
    for user in users:
        locked = _fmt_time(user.locked_until)
        print(
            f"{user.username:<20}{user.role:<8}"
            f"{'启用' if user.is_active else '停用':<6}"
            f"{locked:<20}{_fmt_time(user.last_login_at):<20}{user.created_by or '-'}"
        )
    return 0


def cmd_set_active(db: Session, args: argparse.Namespace) -> int:
    """启用 / 停用账号；停用时立即撤销其全部会话。"""
    user = _find_user(db, args.username)
    active = args.command == "enable"
    user.is_active = active
    if active:
        # 重新启用时清掉历史失败计数，避免刚启用就落在锁定窗口里
        user.failed_login_count = 0
        user.locked_until = None
    db.commit()

    if active:
        print(f"已启用账号：{user.username}")
    else:
        revoked = auth_service.revoke_all_sessions(db, user.id)
        print(f"已停用账号：{user.username}，并撤销登录会话 {revoked} 个")
    return 0


def cmd_unlock(db: Session, args: argparse.Namespace) -> int:
    """人工解除登录失败锁定。"""
    user = _find_user(db, args.username)
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    print(f"已解除账号锁定：{user.username}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(
        description="后台管理员账号管理（创建 / 重置密码 / 启停 / 解锁）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建后台账号")
    create.add_argument("--username", required=True, help="用户名（字母/数字/._-，≥3 位）")
    create.add_argument(
        "--role",
        default=HR_ROLE,
        choices=list(VALID_ROLES),
        help="角色：admin（管理员）/ hr（HR，全业务）/ store_manager（店长，仅线下复试）",
    )
    create.add_argument("--email", default=None, help="邮箱（选填，全局唯一）")
    create.add_argument("--display-name", default="", help="展示名（选填）")
    create.add_argument(
        "--password-stdin", action="store_true", help="从标准输入读密码（自动化用）"
    )

    for name, help_text in (
        ("reset-password", "重置指定账号密码并撤销其全部会话"),
        ("disable", "停用账号并撤销其全部会话"),
        ("enable", "启用账号"),
        ("unlock", "解除登录失败锁定"),
        ("list", "列出全部后台账号"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        if name != "list":
            cmd.add_argument("--username", required=True, help="用户名")
        if name == "reset-password":
            cmd.add_argument(
                "--password-stdin", action="store_true", help="从标准输入读密码（自动化用）"
            )

    return parser


def main() -> int:
    """入口：解析参数 → 执行 → 统一错误处理。"""
    args = build_parser().parse_args()

    db = SessionLocal()
    try:
        handlers = {
            "create": cmd_create,
            "reset-password": cmd_reset_password,
            "list": cmd_list,
            "disable": cmd_set_active,
            "enable": cmd_set_active,
            "unlock": cmd_unlock,
        }
        return handlers[args.command](db, args)
    except BizError as exc:
        print(f"失败：{exc.message}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
