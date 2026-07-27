# -*- coding: utf-8 -*-
"""Smoke tests for P0-C order management enhancements.

Validates:
  1. OrderManager.cancel_order sets cancel_reason + status='canceled'.
  2. OrderManager.modify_order cancels old + creates replacement with parent_order_id.
  3. TradingEngine.cancel_signal cancels order + unfreezes cash + marks signal rejected.
  4. TradingEngine.modify_signal re-freezes cash + creates replacement order.
  5. Terminal-status signal cancel is a no-op.
  6. modify_signal on market order raises ValueError.
"""

from __future__ import annotations

import os
import sys
import tempfile

# Ensure project root on path.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def _make_engine_with_temp_db():
    from paper_trading.account import PaperAccountManager
    from paper_trading.fees import FeeModel
    from paper_trading.order import OrderManager
    from paper_trading.position import PositionManager
    from paper_trading.risk import RiskChecker
    from paper_trading.trading_engine import TradingEngine
    from src.storage import DatabaseManager

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_url = f"sqlite:///{tmp.name}"
    # Reset singleton so each test gets a fresh isolated DB.
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=db_url)  # __init__ auto-runs Base.metadata.create_all

    account_mgr = PaperAccountManager(db)
    order_mgr = OrderManager(db)
    pos_mgr = PositionManager(db)
    fee_model = FeeModel()
    risk = RiskChecker(
        db_manager=db,
        account_manager=account_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
    )
    engine = TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=order_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
        risk_checker=risk,
    )
    return engine, db, tmp.name


def _create_account(engine, name="test", capital=10000.0):
    """Create a paper account and return its id (avoid detached-instance issues)."""
    from src.storage import PaperAccount
    from sqlalchemy import select

    engine.account_mgr.get_or_create_account(name=name, initial_capital=capital)
    with engine.db.session_scope() as session:
        row = session.execute(
            select(PaperAccount).where(PaperAccount.name == name)
        ).scalar_one()
        return int(row.id)


def _make_signal(code="600519", side="buy", trigger_price=100.0, qty=10):
    from strategies_v2.rule_engine import Signal

    return Signal(
        side=side,
        code=code,
        name="贵州茅台",
        strategy_name="test_strategy",
        rule_name="test_rule",
        trigger_price=trigger_price,
        suggested_quantity=qty,
        reason="test signal",
    )


def test_order_cancel():
    """OrderManager.cancel_order sets cancel_reason + status='canceled'."""
    from paper_trading.order import OrderRequest, OrderSide, OrderType
    from src.storage import PaperOrder
    from sqlalchemy import select

    engine, db, _ = _make_engine_with_temp_db()
    account_id = _create_account(engine)

    req = OrderRequest(
        account_id=account_id,
        code="600519",
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.LIMIT,
        price=100.0,
        name="贵州茅台",
        strategy_name="test",
        reason="test",
    )
    order = engine.order_mgr.create_order(req)
    canceled = engine.order_mgr.cancel_order(order.id, reason="user requested")

    assert canceled.status == "canceled", f"expected canceled, got {canceled.status}"
    assert canceled.cancel_reason == "user requested"
    assert canceled.reject_reason == "user requested"  # backwards-compat

    # Verify persisted.
    with db.session_scope() as session:
        row = session.execute(select(PaperOrder).where(PaperOrder.id == order.id)).scalar_one()
        assert row.status == "canceled"
        assert row.cancel_reason == "user requested"
    print("[1] order_cancel OK")


def test_order_modify():
    """OrderManager.modify_order cancels old + creates replacement with parent_order_id."""
    from paper_trading.order import OrderRequest, OrderSide, OrderType
    from src.storage import PaperOrder
    from sqlalchemy import select

    engine, db, _ = _make_engine_with_temp_db()
    account_id = _create_account(engine)

    req = OrderRequest(
        account_id=account_id,
        code="600519",
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.LIMIT,
        price=100.0,
        name="贵州茅台",
        strategy_name="test",
        reason="test",
    )
    orig = engine.order_mgr.create_order(req)
    new_order = engine.order_mgr.modify_order(
        orig.id, new_price=102.0, new_quantity=20, reason="price moved"
    )

    assert new_order.status == "pending", f"expected pending, got {new_order.status}"
    assert new_order.price == 102.0
    assert new_order.quantity == 20
    assert new_order.parent_order_id == orig.id

    # Original should be canceled.
    with db.session_scope() as session:
        old = session.execute(select(PaperOrder).where(PaperOrder.id == orig.id)).scalar_one()
        assert old.status == "canceled"
        assert old.cancel_reason == "price moved"
    print("[2] order_modify OK")


