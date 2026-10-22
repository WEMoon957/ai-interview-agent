"""后台认证路由：登录 / 登出 / 当前用户 / 改密 / 账号管理。

路由清单与保护级别：

===================================== ============== ==========================
路径                                  方法           保护
===================================== ============== ==========================
``/auth/login``                        POST           公开（本身是登录入口）
``/auth/logout``                       POST           已登录（幂等）
``/auth/me``                           GET            需登录
``/auth/change-password``              POST           需登录
``/auth/users``                        GET/POST       仅 admin
``/auth/users/{id}/disable|enable``    POST           仅 admin
``/auth/users/{id}/reset-password``    POST           仅 admin
===================================== ============== ==========================

**系统不提供自助注册**：这是内部招聘系统，开放注册等于把候选人隐私
（简历、面试录音、评分）向公众敞开。账号由 CLI 脚本或 admin 账号创建。
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok, require_role, require_user, verify_csrf
from app.api.schemas import (
    AdminUserOut,
    ChangePasswordRequest,
    CreateAdminUserRequest,
    CreateTokenRequest,
    LoginRequest,
    ResetPasswordRequest,
    TokenOut,
    UpdateAdminUserRequest,
)
from app.config import Settings, get_settings
from app.db.models.admin_user import ADMIN_ROLE, VALID_ROLES, AdminUser
from app.db.models.audit_log import ACTION_ADMIN_WRITE, ACTION_USER_DISABLED, ACTION_USER_CREATED
from app.db.models.personal_access_token import PersonalAccessToken
from app.exceptions import BizError
from app.services import audit
from app.services import auth as auth_service
from app.services import pat as pat_service
from app.services.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Cookie 读写 ───────────────────────────────────────────────
def _cookie_kwargs(settings: Settings) -> dict:
    """会话 Cookie 的公共属性。"""
    kwargs: dict = {
        "path": "/",
        "secure": bool(settings.auth_cookie_secure),
        # Lax 足以防跨站请求伪造，同时不破坏"从飞书卡片点链接进入"的正常跳转
        "samesite": "lax",
    }
    if settings.auth_cookie_domain:
        kwargs["domain"] = settings.auth_cookie_domain
    return kwargs


def _set_auth_cookies(
    response: Response,
    *,
    token: str,
    csrf_token: str,
    expires_at: datetime,
    settings: Settings,
) -> None:
    """写入会话 Cookie（HttpOnly）与 CSRF Cookie（须可被 JS 读取）。"""
    max_age = max(60, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
    common = _cookie_kwargs(settings)

    response.set_cookie(SESSION_COOKIE_NAME, token, max_age=max_age, httponly=True, **common)
    # CSRF Cookie 必须非 HttpOnly：前端要读出来放进 X-CSRF-Token 请求头
    response.set_cookie(CSRF_COOKIE_NAME, csrf_token, max_age=max_age, httponly=False, **common)


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    """清除两个认证 Cookie。"""
    common = _cookie_kwargs(settings)
    response.delete_cookie(SESSION_COOKIE_NAME, **common)
    response.delete_cookie(CSRF_COOKIE_NAME, **common)


def _user_out(user: AdminUser) -> dict:
    """序列化用户（保证绝不带出 password_hash）。"""
    return AdminUserOut.model_validate(user).model_dump(mode="json")


# ── 登录 / 登出 / 当前用户 ─────────────────────────────────────
@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """账号密码登录。成功时下发会话 Cookie，失败文案统一不区分原因。"""
    settings = get_settings()
    user = auth_service.authenticate(
        db, username=payload.username, password=payload.password, request=request
    )
    token, csrf_token, expires_at = auth_service.create_session(db, user, request=request)

    _set_auth_cookies(
        response, token=token, csrf_token=csrf_token, expires_at=expires_at, settings=settings
    )
    return ok(
        {
            "user": _user_out(user),
            "csrf_token": csrf_token,
            "expires_at": expires_at.isoformat(),
        }
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """登出：撤销服务端会话并清除 Cookie（未登录时幂等成功）。"""
    settings = get_settings()
    token = request.cookies.get(SESSION_COOKIE_NAME)
    session = auth_service.resolve_session(db, token, touch=False)

    if session is not None:
        # 已登录态下登出属于"写操作"，同样需要 CSRF 校验，
        # 否则攻击者可以用一张跨站表单把用户强制踢下线
        verify_csrf(request, session)
        actor = db.get(AdminUser, session.user_id)
        auth_service.revoke_session(db, session, request=request, actor=actor)

    _clear_auth_cookies(response, settings)
    return ok({"loggedOut": True})


@router.get("/me")
def me(user: AdminUser = Depends(require_user)):
    """返回当前登录用户（前端路由守卫与刷新页面时用它恢复登录态）。"""
    return ok(_user_out(user))


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AdminUser = Depends(require_user),
):
    """本人修改密码：撤销除当前会话外的所有会话（改密后旧会话失效）。"""
    session = request.state.admin_session
    revoked = auth_service.change_password(
        db,
        user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        request=request,
        keep_session_id=session.id,
    )
    return ok({"revokedSessions": revoked})


# ── 账号管理（仅 admin）────────────────────────────────────────
@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    _actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """后台账号列表。"""
    users = db.query(AdminUser).order_by(AdminUser.created_at.asc()).all()
    return ok([_user_out(u) for u in users])


@router.post("/users")
def create_admin_user(
    payload: CreateAdminUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """创建后台账号（唯一入口除此之外还有 CLI 脚本 scripts/create_admin.py）。"""
    user = auth_service.create_user(
        db,
        username=payload.username,
        password=payload.password,
        role=payload.role,
        email=payload.email,
        display_name=payload.display_name,
        created_by=actor.username,
    )
    return ok(_user_out(user))


def _get_target_user(db: Session, user_id: uuid.UUID, actor: AdminUser) -> AdminUser:
    """取出待操作账号，并挡住"管理员操作自己"的危险路径。"""
    target = db.get(AdminUser, user_id)
    if target is None:
        raise BizError(404, "账号不存在", http_status=404)
    if target.id == actor.id:
        raise BizError(422, "不能对当前登录账号执行该操作", http_status=422)
    return target


@router.put("/users/{user_id}")
def update_admin_user(
    user_id: uuid.UUID,
    payload: UpdateAdminUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """编辑后台账号（显示名 / 角色 / 邮箱）。

    **登录用户名不可修改** —— 它是登录凭据，改名会让使用者困惑；
    如需换登录名，请新建账号并停用旧账号。
    """
    target = _get_target_user(db, user_id, actor)

    changes: list[str] = []
    if payload.display_name is not None:
        target.display_name = (payload.display_name or "").strip()[:128]
        changes.append(f"显示名→{target.display_name or '（空）'}")

    if payload.role is not None:
        if payload.role not in VALID_ROLES:
            raise BizError(
                422, f"角色非法（可选：{'、'.join(VALID_ROLES)}）", http_status=422
            )
        if payload.role != target.role:
            changes.append(f"角色 {target.role}→{payload.role}")
            target.role = payload.role

    if payload.email is not None:
        normalized = (payload.email or "").strip().lower() or None
        if normalized and "@" not in normalized:
            raise BizError(422, "邮箱格式不正确", http_status=422)
        if normalized and normalized != (target.email or None):
            occupied = (
                db.query(AdminUser)
                .filter(AdminUser.email == normalized, AdminUser.id != target.id)
                .first()
            )
            if occupied is not None:
                raise BizError(409, "该邮箱已被其他账号占用", http_status=409)
        target.email = normalized
        changes.append(f"邮箱→{normalized or '（空）'}")

    if not changes:
        # 没有任何变化时不写审计，直接返回现状
        return ok(_user_out(target))

    db.commit()
    db.refresh(target)

    audit.record(
        db,
        action=ACTION_ADMIN_WRITE,
        request=request,
        actor=actor,
        target=f"admin_users/{target.username}",
        detail="编辑账号：" + "；".join(changes),
    )
    return ok(_user_out(target))


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """停用账号：软标记 + 立即撤销该账号全部会话。"""
    target = _get_target_user(db, user_id, actor)
    target.is_active = False
    db.commit()
    revoked = auth_service.revoke_all_sessions(db, target.id)

    audit.record(
        db,
        action=ACTION_USER_DISABLED,
        request=request,
        actor=actor,
        target=f"admin_users/{target.username}",
        detail=f"停用账号并撤销会话 {revoked} 个",
    )
    return ok({"disabled": True, "revokedSessions": revoked})


@router.post("/users/{user_id}/enable")
def enable_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """启用账号（不恢复历史会话，需重新登录）。"""
    target = _get_target_user(db, user_id, actor)
    target.is_active = True
    # 账号解封时清掉历史失败计数，避免刚启用就处于锁定窗口
    target.failed_login_count = 0
    target.locked_until = None
    db.commit()

    audit.record(
        db,
        action=ACTION_ADMIN_WRITE,
        request=request,
        actor=actor,
        target=f"admin_users/{target.username}",
        detail="启用账号",
    )
    return ok({"enabled": True})


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """管理员重置他人密码：撤销对方**全部**会话（含可能的攻击者会话）。"""
    target = _get_target_user(db, user_id, actor)
    revoked = auth_service.reset_password(
        db,
        target,
        new_password=payload.new_password,
        actor=actor,
        request=request,
    )
    return ok({"revokedSessions": revoked})


@router.post("/users/{user_id}/unlock")
def unlock_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: AdminUser = Depends(require_role(ADMIN_ROLE)),
):
    """人工解除登录失败锁定。"""
    target = _get_target_user(db, user_id, actor)
    target.failed_login_count = 0
    target.locked_until = None
    db.commit()

    audit.record(
        db,
        action=ACTION_ADMIN_WRITE,
        request=request,
        actor=actor,
        target=f"admin_users/{target.username}",
        detail="人工解除锁定",
    )
    return ok({"unlocked": True})


# ── 个人访问令牌（PAT）──────────────────────────────────────
# 定位：发给程序化客户端（桌面 Agent / CLI）的长期凭据，不是给人日常登录用的。
# 浏览器会话 12 小时就过期，把账号密码塞进 MCP 配置文件更糟——PAT 是这两者之外
# 唯一正确的选择：可撤销、可限权、可追溯到具体设备。
def _token_out(row) -> dict:
    """序列化令牌（绝不带 token_hash / 原文）。"""
    return TokenOut(
        id=str(row.id),
        name=row.name,
        token_prefix=row.token_prefix,
        scopes=row.scopes,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        last_used_ip=row.last_used_ip,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        is_expired=pat_service.is_expired(row),
    ).model_dump(mode="json")


def _reject_agent_self_issue(request: Request) -> None:
    """令牌不能用来签发新令牌：否则一个只读令牌能"自我提权"到可写。"""
    if getattr(request.state, "auth_via", "") == "pat":
        raise BizError(403, "访问令牌不能用于签发或管理令牌，请用后台或 CLI 操作", http_status=403)


@router.post("/tokens")
def create_access_token(
    payload: CreateTokenRequest,
    request: Request,
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """为当前登录用户签发访问令牌。

    **令牌原文只在此响应中出现一次**，系统只保存 sha256 摘要，之后无法找回。
    """
    _reject_agent_self_issue(request)
    row, raw = pat_service.create_token(
        db,
        user,
        name=payload.name,
        scopes=payload.scopes,
        expires_days=payload.expires_days,
        created_by=user.username,
        request=request,
    )
    data = _token_out(row)
    data["token"] = raw
    data["notice"] = "令牌原文只显示这一次，请立即复制保存；丢失只能重新签发"
    return ok(data)


@router.get("/tokens")
def list_access_tokens(
    request: Request,
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """列出本人的全部令牌（含已撤销，便于排查；不含原文）。"""
    _reject_agent_self_issue(request)
    return ok([_token_out(row) for row in pat_service.list_tokens(db, user.id)])


@router.delete("/tokens/{token_id}")
def revoke_access_token(
    token_id: str,
    request: Request,
    user: AdminUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """撤销本人的一个令牌（幂等）。只能撤销自己的，避免互相踢下线。"""
    _reject_agent_self_issue(request)
    try:
        tid = uuid.UUID(token_id)
    except (ValueError, TypeError, AttributeError):
        raise BizError(422, "令牌 id 格式无效", http_status=422) from None

    row = db.get(PersonalAccessToken, tid)
    # 不区分"不存在"与"不属于你"，避免被用于探测他人令牌 id
    if row is None or row.user_id != user.id:
        raise BizError(404, "令牌不存在", http_status=404)

    pat_service.revoke_token(db, row, actor=user, request=request)
    return ok({"revoked": True, "id": str(row.id)})
