# -*- coding: utf-8 -*-
"""Unit tests for paper_trading/oms_mgmt.py (T18-B)."""
import pytest
from dataclasses import dataclass

from paper_trading.oms_mgmt import OrderParams, OrderManagementSystem
from paper_trading.order import OrderType


@dataclass
class StubOrder:
    id: int = 1


class FakeOrderMgr:
    def __init__(self):
        self.next_id = 1
        self.created = []

    def create_order(self, req):
        self.created.append(req)
        oid = self.next_id
        self.next_id += 1
        return StubOrder(id=oid)

    def fill_order(self, *a, **kw):
        return None


class FakeAcct:
    def freeze_cash(self, *a): pass
    def settle_buy(self, *a): pass
    def settle_sell(self, *a): pass


class FakePos:
    def apply_buy(self, *a, **kw): pass
    def apply_sell(self, *a, **kw): return 0.0


class FakeFee:
    def apply_slippage(self, price, side):
        return price * 1.001 if side == "buy" else price * 0.999

    def compute_fee(self, *a):
        return 5.0

    def estimate_buy_cost(self, price, qty):
        return price * qty + 5.0


class StubSignal:
    name = "TEST"
    strategy_name = "test"
    reason = "test"


def _oms():
    return OrderManagementSystem(
        FakeOrderMgr(), FakeAcct(), FakePos(), FakeFee()
    )


def _params(**kw):
    return OrderParams(
        account_id=1, code="000001", side="buy", quantity=100.0,
        order_type=OrderType.MARKET, limit_price=None, ref_price=10.0,
        signal_id=1, signal=StubSignal(), **kw,
    )


def test_create_order_sets_order_id():
    """create_order must return a TradeResult with valid order_id."""
    oms = _oms()
    r = oms.create_order(_params())
    assert r.order_id == 1
    assert r.status == "pending"


def test_execute_market_fills_buy():
    """Market buy fills at slippage-adjusted price."""
    oms = _oms()
    r = oms.create_order(_params())
    tr = oms.execute_market(r.order_id, _params(order_id=r.order_id))
    assert tr.status == "filled"
    assert tr.fill_price == pytest.approx(10.01)  # 10.0 * 1.001 (fp)


def test_execute_market_sell_fills():
    """Market sell fills at slippage-adjusted price."""
    oms = _oms()
    r = oms.create_order(_params(side="sell"))
    tr = oms.execute_market(r.order_id, _params(side="sell", order_id=r.order_id))
    assert tr.status == "filled"
    assert tr.fill_price == pytest.approx(9.99)  # 10.0 * 0.999 (fp)


def test_execute_market_failure_returns_rejected():
    """Zero ref_price causes fill_order to fail -> rejected status."""
    oms = _oms()
    r = oms.create_order(_params())
    tr = oms.execute_market(r.order_id, _params(order_id=r.order_id, ref_price=0.0))
    assert tr.status == "rejected"
