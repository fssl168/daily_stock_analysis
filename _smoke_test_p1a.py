# -*- coding: utf-8 -*-
"""Smoke tests for P1-A: Smart Stop-Loss / Take-Profit integration.

Covers:
1. SLTPCalculator.compute() with a synthetic DataFrame produces sane SL/TP1/TP2.
2. TradingEngine auto-applies SL/TP after a market buy when sltp_calculator
   is configured.
3. TradingEngine auto-applies SL/TP after a limit buy fills.
4. check_stop_loss_take_profit triggers on TP2 (mid-term target).
5. _apply_sltp_to_position does NOT overwrite SL/TP already set by strategy.
6. SLTPCalculator falls back gracefully when data is insufficient.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

# Project root on sys.path.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic_df(days: int = 90, start_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    """Build a synthetic daily-bar DataFrame with OHLCV columns."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=datetime.now().strftime("%Y-%m-%d"), periods=days, freq="B")

    # Random walk with slight upward drift.
    returns = rng.normal(loc=0.001, scale=0.015, size=days)
    closes = start_price * np.exp(np.cumsum(returns))

    highs = closes * (1 + rng.uniform(0.001, 0.012, size=days))
    lows = closes * (1 - rng.uniform(0.001, 0.012, size=days))
    opens = closes * (1 + rng.uniform(-0.005, 0.005, size=days))
    volumes = rng.integers(1_000_000, 10_000_000, size=days).astype(float)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }, index=dates)
    df.index.name = "date"
    return df


def _make_engine_with_temp_db(sltp_calculator=None):
    """Build a TradingEngine backed by an in-memory SQLite DB."""
    from paper_trading.account import PaperAccountManager
    from paper_trading.fees import FeeModel
    from paper_trading.order import OrderManager
    from paper_trading.position import PositionManager
    from paper_trading.risk import RiskChecker
    from paper_trading.trading_engine import TradingEngine
    from src.storage import DatabaseManager

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    db_url = f"sqlite:///{db_path}"
    # Reset singleton so each test gets a fresh isolated DB.
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=db_url)  # __init__ auto-runs Base.metadata.create_all

    account_mgr = PaperAccountManager(db)
    order_mgr = OrderManager(db)
    pos_mgr = PositionManager(db)
    fee_model = FeeModel()
    risk_checker = RiskChecker(
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
        risk_checker=risk_checker,
        sltp_calculator=sltp_calculator,
    )
    return engine, db, db_path


def _cleanup_db(db_path: str) -> None:
    """Best-effort cleanup of the SQLite temp file (Windows may hold a lock)."""
    try:
        if db_path and os.path.exists(db_path):
            os.unlink(db_path)
    except (PermissionError, OSError):
        # Windows often holds SQLite file locks; the OS temp dir will reap it.
        pass


def _create_account(engine, initial_capital: float = 10000.0) -> int:
    """Create a paper account and return its id (avoid detached-instance issues)."""
    from src.storage import PaperAccount
    from sqlalchemy import select

    engine.account_mgr.get_or_create_account(name="test", initial_capital=initial_capital)
    with engine.db.session_scope() as session:
        row = session.execute(
            select(PaperAccount).where(PaperAccount.name == "test")
        ).scalar_one()
        return int(row.id)


def _make_signal(side: str = "buy", trigger_price: float = 100.0, qty: float = 10):
    """Build a minimal Signal for testing."""
    from strategies_v2.rule_engine import Signal
    return Signal(
        side=side,
        code="600519",
        name="贵州茅台",
        strategy_name="test_strategy",
        rule_name="test_rule",
        trigger_price=trigger_price,
        suggested_quantity=qty,
        reason="test signal",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sltp_calculator_basic():
    """SLTPCalculator.compute produces sane SL < entry < TP1 < TP2."""
    from paper_trading.sltp_calculator import SLTPCalculator

    df = _make_synthetic_df(days=90, start_price=100.0)
    calc = SLTPCalculator(lookback=60, atr_period=14, chip_bins=20, chip_lookback=60)
    entry = float(df["close"].iloc[-1])
    result = calc.compute(code="600519", entry_price=entry, df=df)

    assert result.stop_loss < entry, f"SL {result.stop_loss} should be < entry {entry}"
    assert result.take_profit_1 > entry, f"TP1 {result.take_profit_1} should be > entry {entry}"
    assert result.take_profit_2 > result.take_profit_1, f"TP2 {result.take_profit_2} should be > TP1 {result.take_profit_1}"
    assert result.atr is not None and result.atr > 0
    assert result.method in ("atr_fib_support_blend", "atr_fallback")
    print(f"[1] sltp_calculator_basic OK (entry={entry:.2f} SL={result.stop_loss:.2f} "
          f"TP1={result.take_profit_1:.2f} TP2={result.take_profit_2:.2f} ATR={result.atr:.4f})")


def test_engine_auto_applies_sltp_on_market_buy():
    """After a market BUY fill, position.stop_loss / take_profit / take_profit_2
    should be populated by the sltp_calculator.
    """
    from paper_trading.sltp_calculator import SLTPCalculator
    from src.storage import PaperPosition
    from sqlalchemy import select

    df = _make_synthetic_df(days=90, start_price=100.0)
    # Use a calculator with a stub data_provider that returns our synthetic df.
    class _StubProvider:
        def get_daily_data(self, code, days=90):
            return df.copy()

    calc = SLTPCalculator(data_provider=_StubProvider(), lookback=60, atr_period=14)
    engine, db, db_path = _make_engine_with_temp_db(sltp_calculator=calc)
    account_id = _create_account(engine, initial_capital=10000.0)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(account_id=account_id, signal=sig)
    assert result.status == "executed", f"expected executed, got {result.status}"

    with db.session_scope() as session:
        pos = session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.code == "600519",
            )
        ).scalar_one()
        assert pos.stop_loss is not None, "stop_loss should be set by SLTP calculator"
        assert pos.take_profit is not None, "take_profit should be set by SLTP calculator"
        assert pos.take_profit_2 is not None, "take_profit_2 should be set by SLTP calculator"
        assert pos.stop_loss < float(pos.avg_cost), "SL should be below avg_cost"
        assert pos.take_profit > float(pos.avg_cost), "TP1 should be above avg_cost"
        assert pos.take_profit_2 > pos.take_profit, "TP2 should be above TP1"
        assert pos.sltp_reasoning, "sltp_reasoning should be non-empty"
    print("[2] engine_auto_applies_sltp_on_market_buy OK")
    _cleanup_db(db_path)


