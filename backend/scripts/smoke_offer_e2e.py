"""Offer → 入职闭环 一键自检（默认静默，不打扰真实 HR）。

用法::

    cd backend && .venv/bin/python scripts/smoke_offer_e2e.py              # 静默自检（推荐）
    cd backend && .venv/bin/python scripts/smoke_offer_e2e.py --notify     # 真实推送飞书
    cd backend && .venv/bin/python scripts/smoke_offer_e2e.py --keep       # 保留自检产生的数据

覆盖范围：
- **主干**：起草 → 提交审批 → 审批通过 → 候选人查看 → 接受 → 办理入职（已报到）
- **分支**：停招岗位拦截 / 审批驳回 / 未报到 / 超期自动过期 / 重复进行中拦截

说明：脚本通过 FastAPI ``TestClient`` 走真实 HTTP 层与本机数据库；
默认把飞书推送与审批人配置改为自检专用值（不触碰 .env），结束后清理自建数据。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须在导入应用（读取配置）之前落地自检用环境变量：
# --notify 模式完全沿用 .env 配置（真实推送），静默模式才强制关闭。
_NOTIFY_MODE = "--notify" in sys.argv
if not _NOTIFY_MODE:
    os.environ.setdefault("FEISHU_OFFER_NOTIFY_ENABLED", "false")
    os.environ.setdefault("FEISHU_NOTIFY_ENABLED", "false")
    os.environ.setdefault("OFFER_APPROVER_OPEN_IDS", "ou_smoke_approver")

PASS = "✅"
FAIL = "❌"
INFO = "·"

# 自检专用的后台账号（管理端已启用登录鉴权，自检需先登录）
SMOKE_USERNAME = "__smoke_offer__"
SMOKE_PASSWORD = "SmokeOffer#2026!Test"


class Smoke:
    """极简断言与结果统计。"""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.created_offer_ids: list[str] = []
        self._restore: list = []

    def check(self, cond: bool, label: str) -> bool:
        if cond:
            self.passed += 1
            print(f"  {PASS} {label}")
        else:
            self.failed += 1
            print(f"  {FAIL} {label}")
        return bool(cond)

    def note(self, text: str) -> None:
        print(f"  {INFO} {text}")

    def on_cleanup(self, fn) -> None:
        self._restore.append(fn)


def _login_for_smoke(db, client) -> None:
    """为自检建立管理员会话。

    管理端已启用「账号密码登录 + 会话 Cookie」鉴权（迁移 0011_admin_auth），
    因此自检必须先用一个固定账号登录，并把 session / csrf 写进 TestClient。
    """
    from app.db.models.admin_user import ADMIN_ROLE, AdminUser
    from app.services.auth import (
        CSRF_COOKIE_NAME,
        CSRF_HEADER_NAME,
        SESSION_COOKIE_NAME,
        create_session,
        create_user,
    )

    user = db.query(AdminUser).filter(AdminUser.username == SMOKE_USERNAME).first()
    if user is None:
        user = create_user(
            db,
            username=SMOKE_USERNAME,
            password=SMOKE_PASSWORD,
            role=ADMIN_ROLE,
            display_name="Offer 自检账号",
            created_by="smoke",
        )
    token, csrf, _ = create_session(db, user)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    client.headers[CSRF_HEADER_NAME] = csrf


def _pick_summary(db):
    """挑一条可发起 Offer 的汇总。

    条件：已走完链路 + 最终分达门槛 + 岗位在招 + **该汇总当前没有进行中的 Offer**
    （后者必须排除，否则创建接口会返回 409）。
    """
    from app.config import get_settings
    from app.db.models import JobPosting, Offer, RecruitmentSummary

    threshold = get_settings().offer_min_final_score
    active = ("draft", "pending_approval", "rejected", "sent", "accepted")
    busy = {
        summary_id
        for (summary_id,) in db.query(Offer.summary_id)
        .filter(Offer.status.in_(active))
        .all()
    }

    rows = (
        db.query(RecruitmentSummary)
        .filter(RecruitmentSummary.is_complete.is_(True))
        .order_by(RecruitmentSummary.final_score.desc())
        .all()
    )
    for row in rows:
        if row.id in busy:
            continue
        if row.final_score is None or float(row.final_score) < threshold:
            continue
        job = db.get(JobPosting, row.job_id)
        if job is not None and job.is_active:
            return row, job
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Offer → 入职闭环自检")
    parser.add_argument("--notify", action="store_true", help="真实推送飞书（默认静默）")
    parser.add_argument("--keep", action="store_true", help="保留自检产生的数据（默认清理）")
    args = parser.parse_args()

    if args.notify:
        print("⚠️  真实推送模式：会向配置的 HR / 审批人发送飞书卡片\n")

    from fastapi.testclient import TestClient

    from app.db.models import JobPosting, Offer
    from app.db.session import SessionLocal
    from app.main import app

    smoke = Smoke()
    db = SessionLocal()
    client = TestClient(app)
    _login_for_smoke(db, client)  # 管理端需登录：先建立管理员会话
    created_names: list[str] = []

    def names(tag: str) -> str:
        name = f"自检-{tag}-{datetime.now().strftime('%H%M%S')}"
        created_names.append(name)
        return name

    try:
        print("═" * 62)
        print("Offer → 入职闭环 自检")
        print("═" * 62)

        summary, job = _pick_summary(db)
        if summary is None:
            print(f"{FAIL} 数据库里没有可用于自检的汇总记录"
                  "（需 is_complete=true 且 final_score 达标、岗位在招）")
            return 1
        print(f"\n[准备] 使用汇总：候选人={summary.candidate_name} "
              f"岗位={job.title} 最终分={summary.final_score}\n")

        # ── 1. 停招岗位拦截 ──────────────────────────────────
        print("[1] 停招岗位应拦截发起")
        job.is_active = False
        db.commit()
        smoke.on_cleanup(lambda: _set_job_active(job.id, True))
        r = client.post("/admin/offers", json={
            "summary_id": str(summary.id),
            "store_name": names("停招"),
            "report_date": _future_date(3),
        })
        smoke.check(r.status_code == 422, f"停招岗位返回 422（实际 {r.status_code}）")
        job.is_active = True
        db.commit()

        # ── 2. 起草 ─────────────────────────────────────────
        print("\n[2] 起草 Offer")
        r = client.post("/admin/offers", json={
            "summary_id": str(summary.id),
            "store_name": names("门店"),
            "report_date": _future_date(3),
            "probation_days": 30,
            "note": "自检数据",
        })
        body = r.json()
        if r.status_code != 200 or body.get("data") is None:
            print(f"{FAIL} 起草失败：HTTP {r.status_code} {body.get('message') or r.text[:200]}")
            return 1
        smoke.check(True, "起草返回成功")
        offer = body["data"]
        smoke.created_offer_ids.append(offer["id"])
        smoke.check(offer["status"] == "draft", "状态为 draft")
        smoke.check("offer_link" not in offer, "草稿阶段不生成链接（未发候选人）")

        # ── 3. 重复进行中拦截 ────────────────────────────────
        print("\n[3] 同一汇总重复发起应被拦截")
        r = client.post("/admin/offers", json={
            "summary_id": str(summary.id),
            "store_name": names("重复"),
            "report_date": _future_date(3),
        })
        smoke.check(r.status_code == 409, f"返回 409（实际 {r.status_code}）")

        # ── 4. 提交审批 ──────────────────────────────────────
        print("\n[4] 提交审批")
        r = client.post(f"/admin/offers/{offer['id']}/submit-approval")
        smoke.check(r.status_code == 200, "提交成功")
        body = r.json()["data"]
        smoke.check(body["offer"]["status"] == "pending_approval", "状态为 pending_approval")
        notify = body.get("notify") or {}
        smoke.check(notify.get("status") in ("sent", "skipped", "failed", "dry_run"),
                    f"推送编排已执行（{notify.get('status')}）")
        smoke.check(body["offer"]["approver_open_id"] != "", "已记录审批人")

        # ── 5. 审批驳回 → 重新提交 ───────────────────────────
        print("\n[5] 审批驳回后重新提交")
        r = client.post(f"/admin/offers/{offer['id']}/reject", json={"note": ""})
        smoke.check(r.status_code == 422, f"驳回未填意见返回 422（实际 {r.status_code}）")

        r = client.post(f"/admin/offers/{offer['id']}/reject", json={"note": "自检：薪资超标"})
        smoke.check(r.json()["data"]["offer"]["status"] == "rejected", "驳回后状态为 rejected")

        r = client.post(f"/admin/offers/{offer['id']}/submit-approval")
        smoke.check(r.json()["data"]["offer"]["status"] == "pending_approval", "可重新提交审批")
        smoke.check(r.json()["data"]["offer"]["approval_note"] == "", "重提后清空上次驳回意见")

        # ── 6. 审批通过 ──────────────────────────────────────
        print("\n[6] 审批通过（生成链接）")
        r = client.post(f"/admin/offers/{offer['id']}/approve", json={"note": ""})
        smoke.check(r.status_code == 200, "审批通过成功")
        data = r.json()["data"]
        offer = data["offer"]
        smoke.check(offer["status"] == "sent", "状态为 sent")
        smoke.check("/o/" in data["link"], f"返回确认链接（{data['link']}）")
        smoke.check(summary.candidate_name in data["copy_text"], "话术包含候选人姓名")
        smoke.check(bool(offer.get("expires_at")), "已设置确认截止时间")

        token = data["link"].rsplit("/o/", 1)[-1]
        smoke.check(bool(token), "链接中已生成 token")

        # ── 7. 候选人查看 ────────────────────────────────────
        print("\n[7] 候选人确认页数据")
        r = client.get(f"/offer/{token}")
        smoke.check(r.status_code == 200, "凭 token 可访问")
        cdata = r.json()["data"]
        smoke.check(cdata["status"] == "sent", "状态为 sent")
        smoke.check("summary_id" not in cdata and "approver_open_id" not in cdata,
                    "不泄露管理端字段")
        smoke.check(bool(cdata.get("deadline_text")), f"展示截止时间（{cdata.get('deadline_text')}）")

        # ── 8. 候选人接受 ────────────────────────────────────
        print("\n[8] 候选人接受 Offer")
        r = client.post(f"/offer/{token}/accept")
        smoke.check(r.json()["data"]["status"] == "accepted", "状态变为 accepted")
        r = client.post(f"/offer/{token}/accept")
        smoke.check(r.status_code == 200, "重复接受幂等（不报错）")

        # ── 9. 办理入职（已报到）─────────────────────────────
        print("\n[9] 办理入职 - 已报到")
        r = client.post(f"/admin/offers/{offer['id']}/onboard", json={"result": "onboarded"})
        smoke.check(r.json()["data"]["offer"]["status"] == "onboarded", "状态变为 onboarded")

        # ── 10. 未报到分支 ───────────────────────────────────
        print("\n[10] 办理入职 - 未报到（说明必填）")
        offer2 = _new_offer(client, summary.id, names("门店2"), smoke)
        if offer2:
            token2 = _to_sent(client, offer2["id"])
            client.post(f"/offer/{token2}/accept")
            r = client.post(f"/admin/offers/{offer2['id']}/onboard",
                            json={"result": "no_show", "note": ""})
            smoke.check(r.status_code == 422, f"未填说明返回 422（实际 {r.status_code}）")
            r = client.post(f"/admin/offers/{offer2['id']}/onboard",
                            json={"result": "no_show", "note": "自检：联系不上"})
            smoke.check(r.json()["data"]["offer"]["status"] == "no_show", "状态变为 no_show")

        # ── 11. 超期自动过期 ─────────────────────────────────
        print("\n[11] 超过截止时间自动过期（惰性判定）")
        offer3 = _new_offer(client, summary.id, names("门店3"), smoke)
        if offer3:
            token3 = _to_sent(client, offer3["id"])
            row = db.get(Offer, _uuid(offer3["id"]))
            row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.commit()
            r = client.get("/admin/offers")
            rows = {x["id"]: x for x in r.json()["data"]}
            smoke.check(rows.get(offer3["id"], {}).get("status") == "expired",
                        "列表读取时自动流转为 expired")
            r = client.post(f"/offer/{token3}/accept")
            smoke.check(r.status_code == 409, f"过期后不可接受（409，实际 {r.status_code}）")

        # ── 12. 统计口径 ─────────────────────────────────────
        print("\n[12] 统计与漏斗")
        r = client.get("/admin/offers/stats")
        stats = r.json()["data"]
        smoke.check("funnel" in stats and "accept_rate" in stats, "返回统计与漏斗字段")
        smoke.check(isinstance(stats["funnel"]["summaries_complete"], int), "漏斗可用")

        # ── 13. 录用通知书 ────────────────────────────────────
        print("\n[13] 录用通知书：字段回读与完整渲染")
        offer4 = _new_offer(client, summary.id, "通知书测试门店", smoke)
        if offer4:
            smoke.check(
                offer4.get("work_location") == "昆明市西山区万达北塔2507"
                and offer4.get("contact_name") == "方玺"
                and offer4.get("contact_phone") == "17702348593"
                and offer4.get("probation_salary") == "固定8500元"
                and offer4.get("regular_salary") == "10000元/月"
                and offer4.get("report_time") == "上午9:00",
                "HR 填写的 6 个通知书字段可保存并回读",
            )
            token4 = _to_sent(client, offer4["id"])
            r = client.get(f"/offer/{token4}")
            letter = (r.json().get("data") or {}).get("letter") or {}
            smoke.check(
                letter.get("company_name") == "昆明笨二娘餐饮管理有限公司",
                "通知书含固定公司名",
            )
            smoke.check(len(letter.get("materials") or []) == 5, "通知书含 5 项携带材料")
            smoke.check("劳动合同" in (letter.get("legal_note") or ""), "通知书含法律声明")
            smoke.check(
                letter.get("probation_phrase") == "试用期为一个月", "试用期按天数正确中文化"
            )
            smoke.check(
                letter.get("work_location") == "昆明市西山区万达北塔2507"
                and letter.get("contact_name") == "方玺",
                "HR 填写内容出现在候选人页",
            )
            smoke.check(bool(letter.get("deadline")), "通知书含确认截止时间")
            smoke.check("年" in (letter.get("letter_date") or ""), "通知书含落款日期")

        # ── 14. 卡片按钮回调（直接驱动长连接处理函数，绕开飞书）──
        print("\n[14] 卡片按钮回调：越权 / 驳回 / 同意")
        # 同一汇总只允许一条"进行中"的 Offer，先释放上一步占用的那条
        if offer4:
            _force_delete_offer(offer4["id"], smoke)
        offer5 = _new_offer(client, summary.id, "回调测试门店", smoke)
        if offer5:
            from app.services import feishu_longconn as lc

            # 本步静默通知，避免自检真的给 HR 发消息
            lc._notify_hr_after_approve = lambda *a, **k: None  # noqa: E731
            oid5 = _uuid(offer5["id"])
            client.post(f"/admin/offers/{offer5['id']}/submit-approval")

            # 14.1 非审批人操作 → 拒绝
            resp = _card_resp(
                lc._handle_card_action(
                    _card_event(offer5["id"], "approve", open_id="ou_stranger")
                )
            )
            smoke.check("指定审批人" in _toast_content(resp), "非审批人点同意被拒绝")

            # 14.2 点「驳回」→ 返回填写意见的卡片（状态不变）
            resp = _card_resp(lc._handle_card_action(_card_event(offer5["id"], "reject")))
            smoke.check(
                "驳回意见" in json.dumps(resp, ensure_ascii=False), "点驳回返回填意见卡片"
            )

            # 14.3 提交空意见 → 拦截，状态不变
            lc._handle_card_action(
                _card_event(offer5["id"], lc.ACTION_REJECT_SUBMIT, form_value={})
            )
            row = db.get(Offer, oid5)
            db.refresh(row)
            smoke.check(row.status == "pending_approval", "驳回意见为空时不改状态")

            # 14.4 提交有效意见 → 驳回成功（用真实审批人身份）
            approver = (row.approver_open_id or "").split(",")[0]
            lc._handle_card_action(
                _card_event(
                    offer5["id"],
                    lc.ACTION_REJECT_SUBMIT,
                    form_value={"reason": "岗位已招满"},
                    open_id=approver,
                )
            )
            db.refresh(row)
            smoke.check(row.status == "rejected", "卡片驳回后状态为 rejected")
            smoke.check(row.approval_note == "岗位已招满", "驳回意见已落库")

            # 14.5 重新提交后用卡片「同意」→ sent + 生成 token
            client.post(f"/admin/offers/{offer5['id']}/submit-approval")
            lc._handle_card_action(_card_event(offer5["id"], "approve", open_id=approver))
            db.refresh(row)
            smoke.check(row.status == "sent", "卡片同意后状态为 sent")
            smoke.check(bool(row.token), "卡片同意后生成候选人链接 token")

    finally:
        db.close()

    # ── 清理 ────────────────────────────────────────────────
    if args.keep:
        print(f"\n[清理] --keep 已指定，保留自检数据：{len(smoke.created_offer_ids)} 条 Offer")
    else:
        removed = _cleanup(smoke.created_offer_ids)
        for fn in smoke._restore:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                print(f"  {INFO} 恢复现场失败：{exc}")
        print(f"\n[清理] 已删除自检产生的 {removed} 条 Offer，岗位在招状态已恢复")

    print("\n" + "═" * 62)
    total = smoke.passed + smoke.failed
    if smoke.failed == 0:
        print(f"{PASS} 自检通过：{smoke.passed}/{total} 项")
    else:
        print(f"{FAIL} 自检失败：{smoke.failed}/{total} 项未通过")
    print("═" * 62)
    return 0 if smoke.failed == 0 else 1


# ── 辅助 ─────────────────────────────────────────────────────


def _uuid(value: str):
    import uuid as _u

    return _u.UUID(value)


def _future_date(days: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _card_event(offer_id, action, *, form_value=None, open_id="ou_smoke_approver"):
    """构造与飞书回传结构一致的假事件，用于驱动长连接卡片回调处理函数。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        event=SimpleNamespace(
            action=SimpleNamespace(
                value={"offer_id": str(offer_id), "action": action},
                form_value=form_value or {},
            ),
            operator=SimpleNamespace(open_id=open_id),
        )
    )


