# -*- coding: utf-8 -*-
"""pytest tests for the broker abstraction layer (T16).

Covers:
- ``BrokerOrderStatus`` enum values
- ``BaseBroker`` ABC contract (abstract, not instantiable)
- ``BrokerRouter`` register / resolve / error paths
- ``PaperBroker`` adapter over OrderManager / PaperAccountManager / PositionManager
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.broker import BaseBroker, BrokerOrderStatus, BrokerRouter, PaperBroker
from paper_trading.order import OrderManager, OrderRequest, OrderSide, OrderType
from paper_trading.position import PositionManager
from src.storage import DatabaseManager


class _DummyBroker(BaseBroker):
    """Minimal concrete BaseBroker used for ABC / router behaviour tests."""

    def __init__(self) -> None:
        self.orders: Dict[int, str] = {}

    def submit_order(self, order: Any, **kwargs: Any) -> int:
        order_id = len(self.orders) + 1
        self.orders[order_id] = "pending"
        return order_id

    def cancel_order(self, order_id: Any, reason: Optional[str] = None) -> str:
        self.orders[int(order_id)] = "canceled"
        return "canceled"

    def query_order(self, order_id: Any) -> Optional[str]:
        return self.orders.get(int(order_id))

    def query_positions(self, account_id: Any = None) -> List[Dict[str, Any]]:
        return []

    def query_account(self, account_id: Any = None) -> Dict[str, Any]:
        return {"id": account_id, "cash": 0.0}

    def is_connected(self) -> bool:
        return True


class _IncompleteBroker(BaseBroker):
    """Subclass that implements only part of the ABC (enforcement test)."""

    def is_connected(self) -> bool:
        return True


def _make_broker(db: DatabaseManager, name: str = "default", capital: float = 10000.0):
    order_mgr = OrderManager(db)
    account_mgr = PaperAccountManager(db_manager=db)
    pos_mgr = PositionManager(db)
    broker = PaperBroker(
        db_manager=db,
        order_manager=order_mgr,
        account_manager=account_mgr,
        position_manager=pos_mgr,
        account_name=name,
        initial_capital=capital,
    )
    return broker, order_mgr, account_mgr, pos_mgr


def _buy_request(account_id: int = 0, qty: float = 10.0, price: float = 5.0) -> OrderRequest:
    """Market buy request; account_id is a placeholder overwritten by PaperBroker."""
    return OrderRequest(
        account_id=account_id,
        code="600000",
        name="测试股票",
        side=OrderSide.BUY,
        quantity=qty,
        order_type=OrderType.MARKET,
        price=price,
        reason="broker test",
    )


@pytest.fixture
def broker_setup(temp_db):
    return _make_broker(temp_db)


class TestBrokerOrderStatus:
    def test_values(self):
        assert {s.value for s in BrokerOrderStatus} == {
            "pending",
            "queued",
            "partially_filled",
            "filled",
            "canceled",
            "rejected",
            "expired",
        }

    def test_lookup_by_value(self):
        assert BrokerOrderStatus("pending") is BrokerOrderStatus.PENDING
        assert BrokerOrderStatus("expired") is BrokerOrderStatus.EXPIRED

    def test_str_enum_behavior(self):
        assert BrokerOrderStatus.FILLED.value == "filled"
        assert BrokerOrderStatus.PARTIALLY_FILLED.value == "partially_filled"


class TestBaseBrokerContract:
    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseBroker()  # type: ignore[abstract]

    def test_incomplete_subclass_cannot_instantiate(self):
        with pytest.raises(TypeError):
            _IncompleteBroker()  # type: ignore[abstract]

    def test_concrete_subclass_usable(self):
        broker = _DummyBroker()
        assert broker.is_connected() is True
        order_id = broker.submit_order({"code": "600000"})
        assert broker.query_order(order_id) == "pending"
        assert broker.cancel_order(order_id) == "canceled"
        assert broker.query_positions() == []
        assert broker.query_account(1) == {"id": 1, "cash": 0.0}


class TestBrokerRouter:
    def test_register_and_resolve_case_insensitive(self):
        router = BrokerRouter()
        broker = _DummyBroker()
        assert router.register("paper", broker) is broker
        assert router.resolve("paper") is broker
        assert router.resolve("PAPER") is broker
        assert router.resolve("  Paper  ") is broker

    def test_resolve_unknown_raises(self):
        router = BrokerRouter()
        with pytest.raises(KeyError):
            router.resolve("missing")

    def test_register_non_broker_raises(self):
        router = BrokerRouter()
        with pytest.raises(TypeError):
            router.register("paper", object())  # type: ignore[arg-type]

    def test_register_empty_name_raises(self):
        router = BrokerRouter()
        with pytest.raises(ValueError):
            router.register("   ", _DummyBroker())

    def test_register_duplicate_raises(self):
        router = BrokerRouter()
        router.register("paper", _DummyBroker())
        with pytest.raises(ValueError):
            router.register("Paper", _DummyBroker())

    def test_names_contains_len(self):
        router = BrokerRouter()
        router.register("paper", _DummyBroker())
        router.register("live", _DummyBroker())
        assert router.names() == ["live", "paper"]
        assert "paper" in router
        assert "PAPER" in router
        assert "missing" not in router
        assert len(router) == 2


class TestBrokerRouterWithPaperBroker:
    def test_register_paper_broker_and_resolve(self, temp_db):
        broker = PaperBroker(db_manager=temp_db, account_name="routed", initial_capital=2000.0)
        router = BrokerRouter()
        assert router.register("paper", broker) is broker
        assert router.resolve("Paper") is broker
        snap = router.resolve("paper").query_account()
        assert snap["name"] == "routed"
        assert snap["cash"] == 2000.0


class TestPaperBroker:
    def test_wraps_managers(self, broker_setup):
        broker, order_mgr, account_mgr, pos_mgr = broker_setup
        assert broker.order_mgr is order_mgr
        assert broker.account_mgr is account_mgr
        assert broker.position_mgr is pos_mgr
        assert broker.is_connected() is True

    def test_default_construction_with_db_only(self, temp_db):
        broker = PaperBroker(db_manager=temp_db)
        assert broker.order_mgr.db is temp_db
        assert broker.account_mgr.db is temp_db
        assert broker.position_mgr.db is temp_db
        snap = broker.query_account()
        assert snap["name"] == "default"
        assert snap["cash"] == 1000.0

    def test_query_account_creates_default(self, broker_setup):
        broker, *_ = broker_setup
        snap = broker.query_account()
        assert snap["name"] == "default"
        assert snap["initial_capital"] == 10000.0
        assert snap["cash"] == 10000.0
        assert snap["status"] == "active"

    def test_query_account_idempotent(self, broker_setup):
        broker, *_ = broker_setup
        assert broker.query_account()["id"] == broker.query_account()["id"]

    def test_query_account_explicit(self, broker_setup):
        broker, _, account_mgr, _ = broker_setup
        other = account_mgr.get_or_create_account(name="other", initial_capital=5000.0)
        snap = broker.query_account(account_id=other.id)
        assert snap["name"] == "other"
        assert snap["initial_capital"] == 5000.0
        assert snap["cash"] == 5000.0

    def test_submit_order_default_account(self, broker_setup):
        broker, *_ = broker_setup
        default_id = broker.query_account()["id"]
        order_id = broker.submit_order(_buy_request())
        assert isinstance(order_id, int)
        record = broker.query_order(order_id)
        assert record["account_id"] == default_id
        assert record["code"] == "600000"
        assert record["side"] == "buy"
        assert record["status"] == "pending"

    def test_submit_order_explicit_account(self, broker_setup):
        broker, _, account_mgr, _ = broker_setup
        other = account_mgr.get_or_create_account(name="other", initial_capital=5000.0)
        order_id = broker.submit_order(_buy_request(), account_id=other.id)
        record = broker.query_order(order_id)
        assert record["account_id"] == other.id

    def test_cancel_order(self, broker_setup):
        broker, *_ = broker_setup
        order_id = broker.submit_order(_buy_request())
        canceled = broker.cancel_order(order_id, reason="broker test cancel")
        assert canceled["status"] == "canceled"
        assert canceled["cancel_reason"] == "broker test cancel"
        assert broker.query_order(order_id)["status"] == "canceled"

    def test_query_order_unknown(self, broker_setup):
        broker, *_ = broker_setup
        assert broker.query_order(999999) is None

    def test_fill_then_query_order(self, broker_setup):
        broker, order_mgr, *_ = broker_setup
        order_id = broker.submit_order(_buy_request(qty=10.0, price=5.0))
        order_mgr.fill_order(order_id, fill_price=5.0)
        record = broker.query_order(order_id)
        assert record["status"] == "filled"
        assert record["filled_quantity"] == 10.0
        assert record["filled_price_avg"] == 5.0

    def test_conditional_order(self, broker_setup):
        broker, *_ = broker_setup
        req = OrderRequest(
            account_id=0,
            code="600000",
            side=OrderSide.SELL,
            quantity=10.0,
            order_type=OrderType.STOP_LOSS,
            trigger_price=4.0,
        )
        order_id = broker.submit_order(req)
        record = broker.query_order(order_id)
        assert record["status"] == "conditional"
        assert record["trigger_price"] == 4.0

    def test_query_positions_empty(self, broker_setup):
        broker, *_ = broker_setup
        assert broker.query_positions() == []

    def test_query_positions_with_position(self, broker_setup):
        broker, _, _, pos_mgr = broker_setup
        acc_id = broker.query_account()["id"]
        pos_mgr.apply_buy(acc_id, "600000", 10.0, 5.0, name="测试")
        positions = broker.query_positions()
        assert len(positions) == 1
        assert positions[0]["code"] == "600000"
        assert positions[0]["quantity"] == 10.0

    def test_query_positions_explicit_account(self, broker_setup):
        broker, _, account_mgr, _ = broker_setup
        other = account_mgr.get_or_create_account(name="other", initial_capital=5000.0)
        assert broker.query_positions(account_id=other.id) == []
