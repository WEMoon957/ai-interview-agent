"""飞书长连接回调：让审批人直接在 Offer 卡片上点「同意 / 驳回」完成审批。

为什么用长连接
--------------
卡片按钮的点击需要飞书把回调事件送回系统。传统 Webhook 方式要求提供**公网 HTTPS 地址**，
而本项目线上只有 IP；飞书 SDK 提供的**长连接（WebSocket）**模式正好绕开这个限制：

- 无需公网域名 / 内网穿透，只要能访问公网即可；
- SDK 内部完成鉴权，回调数据是明文，无需处理解密与验签；
- 仅支持**企业自建应用**（本项目就是）；
- 需在开发者后台把「事件订阅」方式设为 **使用长连接接收回调**，并订阅
  `card.action.trigger`（卡片回传交互）；
- 每条回调需在 **3 秒**内处理完；同一应用最多 50 个连接；多实例部署时消息随机分发。

设计要点
--------
- ``lark_oapi`` 导入开销很大（上万文件、4~10 秒），因此**懒加载**，仅在真正启用时才导入，
  避免拖慢服务启动与单元测试。
- 长连接客户端跑在**守护线程**里（``Cli.start()`` 会阻塞），不阻塞 FastAPI 的事件循环。
- 审批业务逻辑**复用服务层** ``approve_offer`` / ``reject_offer``，与后台审批入口行为一致。
- 回调里任何异常都只记日志 + 回 toast，绝不向外抛（否则长连接会被飞书断开）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 驳回操作在卡片上先弹出输入框，用户提交时回传的 action 名
ACTION_REJECT_SUBMIT = "reject_submit"

_client: Any = None
_thread: threading.Thread | None = None
_lock = threading.Lock()
# 供测试注入的 db 工厂；生产环境为 None（走 SessionLocal）
_dbsession_factory: Any = None


def _open_db():
    """打开数据库会话（测试可通过 ``_dbsession_factory`` 注入桩）。"""
    factory = _dbsession_factory
    if factory is None:
        from app.db.session import SessionLocal

        factory = SessionLocal
    return factory()


def _respond(*, toast: tuple[str, str] | None = None, card: dict | None = None) -> Any:
    """构造卡片回调响应。

    - ``toast``：（类型, 文案），飞书客户端弹一条小提示；
    - ``card``：**就地替换**原卡片。

    两者可同时使用。点击后必须替换卡片，否则审批人会以为操作没生效而重复点击。
    """
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse,
    )

    payload: dict = {}
    if toast:
        payload["toast"] = {"type": toast[0], "content": toast[1]}
    if card is not None:
        payload["card"] = {"type": "raw", "data": card}
    return P2CardActionTriggerResponse(payload)


def _toast(kind: str, content: str) -> Any:
    """仅弹 toast 的回调响应（kind: info / success / error / warning）。"""
    return _respond(toast=(kind, content))


def _result_card(*, title: str, template: str, lines: list[str]) -> dict:
    """操作成功后用于**替换**原审批卡片的结果卡片（不带按钮，避免重复点击）。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        ],
    }


def _reject_form_card(offer_id: str) -> Any:
    """点「驳回」后返回一张带输入框的卡片，让审批人填写驳回意见。"""
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse,
    )

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "填写驳回意见"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "请填写驳回原因，提交后该 Offer 将被驳回，候选人不会收到任何通知。",
                },
            },
            {
                "tag": "form",
                "name": "reject_form",
                "elements": [
                    {
                        "tag": "input",
                        "name": "reason",
                        "placeholder": {"tag": "plain_text", "content": "例如：岗位已招满"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "确认驳回"},
                        "type": "danger",
                        "action_type": "form_submit",
                        "name": "reject_submit",
                        "value": {
                            "offer_id": offer_id,
                            "action": ACTION_REJECT_SUBMIT,
                        },
                    },
                ],
            },
        ],
    }
    # 用 card 字段就地替换原卡片（飞书 v2 卡片支持在回调响应里更新）
    return P2CardActionTriggerResponse({"card": {"type": "raw", "data": card}})