def test_engine_auto_applies_sltp_on_limit_buy_fill():
    """After a pending limit BUY fills via match_pending_orders, SL/TP should
    also be auto-applied.
    """
    from paper_trading.sltp_calculator import SLTPCalculator
    from paper_trading.order import OrderType
    from src.storage import PaperPosition
    from sqlalchemy import select

    df = _make_synthetic_df(days=90, start_price=100.0)

    class _StubProvider:
        def get_daily_data(self, code, days=90):
            return df.copy()

    calc = SLTPCalculator(data_provider=_StubProvider(), lookback=60, atr_period=14)
    engine, db, db_path = _make_engine_with_temp_db(sltp_calculator=calc)
    account_id = _create_account(engine, initial_capital=10000.0)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(
        account_id=account_id,
        signal=sig,
        order_type=OrderType.LIMIT,
        limit_price=99.0,  # below current market so it won't fill immediately
    )
    assert result.status == "pending", f"expected pending, got {result.status}"

    # Now drive the matcher with a price that triggers the buy (price <= limit).
    results = engine.match_pending_orders({"600519": 98.5})
    assert len(results) == 1, f"expected 1 fill, got {len(results)}"
    assert results[0].status == "executed"

    with db.session_scope() as session:
        pos = session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.code == "600519",
            )
        ).scalar_one()
        assert pos.stop_loss is not None, "SL should be set after limit buy fill"
        assert pos.take_profit is not None, "TP1 should be set after limit buy fill"
        assert pos.take_profit_2 is not None, "TP2 should be set after limit buy fill"
    print("[3] engine_auto_applies_sltp_on_limit_buy_fill OK")
    _cleanup_db(db_path)


def test_check_stop_loss_take_profit_triggers_on_tp2():
    """When price reaches TP2, the SL/TP guard should emit a sell signal."""
    from paper_trading.sltp_calculator import SLTPCalculator
    from src.storage import PaperPosition
    from sqlalchemy import select

    df = _make_synthetic_df(days=90, start_price=100.0)

    class _StubProvider:
        def get_daily_data(self, code, days=90):
            return df.copy()

    calc = SLTPCalculator(data_provider=_StubProvider(), lookback=60, atr_period=14)
    engine, db, db_path = _make_engine_with_temp_db(sltp_calculator=calc)
    account_id = _create_account(engine, initial_capital=10000.0)

    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine.submit_signal(account_id=account_id, signal=sig)
    assert result.status == "executed"

    # Read the TP2 from the position.
    with db.session_scope() as session:
        pos = session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.code == "600519",
            )
        ).scalar_one()
        tp2 = float(pos.take_profit_2)
        sl = float(pos.stop_loss)

    # T+1 roll so the position is available to sell.
    engine.position_mgr.daily_roll_available(account_id)

    # Drive price up to TP2 — should trigger a sell.
    trigger_results = engine.check_stop_loss_take_profit({"600519": tp2})
    assert len(trigger_results) >= 1, f"expected TP2 trigger, got {len(trigger_results)}"
    trigger = trigger_results[0]
    # Either executed (if cash/position all fine) or rejected (e.g. T+1).
    assert trigger.side == "sell"
    # TradeResult.reason is the execution status ("market order filled" etc.);
    # the *signal* reason (which carries the trigger type) is persisted on
    # PaperSignal.reason. Look it up via signal_id to confirm TP2 trigger.
    from src.storage import PaperSignal
    with db.session_scope() as session:
        sig_row = session.execute(
            select(PaperSignal).where(PaperSignal.id == trigger.signal_id)
        ).scalar_one()
        assert "take_profit_2" in (sig_row.reason or ""), (
            f"signal.reason should mention take_profit_2, got: {sig_row.reason!r}"
        )
        assert sig_row.rule_name == "take_profit_2"
    print(f"[4] check_stop_loss_take_profit_triggers_on_tp2 OK (TP2={tp2:.2f})")
    _cleanup_db(db_path)


