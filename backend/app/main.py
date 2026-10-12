"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, interview, scores
from app.config import get_settings
from app.exceptions import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：可选执行建表兜底（默认走 Alembic）。"""
    settings = get_settings()
    if settings.auto_create_tables:
        from app.db.base import Base
        from app.db.session import engine

        # 兜底建业务表（生产建议使用 alembic upgrade head）
        Base.metadata.create_all(bind=engine)

        # 初始化 LangGraph checkpointer 表（PostgresSaver.setup）
        from app.graph.builder import get_interview_graph

        get_interview_graph()
    yield


app = FastAPI(
    title="AI 面试智能体",
    description="多轮 AI 面试后端核心链路（LangGraph + FastAPI）",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)

# 管理端 + 候选人端 + 评分查询路由
app.include_router(admin.router)
app.include_router(interview.router)
app.include_router(scores.router)


@app.get("/health", tags=["health"])
def health():
    """健康检查。"""
    return {"code": 0, "message": "ok", "data": {"status": "healthy"}}
