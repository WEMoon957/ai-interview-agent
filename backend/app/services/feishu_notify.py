"""飞书 HR 通知：面试达标（综合分 >= 阈值）后自动推送给 HR。

设计要点
--------
- **与主流程解耦**：纯函数（阈值判定 / 消息构造）与传输层分离，传输以参数注入，
  便于单测与替换；发送失败绝不影响面试收尾（调用方 try/except 兜底）。
- **两种传输**（``FEISHU_NOTIFY_TRANSPORT``）：
  * ``cli``（默认）—— 复用本机已登录的 ``lark-cli``，无需把应用密钥写进 ``.env``；
  * ``api`` —— 直接调飞书 Open API（``tenant_access_token``），适合长期部署的服务端。
- **幂等**：由调用方依据 ``session_reports.notified_at`` 保证同一报告只推一次；
  本模块提供 ``notify_report_qualified`` 完成「判定 -> 发送 -> 置位」的闭环。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 传输函数签名：(open_id, text, settings) -> (是否成功, 错误说明)
Sender = Callable[[str, str, Settings], "tuple[bool, str]"]

# 飞书 Open API 基址（tenant_access_token 与消息发送）
_FEISHU_OPEN_API = "https://open.feishu.cn/open-apis"


# ────────────────────────────── 纯函数：判定与消息构造 ──────────────────────────────


def should_notify(total_score: Any, threshold: float) -> bool:
    """综合分是否达到推送阈值（非数值一律视为不达标）。"""
    try:
        return float(total_score) >= float(threshold)
    except (TypeError, ValueError):
        return False


def build_notification_text(
    candidate_name: str,
    session_id: str,
    report: dict,
    threshold: float,
) -> str:
    """构造推送给 HR 的 Markdown 文案。

    Args:
        candidate_name: 候选人姓名（缺失时展示占位）。
        session_id: 面试会话 id，便于 HR 回溯报告。
        report: 报告 dict，含 total_score / verdict / summary / dim_scores。
        threshold: 本次触发阈值，用于文案中交代触发口径。

    Returns:
        Markdown 文本（lark-cli 的 ``--markdown`` 会自动包装为富文本卡片）。
    """
    total = report.get("total_score")
    verdict = report.get("verdict") or "—"
    summary = report.get("summary") or ""
    dims = report.get("dim_scores") or []

    lines = [
        "**面试达标 · 建议推进下一轮**",
        "",
        f"**候选人**：{candidate_name or '（未填写姓名）'}",
        f"**综合评分**：{total} / 100",
        f"**录用建议**：{verdict}",
        f"**触发口径**：综合分 ≥ {threshold}",
        f"**面试会话**：{session_id}",
    ]

    if dims:
        lines += ["", "**维度得分**"]
        for dim in dims:
            name = dim.get("name") or dim.get("key") or "维度"
            lines.append(f"- {name}：{dim.get('score')}")

    if summary:
        lines += ["", f"**总体评价**：{summary}"]

    lines += ["", "> 请 HR 及时安排下一轮面试。"]
    return "\n".join(lines)


# ────────────────────────────── 传输：lark-cli（默认） ──────────────────────────────


def resolve_cli_path(configured: str = "") -> str | None:
    """定位 lark-cli 可执行文件。

    优先级：显式配置 > PATH > 常见受管安装目录（``~/.workbuddy/binaries/node/versions/*/bin``）。
    后端由 launchd 托管时 PATH 不含 node 安装目录，故此处必须能兜底定位。
    """
    if configured and Path(configured).exists():
        return configured

    found = shutil.which("lark-cli")
    if found:
        return found

    for candidate in sorted(
        Path.home().glob(".workbuddy/binaries/node/versions/*/bin/lark-cli")
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _cli_env(cli_path: str) -> dict:
    """构造子进程环境：把 lark-cli 同目录（含 node）注入 PATH。

    lark-cli 是 ``#!/usr/bin/env node`` 脚本，launchd 下 PATH 无 node，
    直接调用会因 shebang 找不到 node 而失败，故显式补 PATH。
    """
    env = os.environ.copy()
    bin_dir = os.path.dirname(cli_path)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    # 抑制 CLI 的升级/技能更新提示，保持输出稳定
    env.setdefault("LARKSUITE_CLI_NO_UPDATE_NOTIFIER", "1")
    env.setdefault("LARKSUITE_CLI_NO_SKILLS_NOTIFIER", "1")
    return env


def send_via_cli(open_id: str, text: str, settings: Settings) -> tuple[bool, str]:
    """通过本机 lark-cli 以 bot 身份向 HR 发送 Markdown 消息。"""
    cli = resolve_cli_path(settings.feishu_lark_cli_path)
    if not cli:
        return False, "未找到 lark-cli 可执行文件，请配置 FEISHU_LARK_CLI_PATH"

    cmd = [
        cli,
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--user-id",
        open_id,
        "--markdown",
        text,
        "--json",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.feishu_notify_timeout,
            env=_cli_env(cli),
        )
    except subprocess.TimeoutExpired:
        return False, f"lark-cli 调用超时（>{settings.feishu_notify_timeout}s）"
    except OSError as exc:
        return False, f"lark-cli 调用失败：{exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"lark-cli 退出码 {proc.returncode}：{detail[:500]}"

    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return False, f"无法解析 lark-cli 输出：{(proc.stdout or '')[:300]}"

    # 契约：成功信封 ok == true（不是 code == 0）
    if payload.get("ok") is True:
        return True, ""
    error = payload.get("error") or payload
    return False, json.dumps(error, ensure_ascii=False)[:500]


