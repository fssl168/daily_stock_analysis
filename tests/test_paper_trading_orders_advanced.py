# -*- coding: utf-8 -*-
"""pytest tests for Phase 1 advanced order features.

Covers:
- Batch order creation (market + limit)
- Conditional orders (stop-loss / take-profit)
- OCO (One-Cancels-the-Other) linkage
- Order list filtering
"""

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
from src.storage import DatabaseManager, PaperAccount, PaperOrder


def _build_engine(db: DatabaseManager) -> tuple[TradingEngine, int]:
    """Create an account + engine wired to the supplied temp database."""
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="pytest_adv", initial_capital=10000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(PaperAccount).where(PaperAccount.name == "pytest_adv")
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
        enable_auto_sltp=False,
    )
    return engine, acc_id


@pytest.fixture
def engine_account(temp_db):
    return _build_engine(temp_db)


class TestBatchOrders:
    """Atomic batch creation with mixed market/limit orders."""

    def test_batch_three_market_buys_all_filled(self, engine_account):
        engine, acc_id = engine_account
        order_mgr = engine.order_mgr

        requests = [
            OrderRequest(
                account_id=acc_id,
                code="600000",
                name="浦发银行",
                side=OrderSide.BUY,
                quantity=10.0,
                order_type=OrderType.MARKET,
                price=5.0,
                strategy_name="batch_test",
                reason="batch market buy 1",
            ),
            OrderRequest(
                account_id=acc_id,
                code="600000",
                name="浦发银行",
                side=OrderSide.BUY,
                quantity=10.0,
                order_type=OrderType.MARKET,
                price=5.0,
                strategy_name="batch_test",
                reason="batch market buy 2",
            ),
            OrderRequest(
                account_id=acc_id,
                code="600000",
                name="浦发银行",
                side=OrderSide.BUY,
                quantity=10.0,
                order_type=OrderType.MARKET,
                price=5.0,
                strategy_name="batch_test",
                reason="batch market buy 3",
            ),
        ]

        created = order_mgr.create_batch_orders(acc_id, requests)
        assert len(created) == 3
        assert all(o.status == OrderStatus.PENDING.value for o in created)

        # Execute market orders immediately, mimicking the API endpoint.
        results = []
        for order in created:
            order_dict = order_mgr._order_to_dict(order)
            result = engine._execute_triggered_market_order(
                order_dict, fill_price=5.0
            )
            results.append(result)

        assert all(r.status == "executed" for r in results)
        assert all(r.fill_price == 5.0 for r in results)
        assert all(r.fill_quantity == 10.0 for r in results)

        snap = engine.account_mgr.snapshot(acc_id)
        assert snap.cash < 10000.0
        pos = engine.position_mgr.get_position(acc_id, "600000")
        assert pos is not None
        assert float(pos.quantity) == 30.0

    def test_batch_limit_orders_left_pending(self, engine_account):
        engine, acc_id = engine_account
        order_mgr = engine.order_mgr

        requests = [
            OrderRequest(
                account_id=acc_id,
                code="600000",
                side=OrderSide.BUY,
                quantity=10.0,
                order_type=OrderType.LIMIT,
                price=4.5,
            ),
            OrderRequest(
                account_id=acc_id,
                code="600000",
                side=OrderSide.BUY,
                quantity=10.0,
                order_type=OrderType.LIMIT,
                price=4.6,
            ),
        ]

        created = order_mgr.create_batch_orders(acc_id, requests)
        assert len(created) == 2
        assert all(o.status == OrderStatus.PENDING.value for o in created)

        # Limit orders are not filled until price crosses.
        rows = order_mgr.list_orders(acc_id, status="pending")
        assert len(rows) == 2


