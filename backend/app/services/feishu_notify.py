"""飞书 HR 通知：面试达标 / 简历初筛推荐 / 招聘汇总复核 三类自动推送。

设计要点
--------
- **与主流程解耦**：纯函数（阈值判定 / 卡片构造）与传输层分离，传输以参数注入，
  便于单测与替换；发送失败绝不影响主流程（调用方 try/except 兜底）。
- **两种传输**（``FEISHU_NOTIFY_TRANSPORT``）：
  * ``cli``（默认）—— 复用本机已登录的 ``lark-cli``，无需把应用密钥写进 ``.env``；
  * ``api`` —— 直接调飞书 Open API（``tenant_access_token``），适合长期部署的服务端。
  发送与部门成员解析**都**跟随该开关，因此切到 ``api`` 后不再依赖本机 lark-cli/node。
- **消息形态**：文本版（Markdown）用于 AI 面试达标通知；**卡片版**（interactive，
  带彩色标题栏）用于简历初筛推荐与招聘汇总复核，两种传输都能发真正的卡片。
- **幂等**：AI 面试通知由 ``session_reports.notified_at`` 保证；
  汇总复核通知由 ``recruitment_summaries.notified_at`` 保证（逐候选人一次）。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 传输函数签名：(open_id, text, settings) -> (是否成功, 错误说明)
Sender = Callable[[str, str, Settings], "tuple[bool, str]"]

# 飞书 Open API 基址。注意：这里存的是域名根，
# 各调用点统一拼「完整路径」（含 /open-apis 前缀），避免二次拼接导致 404。
_FEISHU_API_ROOT = "https://open.feishu.cn"

# tenant_access_token 进程内缓存（提前 5 分钟过期，避免临界失效）
_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()
_TOKEN_SAFETY_MARGIN = 300


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

def _fetch_tenant_access_token(settings: Settings) -> str:
    """向飞书换取 tenant_access_token（自建应用）。失败抛 RuntimeError。"""
    import httpx

    resp = httpx.post(
        f"{_FEISHU_API_ROOT}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        timeout=settings.feishu_notify_timeout,
    )
    data = resp.json()
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise RuntimeError(f"获取 tenant_access_token 失败：{data.get('msg') or data}")
    return data["tenant_access_token"]


def get_tenant_access_token(settings: Settings) -> str:
    """带进程内缓存的 tenant_access_token（默认有效期 2h，提前 5 分钟过期）。"""
    now = time.time()
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get("token") or ""
        expires_at = float(_TOKEN_CACHE.get("expires_at") or 0.0)
        if cached and now < expires_at:
            return cached

    token = _fetch_tenant_access_token(settings)
    with _TOKEN_LOCK:
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expires_at"] = now + 7200 - _TOKEN_SAFETY_MARGIN
    return token


def _api_get(path: str, params: dict, settings: Settings) -> tuple[bool, Any]:
    """以 bot 身份直接调飞书 Open API（GET），返回 (是否成功, data 或错误说明)。"""
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "api 传输需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

    try:
        resp = httpx.get(
            f"{_FEISHU_API_ROOT}{path}",
            params=params,
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


def send_via_api(open_id: str, text: str, settings: Settings) -> tuple[bool, str]:
    """通过飞书 Open API 以 bot 身份向 HR 发送 Markdown 卡片消息。"""
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "api 传输需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001 - 统一降级为失败说明
        return False, str(exc)

    # 用交互卡片承载 Markdown，与 lark-cli 的 --markdown 观感保持一致
    card = {
        "config": {"wide_screen_mode": True},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
    }

    try:
        resp = httpx.post(
            f"{_FEISHU_API_ROOT}/open-apis/im/v1/messages",
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

    path = "/open-apis/contact/v3/users/find_by_department"
    if (settings.feishu_notify_transport or "cli").lower() == "api":
        return _api_get(path, params, settings)
    return _run_cli_api(path, params, settings)


def resolve_department_member_ids(
    department_ids: list[str], settings: Settings
) -> tuple[list[str], str]:
    """按部门（open_department_id）解析成员 open_id，自动翻页。

    传输方式跟随 ``FEISHU_NOTIFY_TRANSPORT``：``api`` 时直连飞书 Open API
    （不依赖本机 lark-cli，可在服务器/容器内运行）；``cli`` 时走 lark-cli。

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


