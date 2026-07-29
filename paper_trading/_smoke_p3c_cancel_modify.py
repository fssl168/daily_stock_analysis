# -*- coding: utf-8 -*-
"""Integration test: order cancel / modify flow (P3-C).

Validates the OrderManager state machine and the TradingEngine limit-order
lifecycle:

1. Create a pending limit buy order, then cancel it. Verify:
   - Status transitions pending -> canceled.
   - cancel_reason is populated.
   - Frozen cash is released (verified via account snapshot).
2. Create a pending limit buy order, then modify its price. Verify:
   - Original order is canceled (cancel_reason="modified").
   - A replacement order is created with parent_order_id set.
   - The replacement has the new price and remaining quantity.
3. Modify a non-existent order -> raises ValueError.
4. Cancel an already-filled order -> raises ValueError.
5. Modify with no parameters -> raises ValueError.
6. Modify a market order -> raises ValueError (only limit orders can be modified).
7. End-to-end via TradingEngine.submit_signal (limit) + match_pending_orders:
   - Place a limit buy below market -> pending.
   - Modify price up to a triggerable level.
   - match_pending_orders fills the replacement order.
8. The PM agent's paper_trading_cancel_order / paper_trading_modify_order
   tools delegate correctly to OrderManager (verified by direct tool call).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows: use tempfile dir to avoid file lock issues with sqlite
os.environ.setdefault("PAPER_TRADING_DB_URL", f"sqlite:///{tempfile.gettempdir()}/smoke_p3c_cm.db")
os.environ.setdefault("PAPER_TRADING_DB_MODE", "sqlite")


def _cleanup_db():
    db_path = Path(tempfile.gettempdir()) / "smoke_p3c_cm.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


def main() -> int:
    from sqlalchemy import select

    from src.storage import (
        DatabaseManager,
        Account,
        PaperOrder,
        get_db,
    )
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

    _cleanup_db()
    DatabaseManager.reset_instance()
    db_url = f"sqlite:///{tempfile.gettempdir()}/smoke_p3c_cm.db"
    db = DatabaseManager(db_url=db_url)

    # --- Build account with 1000 CNY ---
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="smoke_cm", initial_capital=1000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == "smoke_cm")
        ).scalar_one()
        acc_id = acc.id

    fee_model = FeeModel()
    order_mgr = OrderManager(db)
    pos_mgr = PositionManager(db)
    engine = TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=order_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
        risk_checker=RiskChecker(
            db_manager=db,
            account_manager=account_mgr,
            position_manager=pos_mgr,
            fee_model=fee_model,
        ),
    )
    print("[OK] TradingEngine + account built")

    # ------------------------------------------------------------------
    # Test 1: Cancel a pending limit buy order + frozen cash release
    # ------------------------------------------------------------------
    # Use a low-priced stock so 100 shares * 5.0 = 500 CNY < 30% of 1000?
    # Actually 500/1000 = 50% > 30%. Use 30 shares * 5.0 = 150 = 15%.
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
    print(f"[OK] Limit buy created: id={order_id} qty=30 price=5.0")

    # Manually freeze cash (mirrors TradingEngine.submit_signal flow for limit buys).
    estimated_cost = fee_model.estimate_buy_cost(5.0, 30.0)
    account_mgr.freeze_cash(acc_id, estimated_cost)
    snap_before = account_mgr.snapshot(acc_id)
    assert snap_before.frozen_cash == estimated_cost
    print(f"[OK] Cash frozen: {snap_before.frozen_cash:.4f}")

    # Cancel the order.
    canceled = order_mgr.cancel_order(order_id, reason="user canceled")
    assert canceled.status == OrderStatus.CANCELED.value
    assert canceled.cancel_reason == "user canceled"
    # Legacy field should also be populated.
    assert canceled.reject_reason == "user canceled"
    print(f"[OK] Order canceled: id={order_id} status={canceled.status}")

    # Release the frozen cash (caller's responsibility in production code,
    # but we simulate it here to verify the snapshot reflects the release).
    account_mgr.unfreeze_cash(acc_id, estimated_cost)
    snap_after = account_mgr.snapshot(acc_id)
    assert snap_after.frozen_cash == 0.0, (
        f"frozen_cash should be 0 after release, got {snap_after.frozen_cash}"
    )
    assert snap_after.cash == 1000.0, (
        f"cash should be back to 1000, got {snap_after.cash}"
    )
    print("[OK] Frozen cash released after cancel")

    # ------------------------------------------------------------------
    # Test 2: Modify a pending limit order (price change)
    # ------------------------------------------------------------------
    req2 = OrderRequest(
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
    order2 = order_mgr.create_order(req2)
    order2_id = order2.id

    estimated_cost2 = fee_model.estimate_buy_cost(5.0, 30.0)
    account_mgr.freeze_cash(acc_id, estimated_cost2)

    # Modify the price upward.
    modified = order_mgr.modify_order(
        order2_id, new_price=5.5, reason="raise limit to fill faster",
    )
    # The returned row is the new replacement order.
    assert modified.id != order2_id, "modify should create a new order id"
    assert modified.status == OrderStatus.PENDING.value
    assert modified.parent_order_id == order2_id
    assert float(modified.price) == 5.5
    assert float(modified.quantity) == 30.0  # no fills yet, full remaining
    print(f"[OK] Order modified: old_id={order2_id} -> new_id={modified.id} "
          f"new_price={modified.price}")

    # Original order should now be canceled.
    original = order_mgr.get_order(order2_id)
    assert original.status == OrderStatus.CANCELED.value
    assert original.cancel_reason == "raise limit to fill faster"
    print("[OK] Original order canceled after modify")

    # ------------------------------------------------------------------
    # Test 3: Modify non-existent order -> ValueError
    # ------------------------------------------------------------------
    try:
        order_mgr.modify_order(99999, new_price=10.0)
        assert False, "expected ValueError for non-existent order"
    except ValueError as exc:
        assert "not found" in str(exc).lower()
        print(f"[OK] Modify non-existent raises ValueError: {exc}")

    # ------------------------------------------------------------------
    # Test 4: Cancel a filled order -> ValueError
    # ------------------------------------------------------------------
    # Fill the modified replacement order via fill_order.
    fill_order_id = modified.id
    trade = order_mgr.fill_order(fill_order_id, fill_price=5.5, fill_quantity=30.0)
    assert trade.id is not None
    filled = order_mgr.get_order(fill_order_id)
    assert filled.status == OrderStatus.FILLED.value
    print(f"[OK] Replacement order filled: id={fill_order_id}")

    # Now try to cancel the filled order.
    try:
        order_mgr.cancel_order(fill_order_id, reason="try cancel filled")
        assert False, "expected ValueError for canceling filled order"
    except ValueError as exc:
        assert "cannot cancel" in str(exc).lower() or "status" in str(exc).lower()
        print(f"[OK] Cancel filled order raises ValueError: {exc}")

    # ------------------------------------------------------------------
    # Test 5: Modify with no parameters -> ValueError
    # ------------------------------------------------------------------
    # Create another pending order for this test.
    req3 = OrderRequest(
        account_id=acc_id,
        code="000001",
        name="平安银行",
        side=OrderSide.BUY,
        quantity=10.0,
        order_type=OrderType.LIMIT,
        price=10.0,
        strategy_name="test_modify_no_params",
    )
    order3 = order_mgr.create_order(req3)
    try:
        order_mgr.modify_order(order3.id)
        assert False, "expected ValueError for modify with no params"
    except ValueError as exc:
        assert "at least one" in str(exc).lower()
        print(f"[OK] Modify with no params raises ValueError: {exc}")

    # ------------------------------------------------------------------
    # Test 6: Modify a market order -> ValueError
    # ------------------------------------------------------------------
    req_market = OrderRequest(
        account_id=acc_id,
        code="000001",
        name="平安银行",
        side=OrderSide.BUY,
        quantity=5.0,
        order_type=OrderType.MARKET,
    )
    market_order = order_mgr.create_order(req_market)
    try:
        order_mgr.modify_order(market_order.id, new_price=11.0)
        assert False, "expected ValueError for modify market order"
    except ValueError as exc:
        assert "limit" in str(exc).lower()
        print(f"[OK] Modify market order raises ValueError: {exc}")

    # ------------------------------------------------------------------
    # Test 7: End-to-end via TradingEngine.submit_signal + match
    # ------------------------------------------------------------------
    # Place a limit buy below market via submit_signal.
    from strategies_v2.rule_engine import Signal

    signal_low = Signal(
        side="buy",
        code="600519",
        name="贵州茅台",
        strategy_name="e2e_modify",
        rule_name="limit_low",
        trigger_price=15.0,  # well below current price
        suggested_quantity=10.0,
        reason="limit buy below market for modify test",
    )
    result_low = engine.submit_signal(
        account_id=acc_id,
        signal=signal_low,
        order_type=OrderType.LIMIT,
        limit_price=15.0,
    )
    assert result_low.status == "pending", (
        f"expected pending, got {result_low.status}: {result_low.reason}"
    )
    low_order_id = result_low.order_id
    print(f"[OK] Limit buy pending: order_id={low_order_id} price=15.0")

    # Verify cash was frozen.
    snap_pending = account_mgr.snapshot(acc_id)
    assert snap_pending.frozen_cash > 0, "cash should be frozen for limit buy"

    # Modify the order's price up to a level the matcher can fill.
    # The matcher fills buy limit orders when market price <= limit price.
    # Setting limit_price = 20.0 means a market price of 18.0 will fill it.
    modified_e2e = order_mgr.modify_order(low_order_id, new_price=20.0)
    assert modified_e2e.id != low_order_id
    assert float(modified_e2e.price) == 20.0
    print(f"[OK] E2E modify: old_id={low_order_id} -> new_id={modified_e2e.id} "
          f"price=20.0")

    # The original frozen cash should still be frozen (modify doesn't auto-adjust;
    # caller's responsibility). Unfreeze the old and freeze the new amount.
    # Note: in production this would be done by TradingEngine.modify_signal
    # (not yet implemented as of P3-C). For this test, we manually adjust.
    old_freeze = fee_model.estimate_buy_cost(15.0, 10.0)
    new_freeze = fee_model.estimate_buy_cost(20.0, 10.0)
    account_mgr.unfreeze_cash(acc_id, old_freeze)
    account_mgr.freeze_cash(acc_id, new_freeze)

    # Drive the matcher with a market price that triggers the new limit.
    match_results = engine.match_pending_orders({"600519": 18.0})
    assert len(match_results) >= 1, "expected at least 1 match"
    fill_result = match_results[0]
    assert fill_result.status == "executed", (
        f"expected executed, got {fill_result.status}: {fill_result.reason}"
    )
    assert fill_result.fill_quantity == 10.0
    print(f"[OK] Matcher filled modified order: price={fill_result.fill_price} "
          f"qty={fill_result.fill_quantity}")

    # Verify position exists.
    pos = pos_mgr.get_position(acc_id, "600519")
    assert pos is not None and pos.quantity == 10.0
    print(f"[OK] Position created: code={pos.code} qty={pos.quantity}")

    # ------------------------------------------------------------------
    # Test 8: PM agent tool delegation (paper_trading_cancel_order)
    # ------------------------------------------------------------------
    from src.agent.portfolio_manager_agent import register_paper_trading_tools
    from src.agent.tools.registry import ToolRegistry

    # Create a fresh pending order to cancel via the tool.
    req_tool = OrderRequest(
        account_id=acc_id,
        code="000002",
        name="万科A",
        side=OrderSide.BUY,
        quantity=10.0,
        order_type=OrderType.LIMIT,
        price=8.0,
        strategy_name="tool_cancel_test",
    )
    tool_order = order_mgr.create_order(req_tool)
    tool_order_id = tool_order.id

    registry = ToolRegistry()
    register_paper_trading_tools(
        registry=registry, engine=engine, account_id=acc_id, reflection_engine=None,
    )
    # Find the cancel tool and invoke it directly.
    cancel_tool = None
    for t in registry.list_tools():
        if t.name == "paper_trading_cancel_order":
            cancel_tool = t
            break
    assert cancel_tool is not None, "paper_trading_cancel_order tool not registered"

    tool_result = cancel_tool.handler(order_id=tool_order_id)
    assert tool_result.get("status") == "canceled"
    assert tool_result.get("order_id") == tool_order_id
    print(f"[OK] paper_trading_cancel_order tool: {tool_result}")

    # Verify the order is actually canceled in DB.
    canceled_db = order_mgr.get_order(tool_order_id)
    assert canceled_db.status == OrderStatus.CANCELED.value
    print("[OK] Tool cancellation reflected in DB")

    # ------------------------------------------------------------------
    # Test 9: PM agent modify tool delegation
    # ------------------------------------------------------------------
    req_tool2 = OrderRequest(
        account_id=acc_id,
        code="000003",
        name="中信证券",
        side=OrderSide.BUY,
        quantity=10.0,
        order_type=OrderType.LIMIT,
        price=20.0,
        strategy_name="tool_modify_test",
    )
    tool_order2 = order_mgr.create_order(req_tool2)
    tool_order2_id = tool_order2.id

    modify_tool = None
    for t in registry.list_tools():
        if t.name == "paper_trading_modify_order":
            modify_tool = t
            break
    assert modify_tool is not None, "paper_trading_modify_order tool not registered"

    tool_modify_result = modify_tool.handler(
        order_id=tool_order2_id, new_price=21.0,
    )
    assert tool_modify_result.get("status") == "modified"
    assert tool_modify_result.get("order_id") != tool_order2_id  # new id assigned
    print(f"[OK] paper_trading_modify_order tool: {tool_modify_result}")

    # Original order should be canceled.
    orig_db = order_mgr.get_order(tool_order2_id)
    assert orig_db.status == OrderStatus.CANCELED.value
    print("[OK] Tool modification reflected in DB (original canceled)")

    print("\nAll P3-C cancel/modify integration tests passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup_db()
