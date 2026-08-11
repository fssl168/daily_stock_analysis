import os
import sys

# 可选：通过 DSA_VENV_LIB 环境变量注入额外的 site-packages 路径。
# 避免在源码中硬编码本机绝对路径（AGENTS.md「不写死路径」）。
# 未设置时保持默认 sys.path，不影响正常启动。
_extra_lib = os.getenv("DSA_VENV_LIB", "").strip()
if _extra_lib:
    sys.path.insert(0, _extra_lib)

# -*- coding: utf-8 -*-
"""
===================================
Daily Stock Analysis - FastAPI 后端服务入口
===================================

职责：
1. 提供 RESTful API 服务
2. 配置 CORS 跨域支持
3. 健康检查接口
4. 托管前端静态文件（生产模式）

启动方式：
    uvicorn server:app --reload --host 0.0.0.0 --port 8000
    
    或使用 main.py:
    python main.py --serve-only      # 仅启动 API 服务
    python main.py --serve           # API 服务 + 执行分析
"""

import logging

from src.config import setup_env, get_config
from src.logging_config import setup_logging

# 初始化环境变量与日志
setup_env()

config = get_config()
level_name = (config.log_level or "INFO").upper()
level = getattr(logging, level_name, logging.INFO)

setup_logging(
    log_prefix="api_server",
    console_level=level,
    extra_quiet_loggers=['uvicorn', 'fastapi'],
)

# 从 api.app 导入应用实例
from api.app import app  # noqa: E402

# 导出 app 供 uvicorn 使用
__all__ = ['app']


if __name__ == "__main__":
    import uvicorn, os

    # ③ HealthCheckDaemon (T6 integration) — enabled via HEALTH_CHECK_ENABLED env.
    if os.getenv("HEALTH_CHECK_ENABLED", "").strip().lower() in ("1", "true", "yes"):
        from src.services.health_check import (
            HealthCheckDaemon, check_ntp_sync, check_system_resources, check_task_queue,
        )
        daemon = HealthCheckDaemon(
            on_alert=lambda level, msg: logging.getLogger("health").warning("[%s] %s", level, msg),
        )
        daemon.register(check_ntp_sync)
        daemon.register(check_system_resources)
        daemon.register(check_task_queue)
        # Listener-alive and data-source-health checks require runtime objects;
        # register them from the caller (main.py) via daemon.register(lambda: ...).
        daemon.start()
        logging.getLogger("health").info("HealthCheckDaemon started with %d checks", len(daemon._checks))

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