# ────────────────────────────── 传输：飞书 Open API ──────────────────────────────

def _get_tenant_access_token(settings: Settings) -> str:
    """获取 tenant_access_token（自建应用）。失败抛 RuntimeError。"""
    import httpx

    resp = httpx.post(
        f"{_FEISHU_OPEN_API}/auth/v3/tenant_access_token/internal",
        json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        timeout=settings.feishu_notify_timeout,
    )
    data = resp.json()
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise RuntimeError(f"获取 tenant_access_token 失败：{data.get('msg') or data}")
    return data["tenant_access_token"]


def send_via_api(open_id: str, text: str, settings: Settings) -> tuple[bool, str]:
    """通过飞书 Open API 以 bot 身份向 HR 发送 Markdown 卡片消息。"""
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "api 传输需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = _get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001 - 统一降级为失败说明
        return False, str(exc)

    # 用交互卡片承载 Markdown，与 lark-cli 的 --markdown 观感保持一致
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
    }

    try:
        resp = httpx.post(
            f"{_FEISHU_OPEN_API}/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": open_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            timeout=settings.feishu_notify_timeout,
        )
    except httpx.HTTPError as exc:
        return False, f"发送请求失败：{exc}"

    data = resp.json()
    if data.get("code") == 0:
        return True, ""
    return False, f"发送失败 code={data.get('code')} msg={data.get('msg')}"


def default_sender(open_id: str, text: str, settings: Settings) -> tuple[bool, str]:
    """按配置选择传输方式发送。"""
    transport = (settings.feishu_notify_transport or "cli").lower()
    if transport == "api":
        return send_via_api(open_id, text, settings)
    return send_via_cli(open_id, text, settings)


# ────────────────────────────── 部门成员解析（按部门收件） ──────────────────────────────


def _run_cli_api(path: str, params: dict, settings: Settings) -> tuple[bool, Any]:
    """以 bot 身份调用任意飞书 Open API（走 lark-cli 的 api 逃生口）。

    Returns:
        (是否成功, 成功时返回 data 部分 / 失败时返回错误说明字符串)
    """
    cli = resolve_cli_path(settings.feishu_lark_cli_path)
    if not cli:
        return False, "未找到 lark-cli 可执行文件"

    cmd = [
        cli, "api", "GET", path,
        "--params", json.dumps(params, ensure_ascii=False),
        "--as", "bot", "--json",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=settings.feishu_notify_timeout, env=_cli_env(cli),
        )
    except subprocess.TimeoutExpired:
        return False, f"lark-cli 调用超时（>{settings.feishu_notify_timeout}s）"
    except OSError as exc:
        return False, f"lark-cli 调用失败：{exc}"

    if proc.returncode != 0:
        return False, f"lark-cli 退出码 {proc.returncode}：{(proc.stderr or proc.stdout or '')[:500]}"
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return False, f"无法解析 lark-cli 输出：{(proc.stdout or '')[:300]}"

    if payload.get("ok") is not True:
        return False, json.dumps(payload.get("error") or payload, ensure_ascii=False)[:500]
    return True, payload.get("data") or {}


def is_active_member(item: dict) -> bool:
    """是否为在职可用成员（过滤离职 / 退出 / 冻结 / 未激活）。"""
    status = item.get("status") or {}
    if status.get("is_resigned") or status.get("is_exited") or status.get("is_frozen"):
        return False
    # 字段缺失时按「可用」处理，避免因权限字段裁剪而漏发
    return status.get("is_activated", True) is not False


def _fetch_department_users(
    department_id: str, page_token: str, settings: Settings
) -> tuple[bool, Any]:
    """拉取某部门的成员分页。

    - ``api`` 传输：直接调飞书 Open API（**服务端部署推荐**，不依赖本机 lark-cli）；
    - ``cli`` 传输：复用 lark-cli 的 api 逃生口（本机 macOS 场景）。

    Returns:
        (是否成功, 成功时返回 data 对象 / 失败时返回错误说明)。
    """
    params = {
        "department_id": department_id,
        "department_id_type": "open_department_id",
        "page_size": 50,
    }
    if page_token:
        params["page_token"] = page_token

    if (settings.feishu_notify_transport or "cli").lower() != "api":
        return _run_cli_api("/open-apis/contact/v3/users/find_by_department", params, settings)

    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "api 传输需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = _get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001 - 统一降级为失败说明
        return False, str(exc)

    try:
        resp = httpx.get(
            f"{_FEISHU_OPEN_API}/contact/v3/users/find_by_department",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.feishu_notify_timeout,
        )
    except httpx.HTTPError as exc:
        return False, f"请求失败：{exc}"

    payload = resp.json()
    if payload.get("code") == 0:
        return True, payload.get("data") or {}
    return False, f"code={payload.get('code')} msg={payload.get('msg')}"