def test_sltp_does_not_overwrite_strategy_set_values():
    """If the strategy already set SL/TP on the position (via
    update_stop_loss_take_profit), the SLTPCalculator should NOT overwrite.
    """
    from paper_trading.sltp_calculator import SLTPCalculator
    from src.storage import PaperPosition
    from sqlalchemy import select

    df = _make_synthetic_df(days=90, start_price=100.0)

    class _StubProvider:
        def get_daily_data(self, code, days=90):
            return df.copy()

    calc = SLTPCalculator(data_provider=_StubProvider(), lookback=60, atr_period=14)
    engine, db, db_path = _make_engine_with_temp_db(sltp_calculator=calc)
    account_id = _create_account(engine, initial_capital=10000.0)

    # Pre-set SL/TP BEFORE the buy (simulating strategy explicitly setting them).
    # We'll do it by creating the position first via a no-buy path: directly
    # write a row, then run apply_buy which will merge into it.
    # Simpler: do the buy first WITHOUT the calculator, then turn on calculator
    # and do another buy — but that re-averages. Cleanest: do the buy, manually
    # set SL/TP, then re-run _apply_sltp_to_position and verify no change.

    # Build engine WITHOUT calculator first.
    engine_no_calc, db_no_calc, db_path_no_calc = _make_engine_with_temp_db(sltp_calculator=None)
    account_id_no_calc = _create_account(engine_no_calc, initial_capital=10000.0)
    sig = _make_signal(side="buy", trigger_price=100.0, qty=10)
    result = engine_no_calc.submit_signal(account_id=account_id_no_calc, signal=sig)
    assert result.status == "executed"

    # Manually set SL/TP (simulating strategy setting them).
    engine_no_calc.position_mgr.update_stop_loss_take_profit(
        account_id=account_id_no_calc,
        code="600519",
        stop_loss=95.0,
        take_profit=110.0,
        take_profit_2=120.0,
        sltp_reasoning="strategy-explicit",
    )

    # Now attach the calculator to the engine and call _apply_sltp_to_position
    # with the same entry price. It should detect existing SL+TP and skip.
    engine_no_calc.sltp_calculator = calc
    applied = engine_no_calc._apply_sltp_to_position(
        account_id=account_id_no_calc, code="600519", entry_price=100.0,
    )
    assert applied is None, f"SLTP should NOT be applied when SL/TP already set, got {applied}"

    with db_no_calc.session_scope() as session:
        pos = session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id_no_calc,
                PaperPosition.code == "600519",
            )
        ).scalar_one()
        assert pos.stop_loss == 95.0, f"SL should remain 95.0, got {pos.stop_loss}"
        assert pos.take_profit == 110.0, f"TP should remain 110.0, got {pos.take_profit}"
        assert pos.take_profit_2 == 120.0, f"TP2 should remain 120.0, got {pos.take_profit_2}"
        assert pos.sltp_reasoning == "strategy-explicit"
    print("[5] sltp_does_not_overwrite_strategy_set_values OK")
    _cleanup_db(db_path)
    _cleanup_db(db_path_no_calc)


def test_sltp_calculator_fallback_on_insufficient_data():
    """With < lookback rows, SLTPCalculator should fall back to ATR-only defaults."""
    from paper_trading.sltp_calculator import SLTPCalculator

    # Only 5 rows — well below the 60-bar lookback.
    df = _make_synthetic_df(days=5, start_price=100.0)
    calc = SLTPCalculator(lookback=60, atr_period=14)
    result = calc.compute(code="600519", entry_price=100.0, df=df)

    assert result.method == "atr_fallback", f"expected atr_fallback, got {result.method}"
    assert result.stop_loss < 100.0
    assert result.take_profit_1 > 100.0
    assert result.take_profit_2 > result.take_profit_1
    # Synthetic ATR = 1% of entry = 1.0; SL = 100 - 1.5*1 = 98.5.
    assert abs(result.atr - 1.0) < 0.01, f"fallback ATR should be 1.0, got {result.atr}"
    print(f"[6] sltp_calculator_fallback_on_insufficient_data OK "
          f"(method={result.method} SL={result.stop_loss} TP1={result.take_profit_1})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_sltp_calculator_basic()
    test_engine_auto_applies_sltp_on_market_buy()
    test_engine_auto_applies_sltp_on_limit_buy_fill()
    test_check_stop_loss_take_profit_triggers_on_tp2()
    test_sltp_does_not_overwrite_strategy_set_values()
    test_sltp_calculator_fallback_on_insufficient_data()
    print("\nAll P1-A smoke tests passed.")