# ══════════════════════════ 卡片：构造 / 传输 / 编排 ══════════════════════════
#
# 用于两个新环节：
#   1) 简历初筛完成 -> 把「推荐进入 AI 面试」的人以卡片推给 HR；
#   2) 招聘汇总完成 -> 提醒 HR 复核并办理 offer 发放 / 入职。
# 卡片用 interactive + 彩色标题栏，cli 与 api 两种传输都能发。

# 卡片传输函数签名：(open_id, card, settings) -> (是否成功, 错误说明)
CardSender = Callable[[str, dict, Settings], "tuple[bool, str]"]


def build_card(
    title: str,
    template: str,
    blocks: list[str],
    actions: list[dict] | None = None,
) -> dict:
    """构造飞书交互卡片（彩色标题栏 + 若干 Markdown 段落 + 可选按钮区）。

    Args:
        title: 卡片标题（plain_text）。
        template: 标题栏配色，如 blue / orange / green / red。
        blocks: 段落内容列表（lark_md），段落之间自动加分隔线。
        actions: 可选的按钮元素列表（由 :func:`build_button` 构造）；
            为空时卡片结构与历史行为完全一致（纯文本卡片）。

    说明：按钮点击后由飞书回调我方「卡片请求网址」（见 Offer 审批流），
    需要应用侧配置回调地址且为公网 HTTPS。
    """
    elements: list[dict] = []
    for idx, block in enumerate(blocks):
        if idx:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": block}})

    if actions:
        elements.append({"tag": "hr"})
        elements.append({"tag": "action", "actions": actions})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def build_button(text: str, value: dict, *, button_type: str = "default") -> dict:
    """构造卡片按钮元素。

    Args:
        text: 按钮文案（如「同意」「驳回」）。
        value: 点击后随回调事件回传的业务参数（如 ``{"offer_id": ..., "action": "approve"}``）。
        button_type: 飞书按钮样式：primary / danger / default。
    """
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
        "value": dict(value or {}),
    }


def card_to_markdown(card: dict) -> str:
    """把卡片降级为纯 Markdown 文本（用于 dry-run 预览与日志）。"""
    title = (card.get("header") or {}).get("title", {}).get("content", "")
    lines = [f"【{title}】", ""]
    for el in card.get("elements") or []:
        if el.get("tag") == "div":
            lines.append((el.get("text") or {}).get("content", ""))
            lines.append("")
        elif el.get("tag") == "action":
            labels = [
                (item.get("text") or {}).get("content", "")
                for item in (el.get("actions") or [])
            ]
            labels = [label for label in labels if label]
            if labels:
                lines.append("【按钮】" + " / ".join(labels))
                lines.append("")
    return "\n".join(lines).strip()


def _fmt_score(value: Any) -> str:
    """分数字符串化（None 显示为 —）。"""
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def _cap(items: list, limit: int) -> list:
    """按上限截断列表（避免卡片过长）。"""
    return items[: max(1, int(limit or 20))]