def test_cancel_signal_unfreezes_cash():
    """TradingEngine.cancel_signal cancels order + unfreezes cash + marks signal rejected."""
    from paper_trading.order import OrderType
    from src.storage import PaperOrder, PaperSignal
    from sqlalchemy import select

    engine, db, _ = _make_engine_with_temp_db()
    account_id = _create_account(engine)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(
        account_id=account_id,
        signal=sig,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
    )
    assert result.status == "pending"

    # Cash should be frozen.
    snap_before = engine.account_mgr.snapshot(account_id)
    assert snap_before.frozen_cash > 0, f"frozen_cash={snap_before.frozen_cash}"

    # Cancel.
    cancel_result = engine.cancel_signal(result.signal_id, reason="test cancel")
    assert cancel_result.status == "rejected"
    assert cancel_result.order_id == result.order_id

    # Frozen cash should be released.
    snap_after = engine.account_mgr.snapshot(account_id)
    assert snap_after.frozen_cash == 0.0, f"frozen_cash after cancel={snap_after.frozen_cash}"
    assert snap_after.cash == snap_before.cash + snap_before.frozen_cash

    # Signal should be rejected.
    with db.session_scope() as session:
        sig_row = session.execute(
            select(PaperSignal).where(PaperSignal.id == result.signal_id)
        ).scalar_one()
        assert sig_row.status == "rejected"
        order_row = session.execute(
            select(PaperOrder).where(PaperOrder.id == result.order_id)
        ).scalar_one()
        assert order_row.status == "canceled"
        assert order_row.cancel_reason == "test cancel"
    print("[3] cancel_signal_unfreezes_cash OK")


def test_modify_signal_refreezes_cash():
    """TradingEngine.modify_signal re-freezes cash + creates replacement order."""
    from paper_trading.order import OrderType
    from src.storage import PaperOrder
    from sqlalchemy import select

    engine, db, _ = _make_engine_with_temp_db()
    account_id = _create_account(engine)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(
        account_id=account_id,
        signal=sig,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
    )
    assert result.status == "pending"
    old_order_id = result.order_id

    snap_old = engine.account_mgr.snapshot(account_id)
    old_frozen = snap_old.frozen_cash

    # Modify: increase price and quantity.
    mod_result = engine.modify_signal(
        result.signal_id, new_price=102.0, new_quantity=20, reason="price moved"
    )
    assert mod_result.status == "pending"
    assert mod_result.order_id != old_order_id, "expected new order id"

    # New order should have new price/qty.
    with db.session_scope() as session:
        new_order = session.execute(
            select(PaperOrder).where(PaperOrder.id == mod_result.order_id)
        ).scalar_one()
        assert new_order.price == 102.0
        assert new_order.quantity == 20
        assert new_order.parent_order_id == old_order_id
        assert new_order.status == "pending"

        old_order = session.execute(
            select(PaperOrder).where(PaperOrder.id == old_order_id)
        ).scalar_one()
        assert old_order.status == "canceled"

    # Frozen cash should reflect new (larger) cost.
    snap_new = engine.account_mgr.snapshot(account_id)
    assert snap_new.frozen_cash > old_frozen, (
        f"frozen_cash should increase after raising price+qty: "
        f"old={old_frozen} new={snap_new.frozen_cash}"
    )
    print("[4] modify_signal_refreezes_cash OK")


def test_cancel_terminal_signal_is_noop():
    """Canceling an already-executed signal returns the terminal status."""
    from paper_trading.order import OrderType

    engine, db, _ = _make_engine_with_temp_db()
    account_id = _create_account(engine)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(
        account_id=account_id,
        signal=sig,
        order_type=OrderType.MARKET,
    )
    assert result.status == "executed"

    cancel_result = engine.cancel_signal(result.signal_id, reason="too late")
    assert cancel_result.status == "executed"
    assert "terminal" in cancel_result.reason
    print("[5] cancel_terminal_signal_is_noop OK")


def test_modify_market_signal_raises():
    """Modifying a market-order signal cannot create a new pending order.

    Market orders fill immediately, so the signal is in 'executed' terminal
    status. modify_signal should either raise ValueError OR return a
    TradeResult indicating terminal status (no new order created).
    """
    from paper_trading.order import OrderType

    engine, db, _ = _make_engine_with_temp_db()
    account_id = _create_account(engine)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(
        account_id=account_id,
        signal=sig,
        order_type=OrderType.MARKET,
    )
    assert result.status == "executed"

    try:
        mod_result = engine.modify_signal(result.signal_id, new_price=101.0)
    except ValueError as exc:
        # Acceptable: raise on terminal status.
        assert "terminal" in str(exc) or "limit" in str(exc).lower() or "no cancellable" in str(exc).lower(), (
            f"unexpected error message: {exc}"
        )
        print("[6] modify_market_signal_raises OK (raised ValueError)")
        return

    # Acceptable: return a TradeResult indicating terminal status (no new order).
    assert mod_result.status in ("executed", "rejected"), (
        f"expected terminal status, got {mod_result.status}"
    )
    assert mod_result.order_id is None, "no new order should be created for terminal signal"
    assert "terminal" in mod_result.reason, f"unexpected reason: {mod_result.reason}"
    print("[6] modify_market_signal_raises OK (returned terminal status)")


if __name__ == "__main__":
    test_order_cancel()
    test_order_modify()
    test_cancel_signal_unfreezes_cash()
    test_modify_signal_refreezes_cash()
    test_cancel_terminal_signal_is_noop()
    test_modify_market_signal_raises()
    print("\nAll P0-C smoke tests passed.")