def _load_offer_or_none(db, offer_id: str):
    """按 id 取 Offer；非法 id 返回 None（不抛异常，避免打断长连接）。"""
    import uuid

    from app.db.models.offer import Offer

    try:
        return db.get(Offer, uuid.UUID(offer_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _handle_card_action(data: Any) -> Any:
    """卡片按钮回调主逻辑。

    期望的按钮 value：``{"offer_id": "<uuid>", "action": "approve" | "reject" | "reject_submit"}``
    （飞书开放平台**只接受对象类型**的卡片回传参数，不支持字符串。）
    """
    from app.services import offer as offer_svc

    try:
        event = getattr(data, "event", None)
        action = getattr(event, "action", None)
        value = (getattr(action, "value", None) or {}) if action else {}
        form_value = (getattr(action, "form_value", None) or {}) if action else {}
        operator = getattr(event, "operator", None)
        operator_open_id = (getattr(operator, "open_id", "") or "") if operator else ""

        offer_id = str(value.get("offer_id") or "")
        act = str(value.get("action") or "")
        logger.info(
            "收到卡片回调：action=%s offer_id=%s operator=%s", act, offer_id, operator_open_id
        )

        if act == "reject":
            # 不直接驳回，先让审批人填意见
            return _reject_form_card(offer_id)

        if act in {"approve", ACTION_REJECT_SUBMIT}:
            note = str((form_value or {}).get("reason") or "").strip()
            if act == ACTION_REJECT_SUBMIT and not note:
                return _toast("warning", "请先填写驳回意见再提交")

            db = _open_db()
            try:
                offer = _load_offer_or_none(db, offer_id)
                if offer is None:
                    return _toast("error", "未找到该 Offer，可能已被删除")
                if offer.status != "pending_approval":
                    return _toast("warning", f"该 Offer 已被处理（当前状态：{offer.status}）")

                if offer.approver_open_id and operator_open_id:
                    allowed = {x.strip() for x in offer.approver_open_id.split(",") if x.strip()}
                    if allowed and operator_open_id not in allowed:
                        return _toast("error", "你不是该 Offer 的指定审批人，无法操作")

                if act == "approve":
                    offer_svc.approve_offer(db, offer, note=note, settings=get_settings())
                    db.commit()
                    db.refresh(offer)
                    _notify_hr_after_approve(offer, operator_open_id)
                    # 就地替换卡片为「已通过」，否则审批人会以为没生效而重复点击
                    return _respond(
                        toast=("success", "已通过，Offer 链接将推送给 HR"),
                        card=_result_card(
                            title="✅ Offer 已通过",
                            template="green",
                            lines=[
                                f"**{offer.candidate_name}** · {offer.job_title}",
                                "",
                                "审批已通过，候选人确认链接已推送给 HR。",
                                "候选人确认后会收到「已接受 / 已婉拒」卡片通知。",
                            ],
                        ),
                    )
                else:
                    offer_svc.reject_offer(db, offer, note=note)
                    db.commit()
                    # 就地替换卡片为「已驳回」
                    return _respond(
                        toast=("success", "已驳回"),
                        card=_result_card(
                            title="⛔ Offer 已驳回",
                            template="red",
                            lines=[
                                f"**{offer.candidate_name}** · {offer.job_title}",
                                "",
                                f"**驳回意见**：{note}",
                                "",
                                "如需重新发起，请 HR 修改后再次提交审批。",
                            ],
                        ),
                    )
            finally:
                db.close()

        return _toast("warning", "未识别的操作")
    except Exception as exc:  # noqa: BLE001 - 绝不让异常打断长连接
        logger.exception("处理卡片回调失败：%s", exc)
        return _toast("error", "处理失败，请在后台重试")


def _candidate_link(offer) -> str:
    """候选人确认链接（与 API 层构造方式保持一致）。"""
    settings = get_settings()
    base = (settings.public_base_url or "").rstrip("/")
    if not base or not offer.token:
        return ""
    return f"{base}/o/{offer.token}"


def _notify_hr_after_approve(offer, operator_open_id: str) -> None:
    """审批通过后把 Offer 链接推给 HR（best-effort，失败只记日志）。"""
    try:
        from app.services.offer_notify import notify_offer_event

        notify_offer_event(
            "sent",
            offer,
            link=_candidate_link(offer),
            copy_text="",
            actor=operator_open_id,
        )
    except Exception:  # noqa: BLE001 - 通知失败不影响审批结果
        logger.exception("审批通过后推送 HR 通知失败 offer_id=%s", getattr(offer, "id", ""))


def start_long_connection(settings: Settings | None = None) -> bool:
    """启动长连接（幂等）。返回是否成功启动。

    未启用、缺凭据或依赖未安装时返回 False 并记录原因，**不影响服务启动**。
    """
    global _client, _thread

    s = settings or get_settings()
    if not s.feishu_longconn_enabled:
        logger.info("飞书长连接未启用（FEISHU_LONGCONN_ENABLED=false），跳过")
        return False

    if not (s.feishu_app_id and s.feishu_app_secret):
        logger.warning("飞书长连接已启用但缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，跳过启动")
        return False

    with _lock:
        if _thread is not None and _thread.is_alive():
            logger.info("飞书长连接已在运行，跳过重复启动")
            return True

        # 依赖存在性检查放在主线程（缺失要能立刻反馈）；真正的导入在子线程内完成
        import importlib.util

        if importlib.util.find_spec("lark_oapi") is None:
            logger.warning(
                "飞书长连接已启用但未安装 lark-oapi，请执行 "
                "`pip install lark-oapi`（requirements.txt 已包含）"
            )
            return False

        app_id = s.feishu_app_id
        app_secret = s.feishu_app_secret

        def _run() -> None:
            # ⚠️ lark_oapi 必须在本**子线程内**导入：
            #   ``lark_oapi/ws/client.py`` 在**模块级**执行 ``asyncio.get_event_loop()``，
            #   若在 uvicorn 主线程（事件循环已运行）里导入，就会绑到那个正在运行的 loop，
            #   随后 ``client.start()`` 抛 "this event loop is already running"。
            import asyncio

            import lark_oapi as lark

            asyncio.set_event_loop(asyncio.new_event_loop())

            try:
                event_handler = (
                    lark.EventDispatcherHandler.builder("", "")
                    .register_p2_card_action_trigger(_handle_card_action)
                    .build()
                )
                client = lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.INFO,
                )
                logger.info("飞书长连接启动中…")
                client.start()  # 阻塞直到连接结束
            except Exception:  # noqa: BLE001
                logger.exception("飞书长连接异常退出")

        _thread = threading.Thread(target=_run, name="feishu-longconn", daemon=True)
        _thread.start()
        logger.info("飞书长连接已在后台线程启动（卡片按钮审批已可用）")
        return True


def diagnose_longconn(settings: Settings | None = None) -> list[str]:
    """启动自检：把「会导致卡片按钮点了没反应」的配置问题提前喊出来。"""
    s = settings or get_settings()
    problems: list[str] = []
    if not s.feishu_longconn_enabled:
        return problems
    if not s.feishu_app_id or not s.feishu_app_secret:
        problems.append(
            "长连接已启用但缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，卡片按钮将无效"
        )
    if s.feishu_notify_transport != "api":
        problems.append(
            "长连接已启用，但 FEISHU_NOTIFY_TRANSPORT 不是 api —— 卡片需要走 HTTP API 发送，"
            "长连接只负责接收回调"
        )
    problems.append(
        "提醒：请确认飞书开发者后台「事件订阅」已选择「使用长连接接收回调」"
        "并订阅 card.action.trigger，否则按钮点击不会被送达"
    )
    return problems
