"""安全审计日志写入。

设计原则：

- **只追加、不修改**：审计记录是事后追溯的唯一依据。
- **脱敏写入**：即使调用方不小心把整段请求体塞进 ``detail``，
  本模块也会在落库前把形如 ``password=xxx`` / ``token: xxx`` 的片段打码。
- **尽力而为**：审计写入失败绝不能中断业务流程，只记服务端日志。
"""
from __future__ import annotations

import logging
import re

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models.admin_user import AdminUser
from app.db.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

# 形如 password=xxx / "token": "xxx" / api_key: xxx 的片段一律打码。
# 这是一道"兜底防线"：调用方本就不该把凭据传进来。
_SENSITIVE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|cookie)\b\s*[\"']?\s*[=:]\s*[\"']?([^\s,;\"'}]+)"
)

_MAX_DETAIL_LEN = 2000


def scrub(value: str) -> str:
    """把文本中的疑似凭据片段替换为 ***。"""
    if not value:
        return ""
    return _SENSITIVE_PATTERN.sub(lambda m: f"{m.group(1)}=***", value)


def client_ip(request: Request | None) -> str:
    """提取客户端 IP。

    部署在 nginx 反代之后，真实 IP 在 ``X-Forwarded-For`` 的首段。
    注意：该头可被伪造，因此**仅用于审计与限流，不作为鉴权依据**。
    """
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def user_agent(request: Request | None) -> str:
    """提取 User-Agent（截断到列宽）。"""
    if request is None:
        return ""
    return (request.headers.get("user-agent") or "")[:255]


def record(
    db: Session,
    *,
    action: str,
    request: Request | None = None,
    actor: AdminUser | None = None,
    actor_id=None,
    actor_name: str = "",
    target: str = "",
    detail: str = "",
) -> None:
    """写入一条审计日志（失败不抛异常）。

    Args:
        db: 请求级数据库会话。
        action: 动作常量，见 :mod:`app.db.models.audit_log` 的 ``ACTION_*``。
        request: 用于自动提取 IP 与 User-Agent。
        actor: 已登录的操作者（登录失败场景为 None）。
        actor_id: 只有 id、拿不到 ORM 对象时使用（如 ASGI 中间件）。
        actor_name: 未登录场景下记录"尝试使用的用户名"。
        target: 操作对象标识。
        detail: 已脱敏的补充信息。
    """
    try:
        row = AuditLog(
            actor_id=(actor.id if actor is not None else actor_id),
            actor_name=(actor.username if actor is not None else actor_name or "")[:64],
            action=action[:64],
            target=(target or "")[:255],
            detail=scrub(detail)[:_MAX_DETAIL_LEN],
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001 - 审计失败不能影响主流程
        logger.exception("写审计日志失败：action=%s", action)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
