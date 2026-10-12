"""FastAPI 公共依赖与响应工具。"""
from typing import Any, Generator

from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """请求级数据库会话依赖。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ok(data: Any = None) -> dict:
    """统一成功响应格式（共享知识 #1）。"""
    return {"code": 0, "message": "ok", "data": data}
