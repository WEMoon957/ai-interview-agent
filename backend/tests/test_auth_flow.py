"""认证流程端到端测试（需要真实 PostgreSQL）。

覆盖完整链路：创建账号 → 登录 → 访问受保护接口 → 登出 → 再访问被拒，
以及弱口令拒绝、失败文案统一、连续失败锁定、改密/停用撤销会话等。

无数据库时（例如 CI 未挂 Postgres）整个文件自动跳过，
不会让流水线因环境缺失而变红。

所有测试账号以 ``__pytest_`` 前缀创建，并在用例结束后连同其会话与
审计记录一并清理，不污染业务数据。
"""
import uuid

import pytest
from sqlalchemy import or_, text
from starlette.testclient import TestClient

from app.db.models.admin_session import AdminSession
from app.db.models.admin_user import AdminUser
from app.db.models.audit_log import AuditLog
from app.db.session import SessionLocal
from app.exceptions import BizError
from app.main import app
from app.services import auth as auth_service
from app.security.passwords import verify_password

STRONG_PASSWORD = "Kf7#mQ2vLp9x"
ALT_PASSWORD = "Zm4$wR8nQd2t"


def _db_available() -> bool:
    """探测数据库是否可用（不可用则跳过本文件的全部用例）。"""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="需要可用的 PostgreSQL（本机 Docker 容器或 CI service）",
)


def _cleanup(user_ids, usernames) -> None:
    """删除测试产生的账号、会话与审计记录。

    审计日志要按**两个维度**匹配：登录类事件的 ``actor_name`` 是账号名，
    而 ``admin.user.created`` 的 ``actor_name`` 是操作者（cli/pytest），
    只有 ``target`` 里才带被创建的账号名。
    """
    db = SessionLocal()
    try:
        targets = [f"admin_users/{name}" for name in usernames]
        db.query(AuditLog).filter(
            or_(AuditLog.actor_name.in_(list(usernames)), AuditLog.target.in_(targets))
        ).delete(synchronize_session=False)
        db.query(AdminSession).filter(AdminSession.user_id.in_(list(user_ids))).delete(
            synchronize_session=False
        )
        db.query(AdminUser).filter(AdminUser.id.in_(list(user_ids))).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture
def admin_user():
    """创建一个唯一的测试管理员账号，用后清理。"""
    username = f"__pytest_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        user = auth_service.create_user(
            db,
            username=username,
            password=STRONG_PASSWORD,
            role="admin",
            display_name="自动化测试账号",
            created_by="pytest",
        )
        user_id = user.id
    finally:
        db.close()

    yield {"id": user_id, "username": username, "password": STRONG_PASSWORD}

    _cleanup([user_id], [username])


def _client(ip: str = "") -> TestClient:
    """新建一个带独立 Cookie 罐的客户端，并可指定来源 IP（隔离限流计数）。"""
    client = TestClient(app)
    if ip:
        client.headers.update({"X-Forwarded-For": ip})
    return client


def _unique_ip() -> str:
    """每个用例一个独立 IP，避免互相触发 IP 维度限流。"""
    return f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"


def _login(client: TestClient, username: str, password: str):
    """登录并把 CSRF 头挂到客户端上，返回响应。"""
    resp = client.post("/auth/login", json={"username": username, "password": password})
    if resp.status_code == 200:
        client.headers.update({"X-CSRF-Token": resp.json()["data"]["csrf_token"]})
    return resp