def resolve_department_member_ids(
    department_ids: list[str], settings: Settings
) -> tuple[list[str], str]:
    """按部门（open_department_id）解析成员 open_id，自动翻页。

    Returns:
        (open_id 列表, 错误汇总串)。单个部门失败不阻断其它部门。
    """
    open_ids: list[str] = []
    errors: list[str] = []

    for dep_id in department_ids:
        page_token = ""
        while True:
            ok, data = _fetch_department_users(dep_id, page_token, settings)
            if not ok:
                errors.append(f"部门 {dep_id} 解析失败：{data}")
                break

            for item in data.get("items") or []:
                if is_active_member(item) and item.get("open_id"):
                    open_ids.append(item["open_id"])

            if data.get("has_more") and data.get("page_token"):
                page_token = data["page_token"]
            else:
                break

    return open_ids, "; ".join(errors)


def resolve_recipients(settings: Settings | None = None) -> tuple[list[str], str]:
    """汇总收件人：显式 open_id + 部门解析成员，去重保序。"""
    settings = settings or get_settings()
    recipients = list(settings.hr_open_id_list)
    errors: list[str] = []

    if settings.hr_department_id_list:
        dept_ids, err = resolve_department_member_ids(settings.hr_department_id_list, settings)
        recipients.extend(dept_ids)
        if err:
            errors.append(err)

    seen: set[str] = set()
    unique: list[str] = []
    for open_id in recipients:
        if open_id and open_id not in seen:
            seen.add(open_id)
            unique.append(open_id)
    return unique, "; ".join(errors)


# ────────────────────────────── 编排：达标 -> 发送 -> 置位 ──────────────────────────────


def send_notification(
    text: str,
    *,
    settings: Settings | None = None,
    sender: Sender | None = None,
) -> tuple[bool, list[str], str]:
    """向所有配置的 HR 收件人发送；任一人成功即视为成功。

    Returns:
        (是否至少一人成功, 收件人列表, 错误汇总)
    """
    settings = settings or get_settings()
    recipients, resolve_err = resolve_recipients(settings)
    if not recipients:
        return False, [], resolve_err or (
            "未解析到任何 HR 收件人（请检查 FEISHU_HR_OPEN_IDS / FEISHU_HR_DEPARTMENT_IDS）"
        )

    send = sender or default_sender
    ok_any = False
    errors: list[str] = []
    for open_id in recipients:
        try:
            ok, err = send(open_id, text, settings)
        except Exception as exc:  # noqa: BLE001 - 单个收件人失败不拖垮整体
            ok, err = False, repr(exc)
        if ok:
            ok_any = True
        else:
            errors.append(f"{open_id}: {err}")
    if resolve_err:
        errors.insert(0, resolve_err)
    return ok_any, recipients, "; ".join(errors)


def notify_report_qualified(
    report_row: Any,
    *,
    session_id: str,
    candidate_name: str = "",
    settings: Settings | None = None,
    sender: Sender | None = None,
    now: datetime | None = None,
) -> dict:
    """达标则推送并置位 ``notified_at``；调用方负责 commit。

    Args:
        report_row: ``SessionReport`` ORM 行（含 total_score/verdict/summary/dim_scores/notified_at）。
        session_id: 会话 id（用于文案与幂等定位）。
        candidate_name: 候选人姓名。
        settings / sender / now: 便于注入测试。

    Returns:
        结果 dict：``{"status": "sent"|"skipped"|"failed", ...}``。绝不抛异常。
    """
    settings = settings or get_settings()

    if not settings.feishu_notify_enabled:
        return {"status": "skipped", "reason": "通知未启用（FEISHU_NOTIFY_ENABLED=false）"}

    if getattr(report_row, "notified_at", None) is not None:
        return {"status": "skipped", "reason": "该报告已推送过（幂等）"}

    total = getattr(report_row, "total_score", None)
    if not should_notify(total, settings.feishu_notify_threshold):
        return {
            "status": "skipped",
            "reason": f"综合分 {total} 未达阈值 {settings.feishu_notify_threshold}",
        }

    report = {
        "total_score": total,
        "verdict": getattr(report_row, "verdict", ""),
        "summary": getattr(report_row, "summary", ""),
        "dim_scores": getattr(report_row, "dim_scores", []) or [],
    }
    text = build_notification_text(
        candidate_name, session_id, report, settings.feishu_notify_threshold
    )

    ok, recipients, err = send_notification(text, settings=settings, sender=sender)
    if ok:
        report_row.notified_at = now or datetime.now(timezone.utc)
        return {"status": "sent", "recipients": recipients}

    return {"status": "failed", "reason": err or "未知错误", "recipients": recipients}
