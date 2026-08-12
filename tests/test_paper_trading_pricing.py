# -*- coding: utf-8 -*-
"""T-02 pricing-loop regression tests.

Market orders must price off the shared live-quote cache (via the slippage
model) instead of the caller-supplied reference price, with an explicit
degraded path when no fresh quote exists. Position display must value off the
live price too.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel
from paper_trading.order import OrderManager, OrderType
from paper_trading.position import PositionManager
from paper_trading.quote_cache import CachedQuote, SharedQuoteCache
from paper_trading.risk import RiskChecker, RiskConfig
from paper_trading.strategies import Signal
from paper_trading.trading_engine import TradingEngine
from src.storage import Account

from api.v1.endpoints.paper_trading import _apply_live_valuation


def _make_engine(db, cache=None) -> tuple[TradingEngine, int]:
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="pricing_test", initial_capital=1000000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == "pricing_test", Account.account_type == "paper")
        ).scalar_one()
        acc_id = acc.id

    fee_model = FeeModel()
    pos_mgr = PositionManager(db)
    order_mgr = OrderManager(db)
    risk = RiskChecker(
        db_manager=db,
        account_manager=account_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
        config=RiskConfig(max_daily_loss_pct=0.05),
    )
    engine = TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=order_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
        risk_checker=risk,
        enable_auto_sltp=False,
        quote_cache=cache,
    )
    return engine, acc_id


def _quote(price: float) -> CachedQuote:
    return CachedQuote(
        price=price, volume=100, change_pct=1.0, high=price * 1.01, low=price * 0.99,
        open=price, pre_close=price, timestamp=datetime.now(), source="test",
    )


def _buy_signal(code: str = "600519", trigger: float = 1700.0, qty: float = 100.0) -> Signal:
    return Signal(
        side="buy", code=code, name="贵州茅台", strategy_name="test",
        rule_name="t", trigger_price=trigger, suggested_quantity=qty, reason="t",
    )


# ---------------------------------------------------------------------------
# AC-202: market fill prices off the live quote
# ---------------------------------------------------------------------------


def test_market_order_prices_off_live_quote(temp_db):
    cache = SharedQuoteCache()
    cache.update("600519", _quote(1680.0))
    engine, acc_id = _make_engine(temp_db, cache)
    res = engine.submit_signal(
        account_id=acc_id, signal=_buy_signal(), order_type=OrderType.MARKET,
        quantity_override=100,
    )
    assert res.status == "executed", res.reason
    expected = engine.fee_model.apply_slippage(1680.0, "buy")
    assert res.fill_price is not None
    assert abs(res.fill_price - expected) < 1e-6, (res.fill_price, expected)


def test_market_order_fill_reflects_slippage_bps(temp_db):
    cache = SharedQuoteCache()
    cache.update("600519", _quote(100.0))
    engine, acc_id = _make_engine(temp_db, cache)
    res = engine.submit_signal(
        account_id=acc_id, signal=_buy_signal(trigger=105.0), order_type=OrderType.MARKET,
        quantity_override=10,
    )
    assert res.status == "executed", res.reason
    # slippage applies on top of the live 100.0, NOT the trigger 105.0
    expected = engine.fee_model.apply_slippage(100.0, "buy")
    assert abs(res.fill_price - expected) < 1e-6


# ---------------------------------------------------------------------------
# AC-203: degraded path (cache wired but no fresh quote) logs and still fills
# ---------------------------------------------------------------------------


def test_market_order_degrades_and_logs_when_quote_missing(temp_db, caplog):
    cache = SharedQuoteCache()  # empty
    engine, acc_id = _make_engine(temp_db, cache)
    with caplog.at_level("WARNING", logger="paper_trading.trading_engine"):
        res = engine.submit_signal(
            account_id=acc_id, signal=_buy_signal(), order_type=OrderType.MARKET,
            quantity_override=100,
        )
    assert res.status == "executed", res.reason
    expected = engine.fee_model.apply_slippage(1700.0, "buy")
    assert abs(res.fill_price - expected) < 1e-6
    assert any("no fresh quote" in r.message for r in caplog.records)


def test_market_order_without_cache_uses_trigger(temp_db):
    engine, acc_id = _make_engine(temp_db, None)  # no cache wired
    res = engine.submit_signal(
        account_id=acc_id, signal=_buy_signal(trigger=1700.0), order_type=OrderType.MARKET,
        quantity_override=100,
    )
    assert res.status == "executed", res.reason
    expected = engine.fee_model.apply_slippage(1700.0, "buy")
    assert abs(res.fill_price - expected) < 1e-6


# ---------------------------------------------------------------------------
# AC-204: position display values off the live price
# ---------------------------------------------------------------------------


def test_apply_live_valuation_overlays_quote():
    rows = [
        {
            "code": "600519", "quantity": 100.0, "avg_cost": 1680.0,
            "last_price": 1680.0, "market_value": 168000.0,
            "floating_pnl": 0.0, "floating_pnl_pct": 0.0,
        }
    ]
    cache = SharedQuoteCache()
    cache.update("600519", _quote(1720.0))
    out = _apply_live_valuation(rows, cache)
    assert out[0]["last_price"] == 1720.0
    assert out[0]["floating_pnl"] == pytest.approx((1720.0 - 1680.0) * 100)
    assert out[0]["floating_pnl_pct"] == pytest.approx((1720.0 - 1680.0) / 1680.0 * 100)


def test_apply_live_valuation_keeps_row_when_no_quote():
    rows = [
        {
            "code": "600519", "quantity": 100.0, "avg_cost": 1680.0,
            "last_price": 1680.0, "market_value": 168000.0,
            "floating_pnl": 0.0, "floating_pnl_pct": 0.0,
        }
    ]
    out = _apply_live_valuation(rows, SharedQuoteCache())  # empty cache
    assert out[0]["last_price"] == 1680.0
    assert out[0]["floating_pnl"] == 0.0
