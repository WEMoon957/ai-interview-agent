"""个人访问令牌（PAT）服务：签发、解析、撤销。

设计约束（与 ``app.services.auth`` 保持一致的口径）
--------------------------------------------------
- 库里只存 ``sha256(原文)``，原文只在签发那一刻返回一次，之后无法再取回；
- 校验一律用 ``hmac.compare_digest`` 做常量时间比较，避免时序侧信道；
- 令牌可独立撤销、可设过期，**不依赖账号密码**——HR 改密不会让 Agent 掉线，
  反过来停用账号会立即让该账号的全部令牌失效（在 :func:`resolve_token` 里判定）。

scope 语义
----------
``read``  只读：查岗位、查榜单、查汇总。绝大多数「问数据」的场景只需要它。
``write`` 可写：上传简历、发面试链接、约复试。**隐含 read**（写的人必然要读）。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models.admin_user import AdminUser
from app.db.models.audit_log import ACTION_PAT_CREATED, ACTION_PAT_REVOKED
from app.db.models.personal_access_token import PersonalAccessToken
from app.exceptions import BizError
from app.services import audit

# 令牌前缀：一眼能看出这是本系统的 PAT，也便于在日志/泄露扫描里识别
TOKEN_PREFIX = "ia_pat_"

SCOPE_READ = "read"
SCOPE_WRITE = "write"
# 顺序即展示顺序；write 依赖 read
SCOPE_ORDER = (SCOPE_READ, SCOPE_WRITE)
SCOPE_LABELS = {SCOPE_READ: "只读", SCOPE_WRITE: "可写"}

DEFAULT_EXPIRES_DAYS = 90
MAX_EXPIRES_DAYS = 365
# last_used_at 惰性刷新间隔（秒）：避免每个请求都写库
_TOUCH_INTERVAL_SECONDS = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """把可能为 naive 的时间补成 UTC aware（SQLite 等后端会返回 naive）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_token(raw: str) -> str:
    """令牌摘要（sha256 十六进制）。库中只存摘要。"""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def parse_scopes(raw: str | None) -> set[str]:
    """把库里的 "read,write" 解析成集合；未知值直接丢弃（默认最小权限）。"""
    items = {part.strip().lower() for part in (raw or "").split(",") if part.strip()}
    return {s for s in items if s in SCOPE_ORDER}


def normalize_scopes(scopes: list[str] | tuple[str, ...] | None) -> str:
    """校验并规范化 scope 列表，返回落库字符串。

    - 空值默认 ``read``（最小权限原则）；
    - ``write`` 自动补 ``read``；
    - 未知 scope 抛 422，而不是静默忽略——静默会让调用方以为权限已生效。

    Raises:
        BizError: 422。
    """
    requested = [str(s).strip().lower() for s in (scopes or []) if str(s).strip()]
    if not requested:
        return SCOPE_READ

    unknown = [s for s in requested if s not in SCOPE_ORDER]
    if unknown:
        raise BizError(
            422,
            f"权限范围非法：{'、'.join(unknown)}（可选：{'、'.join(SCOPE_ORDER)}）",
            http_status=422,
        )

    chosen = set(requested)
    if SCOPE_WRITE in chosen:
        chosen.add(SCOPE_READ)
    return ",".join(s for s in SCOPE_ORDER if s in chosen)


def has_scope(row: PersonalAccessToken, scope: str) -> bool:
    """判断令牌是否具备某个权限。"""
    return scope in parse_scopes(row.scopes)


