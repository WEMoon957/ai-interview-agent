"""飞书妙记轮询任务：定时搜索店长妙记 → 姓名匹配（短码兜底）→ 拉逐字稿 → 整场评分。

由 ``main.py`` lifespan 启动 asyncio 后台任务（``FEISHU_MINUTES_ENABLED`` 开关）。
设计要点：
- **幂等**：以 ``walk_in_code`` / ``minutes_token`` + ``transcript_status`` 状态机保证重复轮询无副作用；
- **防串场**：姓名命中多条妙记时**不自动绑定**（保持 pending，转人工），漏抓可发现、错绑发现不了；
- **隔离**：任何一步失败只记日志 + 置 ``failed``，绝不抛出、不影响主流程；
- **降级**：LLM 评分失败只跳过 AI 参考分，不影响逐字稿落库。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.db.models import InterviewTemplate, OfflineInterview
from app.db.session import SessionLocal
from app.services.feishu_minutes import (
    extract_walk_in_code,
    get_transcript,
    match_candidate_name,
    search_minutes,
)
from app.services.minutes_auth import get_valid_user_token, list_authorizations
from app.services.qa_extraction import extract_interview_qa
from app.services.summary import auto_rebuild_summary
from app.services.whole_interview_scoring import DEFAULT_RUBRIC, score_whole_interview

logger = logging.getLogger(__name__)

# 转写阶段需要处理的状态
_MATCHABLE_STATUS = ("pending", "failed")
_TRANSCRIBE_STATUS = ("matched", "failed")
# 约面日前后各覆盖几天，避免只在「最近 N 天」窗口内找不到面试当天的录音
_SCHEDULED_PAD_DAYS = 2
# 飞书 search 单次查询的时间跨度上限（天）
_MAX_SEARCH_SPAN_DAYS = 30



def _resolve_rubric(db, row: OfflineInterview) -> list:
    """取该岗位最新面试模板的 rubric；没有模板则用默认维度。"""
    if row.job_id:
        tpl = (
            db.query(InterviewTemplate)
            .filter(InterviewTemplate.job_id == row.job_id)
            .order_by(InterviewTemplate.created_at.desc())
            .first()
        )
        if tpl is not None and tpl.rubric:
            return tpl.rubric
    return DEFAULT_RUBRIC


def _bind_minutes(db, row: OfflineInterview, item: dict) -> bool:
    """把命中的妙记绑定到线下复试记录，置 matched。返回是否发生绑定。"""
    token = (item.get("token") or "").strip()
    if not token:
        return False
    if row.minutes_token == token:
        return False
    row.minutes_token = token
    row.minutes_url = item.get("url") or f"https://www.feishu.cn/minutes/{token}"
    row.minutes_title = item.get("title") or ""
    row.transcript_status = "matched"
    db.commit()
    return True


def _candidate_name(row: OfflineInterview) -> str:
    """记录关联的候选人姓名（妙记标题的匹配依据）。"""
    candidate = getattr(row, "candidate", None)
    return ((getattr(candidate, "name", "") or "") if candidate else "").strip()


def _search_window(
    row: OfflineInterview, now: datetime, settings: Settings
) -> tuple[datetime, datetime]:
    """妙记搜索时间窗：至少覆盖最近 N 天，并额外覆盖约面日前后各 2 天。

    姓名匹配比短码模糊，窗口越窄越安全；但不能只取约面日——记录改期或补录时
    录音可能落在别处，故与「最近 N 天」取并集。
    """
    days = max(1, int(settings.feishu_minutes_search_window_days))
    start = now - timedelta(days=days)
    end = now
    scheduled = getattr(row, "scheduled_at", None)
    if scheduled is not None:
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        start = min(start, scheduled - timedelta(days=_SCHEDULED_PAD_DAYS))
        end = max(end, scheduled + timedelta(days=_SCHEDULED_PAD_DAYS))
    # 飞书 search 单次时间跨度上限 1 个月，超了接口直接报错；宁可缩短窗口也不要整轮失败
    if end - start > timedelta(days=_MAX_SEARCH_SPAN_DAYS):
        start = end - timedelta(days=_MAX_SEARCH_SPAN_DAYS)
    return start, end


def _pick_by_name(items: list[dict], name: str) -> list[dict]:
    """标题含该候选人姓名的妙记（可能多条 → 调用方须按歧义处理）。"""
    if not name:
        return []
    return [it for it in items if match_candidate_name(it.get("title") or "", name)]


def _pick_by_code(items: list[dict], code: str | None) -> list[dict]:
    """标题含该 6 位短码的妙记（老流程标题的兜底识别）。"""
    if not code:
        return []
    return [it for it in items if extract_walk_in_code(it.get("title") or "") == code]


def _owner_open_id(row: OfflineInterview, settings: Settings) -> str:
    """妙记归属人：优先记录上的店长 open_id，其次全局兜底配置。"""
    return (
        row.interviewer_open_id
        or settings.feishu_minutes_default_owner_open_id
        or ""
    ).strip()


def _resolve_owner_token(
    db, row: OfflineInterview, settings: Settings
) -> tuple[str, str | None]:
    """解析「归属人 + 可用 user token」。

    过渡期兜底：记录上没写归属人、且系统里**只有一个**有效授权时，直接用该授权
    （典型场景：先只开 HR 一个人的通道）。多个授权时不猜，避免读错人的妙记。
    """
    owner_id = _owner_open_id(row, settings)
    if owner_id:
        token = get_valid_user_token(db, owner_id, settings)
        if token:
            return owner_id, token

    active = [r for r in list_authorizations(db) if r.status == "active"]
    if len(active) == 1:
        token = get_valid_user_token(db, active[0].open_id, settings)
        if token:
            return active[0].open_id, token
    return owner_id, None


def transcribe_and_score(
    db, row: OfflineInterview, settings: Settings | None = None
) -> bool:
    """拉逐字稿 → 整场评分 → 抽取结构化问答。返回是否成功（转写成功即 True）。

    优先用妙记归属人的 user_access_token（可读私有妙记）；无授权时降级 bot
    （只能读已公开妙记）。评分与问答抽取各自独立降级，任一失败都不影响逐字稿落库。
    供轮询任务与手动触发接口复用（公开）。
    """
    ok, _ = transcribe_and_score_detailed(db, row, settings)
    return ok


def transcribe_and_score_detailed(
    db, row: OfflineInterview, settings: Settings | None = None
) -> tuple[bool, str]:
    """``transcribe_and_score`` 的带原因版本：返回 (是否成功, 失败原因)。

    失败原因直接透给手动绑定接口，避免店长只看到「转写失败」却不知道是权限问题。
    """
    settings = settings or get_settings()
    owner_id, user_token = _resolve_owner_token(db, row, settings)

    ok, text, err = get_transcript(
        row.minutes_token, settings=settings, access_token=user_token
    )
    if not ok:
        row.transcript_status = "failed"
        db.commit()
        logger.warning(
            "拉取妙记逐字稿失败 minute=%s owner=%s user_token=%s err=%s",
            row.minutes_token,
            owner_id or "-",
            "yes" if user_token else "no",
            err,
        )
        return False, err

    now = datetime.now(timezone.utc)
    row.transcript_text = text
    row.transcript_status = "transcribed"
    row.transcript_at = now
    db.commit()

    # 整场评分（失败不阻塞，仅跳过 AI 参考分）
    try:
        rubric = _resolve_rubric(db, row)
        job_desc = row.job.jd_text if row.job else ""
        result = score_whole_interview(text, job_desc, rubric)
        if result.get("total_score") is not None:
            row.ai_score = result["total_score"]
            row.ai_dim_scores = result["dim_scores"] or []
            row.ai_comment = result.get("comment") or ""
            row.ai_scored_at = datetime.now(timezone.utc)
            db.commit()
            # AI 参考分只影响展示，不改变「全链路完成」判定 → 静默重建快照即可
            auto_rebuild_summary(row.job_id, notify=False)
    except Exception:  # noqa: BLE001 - 评分失败不影响转写落库
        logger.exception("整场评分异常 minute=%s", row.minutes_token)

    # 结构化问答抽取（供 BI 看板「点击候选人看问了什么 / 怎么回答」）：
    # 与评分同样独立 try，失败不阻塞；抽不到内容时不写时间戳，留待 --all 回填重试。
    try:
        job = row.job
        qa = extract_interview_qa(
            text,
            job_title=job.title if job else "",
            job_desc=job.jd_text if job else "",
        )
        if qa.get("questions"):
            row.qa_extract = qa
            row.qa_extracted_at = datetime.now(timezone.utc)
            db.commit()
        else:
            logger.warning("面试问答抽取为空 minute=%s", row.minutes_token)
    except Exception:  # noqa: BLE001 - 抽取失败不影响转写与评分
        logger.exception("面试问答抽取异常 minute=%s", row.minutes_token)
        db.rollback()
    return True, ""


def poll_minutes_once(settings: Settings | None = None) -> dict:
    """执行一轮轮询，返回统计 dict（供日志/观测）。"""
    settings = settings or get_settings()
    stats = {"matched": 0, "transcribed": 0, "errors": 0, "ambiguous": 0}

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # 1) 匹配阶段：pending / failed(无 token) 的记录，按候选人姓名搜索妙记标题。
        #    飞书搜索是关键词搜索、没有全量列表接口，故必须带 query（姓名）而不是空 query 遍历。
        #    归属人若有个人授权，用其 user token（可搜私有妙记），否则降级 bot（只能搜公开）。
        pending = (
            db.query(OfflineInterview)
            .filter(
                OfflineInterview.walk_in_code.isnot(None),
                OfflineInterview.transcript_status.in_(_MATCHABLE_STATUS),
                OfflineInterview.minutes_token.is_(None),
            )
            .all()
        )
        for row in pending:
            owner_id, user_token = _resolve_owner_token(db, row, settings)
            if not owner_id:
                continue

            name = _candidate_name(row)
            query = name or row.walk_in_code or ""
            if not query:
                continue

            start, end = _search_window(row, now, settings)
            ok, items, err = search_minutes(
                owner_id,
                start,
                end,
                settings=settings,
                access_token=user_token,
                query=query,
            )
            if not ok:
                stats["errors"] += 1
                logger.warning("妙记搜索失败 owner=%s query=%s err=%s", owner_id, query, err)
                continue

            hit: dict | None = None
            how = ""
            hits = _pick_by_name(items, name)
            if len(hits) == 1:
                hit, how = hits[0], "name"
            elif len(hits) > 1:
                # 同名歧义：宁可漏抓也不能把 A 的录音绑到 B 的档案上（错绑是静默的）
                stats["ambiguous"] += 1
                logger.warning(
                    "姓名命中多条妙记，跳过自动绑定 offline=%s name=%s titles=%s",
                    getattr(row, "id", "-"),
                    name,
                    [it.get("title") for it in hits],
                )

            # 姓名未唯一命中时，用短码再搜一次兜底：老流程「标题写短码」的记录仍能认出来
            if hit is None and row.walk_in_code:
                ok_code, items_code, _ = search_minutes(
                    owner_id,
                    start,
                    end,
                    settings=settings,
                    access_token=user_token,
                    query=row.walk_in_code,
                )
                if ok_code:
                    by_code = _pick_by_code(items_code, row.walk_in_code)
                    if len(by_code) == 1:
                        hit, how = by_code[0], "code"

            if hit is None:
                continue

            if _bind_minutes(db, row, hit):
                stats["matched"] += 1
                logger.info(
                    "妙记匹配成功 offline=%s how=%s title=%s",
                    getattr(row, "id", "-"),
                    how,
                    hit.get("title"),
                )
            # 记录真实 owner：后续转写要用「妙记所有者本人」的 token 才能读私有妙记
            if not row.interviewer_open_id and hit.get("owner_id"):
                row.interviewer_open_id = hit["owner_id"]
                db.commit()

        # 2) 转写 + 评分阶段：matched / failed(有 token) 的记录
        to_transcribe = (
            db.query(OfflineInterview)
            .filter(
                OfflineInterview.minutes_token.isnot(None),
                OfflineInterview.transcript_status.in_(_TRANSCRIBE_STATUS),
            )
            .all()
        )
        for row in to_transcribe:
            if transcribe_and_score(db, row):
                stats["transcribed"] += 1
            else:
                stats["errors"] += 1
    finally:
        db.close()

    return stats


async def minutes_poll_loop(stop_event: asyncio.Event) -> None:
    """后台轮询循环：周期性执行 ``poll_minutes_once``，直到 stop_event 置位。"""
    settings = get_settings()
    interval = max(10, int(settings.feishu_minutes_poll_interval))
    logger.info("飞书妙记轮询任务启动，周期 %s 秒", interval)

    while not stop_event.is_set():
        try:
            stats = await asyncio.to_thread(poll_minutes_once, settings)
            if stats.get("matched") or stats.get("transcribed") or stats.get("errors"):
                logger.info("妙记轮询结果：%s", stats)
        except Exception:  # noqa: BLE001
            logger.exception("妙记轮询异常")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("飞书妙记轮询任务已停止")
