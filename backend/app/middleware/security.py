"""安全相关中间件：安全响应头 + 后台写操作审计。

两个中间件都用 **纯 ASGI** 而非 ``BaseHTTPMiddleware`` 实现：
后者会为每个请求再包一层 Request/Response，且其内部的 ``request.state``
与下游依赖注入看到的不一定是同一个对象，容易在审计场景下读到空值。
"""
from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.db.models.audit_log import ACTION_ADMIN_WRITE
from app.db.session import SessionLocal
from app.services import audit

logger = logging.getLogger(__name__)

# ── 安全响应头 ────────────────────────────────────────────────
# 说明：HSTS 不在这里下发 —— 只有确认全站 HTTPS 后才应启用，
# 否则 HTTP 环境会被浏览器强制升级并"锁死"，统一交给 nginx 控制。
_BASE_SECURITY_HEADERS: dict[str, str] = {
    # 禁止浏览器对响应做 MIME 嗅探（防"图片里藏脚本"）
    "X-Content-Type-Options": "nosniff",
    # 禁止被任意站点 iframe 嵌套（防点击劫持）
    "X-Frame-Options": "DENY",
    # 不把完整 URL（可能含 token）带到第三方
    "Referrer-Policy": "no-referrer",
    # 关闭本项目用不到的敏感设备能力
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# 生产用 CSP：前端是 Vite 构建产物（脚本均为外链文件），
# style 需放开 'unsafe-inline' —— Element Plus 会注入内联样式。
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware:
    """给所有响应补上安全响应头（已存在的头不覆盖）。"""

    def __init__(self, app: ASGIApp, *, enable_csp: bool = False) -> None:
        self.app = app
        self.enable_csp = enable_csp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                existing = {key.lower() for key, _ in headers}
                for key, value in _BASE_SECURITY_HEADERS.items():
                    if key.lower().encode() not in existing:
                        headers.append((key.encode(), value.encode()))
                if self.enable_csp and b"content-security-policy" not in existing:
                    headers.append((b"Content-Security-Policy", CSP_POLICY.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ── 后台写操作审计 ────────────────────────────────────────────
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 需要审计的路径前缀（候选人端 /interview、/offer 不走这套）
_AUDITED_PREFIXES = ("/admin", "/auth/users")
# 这些路径已由业务侧写入更精确的审计（登录/登出/改密），避免重复
_SKIP_PATHS = frozenset(
    {
        "/auth/login",
        "/auth/logout",
        "/auth/change-password",
    }
)


def _write_audit(entry: dict) -> None:
    """在独立线程/会话中落审计，避免污染请求事务。"""
    db = SessionLocal()
    try:
        audit.record(
            db,
            action=entry["action"],
            request=entry["request"],
            actor_id=entry["actor_id"],
            actor_name=entry["actor_name"],
            target=entry["target"],
            detail=entry["detail"],
        )
    finally:
        db.close()


class AdminWriteAuditMiddleware:
    """审计"成功执行"的后台写操作（4xx/5xx 不记录，失败由业务侧自行记录）。

    依赖 ``require_user`` 已把登录用户写入 ``scope['state']``；
    这里只读取 id 与用户名两个标量，不触碰已随请求关闭的 ORM 会话。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if method not in _WRITE_METHODS or not path.startswith(_AUDITED_PREFIXES) or path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        status_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status", 200))
            await send(message)

        await self.app(scope, receive, send_wrapper)

        status = status_holder.get("status", 500)
        if status >= 400:
            return

        state = scope.get("state") or {}
        user = state.get("admin_user")
        if user is None:
            # 未登录却能写成功 = 保护漏挂，属于严重问题，必须告警
            logger.error("后台写操作未被鉴权保护：%s %s", method, path)
            return

        # 区分「人点的」与「Agent 调用的」：两者的责任归属与事后追责完全不同
        actor_type = state.get("actor_type") or "human"
        detail = f"status={status}; actor={actor_type}"
        if actor_type == "agent":
            pat_name = state.get("pat_name") or ""
            pat_prefix = state.get("pat_prefix") or ""
            # 用 pat= 而不是 token=：审计的 scrub() 会把 token=xxx 整段打码，
            # 那样就丢掉「是哪个令牌」的关键线索。这里只放名称与 12 位前缀，
            # 前缀不足以冒用令牌，但足以定位到具体设备。
            detail += f"; pat={pat_name}({pat_prefix})"

        entry = {
            "action": ACTION_ADMIN_WRITE,
            "request": Request(scope),
            "actor_id": getattr(user, "id", None),
            "actor_name": getattr(user, "username", "") or "",
            "target": f"{method} {path}"[:255],
            "detail": detail,
        }
        try:
            # 同步 DB 写入放到线程池，避免阻塞事件循环
            await run_in_threadpool(_write_audit, entry)
        except Exception:  # noqa: BLE001 - 审计失败不影响业务响应
            logger.exception("后台写操作审计失败：%s %s", method, path)
