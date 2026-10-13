#!/usr/bin/env python
"""招聘全链路本地联调脚本（走真实 HTTP + 真实 LLM）。

覆盖：创建岗位/模板 → 岗位文件夹批量上传 → AI 简历初筛排序 → 批量发面试链接
→ 候选人完成 AI 面试 → 约线下复试 → 线下打分 → 汇总实时聚合 → 汇总落库快照。

用法：
    cd backend
    .venv/bin/python scripts/smoke_recruitment_e2e.py                 # 默认 http://localhost:8000
    .venv/bin/python scripts/smoke_recruitment_e2e.py --candidates 3  # 指定跑 AI 面试的人数
    .venv/bin/python scripts/smoke_recruitment_e2e.py --skip-interview  # 跳过 AI 面试答题（省 token）

退出码：全链路断言通过为 0，否则非 0。
"""
import argparse
import os
import sys
import tempfile
import time

import requests

DEFAULT_TIMEOUT = 180


class SmokeError(AssertionError):
    """联调断言失败。"""


def _check(cond, msg: str) -> None:
    if not cond:
        raise SmokeError(msg)


def _api(method: str, path: str, **kwargs):
    """调用后端接口并解包统一响应结构 {code,message,data}。"""
    url = f"{args.base_url}{path}"
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    resp = requests.request(method, url, **kwargs)
    try:
        body = resp.json()
    except Exception:
        raise SmokeError(f"{method} {path} 返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}")
    if resp.status_code >= 400 or body.get("code") != 0:
        raise SmokeError(f"{method} {path} 失败：HTTP {resp.status_code} {body.get('message')}")
    return body.get("data")


def step(title: str) -> None:
    print(f"\n=== {title} ===")


SAMPLE_RESUMES = {
    "张三-后厨帮厨.txt": (
        "姓名：张三\n性别：男\n年龄：32\n电话：13800138001\n邮箱：zhangsan@example.com\n\n"
        "工作经历：\n2018.03-2024.06  昆明某连锁餐饮  后厨砧板/帮厨\n"
        "- 负责食材初加工与配菜，日均出餐 300+ 份，刀工扎实\n"
        "- 熟悉后厨卫生规范，连续 3 年未发生食安问题\n"
        "- 协助新员工带教\n\n教育背景：初中\n求职意向：后厨帮厨"
    ),
    "李四-后厨帮厨.txt": (
        "姓名：李四\n电话：13800138002\n\n"
        "工作经历：\n2023.07-2024.02  某便利店  店员\n"
        "- 负责收银与货架整理\n\n教育背景：中专\n求职意向：后厨帮厨"
    ),
    "王五-后厨帮厨.txt": (
        "姓名：王五\n电话：13800138003\n\n"
        "工作经历：\n2021.05-2024.05  某火锅店  后厨帮厨\n"
        "- 负责切配、传菜、后厨清洁\n"
        "- 高峰期可独立完成备菜\n\n教育背景：高中\n求职意向：后厨帮厨/服务员"
    ),
}


def upload_folder(job_id: str, direct_invite: bool | None = None) -> dict:
    """把样例简历打包成「岗位文件夹」上传。

    direct_invite 留空则不传该字段，由后端按岗位 skip_screening 开关自动决定模式。
    """
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, content in SAMPLE_RESUMES.items():
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            files.append(("files", (name, open(path, "rb"), "text/plain")))
        try:
            data = {
                "job_id": job_id,
                "folder_name": "候选人简历",
                "screen": "true",
                # 联调不向真实 HR 飞书发卡片（避免测试消息打扰 HR）
                "notify": "false",
            }
            if direct_invite is not None:
                data["direct_invite"] = "true" if direct_invite else "false"
            return _api("POST", "/admin/screenings/upload", data=data, files=files)
        finally:
            for _, (_, fh, _) in files:
                fh.close()


def run_ai_interview(token: str) -> str:
    """完整跑完一场 AI 面试，返回最终状态。"""
    ctx = _api("GET", f"/interview/{token}")
    if ctx.get("status") == "consent_required":
        _api("POST", f"/interview/{token}/consent")
        ctx = _api("GET", f"/interview/{token}")

    rounds = 0
    while ctx.get("status") != "finished" and rounds < 30:
        rounds += 1
        answer = (
            f"我有相关后厨经验，能吃苦，身体健康，可立即到岗。（第 {rounds} 次作答）"
        )
        res = _api("POST", f"/interview/{token}/answer", json={"answer": answer})
        if res.get("status") == "finished":
            break
        ctx = _api("GET", f"/interview/{token}")
    return "finished"


