# -*- coding: utf-8 -*-
"""pytest tests for P0-C order cancel / modify flow."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel
from paper_trading.order import (
    OrderManager,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from paper_trading.position import PositionManager
from paper_trading.risk import RiskChecker
from paper_trading.trading_engine import TradingEngine
from src.storage import Account, DatabaseManager


def _build_engine(db: DatabaseManager) -> tuple[TradingEngine, int]:
    """Create an account + engine wired to the supplied temp database."""
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="pytest_cm", initial_capital=1000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == "pytest_cm", Account.account_type == "paper")
        ).scalar_one()
        acc_id = acc.id

    fee_model = FeeModel()
    pos_mgr = PositionManager(db)
    engine = TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=OrderManager(db),
        position_manager=pos_mgr,
        fee_model=fee_model,
        risk_checker=RiskChecker(
            db_manager=db,
            account_manager=account_mgr,
            position_manager=pos_mgr,
            fee_model=fee_model,
        ),
    )
    return engine, acc_id


@pytest.fixture
def engine_account(temp_db):
    return _build_engine(temp_db)


class TestOrderCancel:
    """Cancel order state transitions and cash unfreezing."""

    def test_cancel_pending_limit_buy_releases_frozen_cash(self, engine_account):
        engine, acc_id = engine_account
        order_mgr = engine.order_mgr
        account_mgr = engine.account_mgr
        fee_model = engine.fee_model

        req = OrderRequest(
            account_id=acc_id,
            code="600000",
            name="浦发银行",
            side=OrderSide.BUY,
            quantity=30.0,
            order_type=OrderType.LIMIT,
            price=5.0,
            strategy_name="test_cancel",
            reason="limit buy for cancel test",
        )
        order = order_mgr.create_order(req)
        order_id = order.id
        assert order.status == OrderStatus.PENDING.value

        estimated_cost = fee_model.estimate_buy_cost(5.0, 30.0)
        account_mgr.freeze_cash(acc_id, estimated_cost)
        assert account_mgr.snapshot(acc_id).frozen_cash == estimated_cost

        canceled = order_mgr.cancel_order(order_id, reason="user canceled")
        assert canceled.status == OrderStatus.CANCELED.value
        assert canceled.cancel_reason == "user canceled"

        account_mgr.unfreeze_cash(acc_id, estimated_cost)
        snap = account_mgr.snapshot(acc_id)
        assert snap.frozen_cash == 0.0
        assert snap.cash == 1000.0

    def test_cancel_filled_order_raises(self, engine_account):
        engine, acc_id = engine_account
        order_mgr = engine.order_mgr

        req = OrderRequest(
            account_id=acc_id,
            code="600000",
            side=OrderSide.BUY,
            quantity=10.0,
            order_type=OrderType.MARKET,
        )
        order = order_mgr.create_order(req)
        order_mgr.fill_order(order.id, fill_price=5.0, fill_quantity=10.0)

        with pytest.raises(ValueError):
            order_mgr.cancel_order(order.id, reason="too late")


class TestOrderModify:
    """Modify order creates replacement and cancels original."""

    def test_modify_price_creates_replacement(self, engine_account):
        engine, acc_id = engine_account
        order_mgr = engine.order_mgr
        account_mgr = engine.account_mgr
        fee_model = engine.fee_model

        req = OrderRequest(
            account_id=acc_id,
            code="600000",
            name="浦发银行",
            side=OrderSide.BUY,
            quantity=30.0,
            order_type=OrderType.LIMIT,
            price=5.0,
            strategy_name="test_modify",
            reason="limit buy for modify test",
        )
        original = order_mgr.create_order(req)
        original_id = original.id
        account_mgr.freeze_cash(acc_id, fee_model.estimate_buy_cost(5.0, 30.0))

        replacement = order_mgr.modify_order(
            original_id, new_price=5.5, reason="raise limit"
        )
        assert replacement.id != original_id
        assert replacement.parent_order_id == original_id
        assert replacement.status == OrderStatus.PENDING.value
        assert float(replacement.price) == 5.5

        original_db = order_mgr.get_order(original_id)
        assert original_db.status == OrderStatus.CANCELED.value

    def test_modify_nonexistent_order_raises(self, engine_account):
        engine, _ = engine_account
        with pytest.raises(ValueError):
            engine.order_mgr.modify_order(999999, new_price=10.0)

    def test_modify_market_order_raises(self, engine_account):
        engine, acc_id = engine_account
        req = OrderRequest(
            account_id=acc_id,
            code="000001",
            side=OrderSide.BUY,
            quantity=5.0,
            order_type=OrderType.MARKET,
        )
        order = engine.order_mgr.create_order(req)
        with pytest.raises(ValueError):
            engine.order_mgr.modify_order(order.id, new_price=11.0)

    def test_modify_without_changes_raises(self, engine_account):
        engine, acc_id = engine_account
        req = OrderRequest(
            account_id=acc_id,
            code="000001",
            side=OrderSide.BUY,
            quantity=10.0,
            order_type=OrderType.LIMIT,
            price=10.0,
        )
        order = engine.order_mgr.create_order(req)
        with pytest.raises(ValueError):
            engine.order_mgr.modify_order(order.id)


class TestTradingEngineOrderCancelModify:
    """TradingEngine.cancel_order / modify_order by order id (G5)."""

    def test_cancel_order_by_id_unfreezes_cash(self, engine_account):
        engine, acc_id = engine_account
        req = OrderRequest(
            account_id=acc_id,
            code="600000",
            name="浦发银行",
            side=OrderSide.BUY,
            quantity=30.0,
            order_type=OrderType.LIMIT,
            price=5.0,
            strategy_name="test_cancel_by_id",
            reason="limit buy for order-id cancel test",
        )
        order = engine.order_mgr.create_order(req)
        order_id = order.id
        fee_model = engine.fee_model
        engine.account_mgr.freeze_cash(acc_id, fee_model.estimate_buy_cost(5.0, 30.0))
        assert engine.account_mgr.snapshot(acc_id).frozen_cash > 0

        result = engine.cancel_order(order_id, reason="user canceled by id")
        assert result.order_id == order_id
        assert result.status == "rejected"
        assert engine.account_mgr.snapshot(acc_id).frozen_cash == 0.0

    def test_modify_order_by_id_creates_replacement(self, engine_account):
        engine, acc_id = engine_account
        req = OrderRequest(
            account_id=acc_id,
            code="600000",
            name="浦发银行",
            side=OrderSide.BUY,
            quantity=30.0,
            order_type=OrderType.LIMIT,
            price=5.0,
            strategy_name="test_modify_by_id",
            reason="limit buy for order-id modify test",
        )
        order = engine.order_mgr.create_order(req)
        original_id = order.id
        engine.account_mgr.freeze_cash(acc_id, engine.fee_model.estimate_buy_cost(5.0, 30.0))

        result = engine.modify_order(
            original_id, new_price=5.5, reason="raise limit by id"
        )
        assert result.order_id != original_id
        assert result.status == "pending"

        original_db = engine.order_mgr.get_order(original_id)
        assert original_db.status == OrderStatus.CANCELED.value

    def test_cancel_nonexistent_order_by_id_raises(self, engine_account):
        engine, _ = engine_account
        with pytest.raises(ValueError):
            engine.cancel_order(999999, reason="no such order")

    def test_modify_filled_order_by_id_raises(self, engine_account):
        engine, acc_id = engine_account
        req = OrderRequest(
            account_id=acc_id,
            code="000001",
            side=OrderSide.BUY,
            quantity=5.0,
            order_type=OrderType.MARKET,
        )
        order = engine.order_mgr.create_order(req)
        engine.order_mgr.fill_order(order.id, fill_price=10.0, fill_quantity=5.0)
        with pytest.raises(ValueError):
            engine.modify_order(order.id, new_price=11.0)


class TestTradingEngineModifyFlow:
    """End-to-end submit_signal + modify + match."""

    def test_limit_buy_modify_then_match(self, engine_account):
        engine, acc_id = engine_account
        from paper_trading.strategies.engine.rule_engine import Signal

        signal = Signal(
            side="buy",
            code="600519",
            name="贵州茅台",
            strategy_name="e2e_modify",
            rule_name="limit_low",
            trigger_price=15.0,
            suggested_quantity=10.0,
            reason="limit buy below market",
        )
        result = engine.submit_signal(
            account_id=acc_id,
            signal=signal,
            order_type=OrderType.LIMIT,
            limit_price=15.0,
        )
        assert result.status == "pending"
        low_order_id = result.order_id

        modified = engine.order_mgr.modify_order(
            low_order_id, new_price=20.0, reason="raise to fill"
        )
        assert modified.id != low_order_id

        match_results = engine.match_pending_orders({"600519": 18.0})
        assert len(match_results) >= 1
        fill = match_results[0]
        assert fill.status == "executed"
        assert fill.fill_quantity == 10.0

        pos = engine.position_mgr.get_position(acc_id, "600519")
        assert pos is not None
        assert pos.quantity == 10.0
