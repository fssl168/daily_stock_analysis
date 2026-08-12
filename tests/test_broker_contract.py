# -*- coding: utf-8 -*-
"""Broker contract tests (T-07 / checklist 2.2 & 2.5).

Covers:
- order-status mapping (broker Chinese status strings -> paper status)
- EastMoneyBroker submit/query contract via a mocked easytrader client
  (real easytrader is Windows-only + needs a logged-in desktop client).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.broker.eastmoney_broker import EastMoneyBroker
from paper_trading.broker.order_status import (
    build_order_update,
    is_terminal,
    map_broker_status,
)


# ---------------------------------------------------------------------------
# Order-status mapping (2.5)
# ---------------------------------------------------------------------------


def test_map_broker_status_filled():
    assert map_broker_status("已成") == "filled"
    assert map_broker_status("全部成交") == "filled"
    assert map_broker_status("完全成交") == "filled"


def test_map_broker_status_partially_filled():
    assert map_broker_status("部成") == "partially_filled"
    assert map_broker_status("部分成交") == "partially_filled"


def test_map_broker_status_pending_and_canceled():
    assert map_broker_status("已报") == "pending"
    assert map_broker_status("未成交") == "pending"
    assert map_broker_status("已撤") == "canceled"
    assert map_broker_status("已撤单") == "canceled"


def test_map_broker_status_rejected_and_unknown():
    assert map_broker_status("废单") == "rejected"
    assert map_broker_status("委托失败") == "rejected"
    assert map_broker_status("某些未知名状态") == "pending"
    assert map_broker_status("") == "pending"
    assert map_broker_status(None) == "pending"


def test_build_order_update():
    update = build_order_update({
        "broker_order_id": "1001",
        "status": "部成",
        "code": "600519",
        "price": 1680.0,
        "quantity": 100,
        "filled_quantity": 50,
        "filled_price": 1680.5,
    })
    assert update["status"] == "partially_filled"
    assert update["filled_quantity"] == 50.0
    assert update["filled_price"] == 1680.5


def test_is_terminal():
    assert is_terminal("filled")
    assert is_terminal("canceled")
    assert is_terminal("rejected")
    assert not is_terminal("pending")
    assert not is_terminal("partially_filled")


# ---------------------------------------------------------------------------
# EastMoneyBroker contract via mocked easytrader client (2.2)
# ---------------------------------------------------------------------------


class _FakeEasyTrader:
    """Minimal easytrader client double."""

    def __init__(self, entrusts: Optional[List[Dict[str, Any]]] = None,
                 positions: Optional[List[Dict[str, Any]]] = None):
        self.entrusts = entrusts or []
        self._positions = positions or []
        self.last_side: Optional[str] = None

    def buy(self, code, price, qty):
        self.last_side = "buy"
        return {"entrust_no": "1001"}

    def sell(self, code, price, qty):
        self.last_side = "sell"
        return {"entrust_no": "1002"}

    def cancel_entrust(self, entrust_no):
        return True

    @property
    def today_entrusts(self):
        return self.entrusts

    @property
    def position(self):
        return self._positions


def _connected_broker(fake: _FakeEasyTrader) -> EastMoneyBroker:
    broker = EastMoneyBroker(user="test", password="test")
    broker._client = fake  # type: ignore[attr-defined]
    broker._connected = True  # type: ignore[attr-defined]
    return broker


class _FakeOrder:
    def __init__(self, code="600519", price=1680.0, quantity=100, side="buy"):
        self.code = code
        self.price = price
        self.quantity = quantity
        self.side = side


def test_submit_order_contract():
    broker = _connected_broker(_FakeEasyTrader())
    res = broker.submit_order(_FakeOrder())
    assert res["broker_order_id"] == "1001"
    assert res["status"] == "queued"
    assert res["filled_quantity"] == 0


def test_submit_sell_contract():
    broker = _connected_broker(_FakeEasyTrader())
    res = broker.submit_order(_FakeOrder(side="sell"))
    assert res["broker_order_id"] == "1002"


def test_submit_order_raises_when_not_connected():
    broker = EastMoneyBroker(user="test", password="test")
    with pytest.raises(RuntimeError):
        broker.submit_order(_FakeOrder())


def test_query_order_maps_report():
    fake = _FakeEasyTrader(entrusts=[{
        "entrust_no": "1001", "status": "已成", "证券代码": "600519",
        "委托价格": 1680.0, "委托数量": 100, "成交数量": 100, "成交均价": 1680.2,
    }])
    broker = _connected_broker(fake)
    report = broker.query_order("1001")
    assert report is not None
    assert report["broker_order_id"] == "1001"
    assert report["status"] == "已成"
    assert report["filled_quantity"] == 100
    # then map it to paper status
    assert map_broker_status(report["status"]) == "filled"


def test_query_order_unknown_returns_none():
    fake = _FakeEasyTrader(entrusts=[])
    broker = _connected_broker(fake)
    assert broker.query_order("nope") is None
