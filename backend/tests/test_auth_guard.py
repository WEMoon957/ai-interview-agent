"""鉴权防护层测试（不依赖数据库）。

覆盖"任何人都能直接敲 URL 访问后台"这一原始问题：
- 未登录访问 /admin/* 必须 401，而不是 200；
- 候选人端 /interview、/offer 必须保持公开（面试者要能拿到链接）；
- 写操作缺少/伪造 CSRF 必须 403；
- 外部评分接口必须校验 X-API-Key；
- 响应必须带安全头。

本文件全部用例走 TestClient，不连数据库：``resolve_session`` 在缺少
Cookie 时会提前返回 None，不会触发查询。
"""
import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from app.api.deps import verify_csrf
from app.db.models.admin_session import AdminSession
from app.exceptions import BizError
from app.main import app

client = TestClient(app)


# ── 管理端必须登录 ────────────────────────────────────────────
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/admin/jobs"),
        ("GET", "/admin/templates"),
        ("GET", "/admin/sessions"),
        ("GET", "/admin/company-profile"),
        ("GET", "/admin/screenings"),
        ("GET", "/admin/offline-interviews"),
        ("GET", "/admin/summary"),
        ("GET", "/admin/offers"),
        ("POST", "/admin/jobs"),
        ("POST", "/admin/sessions"),
        ("DELETE", "/admin/jobs/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_admin_endpoints_reject_anonymous(method, path):
    """匿名访问管理端接口必须被 401 挡下（回归"URL 直连可进入后台"）。"""
    resp = client.request(method, path)
    assert resp.status_code == 401, f"{method} {path} 竟然返回 {resp.status_code}"
    body = resp.json()
    assert body["code"] == 401
    # 不得泄露内部细节
    assert "Traceback" not in resp.text


def test_admin_ignores_forged_cookie():
    """伪造一个不存在的会话 token 必须被拒绝（会话状态在服务端）。"""
    resp = client.get("/admin/jobs", cookies={"ia_session": "forged-token-value"})
    assert resp.status_code == 401


def test_protected_endpoint_rejects_garbage_token():
    """畸形 token 不得引发 500（避免异常栈外泄）。"""
    resp = client.get("/admin/jobs", cookies={"ia_session": "x" * 500})
    assert resp.status_code in (401, 403)
    assert "Traceback" not in resp.text


# ── 候选人端必须保持公开 ──────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        "/interview/nonexistent-token",
        "/offer/nonexistent-token",
    ],
)
def test_candidate_endpoints_stay_public(path):
    """候选人端不能被登录态拦死 —— 面试者必须能凭链接直接进入。

    这里只断言"未被 401 拦截"：token 不存在时业务层会返回 404/422，
    说明请求已经走到业务代码，而不是被鉴权层挡在门外。
    """
    resp = client.get(path)
    assert resp.status_code != 401, f"{path} 被鉴权层拦截了，候选人将拿不到链接"


def test_health_stays_public():
    """健康检查保持公开（供探活使用）。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "healthy"


# ── CSRF ─────────────────────────────────────────────────────
def _make_request(headers: dict, cookies: dict) -> Request:
    """构造一个最小 Starlette Request，用于直接单测 verify_csrf。"""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    raw_headers += [(b"cookie", ("; ".join(f"{k}={v}" for k, v in cookies.items())).encode())]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/jobs",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


def _session(csrf_token: str) -> AdminSession:
    """构造一个只带 csrf_token 的会话对象（不落库）。"""
    session = AdminSession()
    session.csrf_token = csrf_token
    return session


def test_csrf_passes_when_header_cookie_and_session_match():
    """三处一致时放行。"""
    request = _make_request({"X-CSRF-Token": "abc"}, {"ia_csrf": "abc"})
    verify_csrf(request, _session("abc"))  # 不抛异常即通过


@pytest.mark.parametrize(
    "headers,cookies,session_token",
    [
        ({}, {"ia_csrf": "abc"}, "abc"),  # 缺请求头
        ({"X-CSRF-Token": "abc"}, {}, "abc"),  # 缺 Cookie
        ({"X-CSRF-Token": "abc"}, {"ia_csrf": "abc"}, ""),  # 服务端没有 CSRF 值
        ({"X-CSRF-Token": "abc"}, {"ia_csrf": "xyz"}, "abc"),  # 头与 Cookie 不一致
        ({"X-CSRF-Token": "abc"}, {"ia_csrf": "abc"}, "xyz"),  # 头与服务端不一致
    ],
)
def test_csrf_rejects_mismatch(headers, cookies, session_token):
    """任一环节不一致都必须 403。"""
    with pytest.raises(BizError) as exc:
        verify_csrf(_make_request(headers, cookies), _session(session_token))
    assert exc.value.http_status == 403


# ── 外部评分接口的 API Key ────────────────────────────────────
def test_scores_endpoint_rejects_without_api_key(monkeypatch):
    """未配置密钥时接口一律拒绝（安全默认，不是"不配置就放行"）。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "scores_api_key", "", raising=False)
    resp = client.get("/api/scores/candidate/00000000-0000-0000-0000-000000000000")
    assert resp.status_code in (401, 503), resp.text


def test_scores_endpoint_rejects_wrong_api_key(monkeypatch):
    """密钥错误必须 401，且不能泄露正确密钥。"""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "scores_api_key", "correct-key-value", raising=False)
    resp = client.get(
        "/api/scores/candidate/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": "wrong-key-value"},
    )
    assert resp.status_code == 401
    assert "correct-key-value" not in resp.text


# ── 安全响应头 ────────────────────────────────────────────────
def test_security_headers_present():
    """基础安全响应头必须下发。"""
    resp = client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"


def test_auth_login_page_is_not_served_by_api():
    """后端不应暴露任何 HTML 登录表单（避免多层认证入口带来绕过面）。"""
    resp = client.get("/auth/login")
    assert resp.status_code == 405  # 该路径只接受 POST


# ── 会话 Cookie 属性 ──────────────────────────────────────────
def test_session_cookie_flags():
    """会话 Cookie 必须 HttpOnly + SameSite=Lax；CSRF Cookie 必须非 HttpOnly。"""
    from app.api.auth import _cookie_kwargs  # noqa: PLC0415
    from app.config import get_settings

    kwargs = _cookie_kwargs(get_settings())
    # 该 helper 只负责公共属性，httpOnly 由 _set_auth_cookies 按 Cookie 用途分别指定
    assert "httponly" not in kwargs
    assert kwargs["samesite"] == "lax"
    assert kwargs["path"] == "/"

    # HttpOnly 由 _set_auth_cookies 分别指定：这里用真实响应验证最终结果
    from starlette.responses import Response

    from app.api.auth import _set_auth_cookies
    from datetime import datetime, timedelta, timezone

    response = Response()
    _set_auth_cookies(
        response,
        token="tok",
        csrf_token="csrf",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        settings=get_settings(),
    )
    raw = "\n".join(response.headers.getlist("set-cookie"))
    session_line = next(line for line in raw.split("\n") if line.startswith("ia_session="))
    csrf_line = next(line for line in raw.split("\n") if line.startswith("ia_csrf="))
    assert "HttpOnly" in session_line, "会话 Cookie 未设置 HttpOnly"
    assert "HttpOnly" not in csrf_line, "CSRF Cookie 不应是 HttpOnly（前端需读取）"
    assert "SameSite=lax" in session_line
