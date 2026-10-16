"""飞书 access token 失效重试单测。

背景：`tenant_access_token` 在进程内缓存 2 小时。但缓存期内 token 仍可能被服务端
**提前作废**（应用重启、凭据轮换、多实例等），此时飞书返回 `99991663`，
旧代码直接判失败 —— 表现为「有时成功、有时失败」的间歇故障。
现在遇到失效码会**清缓存换新 token 重试一次**。

本文件覆盖：
- 失效码识别（含字符串形式与非法值）；
- 清缓存后状态归零；
- 卡片发送遇到失效码会重试，且重试成功后返回成功。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.services.feishu_notify as fn


def _settings(**kw):
    base = dict(
        feishu_app_id="cli_test",
        feishu_app_secret="secret",
        feishu_notify_transport="api",
        feishu_notify_timeout=5.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── 失效码识别 ──────────────────────────────────────────────
def test_is_token_invalid_code_accepts_int_and_str():
    assert fn.is_token_invalid_code(99991663) is True
    assert fn.is_token_invalid_code("99991663") is True
    assert fn.is_token_invalid_code(99991664) is True
    assert fn.is_token_invalid_code(99991668) is True


def test_is_token_invalid_code_rejects_others():
    assert fn.is_token_invalid_code(0) is False
    assert fn.is_token_invalid_code(None) is False
    assert fn.is_token_invalid_code("abc") is False
    assert fn.is_token_invalid_code(99992361) is False  # 跨应用 open_id，不是 token 问题


def test_invalidate_token_cache_resets_state():
    fn._TOKEN_CACHE["token"] = "cached-token"
    fn._TOKEN_CACHE["expires_at"] = 9_999_999_999.0
    fn.invalidate_token_cache()
    assert fn._TOKEN_CACHE["token"] == ""
    assert fn._TOKEN_CACHE["expires_at"] == 0.0


# ── 发送卡片：遇失效码自动重试 ──────────────────────────────
def _resp(code=0, msg="ok"):
    m = MagicMock()
    m.json.return_value = {"code": code, "msg": msg}
    return m


def test_send_card_retries_once_on_invalid_token():
    """第一次返回 99991663 → 刷新 token → 第二次成功。"""
    first = _resp(code=99991663, msg="Invalid access token")
    second = _resp(code=0)

    with (
        patch.object(fn, "get_tenant_access_token", side_effect=["old-token", "new-token"]),
        patch.object(fn, "invalidate_token_cache") as invalidate,
        patch("httpx.post", side_effect=[first, second]) as post,
    ):
        ok, err = fn.send_card_via_api(
            "ou_test", {"config": {}, "elements": []}, _settings()
        )

    assert ok is True
    assert err == ""
    assert invalidate.call_count == 1
    assert post.call_count == 2  # 重试了一次
    # 第二次用的是新 token
    assert "new-token" in post.call_args_list[1].kwargs["headers"]["Authorization"]


def test_send_card_fails_after_retry_still_invalid():
    """两次都失效 → 仍失败，但错误信息如实返回，且不会无限重试。"""
    invalid = _resp(code=99991663, msg="Invalid access token")

    with (
        patch.object(fn, "get_tenant_access_token", side_effect=["t1", "t2"]),
        patch.object(fn, "invalidate_token_cache"),
        patch("httpx.post", side_effect=[invalid, invalid]) as post,
    ):
        ok, err = fn.send_card_via_api(
            "ou_test", {"config": {}, "elements": []}, _settings()
        )

    assert ok is False
    assert "99991663" in err
    assert post.call_count == 2  # 只重试一次
