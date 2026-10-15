"""Offer 审批卡片「长连接」消费者（本地 / 单机场景）。

作用：通过 lark-cli 的**长连接**消费卡片交互事件（``card.action.trigger``），
把审批人在飞书卡片上点的「同意 / 驳回」落成 Offer 状态流转，并把原卡片更新为处理结果。

用法::

    cd backend && .venv/bin/python scripts/offer_card_action_consumer.py
    # 可选参数：--api http://localhost:8000 / --timeout 2h / --dry-run / --lark-cli <path>

前置条件：
- 飞书应用已在开发者后台启用事件 ``card.action.trigger``；
- 审批卡片由**本应用（bot）**发出（``build_approval_card`` 的 value 带 offer_id + action）；
- 目标后端服务在 ``--api`` 地址上运行（默认本机 8000）。

> 本脚本只适用于本机 / 单机场景（长连接无需公网地址）。
> **服务器部署请改用 HTTP 回调**（``POST /admin/feishu/offer-callback``，需公网 HTTPS），
> 两者复用同一套服务层逻辑，互不冲突。
"""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.services.feishu_notify import build_card

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("offer-card-consumer")

DEFAULT_LARK = "/Users/wemoon957/.workbuddy/binaries/node/versions/22.22.2-6/bin/lark-cli"
CARD_UPDATE_PATH = "/open-apis/interactive/v1/card/update"

# 卡片按钮回调的默认审批意见（卡片上无法输入文字，HR 可在后台补充）
DEFAULT_APPROVE_NOTE = "经飞书卡片审批通过"
DEFAULT_REJECT_NOTE = "经飞书卡片驳回（未填意见）"


def _resolve_lark(explicit: str) -> str:
    """定位 lark-cli 可执行文件。"""
    if explicit:
        return explicit
    env = os.environ.get("FEISHU_LARK_CLI_PATH")
    if env:
        return env
    found = shutil.which("lark-cli")
    if found:
        return found
    if Path(DEFAULT_LARK).exists():
        return DEFAULT_LARK
    raise SystemExit("未找到 lark-cli，请用 --lark-cli 指定路径")


def update_card(
    lark: str, token: str, card: dict, operator: str = "", *, dry_run: bool = False
) -> bool:
    """用事件返回的 token 更新原卡片。

    官方约束（见 lark-cli 的 lark-im-card-action-reply 文档）：
    - token 有效期 30 分钟、**最多使用 2 次**；
    - 必须传**完整的新卡片 JSON**，不支持局部更新；
    - **Card 1.0 必须在 card 内带 ``open_ids``**（至少含操作人 open_id），
      传空数组或省略都会导致飞书返回 300090「openid empty」。
    """
    payload_card = dict(card)
    payload_card["open_ids"] = [operator] if operator else []
    payload = json.dumps({"token": token, "card": payload_card}, ensure_ascii=False)
    if dry_run:
        logger.info("[dry-run] 更新卡片 token=%s", token[:12])
        return True
    try:
        proc = subprocess.run(
            [lark, "api", "POST", CARD_UPDATE_PATH, "--as", "bot", "--data", payload],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("更新卡片失败：%s", exc)
        return False
    if proc.returncode != 0:
        logger.warning("更新卡片失败 rc=%s %s", proc.returncode, (proc.stderr or proc.stdout)[:300])
        return False
    logger.info("卡片已更新（token=%s）", token[:12])
    return True


def _result_card(action: str, ok: bool, detail: str, operator: str) -> dict:
    """处理结果卡片（替换原审批卡片，去掉按钮避免重复点击）。"""
    if ok:
        title = "✅ Offer 已批准" if action == "approve" else "❌ Offer 已驳回"
        template = "green" if action == "approve" else "red"
    else:
        title = "⚠️ 审批未完成"
        template = "grey"
    blocks = [
        f"**处理结果**：{detail}",
        f"**操作人**：{operator or '—'}",
    ]
    if ok and action == "approve":
        blocks.append("候选人确认链接已推送给 HR，请留意后续卡片。")
    return build_card(title, template, blocks)


def handle_event(event: dict, api_base: str, lark: str, *, dry_run: bool = False) -> None:
    """处理单个卡片交互事件。"""
    event_type = event.get("type")
    if event_type != "card.action.trigger":
        return

    operator = event.get("operator_id") or ""
    token = event.get("token") or ""
    raw_value = event.get("action_value") or "{}"
    try:
        value = json.loads(raw_value) if isinstance(raw_value, str) else dict(raw_value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("无法解析 action_value：%s", raw_value)
        return

    offer_id = value.get("offer_id")
    action = value.get("action")
    if not offer_id or action not in ("approve", "reject"):
        logger.warning("事件缺少 offer_id / action：%s", value)
        return

    logger.info("收到卡片动作 operator=%s offer_id=%s action=%s", operator, offer_id, action)

    if action == "approve":
        url = f"{api_base}/admin/offers/{offer_id}/approve"
        payload = {"note": DEFAULT_APPROVE_NOTE}
        verb = "审批通过"
    else:
        url = f"{api_base}/admin/offers/{offer_id}/reject"
        payload = {"note": DEFAULT_REJECT_NOTE}
        verb = "驳回"

    ok = False
    detail = ""
    try:
        resp = httpx.post(url, json=payload, timeout=30)
        body = resp.json() if resp.content else {}
        if resp.status_code == 200 and body.get("code") == 0:
            ok = True
            detail = verb
        else:
            detail = body.get("message") or f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        detail = f"后端请求失败：{exc}"

    if not ok:
        logger.warning("处理失败 offer_id=%s：%s", offer_id, detail)
    else:
        logger.info("处理成功 offer_id=%s：%s", offer_id, detail)

    if token:
        card = _result_card(action, ok, detail, operator)
        update_card(lark, token, card, operator=operator, dry_run=dry_run)


def run(api_base: str, lark: str, timeout: str, *, dry_run: bool = False) -> int:
    """启动长连接消费循环（stdin 保持打开，避免 consume 因 EOF 退出）。"""
    cmd = [lark, "event", "consume", "card.action.trigger", "--as", "bot"]
    if timeout:
        cmd += ["--timeout", timeout]
    logger.info("启动长连接：%s", " ".join(cmd))

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,  # 保持打开：consume 把 stdin EOF 当退出信号
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        logger.error("无法启动 lark-cli：%s", exc)
        return 1

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                logger.debug("%s", line)  # [event] 运行日志
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("无法解析事件行：%s", line[:200])
                continue
            handle_event(event, api_base, lark, dry_run=dry_run)
    except KeyboardInterrupt:
        logger.info("收到中断，停止监听")
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Offer 审批卡片长连接消费者")
    parser.add_argument("--api", default="http://localhost:8000", help="后端服务地址")
    parser.add_argument("--timeout", default="0", help="lark-cli 消费超时（如 2h；0=不限）")
    parser.add_argument("--lark-cli", default="", help="lark-cli 可执行文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不调用后端与卡片更新")
    args = parser.parse_args()

    lark = _resolve_lark(args.lark_cli)
    return run(args.api, lark, args.timeout, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
