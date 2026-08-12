# -*- coding: utf-8 -*-
"""WsQuoteFeed tests (方案 3: WebSocket 行情推送 feed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.quote_cache import SharedQuoteCache
from paper_trading.ws_quote_feed import WsQuoteFeed, _default_parse_message


# ---------------------------------------------------------------------------
# 消息解析
# ---------------------------------------------------------------------------


def test_parse_dict_message():
    q = _default_parse_message({"code": "600519", "price": 1680.5, "volume": 100})
    assert q is not None
    assert q["code"] == "600519"
    assert q["cached"].price == 1680.5


def test_parse_json_message():
    q = _default_parse_message('{"code": "300750", "price": 190.2, "change_pct": 1.5}')
    assert q is not None
    assert q["code"] == "300750"
    assert q["cached"].price == 190.2
    assert q["cached"].change_pct == 1.5


def test_parse_longbridge_style_fields():
    q = _default_parse_message({"symbol": "700.HK", "last_done": 320.0, "prev_close": 310.0})
    assert q is not None
    assert q["code"] == "700.HK"
    assert q["cached"].price == 320.0
    assert q["cached"].pre_close == 310.0


def test_parse_invalid_returns_none():
    assert _default_parse_message({"code": "600519"}) is None  # no price
    assert _default_parse_message({"price": 10.0}) is None  # no code
    assert _default_parse_message("not json") is None
    assert _default_parse_message(123) is None


# ---------------------------------------------------------------------------
# Feed -> cache
# ---------------------------------------------------------------------------


def test_feed_on_message_updates_cache():
    cache = SharedQuoteCache()
    feed = WsQuoteFeed(cache, watched_codes=["600519"], url="ws://x")
    feed._on_message({"code": "600519", "price": 1680.5, "volume": 100})
    cached = cache.get("600519")
    assert cached is not None
    assert cached.price == 1680.5


def test_feed_on_message_invalid_ignored():
    cache = SharedQuoteCache()
    feed = WsQuoteFeed(cache, watched_codes=["600519"], url="ws://x")
    feed._on_message({"code": "600519"})  # no price -> ignored
    assert cache.get("600519") is None
