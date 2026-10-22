"""访问令牌（PAT）端到端测试（需要真实 PostgreSQL）。

覆盖三层语义：

1. **身份通道**：``Authorization: Bearer`` 能替代 Cookie，且不要求 CSRF；
2. **能力边界**：只读令牌不能写、令牌不能删、白名单外接口一律 403、
   令牌不能自我签发——这是桌面 Agent 接入的安全底线；
3. **生命周期**：撤销、过期、账号停用后立即失效；原文不落库。

无数据库时（CI 未挂 Postgres）整个文件自动跳过，不会让流水线变红。
所有测试数据以 ``__pytest_pat_`` 前缀创建，用例结束后连同令牌与审计一并清理。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

from app.db.models.admin_user import ADMIN_ROLE, HR_ROLE, STORE_MANAGER_ROLE, AdminUser
from app.db.models.audit_log import AuditLog
from app.db.models.personal_access_token import PersonalAccessToken
from app.db.session import SessionLocal
from app.main import app
from app.services import auth as auth_service
from app.services import pat as pat_service

STRONG_PASSWORD = "Kf7#mQ2vLp9x"
PREFIX = "__pytest_pat_"
FAKE_ID = "00000000-0000-0000-0000-000000000000"

client = TestClient(app)


def _db_available() -> bool:
    """探测数据库是否可用。"""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="需要可用的 PostgreSQL")


def _cleanup() -> None:
    """删除本文件造出来的账号、令牌与审计记录。"""
    db = SessionLocal()
    try:
        users = db.query(AdminUser).filter(AdminUser.username.like(f"{PREFIX}%")).all()
        for user in users:
            db.query(PersonalAccessToken).filter(
                PersonalAccessToken.user_id == user.id
            ).delete(synchronize_session=False)
            db.query(AuditLog).filter(AuditLog.actor_id == user.id).delete(
                synchronize_session=False
            )
            db.delete(user)
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def clean_env():
    """模块前后各清一次，避免历史残留干扰。"""
    _cleanup()
    yield
    _cleanup()


@pytest.fixture(scope="module")
def users():
    """建三种角色的测试账号（密码哈希较慢，模块级复用）。"""
    db = SessionLocal()
    try:
        created = {}
        for role, suffix in (
            (HR_ROLE, "hr"),
            (STORE_MANAGER_ROLE, "sm"),
            (ADMIN_ROLE, "admin"),
        ):
            created[role] = auth_service.create_user(
                db,
                username=f"{PREFIX}{suffix}",
                password=STRONG_PASSWORD,
                role=role,
                created_by="pytest",
            )
        return created
    finally:
        db.close()


def _issue(role: str, scopes: list[str], users, *, days: int = 1) -> tuple[str, uuid.UUID]:
    """给指定角色签发一个令牌，返回 (原文, id)。"""
    db = SessionLocal()
    try:
        user = db.get(AdminUser, users[role].id)
        row, raw = pat_service.create_token(
            db, user, name=f"pytest-{'-'.join(scopes)}", scopes=scopes, expires_days=days,
            created_by="pytest",
        )
        return raw, row.id
    finally:
        db.close()


def _auth(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


# ── 1. 身份通道 ────────────────────────────────────────────────
def test_token_can_read_own_identity(users):
    """令牌能确认「我是谁」——Agent 每次开工前先自检。"""
    raw, _ = _issue(HR_ROLE, ["read"], users)
    resp = client.get("/auth/me", headers=_auth(raw))
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["username"] == f"{PREFIX}hr"
    assert body["role"] == HR_ROLE


def test_token_reads_business_data(users):
    """只读令牌能查岗位（Agent 的典型开场动作）。"""
    raw, _ = _issue(HR_ROLE, ["read"], users)
    resp = client.get("/admin/jobs", headers=_auth(raw))
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_token_write_does_not_need_csrf(users):
    """令牌请求不做 CSRF 校验：没有 Cookie，double-submit 不适用。

    用不存在的岗位触发业务 404，说明请求已经过了鉴权与 CSRF 两道门。
    """
    raw, _ = _issue(HR_ROLE, ["read", "write"], users)
    resp = client.post(
        "/admin/screenings/upload",
        data={"job_id": FAKE_ID, "screen": "false", "notify": "false"},
        files={"files": ("probe.txt", b"probe", "text/plain")},
        headers=_auth(raw),
    )
    assert resp.status_code == 404
    assert "岗位不存在" in resp.json()["message"]


# ── 2. 能力边界 ────────────────────────────────────────────────
def test_readonly_token_cannot_write(users):
    """只读令牌调写接口必须 403，而不是「写成功了但没权限」。"""
    raw, _ = _issue(HR_ROLE, ["read"], users)
    resp = client.post(
        "/admin/screenings/upload",
        data={"job_id": FAKE_ID, "screen": "false", "notify": "false"},
        files={"files": ("probe.txt", b"probe", "text/plain")},
        headers=_auth(raw),
    )
    assert resp.status_code == 403
    assert "只读" in resp.json()["message"]


def test_token_cannot_delete(users):
    """令牌一律不许 DELETE——删除是人的决定，不是 Agent 的。"""
    raw, _ = _issue(HR_ROLE, ["read", "write"], users)
    resp = client.delete(f"/admin/sessions/{FAKE_ID}", headers=_auth(raw))
    assert resp.status_code == 403
    assert "删除" in resp.json()["message"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/auth/users"),
        ("GET", "/admin/company-profile"),
        ("GET", "/admin/templates"),
        ("GET", "/admin/offers"),
        ("GET", "/admin/sessions"),
        ("GET", "/auth/tokens"),
    ],
)
def test_token_blocked_outside_allowlist(method, path, users):
    """白名单外的接口一律 403：账号管理、公司档案、Offer、会话全文都不该进 Agent 视野。"""
    raw, _ = _issue(ADMIN_ROLE, ["read", "write"], users)
    resp = client.request(method, path, headers=_auth(raw))
    assert resp.status_code == 403, f"{method} {path} 竟然返回 {resp.status_code}"
    assert "无权访问" in resp.json()["message"]


def test_token_cannot_issue_new_token(users):
    """令牌不能签发新令牌：否则只读令牌可「自我提权」到可写。"""
    raw, _ = _issue(HR_ROLE, ["read", "write"], users)
    resp = client.post(
        "/auth/tokens", json={"name": "self", "scopes": ["read", "write"]}, headers=_auth(raw)
    )
    assert resp.status_code == 403


def test_token_keeps_role_restrictions(users):
    """令牌只是「换个身份通道」，不放大角色权限：店长令牌仍进不了初筛榜。"""
    raw, _ = _issue(STORE_MANAGER_ROLE, ["read"], users)
    resp = client.get("/admin/screenings", headers=_auth(raw))
    assert resp.status_code == 403
    assert "无权" in resp.json()["message"]


def test_token_rejects_anonymous(users):
    """没有凭据必须 401，且不泄露内部细节。"""
    resp = client.get("/admin/jobs")
    assert resp.status_code == 401


def test_forged_token_rejected(users):
    """伪造令牌必须 401。"""
    resp = client.get("/admin/jobs", headers=_auth("ia_pat_forged_value_should_fail"))
    assert resp.status_code == 401


# ── 3. 生命周期 ────────────────────────────────────────────────
def test_revoked_token_rejected(users):
    """撤销后立即失效（HR 离职、换电脑的处置手段）。"""
    raw, token_id = _issue(HR_ROLE, ["read"], users)
    assert client.get("/admin/jobs", headers=_auth(raw)).status_code == 200

    db = SessionLocal()
    try:
        row = db.get(PersonalAccessToken, token_id)
        pat_service.revoke_token(db, row)
    finally:
        db.close()

    assert client.get("/admin/jobs", headers=_auth(raw)).status_code == 401


def test_expired_token_rejected(users):
    """过期后失效（不依赖定时任务，请求时惰性判定）。"""
    raw, token_id = _issue(HR_ROLE, ["read"], users)

    db = SessionLocal()
    try:
        row = db.get(PersonalAccessToken, token_id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    resp = client.get("/admin/jobs", headers=_auth(raw))
    assert resp.status_code == 401


def test_disabled_account_kills_tokens(users):
    """停用账号 → 该账号名下所有令牌立即失效（不需要逐个回收）。"""
    raw, _ = _issue(HR_ROLE, ["read"], users)
    assert client.get("/admin/jobs", headers=_auth(raw)).status_code == 200

    db = SessionLocal()
    try:
        user = db.get(AdminUser, users[HR_ROLE].id)
        user.is_active = False
        db.commit()
    finally:
        db.close()

    try:
        assert client.get("/admin/jobs", headers=_auth(raw)).status_code == 401
    finally:
        db = SessionLocal()
        try:
            user = db.get(AdminUser, users[HR_ROLE].id)
            user.is_active = True
            db.commit()
        finally:
            db.close()


def test_raw_token_never_stored(users):
    """库里只存 sha256，且无法从任何字段还原原文。"""
    raw, token_id = _issue(HR_ROLE, ["read"], users)

    db = SessionLocal()
    try:
        row = db.get(PersonalAccessToken, token_id)
        assert row.token_hash == pat_service.hash_token(raw)
        assert row.token_hash != raw
        assert len(row.token_hash) == 64
        # 前缀只留 12 位：够识别「是哪个令牌」，远不够冒用（后面还有 43 位随机）
        assert row.token_prefix == raw[:12]
        assert len(row.token_prefix) < len(raw) / 2
        # 全表扫描确认没有任何字段包含完整原文
        values = {str(v) for v in row.__dict__.values() if isinstance(v, str)}
        assert all(raw not in v for v in values)
    finally:
        db.close()


# ── 4. scope 规范化 ────────────────────────────────────────────
def test_write_scope_implies_read():
    """write 必须隐含 read：能写的人一定需要读，避免出现「能传不能查」的怪配置。"""
    assert pat_service.normalize_scopes(["write"]) == "read,write"
    assert pat_service.normalize_scopes(["read"]) == "read"
    assert pat_service.normalize_scopes([]) == "read"
    assert pat_service.normalize_scopes(None) == "read"


def test_invalid_scope_rejected():
    """非法 scope 抛 422，而不是静默忽略——静默会让调用方以为权限已生效。"""
    from app.exceptions import BizError

    with pytest.raises(BizError) as exc:
        pat_service.normalize_scopes(["read", "admin"])
    assert exc.value.code == 422


def test_expiry_bounds():
    """有效期上限 365 天，0 表示长期。"""
    from app.exceptions import BizError

    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.username == f"{PREFIX}hr").first()
        if user is None:  # 单独跑该用例时可能没有账号，建一个
            user = auth_service.create_user(
                db, username=f"{PREFIX}hr", password=STRONG_PASSWORD, role=HR_ROLE,
                created_by="pytest",
            )
        for bad in (-1, 366):
            with pytest.raises(BizError):
                pat_service.create_token(db, user, name="x", expires_days=bad)
    finally:
        db.close()