# ── 完整链路 ─────────────────────────────────────────────────
def test_full_login_flow(admin_user):
    """登录 → 访问受保护接口 → 登出 → 再访问被拒。"""
    client = _client(_unique_ip())

    # 未登录时后台不可访问
    assert client.get("/admin/jobs").status_code == 401

    resp = _login(client, admin_user["username"], admin_user["password"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user"]["username"] == admin_user["username"]
    assert "password" not in str(data["user"]).lower()

    # 会话 Cookie 是 HttpOnly，且不下发任何密码信息
    set_cookie = "\n".join(resp.headers.get_list("set-cookie"))
    assert "ia_session=" in set_cookie
    assert "HttpOnly" in set_cookie

    # 登录后可访问
    assert client.get("/auth/me").status_code == 200
    assert client.get("/admin/jobs").status_code == 200

    # 登出
    assert client.post("/auth/logout").status_code == 200

    # 登出后会话立即失效
    assert client.get("/auth/me").status_code == 401
    assert client.get("/admin/jobs").status_code == 401


def test_write_operation_requires_csrf(admin_user):
    """已登录但缺少 CSRF 头的写操作必须 403（防跨站请求伪造）。"""
    client = _client(_unique_ip())
    resp = _login(client, admin_user["username"], admin_user["password"])
    assert resp.status_code == 200

    # 主动摘掉 CSRF 头
    client.headers.pop("X-CSRF-Token", None)
    blocked = client.post("/admin/jobs", json={"title": "csrf-probe"})
    assert blocked.status_code == 403, blocked.text


def test_logout_requires_csrf(admin_user):
    """登出属于写操作，缺 CSRF 时不能被跨站强制登出。"""
    client = _client(_unique_ip())
    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200

    client.headers.pop("X-CSRF-Token", None)
    assert client.post("/auth/logout").status_code == 403
    # 会话仍然有效
    assert client.get("/auth/me").status_code == 200


# ── 凭据策略 ─────────────────────────────────────────────────
def test_login_failure_message_is_uniform(admin_user):
    """"密码错误"与"账号不存在"必须返回同一文案，不可枚举有效用户名。"""
    client = _client(_unique_ip())
    missing_username = f"__pytest_missing_{uuid.uuid4().hex[:8]}"

    try:
        wrong_pwd = _login(client, admin_user["username"], "WrongPassw0rd#1")
        unknown = _login(client, missing_username, STRONG_PASSWORD)

        assert wrong_pwd.status_code == unknown.status_code == 401
        assert wrong_pwd.json()["message"] == unknown.json()["message"]
        assert wrong_pwd.json()["message"] == "用户名或密码错误"
    finally:
        # "账号不存在"的失败记录里 actor_name 就是这个虚构用户名，需一并清理
        _cleanup([], [missing_username])


def test_weak_password_rejected_on_create():
    """弱口令不得创建成功（密码策略在服务层强制）。"""
    username = f"__pytest_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    created = None
    try:
        for weak in ("123456", "password", "benerniang123", "abcdefghij"):
            with pytest.raises(BizError) as exc:
                auth_service.create_user(db, username=username, password=weak)
            assert exc.value.http_status == 422

        # 合规密码可以创建
        created = auth_service.create_user(db, username=username, password=STRONG_PASSWORD)
        assert created.id is not None
    finally:
        db.close()

    if created is not None:
        _cleanup([created.id], [username])


def test_password_is_stored_as_argon2id(admin_user):
    """库中必须是 Argon2id 哈希，且不含明文。"""
    db = SessionLocal()
    try:
        user = db.get(AdminUser, admin_user["id"])
        assert user.password_hash.startswith("$argon2id$")
        assert STRONG_PASSWORD not in user.password_hash
        assert verify_password(user.password_hash, STRONG_PASSWORD)
    finally:
        db.close()


def test_duplicate_username_rejected(admin_user):
    """用户名唯一性。"""
    db = SessionLocal()
    try:
        with pytest.raises(BizError) as exc:
            auth_service.create_user(
                db, username=admin_user["username"], password=STRONG_PASSWORD
            )
        assert exc.value.http_status == 409
    finally:
        db.close()


# ── 防爆破 ───────────────────────────────────────────────────
def test_lockout_after_repeated_failures(admin_user):
    """连续失败达到阈值后账号锁定，此后即使密码正确也被拒。"""
    client = _client(_unique_ip())

    for _ in range(5):
        assert _login(client, admin_user["username"], "WrongPassw0rd#1").status_code == 401

    # 已进入锁定窗口：正确密码也应被拒
    locked = _login(client, admin_user["username"], admin_user["password"])
    assert locked.status_code == 429, locked.text
    assert "频繁" in locked.json()["message"]

    # 管理员可人工解锁
    db = SessionLocal()
    try:
        user = db.get(AdminUser, admin_user["id"])
        assert user.locked_until is not None
        user.failed_login_count = 0
        user.locked_until = None
        db.commit()
    finally:
        db.close()

    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200


# ── 会话撤销 ─────────────────────────────────────────────────
def test_change_password_revokes_other_sessions(admin_user):
    """本人改密后，其他设备的会话立即失效，当前会话保留。"""
    device_a = _client(_unique_ip())
    device_b = _client(_unique_ip())

    assert _login(device_a, admin_user["username"], admin_user["password"]).status_code == 200
    assert _login(device_b, admin_user["username"], admin_user["password"]).status_code == 200
    assert device_b.get("/auth/me").status_code == 200

    changed = device_a.post(
        "/auth/change-password",
        json={"current_password": STRONG_PASSWORD, "new_password": ALT_PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["revokedSessions"] >= 1

    # 其他设备被踢下线，当前设备仍可用
    assert device_b.get("/auth/me").status_code == 401
    assert device_a.get("/auth/me").status_code == 200

    # 旧密码失效，新密码可登录
    fresh = _client(_unique_ip())
    assert _login(fresh, admin_user["username"], STRONG_PASSWORD).status_code == 401
    assert _login(fresh, admin_user["username"], ALT_PASSWORD).status_code == 200

    # 复位密码，便于 fixture 之外的后续用例（本用例内已无后续）
    db = SessionLocal()
    try:
        user = db.get(AdminUser, admin_user["id"])
        user.password_hash = auth_service.hash_password(STRONG_PASSWORD)
        user.password_changed_at = auth_service._utcnow()
        db.commit()
    finally:
        db.close()


def test_change_password_rejects_weak_and_same(admin_user):
    """新密码不能过弱，也不能与旧密码相同。"""
    client = _client(_unique_ip())
    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200

    weak = client.post(
        "/auth/change-password",
        json={"current_password": STRONG_PASSWORD, "new_password": "123456"},
    )
    assert weak.status_code == 422

    same = client.post(
        "/auth/change-password",
        json={"current_password": STRONG_PASSWORD, "new_password": STRONG_PASSWORD},
    )
    assert same.status_code == 422

    wrong_current = client.post(
        "/auth/change-password",
        json={"current_password": "WrongPassw0rd#1", "new_password": ALT_PASSWORD},
    )
    assert wrong_current.status_code == 401


def test_disable_account_revokes_sessions(admin_user):
    """停用账号后，其存量会话立即失效。"""
    client = _client(_unique_ip())
    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200
    assert client.get("/auth/me").status_code == 200

    db = SessionLocal()
    try:
        user = db.get(AdminUser, admin_user["id"])
        user.is_active = False
        db.commit()
    finally:
        db.close()

    assert client.get("/auth/me").status_code == 401
    assert client.get("/admin/jobs").status_code == 401


def test_forged_session_token_rejected(admin_user):
    """库中不存在对应摘要的 token（伪造/重放）必须被拒。"""
    client = _client(_unique_ip())
    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200

    forged = _client(_unique_ip())
    forged.cookies.set("ia_session", uuid.uuid4().hex + uuid.uuid4().hex)
    assert forged.get("/admin/jobs").status_code == 401


# ── 敏感信息不外泄 ───────────────────────────────────────────
def test_user_listing_hides_password_hash(admin_user):
    """账号列表不得返回 password_hash。"""
    client = _client(_unique_ip())
    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200

    resp = client.get("/auth/users")
    assert resp.status_code == 200
    assert "password_hash" not in resp.text
    assert "$argon2id$" not in resp.text


def test_audit_log_records_login_without_secrets(admin_user):
    """审计日志必须记录登录事件，且绝不落密码与完整 token。"""
    client = _client(_unique_ip())
    assert _login(client, admin_user["username"], admin_user["password"]).status_code == 200
    assert client.post("/auth/logout").status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.actor_name == admin_user["username"])
            .all()
        )
        assert rows, "登录/登出未写入审计日志"
        actions = {row.action for row in rows}
        assert "auth.login.success" in actions

        blob = " ".join(f"{r.target} {r.detail}" for r in rows)
        assert STRONG_PASSWORD not in blob
    finally:
        db.close()
