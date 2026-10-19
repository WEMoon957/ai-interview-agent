"""招聘 BI 看板 —— 独立只读数据服务。

为什么单独起一个服务，而不是复用 backend 的接口：

1. **鉴权边界**。现有后台接口全部挂在 ``require_user`` 登录态后面。BI 大屏通常挂在
   墙上或投屏给管理层看，不应该为此在后台开一个公开口子，也不该让大屏去维护 Session。
2. **口径不同**。后台接口服务于「操作」（分页、筛选、全字段），BI 服务于「看趋势」
   （聚合、分桶、转化率），查询形态差异很大，混在一起两边都别扭。
3. **故障隔离**。BI 挂了不影响招聘业务；BI 被反复刷新也不该拖垮业务库连接。

三条硬约束：

- **只读**：本文件不含任何写 SQL；数据库连接以 ``read_only`` 模式打开，
  即便代码被改坏，Postgres 层面也会拒绝写入。
- **独立**：独立进程、独立端口（默认 8500）、独立依赖，不 import backend 的任何模块。
- **不参与鉴权**：不读 ``admin_sessions``、不校验密码、不签发 Cookie，与主系统登录体系无关。
  唯一的例外是「账号活跃度」面板——它只 **SELECT** ``admin_users`` 的展示字段
  （见 :func:`_q_team_activity`），用于 HR 最后登录与绩效参考，绝不触碰凭据列。

实时性：进程内跑一个数据指纹探测任务（默认 3 秒），表有变化就通过 SSE
（``/api/stream``）通知所有看板立即刷新；连不上时前端自动退回 60 秒轮询。
``?nolive=1`` 可关闭实时通道（截图 / 排查用）。

可选访问口令：设置 ``BI_ACCESS_TOKEN`` 后首次访问需带 ``?token=xxx``（校验通过后写 Cookie）；
不设置则完全开放，适合内网展示大屏。

统计口径（重要）：

- **cohort 口径**：以「候选人在选定时间窗内建档」为起点，追踪其**当前**的全链路状态。
  这样漏斗严格单调递减，转化率不会因为各阶段时间窗不同而出现「下层多于上层」。
- **趋势图为事件口径**：按天统计当天实际发生的事件（建档 / 面试完成 / 发 Offer / 入职），
  与 cohort 口径互补。
- **测试数据过滤**：``scope=real``（默认）排除岗位名含「【联调】/【演示】/测试」的岗位；
  ``scope=all`` 查看全部。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

logger = logging.getLogger("bi")

# ── 配置 ─────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# BI 专用连接串优先；回落到主项目的 DATABASE_URL（便于一键本地起）
DATABASE_URL = (
    os.getenv("BI_DATABASE_URL")
    or os.getenv("DATABASE_URL")
    or "postgresql://interview:interview@localhost:5432/interview"
)
CACHE_TTL = int(os.getenv("BI_CACHE_TTL", "60"))  # 秒；0 表示禁用缓存
ACCESS_TOKEN = os.getenv("BI_ACCESS_TOKEN", "").strip()
# 默认口径：all=全部数据（系统试运行期以联调数据为主，先看全量）；
# 正式对外展示前建议改成 real，只统计真实招聘岗位。
DEFAULT_SCOPE = os.getenv("BI_DEFAULT_SCOPE", "all").strip().lower()
if DEFAULT_SCOPE not in ("real", "all"):
    DEFAULT_SCOPE = "all"
TZ_CN = "Asia/Shanghai"

# 「测试数据」识别规则：岗位名命中任一模式即视为非真实招聘岗位
TEST_PATTERNS = ["%【联调】%", "%【演示】%", "%测试%"]

# 中文时区展示
CN_TZ = timezone(timedelta(hours=8))

# ── KPI 记分卡配置（高管视图）─────────────────
#
# 这是 skill「Executive KPI Dashboard」框架的落地：把招聘漏斗的关键指标
# 抽象成带「目标值 / 预警阈值 / 负责人 / 领先-滞后 / 决策规则」的记分卡。
#
# 配置唯一入口是同级 ``kpi_config.json``（用户可直接改，无需动代码）；
# 这里内嵌一份相同内容的默认值，保证文件缺失/损坏时记分卡照样能算。
# 字段语义：
#   direction: higher=越大越好 / lower=越小越好 / null=仅展示不设目标
#   target:    绿区阈值（达成=绿）
#   alert:     红区阈值（higher: 低于它=红；lower: 高于它=红）；target 与 alert 之间为黄区
#   tier:      leading=领先指标(预测) / lagging=滞后指标(结果) / context=背景参考

DEFAULT_KPI_CONFIG = {
    "title": "招聘高管 KPI 记分卡",
    "subtitle": "笨二娘隆江猪脚饭 · 招聘漏斗健康度",
    "kpis": [
        {"key": "onboarded", "label": "入职到岗", "unit": "人", "type": "count",
         "direction": "higher", "target": 8, "alert": 4, "owner": "HR 负责人", "tier": "lagging",
         "definition": "统计窗口内状态为「已入职(onboarded)」的候选人人数，是招聘漏斗的最终结果指标。",
         "strategic_link": "门店人力到岗的直接产出，决定业务能否按计划扩张与开业。",
         "rules": {"green": "达成目标，维持现有招聘节奏，照常周度复盘。",
                   "yellow": "到岗偏慢，排查 Offer 接受与入职引导环节是否卡顿。",
                   "red": "到岗严重不足，升级至负责人，48h 内复盘渠道质量与 Offer 竞争力。"}},
        {"key": "accept_rate", "label": "Offer 接受率", "unit": "%", "type": "rate",
         "direction": "higher", "target": 0.85, "alert": 0.70, "owner": "HR 负责人", "tier": "lagging",
         "definition": "按 Offer 记录：接受数（含之后转 onboarded/no_show）÷ 已决数（接受+婉拒+超期）。",
         "strategic_link": "反映薪酬竞争力与候选人体验，直接决定「发得出」能否变成「来得了」。",
         "rules": {"green": "竞争力健康，可适度加快发 Offer 节奏。",
                   "yellow": "接受率走低，复盘薪酬/岗位匹配度，约谈 1-2 名待定候选人。",
                   "red": "接受率告警，升级至负责人，48h 内出具挽留/调薪方案并复盘 Offer 设计。"}},
        {"key": "onboard_rate", "label": "到岗率", "unit": "%", "type": "rate",
         "direction": "higher", "target": 0.90, "alert": 0.75, "owner": "HR 负责人", "tier": "lagging",
         "definition": "按 Offer 记录：已入职数 ÷ 已接受数（接受后未报到的计入 no_show 拖累此率）。",
         "strategic_link": "衡量「接受」到「真正报到」的转化，暴露入职引导与到岗阻力。",
         "rules": {"green": "入职引导顺畅，保持。",
                   "yellow": "存在接受后未报到，回访未报到候选人，优化入职前触达。",
                   "red": "到岗率告警，排查 no_show 成因（薪资落差/竞品截胡/入职体验），负责人介入。"}},
        {"key": "days_to_offer", "label": "平均发 Offer 周期", "unit": "天", "type": "days",
         "direction": "lower", "target": 14, "alert": 28, "owner": "招聘运营", "tier": "lagging",
         "definition": "从候选人建档到发出 Offer 的平均天数（建档 → sent_at）。",
         "strategic_link": "招聘效率的核心刻度，周期过长会提高候选人流失与竞品截胡概率。",
         "rules": {"green": "节奏健康，保持。",
                   "yellow": "周期偏长，排查初筛/面试排期瓶颈，缩短决策链路。",
                   "red": "周期严重拖慢，梳理卡点（面试官排期/审批流），负责人跟进。"}},
        {"key": "days_to_onboard", "label": "平均到岗周期", "unit": "天", "type": "days",
         "direction": "lower", "target": 30, "alert": 60, "owner": "HR 负责人", "tier": "lagging",
         "definition": "从候选人建档到实际入职的平均天数（建档 → onboarded_at）。",
         "strategic_link": "端到端招聘时效，影响门店补员速度与开业计划。",
         "rules": {"green": "端到端时效达标。",
                   "yellow": "到岗偏慢，关注入职前空窗与材料准备耗时。",
                   "red": "到岗时效告警，复盘从接受到报到的堵点，负责人介入。"}},
        {"key": "ai_rate", "label": "AI 面试完成率", "unit": "%", "type": "rate",
         "direction": "higher", "target": 0.60, "alert": 0.40, "owner": "招聘运营", "tier": "leading",
         "definition": "窗口内建档候选人中完成 AI 面试的人数占比（ai_done ÷ created）。",
         "strategic_link": "漏斗上游转化，预示后续能进入复试/Offer 的供给量。",
         "rules": {"green": "上游供给充足。",
                   "yellow": "面试邀约转化偏低，优化邀约话术与候选人触达时机。",
                   "red": "邀约大量流失，排查链路（链接失效/体验差），负责人介入。"}},
        {"key": "offline_rate", "label": "复试转化率", "unit": "%", "type": "rate",
         "direction": "higher", "target": 0.70, "alert": 0.50, "owner": "招聘运营", "tier": "leading",
         "definition": "完成线下复试人数占完成 AI 面试人数的比例（offline_done ÷ ai_done）。",
         "strategic_link": "AI 面试到店复试的转化，预示最终可发 Offer 的优质供给。",
         "rules": {"green": "复试转化健康。",
                   "yellow": "复试到店率偏低，优化店长邀约与到店体验。",
                   "red": "复试大量流失，排查到店阻力（时间/地点/激励），负责人介入。"}},
        {"key": "candidates", "label": "候选人建档量", "unit": "人", "type": "count",
         "direction": "higher", "target": 50, "alert": 25, "owner": "招聘运营 / 市场", "tier": "leading",
         "definition": "窗口内新建档的候选人总数，是整条漏斗的源头水量。",
         "strategic_link": "招聘漏斗的最大杠杆——源头水量不足，后续各环节无从谈起。",
         "rules": {"green": "渠道供给充足。",
                   "yellow": "建档量偏低，加大渠道投放与内推激励。",
                   "red": "源头严重缺水，复盘渠道投放与简历获取，负责人介入。"}},
        {"key": "active_jobs", "label": "在招岗位", "unit": "个", "type": "count",
         "direction": None, "target": None, "alert": None, "owner": "HR 负责人", "tier": "context",
         "definition": "当前处于启用状态的招聘岗位数，作为规模背景参考（不设目标）。",
         "strategic_link": "招聘承载能力的背景指标，配合建档量看供需平衡。",
         "rules": {"green": "—", "yellow": "—", "red": "—"}},
    ],
}

KPI_CONFIG_PATH = os.path.join(BASE_DIR, "kpi_config.json")


def _load_kpi_config() -> dict:
    """优先读 kpi_config.json；缺失/损坏则用内建默认，保证记分卡永不崩。"""
    try:
        with open(KPI_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict) and cfg.get("kpis"):
            return cfg
        logger.warning("kpi_config.json 结构异常，回退内建默认配置")
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 kpi_config.json 失败，使用内建默认：%s", exc)
    return DEFAULT_KPI_CONFIG


KPI_CONFIG = _load_kpi_config()


def _kpi_status(value, direction, target, alert) -> str:
    """三色状态：green 达成 / yellow 黄区 / red 红区 / na 无数据。"""
    if value is None or target is None or alert is None:
        return "na"
    if direction == "higher":
        if value >= target:
            return "green"
        if value >= alert:
            return "yellow"
        return "red"
    # lower is better
    if value <= target:
        return "green"
    if value <= alert:
        return "yellow"
    return "red"


def _kpi_delta(value, cfg) -> str:
    """相对目标的可读差值文案（按类型区分百分点/绝对值/天数）。"""
    target = cfg.get("target")
    direction = cfg.get("direction")
    if value is None or target is None:
        return "暂无数据"
    typ = cfg.get("type")
    diff = value - target
    if typ == "rate":
        pts = diff * 100
        if abs(pts) < 0.05:
            return "持平目标"
        return ("高 " if pts > 0 else "低 ") + f"{abs(pts):.1f}pt"
    if typ == "days":
        if abs(diff) < 0.05:
            return "持平目标"
        if diff > 0:
            return f"慢 {diff:.1f} 天"
        return f"快 {abs(diff):.1f} 天"
    # count
    if diff == 0:
        return "持平目标"
    return ("多 " if diff > 0 else "少 ") + f"{abs(int(diff))}{cfg.get('unit', '')}"


def _resolve_kpi_value(key: str, kpi: dict, funnel: dict):
    """从现有 kpi / funnel 数据里取出该 KPI 的当前值。"""
    if key == "offline_rate":
        ai = funnel.get("ai_done") or 0
        return _rate(funnel.get("offline_done") or 0, ai)
    return kpi.get(key)


def build_scorecard(kpi: dict, funnel: dict) -> dict:
    """依据 KPI 配置 + 当前数据，算出记分卡、高管叙事、风险预警与行动项。"""
    cards = []
    for cfg in KPI_CONFIG.get("kpis", []):
        key = cfg["key"]
        value = _resolve_kpi_value(key, kpi, funnel)
        status = _kpi_status(value, cfg.get("direction"), cfg.get("target"), cfg.get("alert"))
        rule = (cfg.get("rules") or {}).get(status, "")
        variance_pct = None
        if value is not None and cfg.get("target"):
            try:
                variance_pct = round((value - cfg["target"]) / cfg["target"] * 100, 1)
            except (TypeError, ZeroDivisionError):
                variance_pct = None
        cards.append({
            "key": key,
            "label": cfg["label"],
            "unit": cfg.get("unit"),
            "type": cfg.get("type"),
            "direction": cfg.get("direction"),
            "tier": cfg.get("tier"),
            "owner": cfg.get("owner"),
            "value": value,
            "target": cfg.get("target"),
            "alert": cfg.get("alert"),
            "status": status,
            "delta_text": _kpi_delta(value, cfg),
            "variance_pct": variance_pct,
            "rule": rule,
            "definition": cfg.get("definition", ""),
            "strategic_link": cfg.get("strategic_link", ""),
        })

    # 高管叙事：用关键数字拼一段中文简报（数据驱动，不编故事）
    ar = kpi.get("accept_rate")
    orr = kpi.get("onboard_rate")
    alerts = [c for c in cards if c["status"] in ("yellow", "red")]
    opps = [c for c in cards if c["status"] == "green" and (c["variance_pct"] or 0) >= 10]
    red_cnt = sum(1 for c in cards if c["status"] == "red")
    yellow_cnt = sum(1 for c in cards if c["status"] == "yellow")

    narrative = (
        f"统计区间内新增候选人 {_int(kpi.get('candidates'))} 人，完成 AI 面试 "
        f"{_int(kpi.get('ai_done'))} 人（完成率 {_pct(kpi.get('ai_rate'))}），"
        f"发出 Offer {_int(kpi.get('offer_sent'))} 份，接受率 {_pct(ar)}、到岗率 {_pct(orr)}，"
        f"实际入职 {_int(kpi.get('onboarded'))} 人，平均招聘周期 "
        f"{kpi.get('days_to_offer') if kpi.get('days_to_offer') is not None else '—'} 天。"
    )
    if red_cnt:
        narrative += f"当前有 {red_cnt} 项指标触及红色预警，需立即处置（见下方行动项）。"
    elif yellow_cnt:
        narrative += f"有 {yellow_cnt} 项指标处于黄色观察区，建议本周内跟进。"
    else:
        narrative += "各项指标均在绿色区间，招聘漏斗运转健康。"

    return {
        "title": KPI_CONFIG.get("title", "招聘高管 KPI 记分卡"),
        "subtitle": KPI_CONFIG.get("subtitle", ""),
        "cards": cards,
        "narrative": narrative,
        "alerts": [
            {"label": c["label"], "status": c["status"], "owner": c["owner"],
             "delta_text": c["delta_text"], "rule": c["rule"]}
            for c in alerts
        ],
        "opportunities": [c["label"] for c in opps],
        "summary": {"green": sum(1 for c in cards if c["status"] == "green"),
                    "yellow": yellow_cnt, "red": red_cnt,
                    "na": sum(1 for c in cards if c["status"] == "na")},
    }


def _normalize_dsn(url: str) -> str:
    """把 SQLAlchemy 风格的连接串转成 psycopg 能认的。"""
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


DSN = _normalize_dsn(DATABASE_URL)

app = FastAPI(
    title="招聘 BI 看板",
    description="只读招聘数据分析看板（独立部署）",
    version="1.0.0",
    docs_url=None,  # 展示服务不暴露接口文档
    redoc_url=None,
    openapi_url=None,
)


# ── 工具 ─────────────────────────────────────────────────────


def _connect() -> psycopg.Connection:
    """建立**只读**连接。

    ``read_only=True`` 会让 psycopg 发出 ``SET SESSION CHARACTERISTICS AS TRANSACTION
    READ ONLY``，之后任何 INSERT/UPDATE/DELETE 都会被 Postgres 拒绝 —— 这是代码之外的第二道锁。
    """
    conn = psycopg.connect(DSN, row_factory=dict_row, connect_timeout=8)
    conn.read_only = True
    return conn


def _rows(conn: psycopg.Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _num(value: Any, digits: int = 1) -> float | None:
    """把 Decimal / None 统一成可 JSON 序列化的 float，避免前端拿到字符串。"""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    return int(value or 0)


def _rate(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _pct(value: Any, digits: int = 1) -> str:
    """把 0~1 的比率格式化成百分数字符串（仅用于中文叙事文本）。"""
    if value is None:
        return "—"
    try:
        return f"{value * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


# ── SQL：公共 CTE ────────────────────────────────────────────
#
# 图层说明：
#   scope_jobs   本次口径允许统计的岗位
#   links        候选人 ↔ 岗位 的全部关联来源（5 张表并集）
#   pair         去重后的（候选人, 岗位）对
#   pair_stage   每一对在各阶段是否达成（EXISTS 判断，避免一对多重复计数）
#   cohort       时间窗内建档、且至少投过一个允许岗位的候选人
#   cohort_stage 候选人在**任一**岗位达成过某阶段即算达成（bool_or）
#
_CTE = """
WITH scope_jobs AS (
    SELECT id, title, is_active
      FROM job_postings
     WHERE %(scope_all)s::boolean
        OR NOT (title LIKE ANY(%(test_patterns)s::text[]))
),
links AS (
    SELECT candidate_id, job_id FROM resume_screenings
    UNION
    SELECT s.candidate_id, t.job_id
      FROM interview_sessions s
      JOIN interview_templates t ON t.id = s.template_id
    UNION
    SELECT candidate_id, job_id FROM offline_interviews
    UNION
    SELECT candidate_id, job_id FROM recruitment_summaries
    UNION
    SELECT candidate_id, job_id FROM offers
),
pair AS (
    SELECT DISTINCT l.candidate_id, l.job_id
      FROM links l
      JOIN scope_jobs sj ON sj.id = l.job_id
),
pair_stage AS (
    SELECT p.candidate_id,
           p.job_id,
           EXISTS (
               SELECT 1 FROM resume_screenings rs
                WHERE rs.candidate_id = p.candidate_id
                  AND rs.job_id = p.job_id
                  AND rs.total_score IS NOT NULL
           ) AS s_screen,
           EXISTS (
               SELECT 1 FROM interview_sessions s
                 JOIN interview_templates t ON t.id = s.template_id
                 JOIN session_reports rp ON rp.session_id = s.id
                WHERE s.candidate_id = p.candidate_id
                  AND t.job_id = p.job_id
           ) AS s_ai,
           EXISTS (
               SELECT 1 FROM offline_interviews o
                WHERE o.candidate_id = p.candidate_id
                  AND o.job_id = p.job_id
                  AND o.status = 'done'
           ) AS s_offline,
           EXISTS (
               SELECT 1 FROM recruitment_summaries m
                WHERE m.candidate_id = p.candidate_id
                  AND m.job_id = p.job_id
                  AND m.is_complete
           ) AS s_summary,
           EXISTS (
               SELECT 1 FROM offers o
                WHERE o.candidate_id = p.candidate_id
                  AND o.job_id = p.job_id
                  AND o.sent_at IS NOT NULL
           ) AS s_offer,
           EXISTS (
               SELECT 1 FROM offers o
                WHERE o.candidate_id = p.candidate_id
                  AND o.job_id = p.job_id
                  AND o.status IN ('accepted', 'onboarded', 'no_show')
           ) AS s_accept,
           EXISTS (
               SELECT 1 FROM offers o
                WHERE o.candidate_id = p.candidate_id
                  AND o.job_id = p.job_id
                  AND o.status = 'onboarded'
           ) AS s_onboard
      FROM pair p
),
cohort AS (
    SELECT c.id,
           c.name,
           c.created_at,
           c.phone,
           coalesce(nullif(btrim(c.source_channel), ''), '未填写') AS channel
      FROM candidates c
     WHERE c.created_at >= %(start)s
       AND EXISTS (SELECT 1 FROM pair p WHERE p.candidate_id = c.id)
),
cohort_stage AS (
    SELECT co.id AS candidate_id,
           bool_or(ps.s_screen)  AS s_screen,
           bool_or(ps.s_ai)      AS s_ai,
           bool_or(ps.s_offline) AS s_offline,
           bool_or(ps.s_summary) AS s_summary,
           bool_or(ps.s_offer)   AS s_offer,
           bool_or(ps.s_accept)  AS s_accept,
           bool_or(ps.s_onboard) AS s_onboard
      FROM cohort co
      LEFT JOIN pair_stage ps ON ps.candidate_id = co.id
     GROUP BY co.id
)
"""


# ── 各项查询 ─────────────────────────────────────────────────


def _q_scope_meta(conn, p) -> dict:
    sql = _CTE + """
    SELECT count(*)                              AS total_jobs,
           count(*) FILTER (WHERE is_active)     AS active_jobs
      FROM scope_jobs
    """
    row = _rows(conn, sql, p)[0]
    return {"active_jobs": _int(row["active_jobs"]), "total_jobs": _int(row["total_jobs"])}


def _q_funnel(conn, p) -> dict:
    sql = _CTE + """
    SELECT count(*)                                     AS s0_created,
           count(*) FILTER (WHERE s_screen)              AS s1_screened,
           count(*) FILTER (WHERE s_ai)                  AS s2_ai_done,
           count(*) FILTER (WHERE s_offline)             AS s3_offline_done,
           count(*) FILTER (WHERE s_offer)               AS s4_offer_sent,
           count(*) FILTER (WHERE s_accept)              AS s5_accepted,
           count(*) FILTER (WHERE s_onboard)             AS s6_onboarded
      FROM cohort_stage
    """
    row = _rows(conn, sql, p)[0]
    return {
        "created": _int(row["s0_created"]),
        "screened": _int(row["s1_screened"]),
        "ai_done": _int(row["s2_ai_done"]),
        "offline_done": _int(row["s3_offline_done"]),
        "offer_sent": _int(row["s4_offer_sent"]),
        "accepted": _int(row["s5_accepted"]),
        "onboarded": _int(row["s6_onboarded"]),
    }


def _q_cycle(conn, p) -> dict:
    """招聘周期（天）：建档 → 发 Offer / 入职。"""
    sql = _CTE + """
    SELECT avg(extract(epoch FROM (o.sent_at - c.created_at)) / 86400.0)      AS to_offer,
           avg(extract(epoch FROM (o.onboarded_at - c.created_at)) / 86400.0) AS to_onboard,
           avg(extract(epoch FROM (o.onboarded_at - o.sent_at)) / 86400.0)    AS offer_to_onboard
      FROM offers o
      JOIN candidates c ON c.id = o.candidate_id
      JOIN cohort co ON co.id = o.candidate_id
     WHERE o.sent_at IS NOT NULL
    """
    row = _rows(conn, sql, p)[0]
    return {
        "days_to_offer": _num(row["to_offer"]),
        "days_to_onboard": _num(row["to_onboard"]),
        "days_offer_to_onboard": _num(row["offer_to_onboard"]),
    }


def _q_jobs(conn, p) -> list[dict]:
    sql = _CTE + """
    SELECT j.id::text                          AS job_id,
           j.title                             AS title,
           j.is_active                          AS is_active,
           count(DISTINCT ps.candidate_id)                              AS candidates,
           count(DISTINCT ps.candidate_id) FILTER (WHERE ps.s_ai)       AS ai_done,
           count(DISTINCT ps.candidate_id) FILTER (WHERE ps.s_offline)  AS offline_done,
           count(DISTINCT ps.candidate_id) FILTER (WHERE ps.s_offer)    AS offer_sent,
           count(DISTINCT ps.candidate_id) FILTER (WHERE ps.s_onboard)  AS onboarded,
           avg(m.final_score)                                          AS avg_final
      FROM pair_stage ps
      JOIN cohort co ON co.id = ps.candidate_id
      JOIN job_postings j ON j.id = ps.job_id
      LEFT JOIN recruitment_summaries m
             ON m.candidate_id = ps.candidate_id
            AND m.job_id = ps.job_id
     GROUP BY j.id, j.title, j.is_active
     ORDER BY candidates DESC, j.title
    """
    out = []
    for r in _rows(conn, sql, p):
        out.append(
            {
                "job_id": r["job_id"],
                "title": r["title"],
                "is_active": bool(r["is_active"]),
                "candidates": _int(r["candidates"]),
                "ai_done": _int(r["ai_done"]),
                "offline_done": _int(r["offline_done"]),
                "offer_sent": _int(r["offer_sent"]),
                "onboarded": _int(r["onboarded"]),
                "avg_final": _num(r["avg_final"]),
            }
        )
    return out


def _q_channels(conn, p) -> list[dict]:
    sql = _CTE + """
    SELECT co.channel                                              AS name,
           count(*)                                                AS candidates,
           count(*) FILTER (WHERE cs.s_screen)                     AS screened,
           count(*) FILTER (WHERE cs.s_ai)                         AS ai_done,
           count(*) FILTER (WHERE cs.s_offer)                      AS offer_sent,
           count(*) FILTER (WHERE cs.s_onboard)                    AS onboarded
      FROM cohort co
      JOIN cohort_stage cs ON cs.candidate_id = co.id
     GROUP BY co.channel
     ORDER BY candidates DESC
    """
    return [
        {
            "name": r["name"],
            "candidates": _int(r["candidates"]),
            "screened": _int(r["screened"]),
            "ai_done": _int(r["ai_done"]),
            "offer_sent": _int(r["offer_sent"]),
            "onboarded": _int(r["onboarded"]),
            "onboard_rate": _rate(_int(r["onboarded"]), _int(r["candidates"])),
        }
        for r in _rows(conn, sql, p)
    ]


def _q_scores(conn, p) -> dict:
    """四个环节的分数分桶分布。"""
    bucket_expr = """
        CASE WHEN {col} < 50 THEN '0-49'
             WHEN {col} < 60 THEN '50-59'
             WHEN {col} < 70 THEN '60-69'
             WHEN {col} < 80 THEN '70-79'
             WHEN {col} < 90 THEN '80-89'
             ELSE '90-100' END
    """
    sql = _CTE + f"""
    SELECT src, bucket, n FROM (
        SELECT 'screen' AS src, {bucket_expr.format(col='rs.total_score')} AS bucket, count(*) AS n
          FROM resume_screenings rs
          JOIN cohort co ON co.id = rs.candidate_id
          JOIN scope_jobs sj ON sj.id = rs.job_id
         WHERE rs.total_score IS NOT NULL
         GROUP BY 1, 2
        UNION ALL
        SELECT 'ai', {bucket_expr.format(col='rp.total_score')}, count(*)
          FROM session_reports rp
          JOIN interview_sessions s ON s.id = rp.session_id
          JOIN interview_templates t ON t.id = s.template_id
          JOIN cohort co ON co.id = s.candidate_id
          JOIN scope_jobs sj ON sj.id = t.job_id
         WHERE rp.total_score IS NOT NULL
         GROUP BY 1, 2
        UNION ALL
        SELECT 'offline', {bucket_expr.format(col='o.score')}, count(*)
          FROM offline_interviews o
          JOIN cohort co ON co.id = o.candidate_id
          JOIN scope_jobs sj ON sj.id = o.job_id
         WHERE o.score IS NOT NULL
         GROUP BY 1, 2
        UNION ALL
        SELECT 'final', {bucket_expr.format(col='m.final_score')}, count(*)
          FROM recruitment_summaries m
          JOIN cohort co ON co.id = m.candidate_id
          JOIN scope_jobs sj ON sj.id = m.job_id
         WHERE m.final_score IS NOT NULL
         GROUP BY 1, 2
    ) buckets
    """
    rows = _rows(conn, sql, p)

    order = ["0-49", "50-59", "60-69", "70-79", "80-89", "90-100"]
    series = {"screen": [0] * 6, "ai": [0] * 6, "offline": [0] * 6, "final": [0] * 6}
    for r in rows:
        idx = order.index(r["bucket"]) if r["bucket"] in order else -1
        if idx >= 0 and r["src"] in series:
            series[r["src"]][idx] = _int(r["n"])

    # 各环节平均分（仅统计有效样本）
    avg_sql = _CTE + """
    SELECT
      (SELECT round(avg(rs.total_score)::numeric, 1)
         FROM resume_screenings rs
         JOIN cohort co ON co.id = rs.candidate_id
         JOIN scope_jobs sj ON sj.id = rs.job_id
        WHERE rs.total_score IS NOT NULL) AS screen,
      (SELECT round(avg(rp.total_score)::numeric, 1)
         FROM session_reports rp
         JOIN interview_sessions s ON s.id = rp.session_id
         JOIN interview_templates t ON t.id = s.template_id
         JOIN cohort co ON co.id = s.candidate_id
         JOIN scope_jobs sj ON sj.id = t.job_id) AS ai,
      (SELECT round(avg(o.score)::numeric, 1)
         FROM offline_interviews o
         JOIN cohort co ON co.id = o.candidate_id
         JOIN scope_jobs sj ON sj.id = o.job_id
        WHERE o.score IS NOT NULL) AS offline,
      (SELECT round(avg(m.final_score)::numeric, 1)
         FROM recruitment_summaries m
         JOIN cohort co ON co.id = m.candidate_id
         JOIN scope_jobs sj ON sj.id = m.job_id
        WHERE m.final_score IS NOT NULL) AS final
    """
    avg_row = _rows(conn, avg_sql, p)[0]

    return {
        "buckets": order,
        "series": series,
        "avg": {
            "screen": _num(avg_row["screen"]),
            "ai": _num(avg_row["ai"]),
            "offline": _num(avg_row["offline"]),
            "final": _num(avg_row["final"]),
        },
    }


def _q_offer_metrics(conn, p) -> dict:
    """Offer **记录层面**的转化指标（与主系统 services/offer.py 的口径一致）。

    注意与漏斗的区别：漏斗按候选人去重（一人多轮 Offer 只算一次，保证单调），
    这里按 Offer 记录算，能反映真实的接受率 / 到岗率。
    """
    sql = _CTE + """
    SELECT count(*)                                                            AS total,
           count(*) FILTER (WHERE o.sent_at IS NOT NULL)                       AS sent,
           count(*) FILTER (WHERE o.status IN ('accepted', 'onboarded', 'no_show')) AS accepted,
           count(*) FILTER (WHERE o.status IN ('accepted', 'onboarded', 'no_show',
                                               'declined', 'expired'))          AS decided,
           count(*) FILTER (WHERE o.status = 'onboarded')                      AS onboarded,
           count(*) FILTER (WHERE o.status = 'pending_approval')               AS pending,
           count(*) FILTER (WHERE o.status = 'no_show')                        AS no_show
      FROM offers o
      JOIN cohort co ON co.id = o.candidate_id
      JOIN scope_jobs sj ON sj.id = o.job_id
    """
    r = _rows(conn, sql, p)[0]
    accepted = _int(r["accepted"])
    decided = _int(r["decided"])
    return {
        "total": _int(r["total"]),
        "sent": _int(r["sent"]),
        "accepted": accepted,
        "decided": decided,
        "onboarded": _int(r["onboarded"]),
        "pending": _int(r["pending"]),
        "no_show": _int(r["no_show"]),
        "accept_rate": _rate(accepted, decided),
        "onboard_rate": _rate(_int(r["onboarded"]), accepted),
    }


def _q_verdicts(conn, p) -> list[dict]:
    sql = _CTE + """
    SELECT m.final_verdict AS name, count(*) AS n
      FROM recruitment_summaries m
      JOIN cohort co ON co.id = m.candidate_id
      JOIN scope_jobs sj ON sj.id = m.job_id
     WHERE coalesce(btrim(m.final_verdict), '') <> ''
     GROUP BY 1
     ORDER BY n DESC
    """
    return [{"name": r["name"], "value": _int(r["n"])} for r in _rows(conn, sql, p)]


def _q_offer_status(conn, p) -> list[dict]:
    labels = {
        "draft": "草稿",
        "pending_approval": "待审批",
        "rejected": "审批驳回",
        "sent": "已发出待确认",
        "accepted": "已接受",
        "declined": "已婉拒",
        "expired": "已超期",
        "withdrawn": "已撤回",
        "onboarded": "已入职",
        "no_show": "未报到",
    }
    sql = _CTE + """
    SELECT o.status AS status, count(*) AS n
      FROM offers o
      JOIN cohort co ON co.id = o.candidate_id
      JOIN scope_jobs sj ON sj.id = o.job_id
     GROUP BY 1
    """
    rows = _rows(conn, sql, p)
    out = [
        {"key": r["status"], "name": labels.get(r["status"], r["status"]), "value": _int(r["n"])}
        for r in rows
    ]
    out.sort(key=lambda x: -x["value"])
    return out


def _q_interviewers(conn, p) -> list[dict]:
    sql = _CTE + """
    SELECT coalesce(nullif(btrim(o.interviewer), ''), '未填写') AS name,
           count(*)                                            AS sessions,
           round(avg(o.score)::numeric, 1)                     AS avg_score,
           count(*) FILTER (WHERE o.decision = 'pass')         AS passed
      FROM offline_interviews o
      JOIN cohort co ON co.id = o.candidate_id
      JOIN scope_jobs sj ON sj.id = o.job_id
     WHERE o.status = 'done'
     GROUP BY 1
     ORDER BY sessions DESC, name
    """
    return [
        {
            "name": r["name"],
            "sessions": _int(r["sessions"]),
            "avg_score": _num(r["avg_score"]),
            "passed": _int(r["passed"]),
            "pass_rate": _rate(_int(r["passed"]), _int(r["sessions"])),
        }
        for r in _rows(conn, sql, p)
    ]


def _q_stores(conn, p) -> list[dict]:
    sql = _CTE + """
    SELECT coalesce(nullif(btrim(o.store_name), ''), '未填写') AS name,
           count(*)                                           AS offers,
           count(*) FILTER (WHERE o.status IN ('accepted', 'onboarded', 'no_show')) AS accepted,
           count(*) FILTER (WHERE o.status = 'onboarded')     AS onboarded
      FROM offers o
      JOIN cohort co ON co.id = o.candidate_id
      JOIN scope_jobs sj ON sj.id = o.job_id
     GROUP BY 1
     ORDER BY offers DESC, name
    """
    return [
        {
            "name": r["name"],
            "offers": _int(r["offers"]),
            "accepted": _int(r["accepted"]),
            "onboarded": _int(r["onboarded"]),
        }
        for r in _rows(conn, sql, p)
    ]


def _q_trend(conn, p, start: datetime, days: int) -> dict:
    """按天事件趋势（事件口径，与 cohort 口径互补）。"""
    trend_sql = _CTE + """
    SELECT
      (SELECT json_agg(json_build_object('d', d, 'v', v))
         FROM (SELECT (c.created_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v
                 FROM candidates c
                WHERE c.created_at >= %(start)s
                  AND EXISTS (SELECT 1 FROM pair p WHERE p.candidate_id = c.id)
                GROUP BY 1) x) AS created,
      (SELECT json_agg(json_build_object('d', d, 'v', v))
         FROM (SELECT (rp.created_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v
                 FROM session_reports rp
                 JOIN interview_sessions s ON s.id = rp.session_id
                 JOIN interview_templates t ON t.id = s.template_id
                 JOIN scope_jobs sj ON sj.id = t.job_id
                WHERE rp.created_at >= %(start)s
                GROUP BY 1) x) AS ai_done,
      (SELECT json_agg(json_build_object('d', d, 'v', v))
         FROM (SELECT (o.sent_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v
                 FROM offers o
                 JOIN scope_jobs sj ON sj.id = o.job_id
                WHERE o.sent_at >= %(start)s
                GROUP BY 1) x) AS offer_sent,
      (SELECT json_agg(json_build_object('d', d, 'v', v))
         FROM (SELECT (o.onboarded_at AT TIME ZONE %(tz)s)::date AS d, count(*) AS v
                 FROM offers o
                 JOIN scope_jobs sj ON sj.id = o.job_id
                WHERE o.onboarded_at >= %(start)s
                GROUP BY 1) x) AS onboarded
    """
    row = _rows(conn, trend_sql, {**p, "tz": TZ_CN})[0]

    # 统一日期轴：最近 N 天（days<=0 时按数据实际范围至少铺 14 天）
    end_day = datetime.now(CN_TZ).date()
    span = days if days > 0 else 30
    axis = [(end_day - timedelta(days=span - 1 - i)) for i in range(span)]
    axis_str = [d.isoformat() for d in axis]
    index = {d: i for i, d in enumerate(axis_str)}

    def _pack(raw) -> list[int]:
        out = [0] * len(axis_str)
        for item in raw or []:
            key = item["d"]
            if isinstance(key, date):
                key = key.isoformat()
            if key in index:
                out[index[key]] = _int(item["v"])
        return out

    return {
        "axis": axis_str,
        "series": {
            "created": _pack(row["created"]),
            "ai_done": _pack(row["ai_done"]),
            "offer_sent": _pack(row["offer_sent"]),
            "onboarded": _pack(row["onboarded"]),
        },
    }


def _q_recent(conn, p, limit: int = 25) -> list[dict]:
    sql = _CTE + """
    SELECT co.id::text                    AS candidate_id,
           co.name                        AS candidate_name,
           j.title                        AS job_title,
           m.screening_score              AS screening_score,
           m.ai_interview_score           AS ai_score,
           m.offline_score                AS offline_score,
           m.final_score                  AS final_score,
           m.final_verdict                AS verdict,
           m.stage                        AS stage,
           m.snapshot_at                  AS snapshot_at,
           coalesce(qa.n, 0)              AS qa_count
      FROM recruitment_summaries m
      JOIN job_postings j ON j.id = m.job_id
      JOIN cohort co ON co.id = m.candidate_id
      JOIN scope_jobs sj ON sj.id = m.job_id
      -- 该候选人已抽取的问答条数：有则可从明细直接点开「问了什么 / 怎么回答」
      LEFT JOIN LATERAL (
            SELECT coalesce(sum(jsonb_array_length(oi.qa_extract -> 'questions')), 0)::int AS n
              FROM offline_interviews oi
             WHERE oi.candidate_id = co.id
               AND jsonb_typeof(oi.qa_extract -> 'questions') = 'array'
      ) qa ON TRUE
     ORDER BY m.snapshot_at DESC
     LIMIT %(limit)s
    """
    rows = _rows(conn, sql, {**p, "limit": limit})
    out = []
    for r in rows:
        snap = r["snapshot_at"]
        qa_count = _int(r["qa_count"]) or 0
        out.append(
            {
                "candidate_id": r["candidate_id"],
                "candidate_name": r["candidate_name"],
                "job_title": r["job_title"],
                "screening_score": _num(r["screening_score"]),
                "ai_score": _num(r["ai_score"]),
                "offline_score": _num(r["offline_score"]),
                "final_score": _num(r["final_score"]),
                "verdict": r["verdict"] or "",
                "stage": r["stage"] or "",
                "snapshot_at": (snap.astimezone(CN_TZ).strftime("%m-%d %H:%M") if snap else ""),
                "has_qa": qa_count > 0,
                "qa_count": qa_count,
            }
        )
    return out


def _q_walkin_qa(conn, p, limit: int = 25) -> list[dict]:
    """到店直招（无 ``recruitment_summaries`` 汇总）但已抽取问答的候选人。

    到店直招直接进线下复试，从不经过简历初筛/汇总，故在汇总明细里天然看不到，
    这批人恰恰是妙记逐字稿的主要来源；这里按「人」聚合成与 ``_q_recent`` 同形的行，
    合并进明细表后就能直接点开问答。

    没有汇总口径的列一律留空（初筛 / AI 面试 / 最终分 / 结论），不做臆造：
    线下复试取整场评分 ``ai_score`` 的最大值，阶段标 ``walk_in``。
    """
    sql = """
    SELECT co.id::text                                              AS candidate_id,
           co.name                                                  AS candidate_name,
           (array_agg(j.title ORDER BY o.created_at DESC))[1]       AS job_title,
           max(o.ai_score)                                          AS offline_score,
           max(coalesce(o.qa_extracted_at, o.transcript_at, o.created_at)) AS snapshot_at,
           coalesce(sum(CASE WHEN jsonb_typeof(o.qa_extract -> 'questions') = 'array'
                             THEN jsonb_array_length(o.qa_extract -> 'questions')
                             ELSE 0 END), 0)::int                   AS qa_count
      FROM offline_interviews o
      JOIN candidates co ON co.id = o.candidate_id
      JOIN job_postings j ON j.id = o.job_id
     WHERE jsonb_typeof(o.qa_extract -> 'questions') = 'array'
       AND NOT EXISTS (SELECT 1 FROM recruitment_summaries m WHERE m.candidate_id = co.id)
       AND (%(scope_all)s::boolean OR NOT (j.title LIKE ANY(%(test_patterns)s::text[])))
     GROUP BY co.id, co.name
     ORDER BY snapshot_at DESC
     LIMIT %(limit)s
    """
    out: list[dict] = []
    for r in _rows(conn, sql, {**p, "limit": limit}):
        snap = r["snapshot_at"]
        qa_count = _int(r["qa_count"]) or 0
        out.append(
            {
                "candidate_id": r["candidate_id"],
                "candidate_name": r["candidate_name"],
                "job_title": r["job_title"] or "—",
                "screening_score": None,
                "ai_score": None,
                "offline_score": _num(r["offline_score"]),
                "final_score": None,
                "verdict": "",
                "stage": "walk_in",
                "snapshot_at": (snap.astimezone(CN_TZ).strftime("%m-%d %H:%M") if snap else ""),
                "has_qa": qa_count > 0,
                "qa_count": qa_count,
            }
        )
    return out


def _merge_recent(summary_rows: list[dict], walkin_rows: list[dict], limit: int = 25) -> list[dict]:
    """把汇总行与到店直招行合成一张明细表：同一候选人以汇总行为准。

    排序用展示用的 ``MM-DD HH:MM`` 串降序——明细只取最近 25 条，窗口内不会跨年，
    字符串比较等价于时间比较，无需额外带一列时间戳。
    """
    seen = {r["candidate_id"] for r in summary_rows}
    rows = list(summary_rows) + [r for r in walkin_rows if r["candidate_id"] not in seen]
    rows.sort(key=lambda r: r.get("snapshot_at") or "", reverse=True)
    return rows[:limit]


def _q_activity(conn, p, limit: int = 12) -> list[dict]:
    """最新动静：建档 / 面完 / 发 Offer / 入职，取最近几条。"""
    sql = _CTE + """
    SELECT * FROM (
        SELECT 'candidate' AS kind, co.name AS who, coalesce(j.title, '—') AS what,
               co.created_at AS ts
          FROM cohort co
          LEFT JOIN LATERAL (
              SELECT jj.title
                FROM pair p2
                JOIN job_postings jj ON jj.id = p2.job_id
               WHERE p2.candidate_id = co.id
               LIMIT 1
          ) j ON true
        UNION ALL
        SELECT 'ai_done', co.name, coalesce(jp.title, '—'), rp.created_at
          FROM session_reports rp
          JOIN interview_sessions s ON s.id = rp.session_id
          JOIN interview_templates t ON t.id = s.template_id
          JOIN job_postings jp ON jp.id = t.job_id
          JOIN cohort co ON co.id = s.candidate_id
          JOIN scope_jobs sj ON sj.id = t.job_id
        UNION ALL
        SELECT 'offer', o.candidate_name, o.job_title, o.sent_at
          FROM offers o
          JOIN cohort co ON co.id = o.candidate_id
          JOIN scope_jobs sj ON sj.id = o.job_id
         WHERE o.sent_at IS NOT NULL
        UNION ALL
        SELECT 'onboard', o.candidate_name, o.job_title, o.onboarded_at
          FROM offers o
          JOIN cohort co ON co.id = o.candidate_id
          JOIN scope_jobs sj ON sj.id = o.job_id
         WHERE o.onboarded_at IS NOT NULL
    ) t
    WHERE ts IS NOT NULL
    ORDER BY ts DESC
    LIMIT %(limit)s
    """
    rows = _rows(conn, sql, {**p, "limit": limit})
    kind_label = {"candidate": "新建档", "ai_done": "AI 面试完成", "offer": "发出 Offer", "onboard": "已入职"}
    return [
        {
            "kind": r["kind"],
            "kind_label": kind_label.get(r["kind"], r["kind"]),
            "who": r["who"] or "—",
            "what": r["what"] or "—",
            "ts": r["ts"].astimezone(CN_TZ).strftime("%m-%d %H:%M") if r["ts"] else "",
        }
        for r in rows
    ]


def _q_team_activity(conn, p) -> list[dict]:
    """后台账号活跃度（HR / 管理员 / 店长）—— 最后登录 + 近期使用强度。

    数据源与口径：

    - ``admin_users.last_login_at``：登录成功时由后端写入，是「最后登录」的权威值；
    - ``audit_logs`` 的 ``auth.login.success``：近 N 天**登录天数**（按北京时区去重到天）
      与登录次数。天数比次数更能反映「有没有在日常干活」，次数容易被连续登录放大；
    - ``audit_logs`` 里非 ``auth.*`` 的动作：近 N 天业务操作次数。

    ⚠ 这是全站唯一读取 ``admin_users`` 的地方：只 SELECT 展示字段，
    **不触碰 password_hash / failed_login_count / locked_until**。

    以 ``__`` 开头的账号（如 ``__smoke_offer__``）是自检脚本用的，不是真人，
    不参与绩效评定，直接排除，否则会出现「从未登录却操作几十次」的怪数据。
    """
    sql = """
    SELECT u.id::text                                        AS user_id,
           u.username,
           u.display_name,
           u.role,
           u.is_active,
           u.last_login_at,
           EXTRACT(EPOCH FROM (now() - u.last_login_at)) / 86400.0 AS days_since_login,
           coalesce(ls.login_count, 0)                       AS login_count,
           coalesce(ls.login_dates, ARRAY[]::text[])         AS login_dates,
           coalesce(ac.action_count, 0)                      AS action_count
      FROM admin_users u
      LEFT JOIN (
          SELECT actor_id,
                 count(*) AS login_count,
                 array_agg(
                     DISTINCT to_char((occurred_at AT TIME ZONE %(tz)s)::date, 'YYYY-MM-DD')
                     ORDER BY to_char((occurred_at AT TIME ZONE %(tz)s)::date, 'YYYY-MM-DD')
                 ) AS login_dates
            FROM audit_logs
           WHERE action = 'auth.login.success'
             AND occurred_at >= %(start)s
           GROUP BY actor_id
      ) ls ON ls.actor_id = u.id
      LEFT JOIN (
          SELECT actor_id, count(*) AS action_count
            FROM audit_logs
           WHERE substr(action, 1, 5) <> 'auth.'   -- 排除登录/登出/改密等账号动作
             AND occurred_at >= %(start)s
           GROUP BY actor_id
      ) ac ON ac.actor_id = u.id
     WHERE left(u.username, 2) <> '__'   -- 排除自检 / 系统账号
     ORDER BY u.last_login_at DESC NULLS LAST, u.username
    """
    role_labels = {"admin": "管理员", "hr": "HR", "store_manager": "店长"}

    out: list[dict] = []
    for r in _rows(conn, sql, p):
        last = r["last_login_at"]
        days = _num(r["days_since_login"])

        if last is None:
            status = "never"           # 从未登录
        elif days is not None and days <= 7:
            status = "active"          # 7 天内来过
        elif days is not None and days <= 30:
            status = "idle"            # 一个月内来过
        else:
            status = "inactive"        # 超过一个月没来

        local = last.astimezone(CN_TZ) if last else None
        # 登录日期列表用于前端画「近 30 天热力条」；全部时间口径下截断，避免传几百个日期
        dates = sorted(r["login_dates"] or [])
        out.append(
            {
                "user_id": r["user_id"],
                "username": r["username"],
                "display_name": (r["display_name"] or "").strip(),
                "role": r["role"],
                "role_label": role_labels.get(r["role"], r["role"]),
                "is_active": bool(r["is_active"]),
                # 最后登录：日期用于「绩效评定」口径，时间点用于看今天是否来过
                "last_login_at": local.strftime("%Y-%m-%d %H:%M") if local else None,
                "last_login_date": local.strftime("%Y-%m-%d") if local else None,
                "last_login_time": local.strftime("%H:%M") if local else None,
                "days_since_login": round(days, 1) if days is not None else None,
                "login_days": len(dates),
                "login_dates": dates[-60:],
                "login_count": _int(r["login_count"]),
                "action_count": _int(r["action_count"]),
                "status": status,
            }
        )
    return out


def _q_interview_qa(conn, p, limit: int = 50) -> list[dict]:
    """面试录音结构化问答（面试官问题 + 候选人回答亮点）—— 明细「点击查看」用。

    口径：只看**已抽取过问答**（``offline_interviews.qa_extract`` 非空）的线下复试记录。

    刻意**不接 cohort 漏斗口径**：到店直招候选人不一定有 ``recruitment_summaries`` 汇总，
    若按汇总表关联会漏掉这批人；这里独立成查询，只看「有没有抽取结果」。
    ``scope=real`` 时同样按岗位名排除联调/演示数据。
    """
    sql = """
    SELECT o.id::text                                              AS id,
           c.id::text                                              AS candidate_id,
           c.name                                                  AS candidate_name,
           j.title                                                 AS job_title,
           o.interviewer                                           AS interviewer,
           o.source                                                AS source,
           coalesce(o.qa_extracted_at, o.transcript_at, o.created_at) AS at,
           o.transcript_status                                     AS transcript_status,
           o.qa_extract                                            AS qa_extract
      FROM offline_interviews o
      JOIN candidates c ON c.id = o.candidate_id
      JOIN job_postings j ON j.id = o.job_id
     WHERE o.qa_extract IS NOT NULL
       AND o.qa_extract::text NOT IN ('{}', 'null', '[]')
       AND (%(scope_all)s::boolean OR NOT (j.title LIKE ANY(%(test_patterns)s::text[])))
     ORDER BY at DESC
     LIMIT %(limit)s
    """
    out: list[dict] = []
    for r in _rows(conn, sql, {**p, "limit": limit}):
        qa = r["qa_extract"] if isinstance(r["qa_extract"], dict) else {}
        raw_qs = qa.get("questions")
        questions = raw_qs if isinstance(raw_qs, list) else []
        at = r["at"]
        out.append(
            {
                "id": r["id"],
                "candidate_id": r["candidate_id"],
                "candidate_name": r["candidate_name"] or "—",
                "job_title": r["job_title"] or "—",
                "interviewer": (r["interviewer"] or "").strip() or "—",
                "source": r["source"] or "",
                "source_label": "到店直招" if r["source"] == "walk_in" else "线上投递",
                "transcript_status": r["transcript_status"] or "",
                "at": at.astimezone(CN_TZ).strftime("%m-%d %H:%M") if at else "",
                "question_count": len(questions),
                "overall": (qa.get("overall") or "").strip(),
                "questions": [
                    {
                        "question": str(q.get("question") or "").strip(),
                        "answer_summary": str(q.get("answer_summary") or "").strip(),
                        "answer_quotes": [
                            str(t).strip()
                            for t in (q.get("answer_quotes") or [])
                            if str(t).strip()
                        ][:3],
                        "highlights": [
                            str(h).strip()
                            for h in (q.get("highlights") or [])
                            if str(h).strip()
                        ],
                    }
                    for q in questions
                    if isinstance(q, dict)
                ],
            }
        )
    return out


# ── 组装 ─────────────────────────────────────────────────────


def build_dashboard(days: int, scope: str) -> dict:
    scope_all = scope == "all"
    now = datetime.now(timezone.utc)
    start = datetime(1970, 1, 1, tzinfo=timezone.utc) if days <= 0 else now - timedelta(days=days)

    params = {
        "scope_all": scope_all,
        "test_patterns": TEST_PATTERNS,
        "start": start,
        "tz": TZ_CN,
    }

    with _connect() as conn:
        meta = _q_scope_meta(conn, params)
        funnel = _q_funnel(conn, params)
        cycle = _q_cycle(conn, params)
        jobs = _q_jobs(conn, params)
        channels = _q_channels(conn, params)
        scores = _q_scores(conn, params)
        verdicts = _q_verdicts(conn, params)
        offer_metrics = _q_offer_metrics(conn, params)
        offer_status = _q_offer_status(conn, params)
        interviewers = _q_interviewers(conn, params)
        stores = _q_stores(conn, params)
        trend = _q_trend(conn, params, start, days)
        recent = _q_recent(conn, params)
        recent_walkin = _q_walkin_qa(conn, params)
        activity = _q_activity(conn, params)
        team = _q_team_activity(conn, params)
        interview_qa = _q_interview_qa(conn, params)

    # 到店直招没有汇总行，单独查出来合并进明细（问答列才有入口）
    recent = _merge_recent(recent, recent_walkin)

    # 派生指标
    ai_rate = _rate(funnel["ai_done"], funnel["created"])
    accept_rate = offer_metrics["accept_rate"]
    onboard_rate = offer_metrics["onboard_rate"]

    kpi_bundle = {
        "candidates": funnel["created"],
        "active_jobs": meta["active_jobs"],
        "ai_done": funnel["ai_done"],
        "ai_rate": ai_rate,
        "screened": funnel["screened"],
        "offline_done": funnel["offline_done"],
        "offer_sent": offer_metrics["sent"],
        "offer_total": offer_metrics["total"],
        "offer_pending": offer_metrics["pending"],
        "accept_rate": accept_rate,
        "onboarded": funnel["onboarded"],
        "onboard_rate": onboard_rate,
        "no_show": offer_metrics["no_show"],
        "days_to_offer": cycle["days_to_offer"],
        "days_to_onboard": cycle["days_to_onboard"],
    }
    scorecard = build_scorecard(kpi_bundle, funnel)

    return {
        "generated_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "meta": {
            "days": days,
            "scope": scope,
            "scope_label": "全部数据" if scope_all else "仅真实岗位",
            "window_start": (
                None if days <= 0 else (now - timedelta(days=days)).astimezone(CN_TZ).strftime("%Y-%m-%d")
            ),
            "window_label": "全部时间" if days <= 0 else f"近 {days} 天",
            "active_jobs": meta["active_jobs"],
            "total_jobs": meta["total_jobs"],
        },
        "kpi": kpi_bundle,
        "scorecard": scorecard,
        "funnel": funnel,
        "cycle": cycle,
        "offer_metrics": offer_metrics,
        "trend": trend,
        "jobs": jobs,
        "channels": channels,
        "scores": scores,
        "verdicts": verdicts,
        "offer_status": offer_status,
        "interviewers": interviewers,
        "stores": stores,
        "recent": recent,
        "activity": activity,
        "interview_qa": interview_qa,
        "team": team,
        "team_summary": {
            "total": len(team),
            "active": sum(1 for t in team if t["status"] == "active"),
            "idle": sum(1 for t in team if t["status"] == "idle"),
            "inactive": sum(1 for t in team if t["status"] == "inactive"),
            "never": sum(1 for t in team if t["status"] == "never"),
            "disabled": sum(1 for t in team if not t["is_active"]),
            "window_label": "全部时间" if days <= 0 else f"近 {days} 天",
        },
    }


# ── 缓存 ─────────────────────────────────────────────────────

_cache: dict[tuple[int, str], tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def get_dashboard(days: int, scope: str) -> dict:
    if CACHE_TTL <= 0:
        return build_dashboard(days, scope)

    key = (days, scope)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    data = build_dashboard(days, scope)
    with _cache_lock:
        _cache[key] = (time.time() + CACHE_TTL, data)
    return data


# ── 路由 ─────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    try:
        with _connect() as conn:
            _rows(conn, "SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"code": 1, "message": f"数据库不可用：{exc}"})
    return {"code": 0, "message": "ok", "data": {"status": "healthy"}}


@app.get("/api/kpi-config")
def api_kpi_config():
    """返回 KPI 记分卡配置（目标值 / 阈值 / 负责人 / 口径），供前端展示指标字典与说明。"""
    return {"code": 0, "message": "ok", "data": KPI_CONFIG}


@app.get("/api/dashboard")
def api_dashboard(
    days: int = Query(default=30, ge=0, le=3650, description="时间窗天数，0 表示全部时间"),
    scope: str | None = Query(default=None, pattern="^(real|all)$", description="real=排除联调/演示岗位"),
):
    try:
        data = get_dashboard(days, scope or DEFAULT_SCOPE)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content={"code": 1, "message": f"数据查询失败：{exc}"},
        )
    return {"code": 0, "message": "ok", "data": data}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), media_type="text/html")


# 静态资源（echarts 等）；html=False 避免覆盖上面的根路由
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── 可选访问口令 ─────────────────────────────────────────────


@app.middleware("http")
async def _gate(request: Request, call_next):
    """未设置 BI_ACCESS_TOKEN 时完全放行；设置后要求 ?token= 或 Cookie。"""
    if not ACCESS_TOKEN or request.url.path in ("/api/health",):
        return await call_next(request)

    cookie = request.cookies.get("bi_access", "")
    if cookie == ACCESS_TOKEN:
        return await call_next(request)

    query_token = request.query_params.get("token", "")
    if query_token and query_token == ACCESS_TOKEN:
        # 去掉 URL 里的 token，避免被截图 / 转发带出去
        clean = str(request.url.remove_query_params("token"))
        resp = RedirectResponse(clean, status_code=302)
        resp.set_cookie(
            "bi_access",
            ACCESS_TOKEN,
            max_age=60 * 60 * 12,
            httponly=True,
            samesite="lax",
        )
        return resp

    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"code": 401, "message": "未授权"})

    return HTMLResponse(
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>招聘 BI 看板</title></head>
<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b1020;color:#e6edf7;font-family:system-ui,-apple-system,'PingFang SC',sans-serif">
<div style="text-align:center">
<div style="font-size:48px;margin-bottom:16px">🔒</div>
<div style="font-size:20px;font-weight:600;margin-bottom:8px">需要访问口令</div>
<div style="color:#8b9bb4;font-size:14px">请在地址后追加 <code>?token=你的口令</code> 后访问</div>
</div></body></html>""",
        status_code=401,
    )


# ── 实时推送（SSE）──────────────────────────────────────────────
#
# 为什么不是「把前端轮询间隔调小」：
#   - 间隔再小也是延迟，且数据没变时照样在查库，大屏越多空转越多；
#   - 真正要的是「数据变了才刷新」。
#
# 做法：后台每 REALTIME_INTERVAL 秒算一次「数据指纹」（8 张业务表的行数 +
# 最新 updated_at 拼成的短串，走 count/max，比全量聚合便宜得多）。
# 指纹一变 → 清掉聚合缓存 → SSE 广播「数据变了」→
# 各块大屏各自拉一次 /api/dashboard（同参数命中同一份缓存，不重复计算）。
#
# 代价：数据变更到上屏最多 REALTIME_INTERVAL 秒（默认 3 秒），展示场景足够。

# 参与「数据是否变化」判断的业务表。表名是硬编码常量，不来自外部输入。
_WATCH_TABLES = (
    "job_postings",
    "candidates",
    "resume_screenings",
    "interview_sessions",
    "session_reports",
    "offline_interviews",
    "recruitment_summaries",
    "offers",
    "admin_users",   # 登录会写 last_login_at，改密码/停用也会改 updated_at
    "audit_logs",    # 只增不改，用 count 就能感知
)

_FINGERPRINT_SQL = " UNION ALL ".join(
    (
        f"SELECT '{t}' AS t, count(*)::text AS c, coalesce(max(updated_at)::text, '') AS u FROM {t}"
        if t != "audit_logs"  # audit_logs 没有 updated_at（只写不改）
        else f"SELECT '{t}' AS t, count(*)::text AS c, '' AS u FROM {t}"
    )
    for t in _WATCH_TABLES
)

# 数据变更探测间隔（秒）
REALTIME_INTERVAL = max(1.0, float(os.getenv("BI_REALTIME_INTERVAL", "3")))

# SSE 订阅者队列（单 worker 部署，进程内广播；compose 已固定 --workers 1）
_subscribers: set[asyncio.Queue] = set()

# 上一次的数据指纹；None 表示尚未建立基线
_last_fingerprint: str | None = None


def _fingerprint() -> str:
    """轻量数据指纹：任一表的行数或 updated_at 变化都会改变结果。"""
    with _connect() as conn:
        rows = _rows(conn, _FINGERPRINT_SQL)
    return "|".join(f"{r['t']}:{r['c']}:{r['u']}" for r in rows)


async def _watch_loop() -> None:
    """后台任务：定期比对数据指纹，变化时清缓存并广播。"""
    global _last_fingerprint

    # 启动先记基线，避免服务一起来就误报「数据变了」
    try:
        _last_fingerprint = await asyncio.to_thread(_fingerprint)
    except Exception:  # noqa: BLE001 - 数据库还没就绪不应拖垮服务
        logger.warning("实时探测初始化失败，稍后自动重试", exc_info=True)

    while True:
        try:
            await asyncio.sleep(REALTIME_INTERVAL)
            fp = await asyncio.to_thread(_fingerprint)
            if _last_fingerprint is not None and fp == _last_fingerprint:
                continue

            _last_fingerprint = fp
            with _cache_lock:
                _cache.clear()

            if _subscribers:
                logger.info("检测到数据变化，通知 %d 块看板", len(_subscribers))
            for queue in list(_subscribers):
                try:
                    queue.put_nowait("data_changed")
                except asyncio.QueueFull:
                    # 客户端消费不过来就丢掉这次通知，等下一条；绝不能阻塞广播
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 单次探测失败不影响下一轮
            logger.warning("实时探测失败，下一轮重试", exc_info=True)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """起一个数据变更探测任务，进程关闭时优雅取消。"""
    task = asyncio.create_task(_watch_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("停止实时探测任务时异常", exc_info=True)


# app 在文件上方已创建，这里回填 lifespan（定义顺序所限，不能用构造参数）
app.router.lifespan_context = _lifespan


@app.get("/api/stream")
async def api_stream(request: Request):
    """SSE 实时通道：数据一有变化就通知前端立刻刷新。

    刻意只推「变了」这个信号，不推数据本身：
    - 一块大屏可能同时开多个标签、或用不同口径（days/scope），推数据得按连接各算一份；
    - 推信号后前端各自去取，相同参数命中同一份缓存。

    空闲时每 20 秒发一次注释心跳，防止中间的 nginx / 云负载均衡把连接掐断。
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subscribers.add(queue)
    logger.info("看板接入实时通道，当前在线 %d", len(_subscribers))

    async def event_gen():
        try:
            # retry 告诉浏览器断线后 3 秒重连
            yield "retry: 3000\nevent: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event}\ndata: {{}}\n\n"
        finally:
            _subscribers.discard(queue)
            logger.info("看板断开实时通道，当前在线 %d", len(_subscribers))

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # 反代禁用缓冲，否则事件会被攒着不发
            "Connection": "keep-alive",
        },
    )