def looks_like_local_base(url: str) -> bool:
    """对外基址是否仍是「本机地址」（会导致 HR/候选人点开链接失败）。

    服务器部署时若忘记把 ``PUBLIC_BASE_URL`` 改成公网地址，卡片里会生成
    ``http://localhost:3000/i/xxx`` —— 在别人机器上指向他们自己的电脑，必然打不开。
    这里显式识别，便于上层告警而不是静默发出坏链接。
    """
    u = (url or "").strip().lower()
    if not u:
        return True  # 空基址 -> 只能输出相对路径，同样不可用
    return any(tok in u for tok in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def diagnose_notify_config(settings: Settings | None = None) -> list[str]:
    """启动自检：返回飞书通知配置中「会导致推送静默失败」的可疑项。

    典型场景：服务器容器里没有 lark-cli，但仍沿用默认的 ``cli`` 传输 ——
    推送会全部失败且只记日志，运营侧表现为「说好的自动推送怎么没来」。
    这里在启动时把问题显式喊出来。
    """
    settings = settings or get_settings()
    if not settings.feishu_notify_enabled:
        return []  # 总开关关闭：属于预期，不告警

    warnings: list[str] = []
    transport = (settings.feishu_notify_transport or "cli").lower()

    if not settings.hr_open_id_list and not settings.hr_department_id_list:
        warnings.append(
            "飞书通知已启用但未配置收件人（FEISHU_HR_OPEN_IDS / FEISHU_HR_DEPARTMENT_IDS），推送会被跳过"
        )

    if transport == "cli":
        if resolve_cli_path(settings.feishu_lark_cli_path) is None:
            warnings.append(
                "传输方式为 cli 但当前环境找不到 lark-cli（容器/服务器常见），"
                "所有飞书推送都会失败；请改用 FEISHU_NOTIFY_TRANSPORT=api 并配置 "
                "FEISHU_APP_ID / FEISHU_APP_SECRET"
            )
    elif not (settings.feishu_app_id and settings.feishu_app_secret):
        warnings.append(
            "传输方式为 api 但缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，推送会失败"
        )

    if looks_like_local_base(settings.public_base_url_normalized):
        warnings.append(
            f"PUBLIC_BASE_URL 仍为本机地址（{settings.public_base_url}），"
            "卡片/待办里的面试链接在 HR、候选人机器上打不开；请改成公网地址"
        )
    return warnings


def build_screening_card(
    *,
    job_title: str,
    folder_name: str,
    total: int,
    succeeded: int,
    recommended: list[dict],
    min_score: float,
    max_items: int = 20,
    base_url: str = "",
) -> dict:
    """构造「简历初筛完成 · 推荐进入 AI 面试」卡片。

    Args:
        recommended: 达标候选人（含 candidate_name / total_score / recommendation /
            highlights / interview_path）。带 ``interview_path`` 时会在卡片里附上答题链接。
        base_url: 对外基址，用于把 ``/i/{token}`` 拼成候选人可直接打开的绝对链接。
    """
    head = "\n".join(
        [
            f"**岗位**：{job_title or '—'}",
            f"**简历来源**：{folder_name or '岗位文件夹'}",
            f"**本次上传**：{total} 份（初筛成功 {succeeded} 份）",
            f"**推荐进入 AI 面试**：{len(recommended)} 人（初筛分 ≥ {min_score:g}）",
        ]
    )

    def _abs_url(path: str) -> str:
        """把前端路由路径拼成绝对链接（已是绝对地址则原样返回）。"""
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base = (base_url or "").rstrip("/")
        return f"{base}{path}" if base else path

    has_link = False
    lines = []
    for idx, item in enumerate(_cap(recommended, max_items), start=1):
        name = item.get("candidate_name") or "（未填写姓名）"
        line = (
            f"{idx}. **{name}** — {_fmt_score(item.get('total_score'))} 分"
            f"（{item.get('recommendation') or '—'}）"
        )
        highlights = item.get("highlights") or []
        if highlights:
            line += f"\n   亮点：{'；'.join(str(h) for h in highlights[:2])}"
        url = _abs_url(item.get("interview_path") or "")
        if url:
            has_link = True
            line += f"\n   AI 面试链接：{url}"
        lines.append(line)
    if len(recommended) > max_items:
        lines.append(f"… 另有 {len(recommended) - max_items} 人未列出")

    tail = (
        "> 以上链接已生成，可直接转发候选人答题；同步已为 HR 创建待办跟进。"
        if has_link
        else "> 请 HR 在后台「简历初筛」页勾选候选人，批量发起 AI 面试链接。"
    )

    return build_card(
        "简历初筛完成 · 推荐进入 AI 面试",
        "blue",
        [head, "**推荐名单**\n" + "\n".join(lines), tail],
    )


def build_summary_card(
    *,
    job_title: str,
    offer_items: list[dict],
    review_items: list[dict],
    snapshot_at: str = "",
    max_items: int = 20,
) -> dict:
    """构造「招聘汇总完成 · 请复核并办理 offer / 入职」卡片。

    Args:
        offer_items: 建议发放 offer（强烈推荐 / 推荐）。
        review_items: 建议复核后决定（待定 / 不推荐）。
    """

    def _render(items: list[dict]) -> str:
        lines = []
        for idx, item in enumerate(_cap(items, max_items), start=1):
            lines.append(
                f"{idx}. **{item.get('candidate_name') or '（未填写姓名）'}** — "
                f"最终 {_fmt_score(item.get('final_score'))} 分"
                f"（{item.get('final_verdict') or '—'}）\n"
                f"   简历初筛 {_fmt_score(item.get('screening_score'))} ／ "
                f"AI 面试 {_fmt_score(item.get('ai_interview_score'))} ／ "
                f"HR 打分 {_fmt_score(item.get('offline_score'))}"
            )
        if len(items) > max_items:
            lines.append(f"… 另有 {len(items) - max_items} 人未列出")
        return "\n".join(lines) if lines else "无"

    head = "\n".join(
        [
            f"**岗位**：{job_title or '全部岗位'}",
            f"**汇总时间**：{snapshot_at or '—'}",
            f"**本次纳入复核**：{len(offer_items) + len(review_items)} 人"
            f"（建议发 offer {len(offer_items)} 人）",
        ]
    )

    blocks = [head]
    if offer_items:
        blocks.append("**建议发放 offer**\n" + _render(offer_items))
    if review_items:
        blocks.append("**建议复核后决定**\n" + _render(review_items))
    blocks.append("> 请 HR 复核面试结论，办理 **offer 发放** 与 **入职手续**；如结论有异议请回后台调整 HR 打分。")

    return build_card("招聘汇总完成 · 请复核并办理 offer / 入职", "orange", blocks)


# ────────────────────────────── 卡片传输 ──────────────────────────────


def send_card_via_cli(open_id: str, card: dict, settings: Settings) -> tuple[bool, str]:
    """通过本机 lark-cli 发送交互卡片（``--msg-type interactive``）。"""
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
        "--content",
        json.dumps(card, ensure_ascii=False),
        "--msg-type",
        "interactive",
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

    if payload.get("ok") is True:
        return True, ""
    return False, json.dumps(payload.get("error") or payload, ensure_ascii=False)[:500]


def send_card_via_api(open_id: str, card: dict, settings: Settings) -> tuple[bool, str]:
    """通过飞书 Open API 发送交互卡片。"""
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "api 传输需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001 - 统一降级为失败说明
        return False, str(exc)

    try:
        resp = httpx.post(
            f"{_FEISHU_API_ROOT}/open-apis/im/v1/messages",
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


def default_card_sender(open_id: str, card: dict, settings: Settings) -> tuple[bool, str]:
    """按配置选择卡片传输方式。"""
    if (settings.feishu_notify_transport or "cli").lower() == "api":
        return send_card_via_api(open_id, card, settings)
    return send_card_via_cli(open_id, card, settings)


def send_card(
    card: dict,
    *,
    settings: Settings | None = None,
    sender: CardSender | None = None,
) -> tuple[bool, list[str], str]:
    """向所有配置的 HR 收件人发送卡片；任一人成功即视为成功。"""
    settings = settings or get_settings()
    recipients, resolve_err = resolve_recipients(settings)
    if not recipients:
        return False, [], resolve_err or (
            "未解析到任何 HR 收件人（请检查 FEISHU_HR_OPEN_IDS / FEISHU_HR_DEPARTMENT_IDS）"
        )

    send = sender or default_card_sender
    ok_any = False
    errors: list[str] = []
    for open_id in recipients:
        try:
            ok, err = send(open_id, card, settings)
        except Exception as exc:  # noqa: BLE001 - 单个收件人失败不拖垮整体
            ok, err = False, repr(exc)
        if ok:
            ok_any = True
        else:
            errors.append(f"{open_id}: {err}")
    if resolve_err:
        errors.insert(0, resolve_err)
    return ok_any, recipients, "; ".join(errors)


# ══════════════════════════ 飞书待办（任务） ══════════════════════════
#
# 光发卡片容易被刷过去，因此推送时顺带为 HR 建一条**飞书待办**（Task v2），
# 让「跟进初筛通过的候选人」真正落到对方的待办列表里。

# 任务创建函数签名：(assignee_open_id, summary, description, settings) -> (是否成功, 错误说明)
TaskCreator = Callable[[str, str, str, Settings], "tuple[bool, str]"]


def _due_timestamp_ms(due_days: int) -> str:
    """把「几天后」换算成飞书任务要求的毫秒时间戳（当天结束，全天任务）。"""
    days = max(0, int(due_days or 0))
    target = datetime.now(timezone.utc) + timedelta(days=days)
    target = target.replace(hour=23, minute=59, second=59, microsecond=0)
    return str(int(target.timestamp() * 1000))


def create_task_via_cli(
    assignee: str, summary: str, description: str, settings: Settings
) -> tuple[bool, str]:
    """通过本机 lark-cli 创建任务并指派给 assignee（``task +create``）。"""
    cli = resolve_cli_path(settings.feishu_lark_cli_path)
    if not cli:
        return False, "未找到 lark-cli 可执行文件，请配置 FEISHU_LARK_CLI_PATH"

    cmd = [
        cli,
        "task",
        "+create",
        "--as",
        (settings.feishu_task_identity or "bot").lower(),
        "--summary",
        summary,
        "--description",
        description,
        "--assignee",
        assignee,
        "--due",
        f"+{max(0, int(settings.feishu_task_due_days or 0))}d",
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

    if payload.get("ok") is True:
        return True, ""
    return False, json.dumps(payload.get("error") or payload, ensure_ascii=False)[:500]


def create_task_via_api(
    assignee: str, summary: str, description: str, settings: Settings
) -> tuple[bool, str]:
    """通过飞书 Open API 创建任务（Task v2）并指派给 assignee。"""
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        return False, "api 传输需配置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    import httpx

    try:
        token = get_tenant_access_token(settings)
    except Exception as exc:  # noqa: BLE001 - 统一降级为失败说明
        return False, str(exc)

    body = {
        "summary": summary,
        "description": description,
        "due": {"timestamp": _due_timestamp_ms(settings.feishu_task_due_days), "is_all_day": True},
        "members": [{"id": assignee, "type": "user", "role": "assignee"}],
    }
    try:
        resp = httpx.post(
            f"{_FEISHU_API_ROOT}/open-apis/task/v2/tasks",
            params={"user_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=settings.feishu_notify_timeout,
        )
    except httpx.HTTPError as exc:
        return False, f"创建任务请求失败：{exc}"

    data = resp.json()
    if data.get("code") == 0:
        return True, ""
    return False, f"创建任务失败 code={data.get('code')} msg={data.get('msg')}"


def default_task_creator(
    assignee: str, summary: str, description: str, settings: Settings
) -> tuple[bool, str]:
    """按配置选择任务创建方式（与消息传输保持一致）。"""
    if (settings.feishu_notify_transport or "cli").lower() == "api":
        return create_task_via_api(assignee, summary, description, settings)
    return create_task_via_cli(assignee, summary, description, settings)


def resolve_task_assignees(settings: Settings) -> list[str]:
    """待办负责人：优先取显式配置，留空则回退为所有飞书收件人。"""
    configured = settings.task_assignee_list
    if configured:
        return configured
    recipients, _err = resolve_recipients(settings)
    return recipients


def create_tasks(
    summary: str,
    description: str,
    *,
    settings: Settings | None = None,
    creator: TaskCreator | None = None,
) -> tuple[bool, list[str], str]:
    """为每个待办负责人创建一条任务；任一人成功即视为成功。绝不抛异常。"""
    settings = settings or get_settings()
    assignees = resolve_task_assignees(settings)
    if not assignees:
        return False, [], "未解析到任何待办负责人"

    create = creator or default_task_creator
    ok_any = False
    errors: list[str] = []
    for assignee in assignees:
        try:
            ok, err = create(assignee, summary, description, settings)
        except Exception as exc:  # noqa: BLE001 - 单人失败不拖垮整体
            ok, err = False, repr(exc)
        if ok:
            ok_any = True
        else:
            errors.append(f"{assignee}: {err}")
    return ok_any, assignees, "; ".join(errors)


def build_screening_task(
    *,
    job_title: str,
    recommended: list[dict],
    min_score: float,
    base_url: str = "",
    max_items: int = 20,
) -> tuple[str, str]:
    """构造初筛跟进的待办（标题, 描述）。"""
    title = f"【{job_title or '招聘'}】跟进 {len(recommended)} 位初筛通过候选人 · 推进 AI 面试"

    lines = [
        f"岗位：{job_title or '—'}",
        f"初筛通过：{len(recommended)} 人（初筛分 ≥ {min_score:g}）",
        "",
    ]
    for idx, item in enumerate(_cap(recommended, max_items), start=1):
        name = item.get("candidate_name") or "（未填写姓名）"
        lines.append(f"{idx}. {name} — {_fmt_score(item.get('total_score'))} 分（{item.get('recommendation') or '—'}）")
        path = item.get("interview_path") or ""
        if path:
            url = path if path.startswith("http") else f"{(base_url or '').rstrip('/')}{path}"
            lines.append(f"   AI 面试链接：{url}")
    if len(recommended) > max_items:
        lines.append(f"… 另有 {len(recommended) - max_items} 人未列出")

    lines += [
        "",
        "请跟进候选人 AI 面试进度，达标后约线下复试并录入打分。",
    ]
    return title, "\n".join(lines)


def build_summary_task(
    *,
    job_title: str,
    offer_items: list[dict],
    review_items: list[dict],
    snapshot_at: str = "",
    max_items: int = 20,
) -> tuple[str, str]:
    """构造汇总复核跟进的待办（标题, 描述）。

    与彩色卡片一一对应，确保「复核 + 办理 offer / 入职」这件事落到 HR 的待办列表里，
    而不是只有一条容易被刷过去的卡片消息。
    """
    total = len(offer_items) + len(review_items)
    title = (
        f"【{job_title or '招聘'}】汇总完成 · {total} 人待复核"
        f"（建议发 offer {len(offer_items)} 人）"
    )

    def _render(items: list[dict]) -> list[str]:
        out = []
        for idx, item in enumerate(_cap(items, max_items), start=1):
            out.append(
                f"{idx}. {item.get('candidate_name') or '（未填写姓名）'} — "
                f"最终 {_fmt_score(item.get('final_score'))} 分"
                f"（{item.get('final_verdict') or '—'}）"
            )
            out.append(
                f"   简历初筛 {_fmt_score(item.get('screening_score'))} ／ "
                f"AI 面试 {_fmt_score(item.get('ai_interview_score'))} ／ "
                f"HR 打分 {_fmt_score(item.get('offline_score'))}"
            )
        if len(items) > max_items:
            out.append(f"… 另有 {len(items) - max_items} 人未列出")
        return out

    lines = [
        f"岗位：{job_title or '全部岗位'}",
        f"汇总时间：{snapshot_at or '—'}",
        f"待复核：{total} 人（建议发 offer {len(offer_items)} 人）",
        "",
    ]
    if offer_items:
        lines.append("【建议发放 offer】")
        lines += _render(offer_items)
        lines.append("")
    if review_items:
        lines.append("【建议复核后决定】")
        lines += _render(review_items)
        lines.append("")

    lines.append("请复核面试结论，办理 offer 发放与入职手续；如结论有异议请回后台调整 HR 打分。")
    return title, "\n".join(lines)


# ────────────────────────────── 编排：初筛推荐 / 汇总复核 ──────────────────────────────


def notify_screening_recommendations(
    *,
    job_title: str,
    folder_name: str,
    items: list[dict],
    total: int | None = None,
    succeeded: int | None = None,
    settings: Settings | None = None,
    sender: CardSender | None = None,
    creator: TaskCreator | None = None,
    min_score: float | None = None,
    base_url: str | None = None,
) -> dict:
    """简历初筛完成后，把「推荐进入 AI 面试」的人推给 HR，并为 HR 建待办。绝不抛异常。

    Returns:
        ``{"status": "sent"|"skipped"|"failed", ...}``；``task`` 字段为待办创建结果。
    """
    settings = settings or get_settings()

    if not settings.feishu_screening_notify_enabled:
        return {"status": "skipped", "reason": "初筛推送未启用"}
    if not settings.feishu_notify_ready:
        return {"status": "skipped", "reason": "飞书通知未就绪（开关/收件人/传输凭据）"}

    threshold = settings.feishu_screening_min_score if min_score is None else min_score
    recommended = [it for it in (items or []) if should_notify(it.get("total_score"), threshold)]
    if not recommended:
        return {"status": "skipped", "reason": f"无初筛分 ≥ {threshold:g} 的推荐候选人"}

    recommended.sort(key=lambda it: float(it.get("total_score") or 0), reverse=True)
    base = settings.public_base_url_normalized if base_url is None else (base_url or "").rstrip("/")

    # 防呆：卡片里带了链接、但对外基址还是本机地址 -> 发出去也是坏链接，显式告警
    warning = ""
    if any(it.get("interview_path") for it in recommended) and looks_like_local_base(base):
        warning = (
            f"卡片中的面试链接基址仍为本机地址（PUBLIC_BASE_URL={base or '未配置'}），"
            "HR/候选人在自己机器上打不开；请改成公网地址，如 http://<服务器IP或域名>:3000"
        )
        logger.warning("飞书推送告警：%s", warning)

    card = build_screening_card(
        job_title=job_title,
        folder_name=folder_name,
        total=total if total is not None else len(items or []),
        succeeded=succeeded if succeeded is not None else len(items or []),
        recommended=recommended,
        min_score=threshold,
        max_items=settings.feishu_notify_max_items,
        base_url=base,
    )

    ok, recipients, err = send_card(card, settings=settings, sender=sender)
    if not ok:
        return {
            "status": "failed",
            "reason": err or "未知错误",
            "recipients": recipients,
            "recommended": len(recommended),
            "warning": warning,
        }

    result: dict = {
        "status": "sent",
        "recipients": recipients,
        "recommended": len(recommended),
        "warning": warning,
    }

    # 卡片之外再建飞书待办：确保「跟进初筛通过的候选人」落到 HR 的待办列表
    if settings.feishu_task_enabled:
        title, desc = build_screening_task(
            job_title=job_title,
            recommended=recommended,
            min_score=threshold,
            base_url=base,
            max_items=settings.feishu_notify_max_items,
        )
        try:
            task_ok, assignees, task_err = create_tasks(
                title, desc, settings=settings, creator=creator
            )
        except Exception as exc:  # noqa: BLE001 - 待办失败不影响卡片已送达的事实
            task_ok, assignees, task_err = False, [], repr(exc)
        result["task"] = {
            "status": "created" if task_ok else "failed",
            "assignees": assignees,
            "reason": task_err,
        }
    else:
        result["task"] = {"status": "skipped", "reason": "待办创建未启用"}

    return result


def notify_summary_review(
    *,
    job_title: str,
    items: list[dict],
    settings: Settings | None = None,
    sender: CardSender | None = None,
    creator: TaskCreator | None = None,
    snapshot_at: str = "",
) -> dict:
    """招聘汇总完成后，提醒 HR 复核并办理 offer 发放 / 入职；同时创建飞书待办。绝不抛异常。

    Args:
        items: 待复核候选人（含 candidate_name / final_score / final_verdict / 三段分）。

    Returns:
        ``{"status": "sent"|"skipped"|"failed", ...}``；``task`` 字段为待办创建结果。
    """
    settings = settings or get_settings()

    if not settings.feishu_summary_notify_enabled:
        return {"status": "skipped", "reason": "汇总推送未启用"}
    if not settings.feishu_notify_ready:
        return {"status": "skipped", "reason": "飞书通知未就绪（开关/收件人/传输凭据）"}

    rows = list(items or [])
    if not rows:
        return {"status": "skipped", "reason": "无可复核候选人"}

    offer_kinds = {"强烈推荐", "推荐"}
    offer_items = [r for r in rows if r.get("final_verdict") in offer_kinds]
    review_items = [r for r in rows if r.get("final_verdict") not in offer_kinds]
    for group in (offer_items, review_items):
        group.sort(key=lambda r: float(r.get("final_score") or 0), reverse=True)

    card = build_summary_card(
        job_title=job_title,
        offer_items=offer_items,
        review_items=review_items,
        snapshot_at=snapshot_at,
        max_items=settings.feishu_notify_max_items,
    )

    ok, recipients, err = send_card(card, settings=settings, sender=sender)
    if not ok:
        return {
            "status": "failed",
            "reason": err or "未知错误",
            "recipients": recipients,
            "reviewed": len(rows),
        }

    result: dict = {
        "status": "sent",
        "recipients": recipients,
        "reviewed": len(rows),
    }

    # 卡片之外再建飞书待办：确保「复核 + 办理 offer / 入职」落到 HR 的待办列表
    if settings.feishu_task_enabled:
        title, desc = build_summary_task(
            job_title=job_title,
            offer_items=offer_items,
            review_items=review_items,
            snapshot_at=snapshot_at,
            max_items=settings.feishu_notify_max_items,
        )
        try:
            task_ok, assignees, task_err = create_tasks(
                title, desc, settings=settings, creator=creator
            )
        except Exception as exc:  # noqa: BLE001 - 待办失败不影响卡片已送达的事实
            task_ok, assignees, task_err = False, [], repr(exc)
        result["task"] = {
            "status": "created" if task_ok else "failed",
            "assignees": assignees,
            "reason": task_err,
        }
    else:
        result["task"] = {"status": "skipped", "reason": "待办创建未启用"}

    return result
