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
from app.services import pat as pat_service
from app.services.auth import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    resolve_session,
)

# 不需要 CSRF 校验的"安全方法"（只读语义）
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# ── 访问令牌（PAT）的收窄边界 ──────────────────────────────────
# 令牌是给程序化客户端（桌面 Agent / CLI）用的，能力必须比人小：
#
#   1) 只能走白名单内的业务前缀——账号管理、改密、公司档案、模板等一律不可达；
#   2) 不许 DELETE——Agent 只该"查、加、改"，删除是人的决定；
#   3) 写方法还要求令牌带 write scope（见 _authenticate_token）。
#
# 这三道闸是叠加的：任何一条不满足都直接 403。
_PAT_ALLOWED_PREFIXES = (
    "/admin/jobs",
    "/admin/screenings",
    "/admin/candidates",
    "/admin/offline-interviews",
    "/admin/summary",
)
# 精确匹配的只读端点（不在上面的业务前缀里，但 Agent 需要它确认身份）
_PAT_ALLOWED_EXACT = frozenset({("GET", "/auth/me")})

_BEARER_PREFIX = "bearer "


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
    """强制登录：解析 ``Authorization: Bearer`` 或会话 Cookie，返回当前用户。

    两条身份通道：

    - **访问令牌（Bearer）**：程序化客户端用，不做 CSRF 校验（令牌不会被浏览器
      自动携带，CSRF 威胁模型不成立），但受白名单、禁 DELETE、scope 三道约束。
    - **会话 Cookie**：浏览器用，非安全方法强制 CSRF double-submit 校验。

    失败时统一 401，不区分"未登录"与"令牌无效"，避免被用于探测令牌状态。

    Raises:
        BizError: 401（未登录/会话失效/令牌无效/账号停用）、403（CSRF 校验失败、
            令牌越界或权限不足）。
    """
    raw_token = _bearer_token(request)
    if raw_token is not None:
        return _authenticate_token(request, db, raw_token)

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
    request.state.actor_type = "human"
    request.state.auth_via = "session"
    return user


def _bearer_token(request: Request) -> str | None:
    """取出 Bearer 令牌原文；没有该请求头时返回 None。"""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith(_BEARER_PREFIX):
        return None
    return header[len(_BEARER_PREFIX):].strip() or None


def _authenticate_token(request: Request, db: Session, raw_token: str) -> AdminUser:
    """用访问令牌完成鉴权，并施加程序化客户端的能力边界。

    这里存进 ``request.state`` 的只有标量（id/名称/前缀），不存 ORM 对象——
    请求结束后数据库会话已关闭，审计中间件不该再去碰它。
    """
    resolved = pat_service.resolve_token(db, raw_token, request=request)
    if resolved is None:
        raise BizError(401, "访问令牌无效或已过期", http_status=401)

    user, row = resolved
    method = request.method
    path = request.url.path

    if method == "DELETE":
        raise BizError(403, "访问令牌不允许执行删除操作", http_status=403)
    if (method, path) not in _PAT_ALLOWED_EXACT and not path.startswith(_PAT_ALLOWED_PREFIXES):
        raise BizError(403, "访问令牌无权访问该接口", http_status=403)
    if method not in _SAFE_METHODS and not pat_service.has_scope(row, pat_service.SCOPE_WRITE):
        raise BizError(403, "该访问令牌为只读权限，不能执行写操作", http_status=403)

    request.state.admin_user = user
    request.state.actor_type = "agent"
    request.state.auth_via = "pat"
    request.state.pat_id = str(row.id)
    request.state.pat_name = row.name
    request.state.pat_prefix = row.token_prefix
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
