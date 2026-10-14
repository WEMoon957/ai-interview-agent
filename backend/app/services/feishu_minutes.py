"""飞书妙记（Minutes）拉取服务：search 搜索 + 逐字稿导出 + 短码匹配。

设计要点
--------
- 复用 ``feishu_notify.get_tenant_access_token`` 的 tenant_access_token（bot 身份）缓存；
- 只做**读**操作：search / get transcript（二者均支持 bot 身份），不做写——妙记由店长在飞书里录制生成；
- 精确 Open API 路径与字段名以飞书官方文档为准，联调阶段用 ``lark-cli minutes +search --dry-run`` 复核。

身份与权限（bot / tenant_access_token，应用需开通对应 scope）：
- search       : ``minutes:minutes.search:read``
- transcript   : ``minutes:minutes.transcript:export``（导出妙记转写的文字内容，新应用请用此项）
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from typing import Any

from app.config import Settings, get_settings
from app.services.feishu_notify import get_tenant_access_token

logger = logging.getLogger(__name__)

_FEISHU_API_ROOT = "https://open.feishu.cn"

# 短码：6 位数字（店长填到妙记标题里，后端据此反查）
_WALK_IN_CODE_RE = re.compile(r"\b(\d{6})\b")
# 妙记 token：从 URL 末段提取（形如 /minutes/obcnxxx，token 长度 24）
_MINUTE_TOKEN_RE = re.compile(r"/minutes/([A-Za-z0-9_-]+)")

# search 每次最多取 30 条（lark-cli 口径）
_SEARCH_PAGE_SIZE = 30


def generate_walk_in_code() -> str:
    """生成 6 位数字短码（店长填到妙记标题里，后端据此匹配）。"""
    return f"{secrets.randbelow(1_000_000):06d}"


def extract_walk_in_code(title: str) -> str | None:
    """从妙记标题中提取 6 位数字短码；没有则返回 None。"""
    m = _WALK_IN_CODE_RE.search(title or "")
    return m.group(1) if m else None


def extract_minute_token(value: str) -> str | None:
    """从妙记 URL 或裸 token 中提取 minute_token（用于手动绑定）。"""
    v = (value or "").strip()
    m = _MINUTE_TOKEN_RE.search(v)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,64}", v):
        return v
    return None


def _minutes_post(path: str, body: dict, settings: Settings) -> tuple[bool, Any]:
    """以 bot 身份 POST 飞书 Open API，返回 (是否成功, data 或错误说明)。"""
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "妙记接口需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001
        return False, f"获取 tenant_access_token 失败：{exc}"

    try:
        resp = httpx.post(
            f"{_FEISHU_API_ROOT}{path}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.feishu_notify_timeout,
        )
    except httpx.HTTPError as exc:
        return False, f"请求失败：{exc}"

    try:
        payload = resp.json()
    except ValueError:
        return False, f"无法解析响应：{resp.text[:300]}"
    if payload.get("code") != 0:
        return False, f"code={payload.get('code')} msg={payload.get('msg')}"
    return True, payload.get("data") or {}


def search_minutes(
    owner_open_id: str,
    start_time: datetime,
    end_time: datetime,
    *,
    settings: Settings | None = None,
) -> tuple[bool, list[dict], str]:
    """搜索某店长在时间窗内拥有的妙记，返回 (是否成功, 妙记列表, 错误说明)。

    妙记列表每项为 dict，含 ``token`` / ``title`` / ``url`` / ``owner_id`` / ``create_time``。
    自动翻页取全；search 时间范围单次最多 1 个月。
    """
    settings = settings or get_settings()
    items: list[dict] = []
    page_token: str | None = None

    while True:
        body: dict[str, Any] = {
            "query": "",
            "owner_ids": [owner_open_id],
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "page_size": _SEARCH_PAGE_SIZE,
        }
        if page_token:
            body["page_token"] = page_token

        ok, data = _minutes_post("/open-apis/minutes/v1/minutes/search", body, settings)
        if not ok:
            return False, [], str(data)

        page_items = (data or {}).get("items") or []
        items.extend(page_items)

        has_more = bool((data or {}).get("has_more"))
        page_token = (data or {}).get("page_token") or None
        if not has_more or not page_token:
            break

    return True, items, ""


def get_transcript(
    minute_token: str,
    *,
    settings: Settings | None = None,
) -> tuple[bool, str, str]:
    """导出妙记逐字稿全文（txt），返回 (是否成功, 文本, 错误说明)。

    接口返回**文件二进制流**（非 JSON），``file_format=txt`` 得到纯文本逐字稿。
    """
    settings = settings or get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "", "妙记接口需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001
        return False, "", f"获取 tenant_access_token 失败：{exc}"

    path = f"/open-apis/minutes/v1/minutes/{minute_token}/transcript"
    try:
        resp = httpx.get(
            f"{_FEISHU_API_ROOT}{path}",
            params={"need_speaker": "true", "file_format": "txt"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.feishu_notify_timeout,
        )
    except httpx.HTTPError as exc:
        return False, "", f"请求失败：{exc}"

    if resp.status_code != 200:
        # 妙记未就绪 / 权限不足等都会走到这里，尽量带出可读信息
        snippet = resp.text[:300] if resp.text else ""
        return False, "", f"HTTP {resp.status_code}：{snippet}"

    try:
        text = resp.content.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return False, "", f"解码逐字稿失败：{exc}"

    if not text.strip():
        return False, "", "逐字稿为空"
    return True, text, ""
