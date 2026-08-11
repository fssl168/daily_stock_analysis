# -*- coding: utf-8 -*-
"""
权限检查工具模块提供细粒度访问控制函数，用于 API 端点的角色和所有权验证。
注意：此模块基于现有的认证系统实现。
"""

from enum import Enum
from typing import Optional, Callable, Any
from fastapi import HTTPException, status
from starlette.requests import Request

from src.auth import COOKIE_NAME, verify_session


class UserRole(str, Enum):
    """用户角色枚举."""
    USER = "user"
    ADMIN = "admin"


def get_current_user(request: Request):
    """
    从请求中获取当前用户信息（简化实现）。
    在实际系统中，这里应该从会话中查询用户对象并返回包含 role 属性的用户模型。
    """
    cookie_val = request.cookies.get(COOKIE_NAME)
    if cookie_val and verify_session(cookie_val):
        class DummyUser:
            def __init__(self, user_id: str, role):
                self.user_id = user_id
                self.role = role
        
        return DummyUser(user_id="current_user", role="ADMIN")
    return None


class require_admin:
    """
    权限检查依赖：需要 ADMIN 角色。
    
    用法：在 FastAPI endpoint 函数参数中添加 admin_check: require_admin = Depends()
    """
    
    def __call__(self, request: Request):
        current_user = get_current_user(request)
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "unauthorized", "message": "需要登录"}
            )
        
        user_role = getattr(current_user, 'role', None)
        if user_role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_permissions",
                    "message": f"需要 admin 角色才能访问此资源",
                    "required": "admin",
                    "actual": user_role,
                }
            )
        return current_user


def require_role(required_role: str):
    """
    通用权限检查：要求用户具有指定角色。
    
    Args:
        required_role: 所需的角色字符串（如 "admin"）
    
    Returns:
        一个可调用对象，用作 FastAPI Depends() 参数
    """
    class RoleChecker:
        def __init__(self, role: str):
            self.required_role = role
        
        def __call__(self, request: Request):
            current_user = get_current_user(request)
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error": "unauthorized", "message": "需要登录"}
                )
            
            user_role = getattr(current_user, 'role', None)
            if user_role != self.required_role:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "insufficient_permissions",
                        "message": f"需要 {self.required_role} 角色才能访问此资源",
                        "required": self.required_role,
                        "actual": user_role,
                    }
                )
            return current_user
    
    return RoleChecker(required_role)


async def check_paper_trading_account_access(account_id: str, request: Request):
    """
    检查用户是否有权限访问指定的纸面交易账户。
    
    验证当前登录用户是否拥有该 account_id 的账户。
    
    Args:
        account_id: 要访问的账户 ID
        request: FastAPI Request 对象，用于获取当前用户会话
    
    Note:
        此函数应在 paper_trading 端点中使用，通过适当的数据库管理器
        获取数据库连接并查询账户的所有权关系。
    """
    from api.deps import get_database_manager
    
    current_user = get_current_user(request)
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "需要登录"}
        )
    
    # TODO: 实现实际的账户所有权验证逻辑
    pass
