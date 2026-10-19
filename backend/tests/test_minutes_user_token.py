"""妙记个人授权（user_access_token）单测。

覆盖：
- token 可用性判定（无记录 / 撤销 / refresh 过期 / 关闭开关）；
- access_token 临近过期时自动刷新，且 refresh_token 轮换后回写；
- 搜索与导出逐字稿确实使用传入的 user token（而不是 bot token）；
- 设备授权轮询的 pending / denied / expired 状态映射；
- 对外序列化**不泄露** token 原文；
- 轮询取妙记归属人的优先级（记录上的 open_id > 全局兜底）。
"""
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import app.services.feishu_minutes as fm
import app.services.minutes_auth as ma
import app.services.minutes_poller as mp


def _settings(**kw):
    base = dict(
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        feishu_notify_timeout=5.0,
        feishu_minutes_user_token_enabled=True,
        feishu_minutes_oauth_scopes="",
        feishu_minutes_default_owner_open_id="",
        feishu_minutes_search_window_days=7,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Query:
    def __init__(self, row):
        self._row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._row


class _DB:
    def __init__(self, row=None):
        self.row = row
        self.commits = 0

    def query(self, _model):
        return _Query(self.row)

    def add(self, row):
        self.row = row

    def commit(self):
        self.commits += 1

    def refresh(self, _row):
        return None


def _auth_row(**kw):
    now = datetime.now(timezone.utc)
    base = dict(
        open_id="ou_a",
        name="赵江荷",
        access_token="u-old",
        refresh_token="ur-old",
        scope="minutes:minutes.search:read",
        expires_at=now + timedelta(hours=1),
        refresh_expires_at=now + timedelta(days=20),
        status="active",
        last_refreshed_at=now,
        authorized_by="admin001",
        updated_at=now,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── 可用性判定 ──────────────────────────────────────────────
def test_no_row_returns_none():
    assert ma.get_valid_user_token(_DB(None), "ou_a", _settings()) is None


def test_disabled_switch_returns_none():
    row = _auth_row()
    assert (
        ma.get_valid_user_token(_DB(row), "ou_a", _settings(feishu_minutes_user_token_enabled=False))
        is None
    )


def test_revoked_returns_none():
    row = _auth_row(status="revoked")
    assert ma.get_valid_user_token(_DB(row), "ou_a", _settings()) is None


def test_refresh_expired_marks_expired():
    row = _auth_row(refresh_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    db = _DB(row)
    assert ma.get_valid_user_token(db, "ou_a", _settings()) is None
    assert row.status == "expired"
    assert db.commits == 1


def test_valid_token_returned_without_refresh():
    row = _auth_row(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    with patch.object(ma, "_post_form") as post:
        token = ma.get_valid_user_token(_DB(row), "ou_a", _settings())
    assert token == "u-old"
    post.assert_not_called()


def test_near_expiry_refreshes_and_rotates_refresh_token():
    row = _auth_row(expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    new_payload = {
        "code": 0,
        "access_token": "u-new",
        "refresh_token": "ur-new",
        "expires_in": 7200,
        "refresh_expires_in": 2592000,
    }
    with patch.object(ma, "_post_form", return_value=(True, new_payload, "")) as post:
        token = ma.get_valid_user_token(_DB(row), "ou_a", _settings())

    assert token == "u-new"
    assert row.access_token == "u-new"
    assert row.refresh_token == "ur-new"  # 轮换后的新值必须回写
    assert row.status == "active"
    assert post.call_count == 1
    assert post.call_args.args[1]["grant_type"] == "refresh_token"


def test_refresh_invalid_grant_marks_expired():
    row = _auth_row(expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    with patch.object(ma, "_post_form", return_value=(True, {"code": 20038, "msg": "invalid"}, "")):
        assert ma.get_valid_user_token(_DB(row), "ou_a", _settings()) is None
    assert row.status == "expired"


# ── 使用 user token 调用妙记接口 ────────────────────────────
def _dt():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now


def test_search_minutes_uses_user_token_and_code_query():
    captured = {}

    def fake_post(path, body, settings, access_token=None):
        captured["path"] = path
        captured["body"] = body
        captured["token"] = access_token
        return True, {"items": [], "has_more": False}

    start, end = _dt()
    with patch.object(fm, "_minutes_post", side_effect=fake_post):
        ok, items, err = fm.search_minutes(
            "ou_a", start, end, settings=_settings(), access_token="u-1", query="955734"
        )

    assert ok and items == [] and err == ""
    assert captured["token"] == "u-1"
    assert captured["body"]["query"] == "955734"
    assert captured["body"]["owner_ids"] == ["ou_a"]


class _Resp:
    status_code = 200
    content = "说话人1：你好\n说话人2：你好".encode()
    text = ""


def test_get_transcript_uses_user_token():
    with patch("httpx.get", return_value=_Resp()) as get:
        ok, text, err = fm.get_transcript("obcnx", settings=_settings(), access_token="u-1")

    assert ok and text and err == ""
    assert "Bearer u-1" in get.call_args.kwargs["headers"]["Authorization"]


# ── 设备授权状态映射 ────────────────────────────────────────
def _seed_session(session_id="s1", ttl=600):
    ma._DEVICE_SESSIONS[session_id] = {
        "device_code": "d1",
        "interval": 5,
        "authorized_by": "admin001",
        "created_at": time.time(),
        "expires_at": time.time() + ttl,
    }


def test_poll_device_pending():
    _seed_session()
    payload = {"code": 20001, "error": "authorization_pending"}
    with patch.object(ma, "_post_form", return_value=(True, payload, "")):
        status, auth, _ = ma.poll_device_authorization(_DB(), "s1", _settings())
    assert status == "pending" and auth is None


def test_poll_device_denied():
    _seed_session()
    payload = {"code": 20002, "error": "access_denied"}
    with patch.object(ma, "_post_form", return_value=(True, payload, "")):
        status, auth, _ = ma.poll_device_authorization(_DB(), "s1", _settings())
    assert status == "denied" and auth is None
    assert "s1" not in ma._DEVICE_SESSIONS


def test_poll_device_expired():
    _seed_session(ttl=-1)
    status, auth, _ = ma.poll_device_authorization(_DB(), "s1", _settings())
    assert status == "expired" and auth is None


def test_poll_device_success_saves_authorization():
    _seed_session()
    token_payload = {
        "code": 0,
        "access_token": "u-new",
        "refresh_token": "ur-new",
        "expires_in": 7200,
        "refresh_expires_in": 2592000,
        "scope": "minutes:minutes.search:read",
    }
    row = _auth_row(open_id="ou_a", name="赵江荷")
    db = _DB(row)
    with (
        patch.object(ma, "_post_form", return_value=(True, token_payload, "")),
        patch.object(ma, "fetch_user_info", return_value=(True, {"open_id": "ou_a", "name": "赵江荷"}, "")),
    ):
        status, auth, err = ma.poll_device_authorization(db, "s1", _settings())

    assert status == "success" and err == ""
    assert auth["open_id"] == "ou_a"
    assert "access_token" not in auth and "refresh_token" not in auth
    assert row.access_token == "u-new" and row.refresh_token == "ur-new"


# ── 序列化不泄露 token ─────────────────────────────────────
def test_serialize_excludes_tokens():
    data = ma.serialize_authorization(_auth_row())
    assert "access_token" not in data
    assert "refresh_token" not in data
    assert data["open_id"] == "ou_a"


# ── 归属人优先级与转写使用 user token ───────────────────────
def test_owner_open_id_prefers_record_then_default():
    s = _settings(feishu_minutes_default_owner_open_id="ou_default")
    assert mp._owner_open_id(SimpleNamespace(interviewer_open_id="ou_record"), s) == "ou_record"
    assert mp._owner_open_id(SimpleNamespace(interviewer_open_id=""), s) == "ou_default"


def test_resolve_owner_token_falls_back_to_single_active_auth():
    """过渡期：只开了一个人的通道时，无归属人的记录也能用该授权。"""
    row = SimpleNamespace(interviewer_open_id="")
    auth = _auth_row(open_id="ou_hr", status="active")
    with (
        patch.object(
            mp,
            "get_valid_user_token",
            side_effect=lambda _db, oid, _s: "u-1" if oid == "ou_hr" else None,
        ),
        patch.object(mp, "list_authorizations", return_value=[auth]),
    ):
        owner, token = mp._resolve_owner_token(_DB(), row, _settings())
    assert owner == "ou_hr" and token == "u-1"


def test_resolve_owner_token_does_not_guess_when_multiple_auths():
    """多个授权且记录无归属人时不猜，避免读错人的妙记。"""
    row = SimpleNamespace(interviewer_open_id="")
    authed = [_auth_row(open_id="ou_a"), _auth_row(open_id="ou_b")]
    with (
        patch.object(mp, "get_valid_user_token", return_value=None),
        patch.object(mp, "list_authorizations", return_value=authed),
    ):
        owner, token = mp._resolve_owner_token(_DB(), row, _settings())
    assert owner == "" and token is None


def test_transcribe_and_score_passes_owner_user_token():
    row = SimpleNamespace(
        minutes_token="obcn",
        interviewer_open_id="ou_a",
        transcript_status="matched",
        transcript_text=None,
        transcript_at=None,
        job=None,
        job_id=None,
        ai_score=None,
        ai_dim_scores=[],
        ai_comment="",
        ai_scored_at=None,
    )
    db = _DB()
    with (
        patch.object(mp, "get_valid_user_token", return_value="u-1") as get_token,
        patch.object(mp, "get_transcript", return_value=(True, "逐字稿", "")) as get_transcript,
        patch.object(mp, "score_whole_interview", return_value={"total_score": None}),
    ):
        assert mp.transcribe_and_score(db, row, _settings()) is True

    get_token.assert_called_once()
    assert get_transcript.call_args.kwargs["access_token"] == "u-1"
    assert row.transcript_status == "transcribed"
    assert row.transcript_text == "逐字稿"
