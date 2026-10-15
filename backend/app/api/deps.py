"""FastAPI 公共依赖与响应工具。

本模块是**唯一的鉴权入口**：所有需要保护的 router 都通过
``dependencies=[Depends(require_user)]`` 挂载，端点函数内部无需重复写校验。

安全约定：

- 默认保护：管理端 router 在 ``app/main.py`` 的 include_router 处显式声明
  依赖，不要"忘记挂"；候选人端（``/interview``、``/offer``）以 URL 中的
  不可猜 token 作为凭证，属于显式白名单。
- CSRF：``require_user`` 在非安全方法（POST/PUT/PATCH/DELETE）上自动做
  double-submit 校验，调用方无需额外处理。
"""
import hmac
from typing import Any, Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models.admin_session import AdminSession
from app.db.models.admin_user import AdminUser
from app.db.session import SessionLocal
from app.exceptions import BizError
from app.services.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    resolve_session,
)

# 不需要 CSRF 校验的"安全方法"（只读语义）
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_db() -> Generator[Session, None, None]:
    """请求级数据库会话依赖。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ok(data: Any = None) -> dict:
    """统一成功响应格式（共享知识 #1）。"""
    return {"code": 0, "message": "ok", "data": data}


def verify_csrf(request: Request, session: AdminSession) -> None:
    """校验 CSRF double-submit 三处一致：请求头、非 HttpOnly Cookie、服务端会话。

    三处都参与比较：只比"请求头 vs Cookie"能防跨站表单提交，但挡不住
    "攻击者自己写了一个 Cookie"，再与库中会话记录比对才闭环。

    Raises:
        BizError: 403。
    """
    header_value = request.headers.get(CSRF_HEADER_NAME) or ""
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME) or ""
    expected = session.csrf_token or ""

    if (
        not header_value
        or not cookie_value
        or not expected
        or not hmac.compare_digest(header_value, cookie_value)
        or not hmac.compare_digest(header_value, expected)
    ):
        raise BizError(403, "请求校验失败，请刷新页面后重试", http_status=403)


def require_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    """强制登录：解析会话 Cookie 返回当前用户，并对写操作做 CSRF 校验。

    Raises:
        BizError: 401（未登录/会话失效/账号停用）、403（CSRF 校验失败）。
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session = resolve_session(db, token)
    if session is None:
        raise BizError(401, "登录已失效，请重新登录", http_status=401)

    if request.method not in _SAFE_METHODS:
        verify_csrf(request, session)

    user = db.get(AdminUser, session.user_id)
    if user is None or not user.is_active:
        raise BizError(401, "登录已失效，请重新登录", http_status=401)

    # 供审计/业务代码取用，避免重复解析会话
    request.state.admin_session = session
    request.state.admin_user = user
    return user


def require_role(*roles: str):
    """角色校验依赖工厂：仅列出的角色可访问。

    用法::

        @router.post("/users", dependencies=[Depends(require_role(ADMIN_ROLE))])

    Raises:
        BizError: 403。
    """

    def _dependency(user: AdminUser = Depends(require_user)) -> AdminUser:
        if user.role not in roles:
            raise BizError(403, "当前账号无权执行该操作", http_status=403)
        return user

    # 打上角色标记：供路由自检/测试枚举每个端点允许的角色，
    # 防止"新增管理路由忘挂角色"这类静默越权（漏挂会被 CI 检出）
    _dependency._ia_allowed_roles = tuple(roles)
    return _dependency


def require_api_key(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """外部系统调用凭据校验（请求头 ``X-API-Key``）。

    安全默认：**未配置密钥时一律拒绝**（503），而不是"未配置就放行"。
    """
    expected = (settings.scores_api_key or "").strip()
    if not expected:
        raise BizError(503, "该接口未开放：服务端未配置访问密钥", http_status=503)

    provided = request.headers.get("x-api-key") or ""
    if not provided or not hmac.compare_digest(provided, expected):
        raise BizError(401, "API Key 无效", http_status=401)
