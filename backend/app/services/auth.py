"""后台认证服务：登录校验、服务端会话生命周期、防爆破与审计联动。

会话方案（见 docs/安全改造方案-账号密码登录.md）
------------------------------------------------
浏览器只持有随机 token（放在 HttpOnly Cookie 中），库里只存 ``sha256(token)``。
因此"登出""改密""管理员强制下线"都能**立即生效**——这是本项目选择
服务端会话而非无状态 JWT 的核心原因。

失败提示策略
------------
1. 凭据错误（含"账号不存在""账号已停用"）→ 统一文案，不泄露账号是否存在；
2. 限流 / 锁定 → 与账号无关的统一文案，避免通过提示差异枚举有效用户名。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models.admin_session import AdminSession
from app.db.models.admin_user import HR_ROLE, VALID_ROLES, AdminUser
from app.db.models.audit_log import (
    ACTION_LOGIN_FAILED,
    ACTION_LOGIN_LOCKED,
    ACTION_LOGIN_SUCCESS,
    ACTION_PASSWORD_CHANGED,
    ACTION_SESSION_REVOKED,
    ACTION_USER_CREATED,
    AuditLog,
)
from app.exceptions import BizError
from app.security.passwords import (
    ensure_strong_password,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.services.audit import client_ip, record, user_agent

logger = logging.getLogger(__name__)

# Cookie 与请求头名称（前端 utils/request.js 需保持一致）
SESSION_COOKIE_NAME = "ia_session"
CSRF_COOKIE_NAME = "ia_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

# 统一失败文案
MSG_INVALID_CREDENTIALS = "用户名或密码错误"
MSG_TOO_MANY_ATTEMPTS = "登录尝试过于频繁，请稍后再试"

# 指数退避封顶
MAX_LOCK_MINUTES = 24 * 60
# last_seen_at 惰性刷新间隔（秒）：避免每个请求都写库
_TOUCH_INTERVAL_SECONDS = 60


def _utcnow() -> datetime:
    """统一的 UTC 当前时间（与 :mod:`app.db.base` 口径一致）。"""
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """把可能为 naive 的时间补成 UTC aware。

    生产库（PostgreSQL）返回的是 aware 时间，但测试用的 SQLite 等后端
    可能返回 naive，直接比较会抛 ``TypeError``。这里统一收口。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """一个真实的 Argon2id 哈希，用于"账号不存在"时的恒定时间兜底校验。

    懒加载而非模块级常量：避免 import 时就付出一次约几十毫秒的哈希开销。
    """
    return hash_password(secrets.token_urlsafe(16))


def normalize_username(username: str | None) -> str:
    """用户名规范化：去空白 + 转小写（保证唯一性与登录查询口径一致）。"""
    return (username or "").strip().lower()


