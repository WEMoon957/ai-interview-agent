"""飞书妙记个人授权（OAuth 2.0 设备授权流）。

为什么用设备授权而不是授权码回调
--------------------------------
服务器没有公网 HTTPS 域名，授权码流要求配置重定向 URL；设备授权（RFC 8628）
由用户在飞书里扫码/点链接确认，服务端轮询取 token，不需要回调域名，
与 lark-cli 使用的是同一套流程。

端点（2026-10 官方 / lark-cli 口径）
------------------------------------
- 申请设备码：``POST https://accounts.feishu.cn/oauth/v1/device_authorization``
- 轮询/刷新：``POST https://open.feishu.cn/open-apis/authen/v2/oauth/token``
- 用户信息：``GET  https://open.feishu.cn/open-apis/authen/v1/user_info``

状态机
------
- 设备码 ``pending`` → 用户确认 → 拿到 access_token + refresh_token → 落库 ``active``；
- access_token 过期前 5 分钟用 refresh_token 刷新（**refresh_token 会轮换，必须回写**）；
- refresh_token 过期/刷新失败 → ``expired``，需重新授权。
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.db.models import FeishuMinutesAuth

logger = logging.getLogger(__name__)

_DEVICE_AUTH_URL = "https://accounts.feishu.cn/oauth/v1/device_authorization"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

# access_token 提前 5 分钟刷新，避免临界失效
_REFRESH_AHEAD_SECONDS = 300

# 默认 scope：搜索 + 导出逐字稿 + 离线访问（拿 refresh_token）
DEFAULT_SCOPES = (
    "minutes:minutes.search:read "
    "minutes:minutes.transcript:export "
    "offline_access"
)

# 设备授权会话（内存）：单进程 uvicorn 下足够；重启后用户重新扫码即可
_DEVICE_SESSIONS: dict[str, dict] = {}
_DEVICE_LOCK = threading.Lock()
_DEFAULT_DEVICE_TTL = 600


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """把可能 naive 的时间统一按 UTC 处理。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def resolve_scopes(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    scopes = (settings.feishu_minutes_oauth_scopes or "").strip()
    return scopes or DEFAULT_SCOPES


def _post_form(
    url: str, form: dict, settings: Settings, basic_auth: str | None = None
) -> tuple[bool, dict, str]:
    """POST form-urlencoded（OAuth 设备授权/换 token 的标准格式）。

    ``basic_auth`` 为 ``base64(app_id:app_secret)``，仅设备授权申请需要
    （与 lark-cli 一致；用 JSON 会被飞书拒绝为 20140 invalid_client）。
    """
    import httpx

    headers = {}
    if basic_auth:
        headers["Authorization"] = f"Basic {basic_auth}"
    try:
        resp = httpx.post(
            url, data=form, headers=headers, timeout=settings.feishu_notify_timeout
        )
    except httpx.HTTPError as exc:
        return False, {}, f"请求失败：{exc}"

    try:
        payload = resp.json()
    except ValueError:
        return False, {}, f"无法解析响应：{resp.text[:300]}"

    if resp.status_code >= 500:
        return False, payload, f"HTTP {resp.status_code}：{resp.text[:300]}"
    # 业务错误（4xx）也返回 payload，由调用方按 code/error 判断
    return True, payload if isinstance(payload, dict) else {}, ""


def _refresh_expires_seconds(data: dict) -> int | None:
    """兼容 v1（refresh_expires_in）与 v2（refresh_token_expires_in）字段名。"""
    value = data.get("refresh_expires_in")
    if value is None:
        value = data.get("refresh_token_expires_in")
    return int(value) if value is not None else None


def _payload_data(payload: dict) -> dict:
    """兼容 v2 OAuth（字段在顶层）与 v1（字段在 data 内）两种响应。"""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _error_text(payload: dict) -> str:
    error = str(payload.get("error") or "")
    msg = str(payload.get("error_description") or payload.get("msg") or "")
    code = payload.get("code")
    parts = [p for p in (error, msg, f"code={code}" if code not in (None, 0) else "") if p]
    return " ".join(parts) or "未知错误"


# ────────────────────────────── 设备授权 ──────────────────────────────


def start_device_authorization(
    settings: Settings | None = None, authorized_by: str = ""
) -> tuple[bool, dict, str]:
    """申请设备码，返回 (是否成功, 授权信息, 错误说明)。

    授权信息含 ``session_id`` / ``user_code`` / ``verification_uri`` /
    ``verification_uri_complete`` / ``expires_in`` / ``interval``。
    """
    settings = settings or get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, {}, "妙记授权需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import base64

    basic_auth = base64.b64encode(
        f"{settings.feishu_app_id}:{settings.feishu_app_secret}".encode()
    ).decode()
    ok, payload, err = _post_form(
        _DEVICE_AUTH_URL,
        {"client_id": settings.feishu_app_id, "scope": resolve_scopes(settings)},
        settings,
        basic_auth=basic_auth,
    )
    if not ok:
        return False, {}, err

    if payload.get("code") not in (None, 0):
        return False, {}, _error_text(payload)

    data = _payload_data(payload)
    device_code = data.get("device_code") or ""
    if not device_code:
        return False, {}, f"设备授权响应缺少 device_code：{payload}"

    expires_in = int(data.get("expires_in") or _DEFAULT_DEVICE_TTL)
    interval = max(1, int(data.get("interval") or 5))
    session_id = secrets.token_urlsafe(18)

    with _DEVICE_LOCK:
        _DEVICE_SESSIONS[session_id] = {
            "device_code": device_code,
            "interval": interval,
            "authorized_by": authorized_by,
            "created_at": time.time(),
            "expires_at": time.time() + expires_in,
        }
        # 顺手清理过期会话，避免内存累积
        stale = [k for k, v in _DEVICE_SESSIONS.items() if v["expires_at"] < time.time()]
        for key in stale:
            _DEVICE_SESSIONS.pop(key, None)

    return (
        True,
        {
            "session_id": session_id,
            "user_code": data.get("user_code") or "",
            "verification_uri": data.get("verification_uri") or "",
            "verification_uri_complete": data.get("verification_uri_complete") or "",
            "expires_in": expires_in,
            "interval": interval,
        },
        "",
    )


def poll_device_authorization(
    db,
    session_id: str,
    settings: Settings | None = None,
) -> tuple[str, dict | None, str]:
    """轮询一次设备授权结果。

    Returns:
        (status, authorization | None, error)
        status ∈ ``pending`` / ``success`` / ``denied`` / ``expired`` / ``error``。
    """
    settings = settings or get_settings()

    with _DEVICE_LOCK:
        session = _DEVICE_SESSIONS.get(session_id)
        if session is None:
            return "expired", None, "授权会话不存在或已过期，请重新生成"
        if session["expires_at"] < time.time():
            _DEVICE_SESSIONS.pop(session_id, None)
            return "expired", None, "授权码已过期，请重新生成"

    ok, payload, err = _post_form(
        _TOKEN_URL,
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": session["device_code"],
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
        },
        settings,
    )
    if not ok:
        return "error", None, err

    code = payload.get("code")
    error = str(payload.get("error") or "").lower()
    msg = str(payload.get("msg") or "").lower()

    if code not in (None, 0) or error:
        if error in ("authorization_pending", "slow_down") or "pending" in msg:
            return "pending", None, ""
        if error == "access_denied" or code in (20027,):
            with _DEVICE_LOCK:
                _DEVICE_SESSIONS.pop(session_id, None)
            return "denied", None, "用户拒绝了授权"
        if error == "expired_token" or code in (20026, 20037, 20038):
            with _DEVICE_LOCK:
                _DEVICE_SESSIONS.pop(session_id, None)
            return "expired", None, "授权码已过期，请重新生成"
        return "error", None, _error_text(payload)

    data = _payload_data(payload)
    access_token = data.get("access_token") or ""
    refresh_token = data.get("refresh_token") or ""
    if not access_token:
        return "error", None, f"授权响应缺少 access_token：{payload}"

    # 用 user token 反查 open_id / 姓名（授权主体身份）
    user_ok, user, user_err = fetch_user_info(access_token, settings)
    if not user_ok:
        return "error", None, f"获取授权用户信息失败：{user_err}"
    open_id = user.get("open_id") or ""
    if not open_id:
        return "error", None, "授权用户信息缺少 open_id"

    row = save_authorization(
        db,
        open_id=open_id,
        name=user.get("name") or user.get("en_name") or "",
        access_token=access_token,
        refresh_token=refresh_token,
        scope=str(data.get("scope") or ""),
        expires_in=int(data.get("expires_in") or 7200),
        refresh_expires_in=_refresh_expires_seconds(data),
        authorized_by=session.get("authorized_by") or "",
    )
    with _DEVICE_LOCK:
        _DEVICE_SESSIONS.pop(session_id, None)
    return "success", serialize_authorization(row), ""