class TestConditionalOrders:
    """Stop-loss / take-profit / OCO behavior."""

    def _buy_position(self, engine: TradingEngine, acc_id: int, code: str, qty: float, price: float):
        """Helper: buy a position so we have something to protect."""
        req = OrderRequest(
            account_id=acc_id,
            code=code,
            side=OrderSide.BUY,
            quantity=qty,
            order_type=OrderType.MARKET,
            price=price,
            strategy_name="setup",
            reason="acquire position for conditional test",
        )
        order = engine.order_mgr.create_order(req)
        engine._execute_triggered_market_order(
            engine.order_mgr._order_to_dict(order), fill_price=price
        )
        # Make shares available for protective sells (T+1 roll).
        engine.position_mgr.daily_roll_available(acc_id)

    def test_stop_loss_sell_triggers_when_price_drops(self, engine_account):
        engine, acc_id = engine_account
        self._buy_position(engine, acc_id, "600000", 10.0, 10.0)

        sl_order = engine.order_mgr.create_conditional_order(
            account_id=acc_id,
            code="600000",
            side=OrderSide.SELL,
            quantity=10.0,
            order_type=OrderType.STOP_LOSS,
            trigger_price=9.5,
            reason="protect position",
        )
        assert sl_order.status == OrderStatus.CONDITIONAL.value
        assert sl_order.trigger_price == 9.5

        # Price drops below stop -> trigger and fill as market sell.
        results = engine.tick_market_price(acc_id, "600000", 9.4)
        assert len(results) == 1
        assert results[0].status == "executed"
        assert results[0].side == "sell"
        assert results[0].code == "600000"

        refreshed = engine.order_mgr.get_order(sl_order.id)
        assert refreshed.status == OrderStatus.FILLED.value

    def test_take_profit_sell_triggers_when_price_rises(self, engine_account):
        engine, acc_id = engine_account
        self._buy_position(engine, acc_id, "600000", 10.0, 10.0)

        tp_order = engine.order_mgr.create_conditional_order(
            account_id=acc_id,
            code="600000",
            side=OrderSide.SELL,
            quantity=10.0,
            order_type=OrderType.TAKE_PROFIT,
            trigger_price=11.0,
            reason="take profit",
        )
        assert tp_order.status == OrderStatus.CONDITIONAL.value

        results = engine.tick_market_price(acc_id, "600000", 11.1)
        assert len(results) == 1
        assert results[0].status == "executed"

    def test_oco_pair_one_triggers_other_cancels(self, engine_account):
        engine, acc_id = engine_account
        self._buy_position(engine, acc_id, "600000", 10.0, 10.0)

        # Create OCO pair: stop-loss primary + take-profit secondary.
        primary = engine.order_mgr.create_conditional_order(
            account_id=acc_id,
            code="600000",
            side=OrderSide.SELL,
            quantity=10.0,
            order_type=OrderType.OCO_PRIMARY,
            trigger_price=9.5,
        )
        secondary = engine.order_mgr.create_conditional_order(
            account_id=acc_id,
            code="600000",
            side=OrderSide.SELL,
            quantity=10.0,
            order_type=OrderType.OCO_SECONDARY,
            trigger_price=11.0,
            linked_order_id=primary.id,
        )

        # Link back the primary to the secondary for full OCO semantics.
        with engine.db.session_scope() as session:
            row = session.execute(
                select(PaperOrder).where(PaperOrder.id == primary.id)
            ).scalar_one()
            row.linked_order_id = secondary.id

        assert primary.status == OrderStatus.CONDITIONAL.value
        assert secondary.status == OrderStatus.CONDITIONAL.value

        # Price drops below stop-loss: primary should fill, secondary cancel.
        results = engine.tick_market_price(acc_id, "600000", 9.4)
        executed = [r for r in results if r.status == "executed"]
        assert len(executed) == 1
        assert executed[0].order_id == primary.id

        refreshed_primary = engine.order_mgr.get_order(primary.id)
        refreshed_secondary = engine.order_mgr.get_order(secondary.id)
        assert refreshed_primary.status == OrderStatus.FILLED.value
        assert refreshed_secondary.status == OrderStatus.CANCELED.value


class TestOrderListFiltering:
    """OrderManager.list_orders supports status/side/code/date filters."""

    def test_filter_orders_by_status_and_side(self, engine_account):
        engine, acc_id = engine_account
        order_mgr = engine.order_mgr

        # One filled market buy, one pending limit buy.
        market_req = OrderRequest(
            account_id=acc_id,
            code="600000",
            side=OrderSide.BUY,
            quantity=10.0,
            order_type=OrderType.MARKET,
            price=5.0,
        )
        market_order = order_mgr.create_order(market_req)
        engine._execute_triggered_market_order(
            order_mgr._order_to_dict(market_order), fill_price=5.0
        )

        limit_req = OrderRequest(
            account_id=acc_id,
            code="600001",
            side=OrderSide.BUY,
            quantity=10.0,
            order_type=OrderType.LIMIT,
            price=4.0,
        )
        order_mgr.create_order(limit_req)

        filled = order_mgr.list_orders(acc_id, status="filled")
        assert len(filled) == 1
        assert filled[0]["side"] == "buy"

        pending = order_mgr.list_orders(acc_id, status="pending")
        assert len(pending) == 1
        assert pending[0]["code"] == "600001"

        buy_orders = order_mgr.list_orders(acc_id, side="buy")
        assert len(buy_orders) == 2