def hash_token(token: str) -> str:
    """会话令牌摘要（sha256 十六进制）。库中只存摘要，永不存原文。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


# ────────────────────────────────────────────────────────────
# 登录
# ────────────────────────────────────────────────────────────
def _ip_rate_limited(db: Session, ip: str, settings: Settings) -> bool:
    """IP 维度限流：统计窗口内该 IP 的登录失败次数是否超限。

    直接复用 ``audit_logs``，不额外建限流表——登录失败本就该被审计，
    让同一份数据承担两个职责，避免多一张需要清理的表。
    """
    if not ip:
        return False
    window_start = _utcnow() - timedelta(
        seconds=max(60, int(settings.auth_login_rate_window_seconds))
    )
    count = (
        db.query(func.count(AuditLog.id))
        .filter(
            AuditLog.action.in_((ACTION_LOGIN_FAILED, ACTION_LOGIN_LOCKED)),
            AuditLog.ip == ip,
            AuditLog.occurred_at >= window_start,
        )
        .scalar()
        or 0
    )
    return count >= max(1, int(settings.auth_login_rate_max_attempts))


def _register_failure(db: Session, user: AdminUser, settings: Settings, request: Request | None) -> None:
    """记录一次登录失败，必要时按指数退避锁定账号。"""
    threshold = max(1, int(settings.auth_login_max_failures))
    user.failed_login_count = int(user.failed_login_count or 0) + 1

    detail = f"连续失败 {user.failed_login_count} 次"
    if user.failed_login_count >= threshold:
        # 第 1 轮锁定 15 分钟，第 2 轮 30 分钟，第 3 轮 60 分钟…… 封顶 24 小时
        rounds = user.failed_login_count // threshold
        minutes = min(
            max(1, int(settings.auth_login_lock_minutes)) * (2 ** (rounds - 1)),
            MAX_LOCK_MINUTES,
        )
        user.locked_until = _utcnow() + timedelta(minutes=minutes)
        detail += f"，锁定 {minutes} 分钟"

    db.commit()
    record(
        db,
        action=ACTION_LOGIN_FAILED,
        request=request,
        actor_name=user.username,
        target=f"admin_users/{user.username}",
        detail=detail,
    )


def authenticate(
    db: Session,
    *,
    username: str,
    password: str,
    request: Request | None = None,
    settings: Settings | None = None,
) -> AdminUser:
    """校验账号密码并返回用户；任何失败都抛业务异常。

    Raises:
        BizError: 401（凭据错误）/ 429（限流或锁定）。
    """
    settings = settings or get_settings()
    normalized = normalize_username(username)
    ip = client_ip(request)

    # ① IP 维度限流：放在查库之前，且文案与账号无关，不泄露账号是否存在
    if _ip_rate_limited(db, ip, settings):
        record(
            db,
            action=ACTION_LOGIN_LOCKED,
            request=request,
            actor_name=normalized,
            detail=f"IP {ip} 触发登录频率限流",
        )
        raise BizError(429, MSG_TOO_MANY_ATTEMPTS, http_status=429)

    user = db.query(AdminUser).filter(AdminUser.username == normalized).first()

    # ② 账号不存在或已停用：做一次等价的哈希校验再返回统一错误。
    #    若直接返回，响应耗时会明显短于真实校验，可被用于枚举有效用户名。
    if user is None or not user.is_active:
        verify_password(_dummy_hash(), password)
        record(
            db,
            action=ACTION_LOGIN_FAILED,
            request=request,
            actor_name=normalized,
            detail="账号不存在或已停用",
        )
        raise BizError(401, MSG_INVALID_CREDENTIALS, http_status=401)

    # ③ 账号锁定窗口
    locked_until = _aware(user.locked_until)
    if locked_until is not None and locked_until > _utcnow():
        record(
            db,
            action=ACTION_LOGIN_LOCKED,
            request=request,
            actor=user,
            target=f"admin_users/{user.username}",
            detail=f"账号处于锁定窗口，至 {locked_until.isoformat()}",
        )
        raise BizError(429, MSG_TOO_MANY_ATTEMPTS, http_status=429)

    # ④ 密码校验
    if not verify_password(user.password_hash, password):
        _register_failure(db, user, settings, request)
        raise BizError(401, MSG_INVALID_CREDENTIALS, http_status=401)

    # ⑤ 登录成功：清零失败计数、解封，并在参数升级后惰性重哈希
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    db.commit()

    record(
        db,
        action=ACTION_LOGIN_SUCCESS,
        request=request,
        actor=user,
        target=f"admin_users/{user.username}",
    )
    return user


# ────────────────────────────────────────────────────────────
# 会话生命周期
# ────────────────────────────────────────────────────────────
def create_session(
    db: Session,
    user: AdminUser,
    *,
    request: Request | None = None,
    settings: Settings | None = None,
) -> tuple[str, str, datetime]:
    """为用户新建登录会话。

    Returns:
        ``(token 原文, csrf 值, 过期时间)``。token 原文只应写入浏览器 Cookie，
        绝不落库、绝不写日志。
    """
    settings = settings or get_settings()
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = _utcnow() + timedelta(hours=max(1, int(settings.auth_session_hours)))

    db.add(
        AdminSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf_token,
            expires_at=expires_at,
            last_seen_at=_utcnow(),
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
    )
    db.commit()
    return token, csrf_token, expires_at


def resolve_session(
    db: Session,
    token: str | None,
    *,
    touch: bool = True,
) -> AdminSession | None:
    """按 token 原文解析有效会话；无效（不存在/已撤销/已过期/账号停用）返回 None。"""
    if not token:
        return None

    row = db.query(AdminSession).filter(AdminSession.token_hash == hash_token(token)).first()
    if row is None or row.revoked_at is not None:
        return None

    expires_at = _aware(row.expires_at)
    if expires_at is None or expires_at <= _utcnow():
        return None

    user = db.get(AdminUser, row.user_id)
    if user is None or not user.is_active:
        return None

    if touch:
        last_seen = _aware(row.last_seen_at)
        now = _utcnow()
        if last_seen is None or (now - last_seen).total_seconds() > _TOUCH_INTERVAL_SECONDS:
            row.last_seen_at = now
            db.commit()

    return row


def revoke_session(
    db: Session,
    session: AdminSession,
    *,
    request: Request | None = None,
    actor: AdminUser | None = None,
) -> None:
    """撤销单个会话（登出）。重复撤销为幂等操作。"""
    if session.revoked_at is not None:
        return
    session.revoked_at = _utcnow()
    db.commit()
    record(
        db,
        action=ACTION_SESSION_REVOKED,
        request=request,
        actor=actor,
        target=f"admin_sessions/{session.id}",
        detail="主动登出",
    )


def revoke_all_sessions(
    db: Session,
    user_id,
    *,
    except_session_id=None,
) -> int:
    """撤销某用户的全部有效会话，返回被撤销的条数。

    用于"改密后旧会话失效"与"管理员重置他人密码"两个场景。
    """
    query = db.query(AdminSession).filter(
        AdminSession.user_id == user_id,
        AdminSession.revoked_at.is_(None),
    )
    if except_session_id is not None:
        query = query.filter(AdminSession.id != except_session_id)

    rows = query.all()
    if not rows:
        return 0

    now = _utcnow()
    for row in rows:
        row.revoked_at = now
    db.commit()
    return len(rows)


def prune_expired_sessions(db: Session, *, keep_days: int = 7) -> int:
    """清理过期/已撤销且超过保留期的会话记录，返回删除条数。

    会话表会随登录次数持续增长，需要一个清理入口（可由运维定时执行）。
    """
    cutoff = _utcnow() - timedelta(days=max(0, keep_days))
    deleted = (
        db.query(AdminSession)
        .filter(
            AdminSession.expires_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted or 0)


# ────────────────────────────────────────────────────────────
# 账号与密码管理
# ────────────────────────────────────────────────────────────
def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str = HR_ROLE,
    email: str | None = None,
    display_name: str = "",
    created_by: str = "cli",
) -> AdminUser:
    """创建后台账号（唯一入口：CLI 脚本或管理员后台）。

    系统**不提供自助注册**：这是内部招聘系统，开放注册等于把候选人
    隐私数据（简历、面试录音、评分）向公众敞开。见方案文档的决策记录。

    Raises:
        BizError: 422（用户名/角色/邮箱非法或密码强度不足）/ 409（账号已存在）。
    """
    normalized = normalize_username(username)
    if not normalized or len(normalized) < 3:
        raise BizError(422, "用户名至少 3 个字符", http_status=422)
    if not all(ch.isalnum() or ch in "._-" for ch in normalized):
        raise BizError(422, "用户名只能包含字母、数字、点、下划线和短横线", http_status=422)
    if role not in VALID_ROLES:
        raise BizError(422, f"角色非法（可选：{'、'.join(VALID_ROLES)}）", http_status=422)

    normalized_email = (email or "").strip().lower() or None
    if normalized_email and "@" not in normalized_email:
        raise BizError(422, "邮箱格式不正确", http_status=422)

    if db.query(AdminUser).filter(AdminUser.username == normalized).first() is not None:
        raise BizError(409, "该用户名已被占用", http_status=409)
    if normalized_email and (
        db.query(AdminUser).filter(AdminUser.email == normalized_email).first() is not None
    ):
        raise BizError(409, "该邮箱已被占用", http_status=409)

    ensure_strong_password(password, username=normalized, email=normalized_email)

    user = AdminUser(
        username=normalized,
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip()[:128],
        role=role,
        is_active=True,
        password_changed_at=_utcnow(),
        created_by=(created_by or "")[:64],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    record(
        db,
        action=ACTION_USER_CREATED,
        actor_name=user.created_by or "cli",
        target=f"admin_users/{user.username}",
        detail=f"角色={user.role}",
    )
    return user


def change_password(
    db: Session,
    user: AdminUser,
    *,
    current_password: str,
    new_password: str,
    request: Request | None = None,
    keep_session_id=None,
) -> int:
    """本人修改密码：校验旧密码 → 强度校验 → 撤销除当前会话外的全部会话。

    Returns:
        被撤销的其他会话数量。

    Raises:
        BizError: 401（当前密码错误）/ 422（新密码不合规或与旧密码相同）。
    """
    if not verify_password(user.password_hash, current_password):
        # 与登录失败一致：不提示"该账号的密码是错的"
        raise BizError(401, MSG_INVALID_CREDENTIALS, http_status=401)

    ensure_strong_password(new_password, username=user.username, email=user.email)

    if verify_password(user.password_hash, new_password):
        raise BizError(422, "新密码不能与当前密码相同", http_status=422)

    user.password_hash = hash_password(new_password)
    user.password_changed_at = _utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()

    revoked = revoke_all_sessions(db, user.id, except_session_id=keep_session_id)
    record(
        db,
        action=ACTION_PASSWORD_CHANGED,
        request=request,
        actor=user,
        target=f"admin_users/{user.username}",
        detail=f"本人修改密码，撤销其他会话 {revoked} 个",
    )
    return revoked


def reset_password(
    db: Session,
    user: AdminUser,
    *,
    new_password: str,
    actor: AdminUser | None = None,
    request: Request | None = None,
) -> int:
    """管理员重置他人密码：撤销该用户**全部**会话（含可能的攻击者会话）。

    Returns:
        被撤销的会话数量。
    """
    ensure_strong_password(new_password, username=user.username, email=user.email)

    user.password_hash = hash_password(new_password)
    user.password_changed_at = _utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()

    revoked = revoke_all_sessions(db, user.id)
    record(
        db,
        action=ACTION_PASSWORD_CHANGED,
        request=request,
        actor=actor,
        actor_name=(actor.username if actor else "cli"),
        target=f"admin_users/{user.username}",
        detail=f"管理员重置密码，撤销全部会话 {revoked} 个",
    )
    return revoked
