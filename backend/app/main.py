"""FastAPI 应用入口。"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin,
    auth,
    final_interview,
    interview,
    minutes_auth,
    offline,
    offers,
    scores,
    screening,
    summary,
)
from app.api.deps import require_role, require_user
from app.config import get_settings
from app.db.models.admin_user import ADMIN_ROLE, BOSS_ROLE, HR_ROLE
from app.exceptions import register_exception_handlers
from app.middleware.security import AdminWriteAuditMiddleware, SecurityHeadersMiddleware
from app.services.feishu_longconn import diagnose_longconn, start_long_connection
from app.services.feishu_notify import diagnose_notify_config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：可选执行建表兜底（默认走 Alembic），关闭时释放连接池。"""
    settings = get_settings()

    # 飞书通知配置自检：把「会导致推送静默失败」的配置问题在启动时喊出来
    for warning in diagnose_notify_config(settings):
        logger.warning("飞书通知配置告警：%s", warning)

    # 长连接回调自检 + 启动：让审批人能直接在卡片上点「同意 / 驳回」。
    # 未启用时静默跳过；启动失败只记日志，绝不拖垮服务。
    for warning in diagnose_longconn(settings):
        logger.warning("飞书长连接告警：%s", warning)
    try:
        start_long_connection(settings)
    except Exception:  # noqa: BLE001 - 长连接不可用不应影响服务启动
        logger.exception("飞书长连接启动失败（服务继续运行，卡片按钮暂不可用）")

    if settings.auto_create_tables:
        from app.db.base import Base
        from app.db.session import engine

        # 兜底建业务表（生产建议使用 alembic upgrade head）
        Base.metadata.create_all(bind=engine)

        # 初始化 LangGraph checkpointer 表（PostgresSaver.setup）
        from app.graph.builder import get_interview_graph

        get_interview_graph()

    # 飞书妙记轮询任务（到店直招录音节点）：按开关启动，关闭时优雅停止
    minutes_stop_event: asyncio.Event | None = None
    minutes_task: asyncio.Task | None = None
    if settings.feishu_minutes_enabled:
        from app.services.minutes_poller import minutes_poll_loop

        minutes_stop_event = asyncio.Event()
        minutes_task = asyncio.create_task(minutes_poll_loop(minutes_stop_event))

    yield

    # 停止妙记轮询任务
    if minutes_stop_event is not None:
        minutes_stop_event.set()
    if minutes_task is not None:
        try:
            await minutes_task
        except Exception:  # noqa: BLE001
            logger.exception("停止妙记轮询任务时异常")

    # 释放 checkpointer 连接池（懒加载模式下若从未使用则为空操作）
    from app.graph.builder import close_interview_graph

    close_interview_graph()


_settings = get_settings()

app = FastAPI(
    title="AI 面试智能体",
    description="多轮 AI 面试后端核心链路（LangGraph + FastAPI）",
    version="0.1.0",
    lifespan=lifespan,
    # 生产建议关闭接口文档：/docs 会把全部接口结构摊开给任何人看
    docs_url="/docs" if _settings.enable_api_docs else None,
    redoc_url="/redoc" if _settings.enable_api_docs else None,
    openapi_url="/openapi.json" if _settings.enable_api_docs else None,
)

register_exception_handlers(app)

# ── 中间件（add_middleware 后加者位于外层）────────────────────
# 1) 审计放最内层：只有在这里才能读到 require_user 写进 scope["state"] 的登录用户
app.add_middleware(AdminWriteAuditMiddleware)
# 2) 安全响应头放外层：保证 CORS 预检等被短路的响应同样带上安全头
if _settings.security_headers_enabled:
    app.add_middleware(SecurityHeadersMiddleware, enable_csp=_settings.security_csp_enabled)
# 3) CORS 只按白名单放行；同域部署（本项目默认）留空即等于不开放跨域
if _settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-API-Key"],
    )

# ── 路由挂载 ─────────────────────────────────────────────────
# 公开路由（显式白名单）：改动此处必须同步更新 docs/安全改造方案 的清单
app.include_router(auth.router)  # 登录/登出/改密（内部按接口自带保护）
app.include_router(interview.router)  # 候选人答题：以 URL 中不可猜 token 为凭证
app.include_router(offers.candidate_router)  # 候选人 Offer 确认：同上

# 受保护路由：统一挂 require_user，端点函数里不再重复写校验
_protected = [Depends(require_user)]
# HR 职责路由：简历原件、面试对话全文、汇总排名、Offer 属于 HR/管理员的
# 数据视野，不放开给店长（店长的入口在 offline 路由）
_hr_only = [Depends(require_user), Depends(require_role(ADMIN_ROLE, HR_ROLE))]
app.include_router(admin.router, dependencies=_hr_only)
app.include_router(screening.router, dependencies=_hr_only)
app.include_router(summary.router, dependencies=_hr_only)
# Offer 路由：老板需在操作台查看待审批 Offer 并做录用审批，故放开给 boss；
# 其余 HR 专属动作（起草/提交/撤回/入职等）老板虽可见但不影响「只做最后操作」的体验定位
app.include_router(
    offers.router,
    dependencies=[Depends(require_user), Depends(require_role(ADMIN_ROLE, HR_ROLE, BOSS_ROLE))],
)
# 妙记个人授权（设备授权流）：仅 HR / 管理员可发起与查看
app.include_router(minutes_auth.router, dependencies=_hr_only)
# 终试决策（老板操作台）：admin / boss 均可操作，老板的最后拍板环节
app.include_router(
    final_interview.router,
    dependencies=[Depends(require_user), Depends(require_role(ADMIN_ROLE, BOSS_ROLE))],
)
# 基础数据（岗位列表）：三种角色登录后均可读 —— 店长的工作台需要岗位下拉
app.include_router(admin.base_data_router, dependencies=_protected)
# 线下复试：三种角色都能进（店长的核心工作台），端点级再加细粒度限制
# （如删除限 HR、改面试官限 HR，见 app/api/offline.py）
app.include_router(offline.router, dependencies=_protected)
# 评分查询：面向外部系统，用 API Key 而非登录态（端点级依赖见 scores.py）
app.include_router(scores.router)


@app.get("/health", tags=["health"])
def health():
    """健康检查。"""
    return {"code": 0, "message": "ok", "data": {"status": "healthy"}}