def fetch_user_info(
    access_token: str, settings: Settings | None = None
) -> tuple[bool, dict, str]:
    """用 user_access_token 获取当前用户信息（open_id / name）。"""
    settings = settings or get_settings()
    import httpx

    try:
        resp = httpx.get(
            _USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=settings.feishu_notify_timeout,
        )
    except httpx.HTTPError as exc:
        return False, {}, f"请求失败：{exc}"

    try:
        payload = resp.json()
    except ValueError:
        return False, {}, f"无法解析响应：{resp.text[:300]}"

    if payload.get("code") not in (None, 0):
        return False, {}, _error_text(payload)
    return True, _payload_data(payload), ""


# ────────────────────────────── token 落库与刷新 ──────────────────────────────


def save_authorization(
    db,
    *,
    open_id: str,
    name: str,
    access_token: str,
    refresh_token: str,
    scope: str,
    expires_in: int,
    refresh_expires_in: int | None,
    authorized_by: str,
) -> FeishuMinutesAuth:
    """新增或覆盖保存一份授权（同一 open_id 只保留最新一份）。"""
    now = _utcnow()
    row = db.query(FeishuMinutesAuth).filter(FeishuMinutesAuth.open_id == open_id).first()
    if row is None:
        row = FeishuMinutesAuth(open_id=open_id)
        db.add(row)

    row.name = name or ""
    row.access_token = access_token
    row.refresh_token = refresh_token
    row.scope = scope or ""
    row.expires_at = now + timedelta(seconds=max(60, expires_in))
    row.refresh_expires_at = (
        now + timedelta(seconds=max(60, refresh_expires_in))
        if refresh_expires_in
        else None
    )
    row.status = "active"
    row.last_refreshed_at = now
    row.authorized_by = authorized_by or ""
    db.commit()
    db.refresh(row)
    return row


