"""统一业务异常与全局异常处理。

所有接口响应统一为 {"code": 0, "message": "ok", "data": ...}，
错误时 data 恒为 null（见架构共享知识 #1）。
"""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class BizError(Exception):
    """业务异常：携带自定义 code 与 message。

    Args:
        code: 业务错误码（非 0）。
        message: 面向调用方的错误描述。
        http_status: 对应的 HTTP 状态码，默认 400。
    """

    def __init__(self, code: int, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（统一响应格式）。"""

    @app.exception_handler(BizError)
    async def _biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": "请求参数校验失败", "data": None},
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "data": None},
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "服务器内部错误", "data": None},
        )