def run_direct_invite_phase(ts: str) -> None:
    """免初筛岗位：上传简历 → 直接生成 AI 面试链接 → 汇总不计初筛分。"""
    step("11) 免初筛岗位：上传即发面试链接")
    job = _api(
        "POST",
        "/admin/jobs",
        json={
            "title": f"【联调】洗碗工-免初筛-{ts}",
            "jd_text": "负责餐具清洗、后厨卫生；能吃苦耐劳即可，无需经验。",
            "requirements": {"years": 0, "skills": ["后厨卫生"]},
            "skip_screening": True,
        },
    )
    job_id = job["id"]
    _check(job["skip_screening"] is True, "岗位应标记为免初筛")
    print(f"   已建免初筛岗位：{job['title']}")

    _api(
        "POST",
        "/admin/templates",
        json={
            "job_id": job_id,
            "questions": [{"id": "q1", "text": "你能接受洗碗岗的工作强度吗？"}],
            "rubric": [
                {"key": "attitude", "name": "态度稳定性", "weight": 0.6, "max_score": 10, "description": "吃苦耐劳"},
                {"key": "experience", "name": "相关经验", "weight": 0.4, "max_score": 10, "description": "后厨经验"},
            ],
            "duration_limit": 15,
            "max_follow_ups": 0,
        },
    )

    # 不传 direct_invite -> 后端按岗位开关自动走直通模式
    up = upload_folder(job_id)
    print(f"   模式：{up['mode']}；上传 {up['total']} 份，成功 {up['succeeded']} 份")
    _check(up["mode"] == "direct_invite", "免初筛岗位上传应走直通模式")
    _check(all(it["interview_path"] for it in up["items"]), "直通模式应每人都生成答题链接")
    _check(all(it["total_score"] is None for it in up["items"]), "直通模式不应有初筛分")
    _check(all(it["status"] == "interview_sent" for it in up["items"]), "直通模式状态应为已发面试")
    for it in up["items"]:
        print(f"   - {it['candidate_name']:<6} 链接 {args.base_url}{it['interview_path']}")

    first = up["items"][0]

    if not args.skip_interview:
        step("12) 免初筛候选人完成 AI 面试")
        print(f"   {first['candidate_name']}: AI 面试 {run_ai_interview(first['session_token'])}")
        rep = _api("GET", f"/admin/sessions/{first['session_id']}/report")
        _check(rep.get("report") is not None, "免初筛候选人完成面试后也应生成报告")
        print(f"   AI 面试综合分：{rep['report']['total_score']}（{rep['report']['verdict']}）")

    step("13) 免初筛候选人约线下复试 + 打分")
    offline = _api(
        "POST",
        "/admin/offline-interviews",
        json={
            "screening_id": first["id"],
            "scheduled_at": "2026-10-21T10:30:00+08:00",
            "location": "昆明总店",
            "interviewer": "李HR",
            "interviewer_role": "hr",
        },
    )
    _api(
        "POST",
        f"/admin/offline-interviews/{offline['id']}/score",
        json={
            "score": 92,
            "dim_scores": [{"key": "attitude", "name": "态度稳定性", "score": 9.5, "max_score": 10}],
            "comment": "踏实肯干，到岗意愿强。",
            "decision": "pass",
        },
    )
    print(f"   线下打分 92 分（通过）")

    step("14) 免初筛岗位汇总（不计初筛分）")
    summary = _api("GET", "/admin/summary", params={"job_id": job_id})
    row = next((r for r in summary if r["candidate_id"] == first["candidate_id"]), None)
    _check(row is not None, "汇总应包含免初筛候选人")
    _check(row["screening_score"] is None, "免初筛候选人不应有初筛分")
    _check(row["final_score"] is not None, "免初筛候选人最终分应由后续环节算出")
    print(f"   {row['candidate_name']}：初筛 {row['screening_score']} / AI {row['ai_interview_score']} / 线下 {row['offline_score']} → 最终 {row['final_score']}，阶段 {row['stage_label']}")

    rebuilt = _api(
        "POST", "/admin/summary/rebuild", params={"job_id": job_id, "notify": False}
    )
    snaps = _api("GET", "/admin/summary/snapshots", params={"job_id": job_id})
    print(f"   落库 {rebuilt['saved']} 条，快照读取 {len(snaps)} 条")
    _check(all(s["screening_score"] is None for s in snaps), "免初筛岗位落库快照不应含初筛分")


