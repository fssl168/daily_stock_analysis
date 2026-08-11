# -*- coding: utf-8 -*-
"""
===================================
全局异常处理中间件
===================================

职责：
1. 捕获未处理的异常
2. 统一错误响应格式
3. 记录错误日志
"""

import logging
import traceback
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    全局异常处理中间件
    
    捕获所有未处理的异常，返回统一格式的错误响应
    """
    
    async def dispatch(
        self, 
        request: Request, 
        call_next: Callable
    ) -> Response:
        """
        处理请求，捕获异常
        
        Args:
            request: 请求对象
            call_next: 下一个处理器
            
        Returns:
            Response: 响应对象
        """
        try:
            response = await call_next(request)
            return response
            
        except Exception as e:
            # 记录错误日志
            logger.error(
                f"未处理的异常: {e}\n"
                f"请求路径: {request.url.path}\n"
                f"请求方法: {request.method}\n"
                f"堆栈: {traceback.format_exc()}"
            )
            
            # 返回统一格式的错误响应
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "服务器内部错误，请稍后重试",
                    "detail": str(e) if logger.isEnabledFor(logging.DEBUG) else None
                }
            )


def add_error_handlers(app) -> None:
    """
    添加全局异常处理器
    
    为 FastAPI 应用添加各类异常的处理器
    
    Args:
        app: FastAPI 应用实例
    """
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """处理 HTTP 异常"""
        # Build standardized response with timestamp
        from datetime import datetime

        # If detail is already a rich ErrorResponse dict with "error" and "message", use it directly
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            response_data = exc.detail.copy()
            # Ensure timestamp exists
            if "timestamp" not in response_data:
                response_data["timestamp"] = datetime.now().isoformat()
            return JSONResponse(
                status_code=exc.status_code,
                content=response_data
            )

        # Otherwise wrap in standardized format
        message = exc.detail if exc.detail else str(exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "http_error",
                "message": message,
                "detail": None,
                "timestamp": datetime.now().isoformat()
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """处理请求验证异常"""
        from datetime import datetime
        errors = exc.errors()
        # Flatten error messages for concise response
        messages = [
            f"{e['loc'][-1]}: {e['msg']}"
            for e in errors
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "; ".join(messages),
                "detail": errors,
                "timestamp": datetime.now().isoformat()
            }
        )