def _card_resp(resp) -> dict:
    """把飞书 SDK 的回调响应对象转成可断言的 dict。"""
    import json as _json

    import lark_oapi as lark

    return _json.loads(lark.JSON.marshal(resp))


def _toast_content(resp: dict) -> str:
    return ((resp or {}).get("toast") or {}).get("content") or ""


def _force_delete_offer(offer_id: str, smoke) -> None:
    """删除一条自检 Offer 并移出清理列表（用于释放「进行中」占位）。

    同一汇总只允许一条进行中的 Offer（唯一索引 uq_offers_summary_active），
    因此下一步要新建 Offer 时，必须先终结/删除上一步留下的那条。
    """
    from app.db.models import Offer
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(Offer, _uuid(offer_id))
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    if offer_id in smoke.created_offer_ids:
        smoke.created_offer_ids.remove(offer_id)


def _new_offer(client, summary_id, store_name, smoke):
    """起草一条新 Offer（用于分支验证）。"""
    r = client.post("/admin/offers", json={
        "summary_id": str(summary_id),
        "store_name": store_name,
        "report_date": _future_date(5),
        "note": "自检数据",
        # 录用通知书字段（候选人确认页展示）
        "work_location": "昆明市西山区万达北塔2507",
        "contact_name": "方玺",
        "contact_phone": "17702348593",
        "probation_salary": "固定8500元",
        "regular_salary": "10000元/月",
        "report_time": "上午9:00",
    })
    if r.status_code != 200:
        smoke.check(False, f"起草分支 Offer 失败：{r.status_code} {r.text[:120]}")
        return None
    data = r.json()["data"]
    smoke.created_offer_ids.append(data["id"])
    return data


def _to_sent(client, offer_id) -> str:
    """提交审批 → 审批通过；返回候选人 token（从返回的链接中提取）。"""
    client.post(f"/admin/offers/{offer_id}/submit-approval")
    r = client.post(f"/admin/offers/{offer_id}/approve", json={"note": ""})
    if r.status_code != 200:
        return ""
    link = (r.json().get("data") or {}).get("link") or ""
    return link.rsplit("/o/", 1)[-1] if "/o/" in link else ""


def _set_job_active(job_id, active: bool) -> None:
    from app.db.models import JobPosting
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(JobPosting, job_id)
        if job is not None:
            job.is_active = active
            db.commit()
    finally:
        db.close()


def _cleanup(offer_ids: list[str]) -> int:
    """删除自检创建的 Offer 记录（只删自己创建的 id）。"""
    from app.db.models import Offer
    from app.db.session import SessionLocal

    if not offer_ids:
        return 0
    db = SessionLocal()
    try:
        removed = 0
        for oid in offer_ids:
            row = db.get(Offer, _uuid(oid))
            if row is not None:
                db.delete(row)
                removed += 1
        db.commit()
        return removed
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
