"""数据库引擎与会话管理。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# 业务表与 LangGraph checkpointer 复用同一 PostgreSQL 实例（共享知识 #6）
engine = create_engine(
    _settings.sqlalchemy_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    """FastAPI 依赖：请求级数据库会话，请求结束后自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
