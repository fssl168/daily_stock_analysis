# -*- coding: utf-8 -*-
"""Regression tests for the paper-trading WebSocket endpoints.

Covers the fix where the router-level ``Depends(require_login)`` was being
applied to websocket routes and turned every handshake into a 500
("require_login() missing 1 required positional argument: 'request'").

The fix moved the two ws endpoints onto a dependency-free ``ws_router`` and
kept authentication inside the endpoint via ``verify_ws_account_ownership``.
These tests pin that contract so it does not regress.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api.v1.endpoints.paper_trading as pt
from api.v1.endpoints.paper_trading import router, ws_router


class _StubWS:
    """Minimal stand-in for a Starlette WebSocket used by ws_* endpoints."""

    def __init__(self) -> None:
        self.accepted = False
        self.closed: int | None = None
        self.sent: list[dict] = []
        self.cookies: dict[str, str] = {}

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


def _route_dependencies(route) -> list:
    deps = getattr(route, "dependencies", None) or []
    return deps


def _route_paths(routes) -> set[str]:
    return {getattr(r, "path", "") for r in routes}


# ---------------------------------------------------------------------------
# AC-101 / config-level: ws routes must not carry the require_login dependency
# ---------------------------------------------------------------------------


def test_ws_router_has_no_require_login_dependency():
    """ws_router routes carry no router-level require_login dependency."""
    login = pt.require_login
    for route in ws_router.routes:
        for dep in _route_dependencies(route):
            inner = getattr(dep, "dependency", None)
            assert inner is not login, (
                f"ws route {getattr(route, 'path', '?')} must not depend on require_login"
            )


def test_ws_routes_are_not_on_http_router():
    """The HTTP router must not still expose the ws endpoints."""
    paths = _route_paths(router.routes)
    assert "/{account_id}/ws/quotes" not in paths
    assert "/{account_id}/ws/events" not in paths


def test_ws_router_exposes_both_endpoints():
    paths = _route_paths(ws_router.routes)
    assert "/{account_id}/ws/quotes" in paths
    assert "/{account_id}/ws/events" in paths


# ---------------------------------------------------------------------------
# AC-101 / functional: unauthenticated handshake is rejected (close 1008), not 500
# ---------------------------------------------------------------------------


def test_ws_quotes_rejects_unauthenticated(monkeypatch):
    ws = _StubWS()

    def deny(*_a, **_k):
        raise HTTPException(status_code=401, detail="需要登录")

    monkeypatch.setattr(pt, "verify_ws_account_ownership", deny)
    asyncio.run(pt.ws_quotes(ws, 1))
    assert ws.closed == 1008
    assert ws.accepted is False


def test_ws_events_rejects_unauthenticated(monkeypatch):
    ws = _StubWS()

    def deny(*_a, **_k):
        raise HTTPException(status_code=401, detail="需要登录")

    monkeypatch.setattr(pt, "verify_ws_account_ownership", deny)
    asyncio.run(pt.ws_events(ws, 1))
    assert ws.closed == 1008
    assert ws.accepted is False


# ---------------------------------------------------------------------------
# AC-102: authenticated handshake succeeds (accept) and stays alive
# ---------------------------------------------------------------------------


def test_ws_quotes_accepts_when_authenticated(monkeypatch):
    ws = _StubWS()

    class _Svc:
        def get_listener(self):
            return None

    def allow(*_a, **_k):
        return None

    def svc(_websocket):
        return _Svc()

    def collect(_listener):
        return []  # _collect_quotes is synchronous (returns a list)

    async def sleep(_t):
        raise WebSocketDisconnect()

    monkeypatch.setattr(pt, "verify_ws_account_ownership", allow)
    monkeypatch.setattr(pt, "_service_from_websocket", svc)
    monkeypatch.setattr(pt, "_collect_quotes", collect)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    asyncio.run(pt.ws_quotes(ws, 1))
    assert ws.accepted is True


def test_ws_events_accepts_and_cleans_up(monkeypatch):
    ws = _StubWS()

    import paper_trading.events as events_mod

    class _Bus:
        def __init__(self):
            self.unsubscribed = False

        def subscribe(self, _fn):
            return None

        def replay(self):
            return []

        def unsubscribe(self, _fn):
            self.unsubscribed = True

    bus = _Bus()

    class _BusSingleton:
        @classmethod
        def instance(cls):
            return bus

    def allow(*_a, **_k):
        return None

    async def sleep(_t):
        raise WebSocketDisconnect()

    monkeypatch.setattr(pt, "verify_ws_account_ownership", allow)
    monkeypatch.setattr(events_mod, "PaperTradingEventBus", _BusSingleton)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    asyncio.run(pt.ws_events(ws, 1))
    assert ws.accepted is True
    assert bus.unsubscribed is True