def main() -> int:
    ts = time.strftime("%m%d-%H%M%S")

    step("1) 健康检查")
    health = _api("GET", "/health")
    print("   health:", health)

    step("2) 创建岗位 + 面试模板")
    job = _api(
        "POST",
        "/admin/jobs",
        json={
            "title": f"【联调】后厨帮厨-{ts}",
            "jd_text": "负责后厨切配、备菜、卫生清洁；要求身体健康、吃苦耐劳，有餐饮后厨经验优先。",
            "requirements": {"years": 1, "skills": ["切配", "后厨卫生"]},
        },
    )
    job_id = job["id"]
    print("   job_id:", job_id)

    tpl = _api(
        "POST",
        "/admin/templates",
        json={
            "job_id": job_id,
            "questions": [
                {"id": "q1", "text": "请介绍一下你在餐饮后厨的工作经历。"},
                {"id": "q2", "text": "如果高峰期同时有多个订单，你会怎么安排先后？"},
            ],
            "rubric": [
                {"key": "skill", "name": "岗位技能", "weight": 0.4, "max_score": 10, "description": "切配/出餐能力"},
                {"key": "experience", "name": "相关经验", "weight": 0.3, "max_score": 10, "description": "后厨经验匹配度"},
                {"key": "attitude", "name": "态度稳定性", "weight": 0.3, "max_score": 10, "description": "吃苦耐劳与稳定性"},
            ],
            "duration_limit": 20,
            "max_follow_ups": 1,
        },
    )
    print("   template_id:", tpl["id"])

    step("3) 岗位文件夹批量上传 + AI 简历初筛")
    up = upload_folder(job_id)
    print(f"   上传 {up['total']} 份，成功 {up['succeeded']} 份，失败 {up['failed']} 份")
    for it in up["items"]:
        print(f"   - {it['candidate_name']:<6} 初筛 {it['total_score']:>5.1f} 分  {it['recommendation']}")
    _check(up["succeeded"] >= 2, "至少应有 2 份简历初筛成功")
    scores = [it["total_score"] for it in up["items"]]
    _check(scores == sorted(scores, reverse=True), "初筛结果应按总分降序返回")
    # 联调用 notify=false 静默，不应向真实 HR 发卡片
    _check(up.get("notify", {}).get("status") == "skipped", "notify=false 时应跳过推送")
    print(f"   初筛推送状态：{up.get('notify')}")
    # 初筛通过（达到门槛）的候选人应已自动生成 AI 面试链接
    passed = [it for it in up["items"] if it["total_score"] is not None and it["total_score"] >= 65]
    _check(passed, "应至少有 1 人达到初筛门槛")
    _check(
        all(it["interview_path"] for it in passed),
        "初筛通过的候选人应已自动生成 AI 面试链接",
    )
    print(f"   初筛通过 {len(passed)} 人，均已自动生成面试链接")

    step("4) 榜单查询（按分降序）")
    ranked = _api("GET", "/admin/screenings", params={"job_id": job_id})
    print(f"   榜单共 {len(ranked)} 人，首位：{ranked[0]['candidate_name']}（{ranked[0]['total_score']} 分）")
    _check(len(ranked) == up["succeeded"], "榜单条数应与初筛成功数一致")

    step("5) 批量发起 AI 面试（发链接）")
    top_ids = [r["id"] for r in ranked[: max(1, args.candidates)]]
    invited = _api("POST", "/admin/screenings/invite", json={"screening_ids": top_ids})
    print(f"   已发起 {invited['count']} 场 AI 面试")
    for it in invited["items"]:
        print(f"   - {it['candidate_name']:<6} 链接 {args.base_url}{it['interview_path']}")
        _check(it["session_token"], "应返回答题 token")

    step("6) 候选人完成 AI 面试（真实 LLM 评分）")
    session_ids = []
    for it in invited["items"]:
        if args.skip_interview:
            print(f"   - {it['candidate_name']}: 跳过（--skip-interview）")
            continue
        final_status = run_ai_interview(it["session_token"])
        print(f"   - {it['candidate_name']}: AI 面试 {final_status}")
        session_ids.append(it["session_id"])

    ai_scores = {}
    for sid in session_ids:
        rep = _api("GET", f"/admin/sessions/{sid}/report")
        total = rep["report"]["total_score"] if rep.get("report") else None
        ai_scores[sid] = total
        print(f"   - 会话 {sid[-8:]} AI 面试综合分：{total}（{rep['report']['verdict'] if rep.get('report') else '—'}）")
        _check(rep.get("report") is not None, "AI 面试完成后应生成报告")

    step("7) 约线下复试（HR / 店长）")
    target = ranked[0]
    offline = _api(
        "POST",
        "/admin/offline-interviews",
        json={
            "screening_id": target["id"],
            "scheduled_at": "2026-10-20T14:00:00+08:00",
            "location": "昆明总部会议室",
            "interviewer": "王店长",
            "interviewer_role": "store_manager",
        },
    )
    print(f"   已约面：{offline['candidate_name']} @ {offline['scheduled_at']} · {offline['location']} · {offline['interviewer']}")
    _check(offline["status"] == "scheduled", "新建约面状态应为 scheduled")

    step("8) 线下复试打分")
    scored = _api(
        "POST",
        f"/admin/offline-interviews/{offline['id']}/score",
        json={
            "score": 88,
            "dim_scores": [
                {"key": "service", "name": "服务意识", "score": 9, "max_score": 10},
                {"key": "stability", "name": "稳定性", "score": 9, "max_score": 10},
                {"key": "skill", "name": "岗位技能", "score": 8.5, "max_score": 10},
            ],
            "comment": "刀工扎实，沟通顺畅，稳定性好，符合录用标准。",
            "decision": "pass",
        },
    )
    print(f"   打分完成：{scored['score']} 分，结论 {scored['decision']}，状态 {scored['status']}")
    _check(scored["status"] == "done", "打分后状态应为 done")

    step("9) 汇总实时聚合")
    summary = _api("GET", "/admin/summary", params={"job_id": job_id})
    print("   候选人      初筛   AI面试  线下   最终   阶段")
    for r in summary:
        print(
            f"   {r['candidate_name']:<8} "
            f"{str(r['screening_score']):>5} {str(r['ai_interview_score']):>7} "
            f"{str(r['offline_score']):>6} {str(r['final_score']):>6}  {r['stage_label']}"
        )
    _check(len(summary) >= 1, "汇总应有候选人数据")
    top = summary[0]
    _check(top["final_score"] is not None, "汇总应给出最终加权分")

    step("10) 汇总数据落库（快照）")
    rebuilt = _api(
        "POST", "/admin/summary/rebuild", params={"job_id": job_id, "notify": False}
    )
    print(f"   已落库 {rebuilt['saved']} 条")
    snapshots = _api("GET", "/admin/summary/snapshots", params={"job_id": job_id})
    print(f"   快照读取 {len(snapshots)} 条；首位 {snapshots[0]['candidate_name']} 最终分 {snapshots[0]['final_score']}")
    _check(len(snapshots) == len(summary), "落库条数应与实时聚合一致")
    _check(snapshots[0]["final_score"] is not None, "落库快照应含最终分")
    # 联调用 notify=false 静默，不应向真实 HR 发卡片
    _check(all(n.get("status") == "skipped" for n in rebuilt.get("notify", [])), "notify=false 时应跳过汇总推送")
    print(f"   汇总推送状态：{rebuilt.get('notify')}")

    # ── 免初筛岗位链路 ──────────────────────────────────────
    run_direct_invite_phase(ts)

    print("\n全链路联调通过 ✅")
    print(f"岗位：{job['title']}（{job_id}）")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="招聘全链路本地联调")
    parser.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--candidates", type=int, default=2, help="跑 AI 面试的候选人数（按初筛分取前 N）")
    parser.add_argument("--skip-interview", action="store_true", help="跳过 AI 面试答题（省 token）")
    args = parser.parse_args()

    try:
        sys.exit(main())
    except SmokeError as exc:
        print(f"\n联调失败 ❌：{exc}")
        sys.exit(1)