def _new_raw_token() -> str:
    """生成令牌原文：前缀 + 32 字节随机（约 43 字符）。"""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def create_token(
    db: Session,
    user: AdminUser,
    *,
    name: str = "",
    scopes: list[str] | tuple[str, ...] | None = None,
    expires_days: int = DEFAULT_EXPIRES_DAYS,
    created_by: str = "",
    request: Request | None = None,
) -> tuple[PersonalAccessToken, str]:
    """为用户签发一个令牌。

    Args:
        name: 设备/用途标识，便于本人识别（如 "MacBook-招聘助手"）。
        scopes: 权限范围，默认只读。
        expires_days: 有效天数，0 表示长期有效；范围 0~365。

    Returns:
        ``(记录, 令牌原文)``。原文**只在此刻返回一次**，调用方必须当场交付，
        系统不留存任何可还原的副本。

    Raises:
        BizError: 422（参数非法）。
    """
    clean_name = (name or "").strip()[:64] or "未命名设备"
    normalized_scopes = normalize_scopes(scopes)

    try:
        days = int(expires_days)
    except (TypeError, ValueError):
        raise BizError(422, "有效天数必须是整数", http_status=422) from None
    if days < 0 or days > MAX_EXPIRES_DAYS:
        raise BizError(422, f"有效天数应在 0~{MAX_EXPIRES_DAYS} 之间（0 表示长期）", http_status=422)

    raw = _new_raw_token()
    row = PersonalAccessToken(
        user_id=user.id,
        name=clean_name,
        token_hash=hash_token(raw),
        token_prefix=raw[:12],
        scopes=normalized_scopes,
        expires_at=(_utcnow() + timedelta(days=days)) if days > 0 else None,
        created_by=(created_by or user.username or "")[:64],
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    audit.record(
        db,
        action=ACTION_PAT_CREATED,
        request=request,
        actor=user,
        target=f"personal_access_tokens/{row.id}",
        detail=f"名称={row.name}；范围={row.scopes}；有效期={days or '长期'}天",
    )
    return row, raw


def resolve_token(db: Session, raw: str | None, *, request: Request | None = None) -> tuple[AdminUser, PersonalAccessToken] | None:
    """按原文解析有效令牌。

    返回 ``None`` 表示无效（不存在 / 已撤销 / 已过期 / 账号已停用），
    调用方统一按 401 处理，**不区分具体原因**，避免被用于探测令牌状态。

    注意：账号停用会立即让该账号的所有令牌失效——这是「停用账号」
    与「撤销令牌」两条路径的兜底，不需要逐个回收。
    """
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None

    digest = hash_token(raw)
    row = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token_hash == digest)
        .first()
    )
    if row is None or row.revoked_at is not None:
        return None

    # 常量时间比对：即便查到了记录，也再做一次摘要比对，避免理论上被构造的哈希碰撞路径
    if not hmac.compare_digest(row.token_hash, digest):
        return None

    expires_at = _aware(row.expires_at)
    if expires_at is not None and expires_at <= _utcnow():
        return None

    user = db.get(AdminUser, row.user_id)
    if user is None or not user.is_active:
        return None

    now = _utcnow()
    last_used = _aware(row.last_used_at)
    if last_used is None or (now - last_used).total_seconds() > _TOUCH_INTERVAL_SECONDS:
        row.last_used_at = now
        if request is not None:
            row.last_used_ip = audit.client_ip(request)
        db.commit()

    return user, row


def revoke_token(
    db: Session,
    row: PersonalAccessToken,
    *,
    actor: AdminUser | None = None,
    request: Request | None = None,
) -> None:
    """撤销单个令牌（幂等）。"""
    if row.revoked_at is not None:
        return
    row.revoked_at = _utcnow()
    db.commit()
    audit.record(
        db,
        action=ACTION_PAT_REVOKED,
        request=request,
        actor=actor,
        actor_name=(actor.username if actor else "cli"),
        target=f"personal_access_tokens/{row.id}",
        detail=f"名称={row.name}",
    )


def revoke_all_for_user(db: Session, user_id, *, except_id=None) -> int:
    """撤销某用户的全部有效令牌，返回条数。

    用于「重置密码后回收旧设备凭据」这类场景；改密本身不会自动调用，
    因为 PAT 的定位是独立于密码的长期凭据。
    """
    query = db.query(PersonalAccessToken).filter(
        PersonalAccessToken.user_id == user_id,
        PersonalAccessToken.revoked_at.is_(None),
    )
    if except_id is not None:
        query = query.filter(PersonalAccessToken.id != except_id)

    rows = query.all()
    if not rows:
        return 0
    now = _utcnow()
    for row in rows:
        row.revoked_at = now
    db.commit()
    return len(rows)


def list_tokens(db: Session, user_id) -> list[PersonalAccessToken]:
    """列出某用户的全部令牌（含已撤销，便于排查；不含原文）。"""
    return (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.user_id == user_id)
        .order_by(PersonalAccessToken.created_at.desc())
        .all()
    )


def is_expired(row: PersonalAccessToken) -> bool:
    """展示用：判断令牌是否已过期。"""
    expires_at = _aware(row.expires_at)
    return expires_at is not None and expires_at <= _utcnow()
