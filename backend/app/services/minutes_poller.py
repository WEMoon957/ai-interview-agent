"""飞书妙记轮询任务：定时搜索店长妙记 → 短码匹配 → 拉逐字稿 → 整场评分。

由 ``main.py`` lifespan 启动 asyncio 后台任务（``FEISHU_MINUTES_ENABLED`` 开关）。
设计要点：
- **幂等**：以 ``walk_in_code`` / ``minutes_token`` + ``transcript_status`` 状态机保证重复轮询无副作用；
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
from app.services.feishu_minutes import extract_walk_in_code, get_transcript, search_minutes
from app.services.whole_interview_scoring import DEFAULT_RUBRIC, score_whole_interview

logger = logging.getLogger(__name__)

# 转写阶段需要处理的状态
_MATCHABLE_STATUS = ("pending", "failed")
_TRANSCRIBE_STATUS = ("matched", "failed")


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


def transcribe_and_score(db, row: OfflineInterview) -> bool:
    """拉逐字稿 → 整场评分 → 落 AI 参考分。返回是否成功（转写成功即 True）。

    供轮询任务与手动触发接口复用（公开）。
    """
    ok, text, err = get_transcript(row.minutes_token)
    if not ok:
        row.transcript_status = "failed"
        db.commit()
        logger.warning("拉取妙记逐字稿失败 minute=%s err=%s", row.minutes_token, err)
        return False

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
    except Exception:  # noqa: BLE001 - 评分失败不影响转写落库
        logger.exception("整场评分异常 minute=%s", row.minutes_token)
    return True


def poll_minutes_once(settings: Settings | None = None) -> dict:
    """执行一轮轮询，返回统计 dict（供日志/观测）。"""
    settings = settings or get_settings()
    stats = {"matched": 0, "transcribed": 0, "errors": 0}

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=max(1, int(settings.feishu_minutes_search_window_days)))

        # 1) 匹配阶段：pending / failed(无 token) 的记录，按店长搜索命中
        pending = (
            db.query(OfflineInterview)
            .filter(
                OfflineInterview.walk_in_code.isnot(None),
                OfflineInterview.transcript_status.in_(_MATCHABLE_STATUS),
                OfflineInterview.minutes_token.is_(None),
            )
            .all()
        )
        by_owner: dict[str, list[OfflineInterview]] = {}
        for r in pending:
            if r.interviewer_open_id:
                by_owner.setdefault(r.interviewer_open_id, []).append(r)

        for owner_id, owner_rows in by_owner.items():
            ok, items, err = search_minutes(owner_id, start, now, settings=settings)
            if not ok:
                stats["errors"] += 1
                logger.warning("妙记搜索失败 owner=%s err=%s", owner_id, err)
                continue
            code_map = {r.walk_in_code: r for r in owner_rows if r.walk_in_code}
            for item in items:
                code = extract_walk_in_code(item.get("title") or "")
                target = code_map.get(code)
                if target is not None and _bind_minutes(db, target, item):
                    stats["matched"] += 1

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
