# -*- coding: utf-8 -*-
"""Minimal FastAPI server for paper trading + health endpoints.

Stubs litellm to avoid heavy AI dependency install during dev startup.
"""
import logging, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Inject litellm stub before anything imports src.analyzer
_STUBS = str(ROOT / "stubs")
sys.path.insert(0, _STUBS)
import litellm  # noqa: F401 — loads our stub

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("mini-server")

app = FastAPI(title="DSA Mini Server", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

from api.v1.endpoints.health import router as health_router
from api.v1.endpoints.paper_trading import router as paper_router

app.include_router(health_router, prefix="/api/v1")
app.include_router(paper_router, prefix="/api/v1/paper-trading")

logger.info("Routes: /api/v1/health, /api/v1/paper-trading/*")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