def refresh_authorization(
    db, row: FeishuMinutesAuth, settings: Settings | None = None
) -> bool:
    """用 refresh_token 换新 token；成功返回 True。

    注意：飞书刷新后 **refresh_token 会轮换**，必须回写新值，旧值立即失效。
    """
    settings = settings or get_settings()
    if not row.refresh_token:
        row.status = "expired"
        db.commit()
        return False

    ok, payload, err = _post_form(
        _TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": row.refresh_token,
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
        },
        settings,
    )
    if not ok:
        logger.warning("妙记 token 刷新请求失败 open_id=%s err=%s", row.open_id, err)
        return False

    code = payload.get("code")
    if code not in (None, 0):
        # 20026/20037/20038 = refresh_token 无效/过期/已被使用 → 必须重新授权
        if code in (20026, 20037, 20038):
            row.status = "expired"
            db.commit()
        logger.warning(
            "妙记 token 刷新失败 open_id=%s err=%s", row.open_id, _error_text(payload)
        )
        return False

    data = _payload_data(payload)
    access_token = data.get("access_token") or ""
    if not access_token:
        logger.warning("妙记 token 刷新响应缺少 access_token open_id=%s", row.open_id)
        return False

    now = _utcnow()
    row.access_token = access_token
    if data.get("refresh_token"):
        row.refresh_token = data["refresh_token"]
    row.expires_at = now + timedelta(seconds=int(data.get("expires_in") or 7200))
    refresh_seconds = _refresh_expires_seconds(data)
    if refresh_seconds is not None:
        row.refresh_expires_at = now + timedelta(seconds=max(60, refresh_seconds))
    if data.get("scope"):
        row.scope = str(data["scope"])
    row.status = "active"
    row.last_refreshed_at = now
    db.commit()
    return True


def get_valid_user_token(
    db, open_id: str, settings: Settings | None = None
) -> str | None:
    """取某用户当前可用的 user_access_token；不可用返回 None（调用方降级 bot）。

    规则：
    - 未开启个人授权 / 无记录 / 已撤销 / refresh 已过期 → None；
    - access_token 距过期不足 5 分钟 → 先刷新，刷新失败 → None。
    """
    settings = settings or get_settings()
    if not getattr(settings, "feishu_minutes_user_token_enabled", True):
        return None
    open_id = (open_id or "").strip()
    if not open_id:
        return None

    row = db.query(FeishuMinutesAuth).filter(FeishuMinutesAuth.open_id == open_id).first()
    if row is None or row.status != "active":
        return None

    now = _utcnow()
    refresh_expires_at = _as_utc(row.refresh_expires_at)
    if refresh_expires_at is not None and refresh_expires_at <= now:
        row.status = "expired"
        db.commit()
        return None

    expires_at = _as_utc(row.expires_at)
    if expires_at is None or expires_at - now <= timedelta(seconds=_REFRESH_AHEAD_SECONDS):
        if not refresh_authorization(db, row, settings):
            return None

    return row.access_token or None


def list_authorizations(db) -> list[FeishuMinutesAuth]:
    return (
        db.query(FeishuMinutesAuth)
        .order_by(FeishuMinutesAuth.created_at.desc())
        .all()
    )


def revoke_authorization(db, open_id: str) -> bool:
    """撤销授权：清空 token 并置 revoked（幂等）。"""
    row = db.query(FeishuMinutesAuth).filter(FeishuMinutesAuth.open_id == open_id).first()
    if row is None:
        return False
    row.status = "revoked"
    row.access_token = ""
    row.refresh_token = ""
    db.commit()
    return True


def serialize_authorization(row: FeishuMinutesAuth) -> dict:
    """对外序列化：**绝不返回 token 原文**。"""

    def _iso(value: datetime | None) -> str:
        return value.isoformat() if value else ""

    return {
        "open_id": row.open_id,
        "name": row.name or "",
        "status": row.status,
        "scope": row.scope or "",
        "expires_at": _iso(_as_utc(row.expires_at)),
        "refresh_expires_at": _iso(_as_utc(row.refresh_expires_at)),
        "last_refreshed_at": _iso(_as_utc(row.last_refreshed_at)),
        "authorized_by": row.authorized_by or "",
        "updated_at": _iso(_as_utc(row.updated_at)),
    }
