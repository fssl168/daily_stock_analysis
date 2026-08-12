# -*- coding: utf-8 -*-
"""Paper account update_account tests.

Editing the initial capital must sync live cash by the same delta so cash and
net value change immediately (positions are preserved; only cash moves).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel
from paper_trading.order import OrderManager, OrderType
from paper_trading.position import PositionManager
from paper_trading.risk import RiskChecker, RiskConfig
from paper_trading.strategies import Signal
from paper_trading.trading_engine import TradingEngine


def _make_engine(db):
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="upd_test", initial_capital=1000.0)
    from sqlalchemy import select
    from src.storage import Account

    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == "upd_test", Account.account_type == "paper")
        ).scalar_one()
        acc_id = acc.id
    fee = FeeModel()
    pos_mgr = PositionManager(db)
    om = OrderManager(db)
    risk = RiskChecker(db_manager=db, account_manager=account_mgr,
                       position_manager=pos_mgr, fee_model=fee,
                       config=RiskConfig(max_daily_loss_pct=0.05))
    engine = TradingEngine(db_manager=db, account_manager=account_mgr,
                           order_manager=om, position_manager=pos_mgr,
                           fee_model=fee, risk_checker=risk,
                           enable_auto_sltp=False, quote_cache=None)
    return engine, acc_id, account_mgr


def test_update_initial_capital_syncs_cash(temp_db):
    mgr = PaperAccountManager(db_manager=temp_db)
    acc = mgr.get_or_create_account(name="t1", initial_capital=1000.0)
    mgr.update_account(acc.id, initial_capital=2000.0)
    snap = mgr.snapshot(acc.id)
    assert snap.initial_capital == pytest.approx(2000.0)
    assert snap.cash == pytest.approx(2000.0)
    assert snap.total_assets == pytest.approx(2000.0)


def test_update_initial_capital_down_moves_cash_and_net_value(temp_db):
    mgr = PaperAccountManager(db_manager=temp_db)
    acc = mgr.get_or_create_account(name="t2", initial_capital=5000.0)
    mgr.update_account(acc.id, initial_capital=3000.0)
    snap = mgr.snapshot(acc.id)
    assert snap.initial_capital == pytest.approx(3000.0)
    assert snap.cash == pytest.approx(3000.0)
    assert snap.total_assets == pytest.approx(3000.0)


def test_update_initial_capital_preserves_positions_and_moves_cash(temp_db):
    engine, acc_id, mgr = _make_engine(temp_db)
    sig = Signal(side="buy", code="600519", name="贵州茅台", strategy_name="test",
                 rule_name="t", trigger_price=10.0, suggested_quantity=10, reason="t")
    res = engine.submit_signal(account_id=acc_id, signal=sig,
                               order_type=OrderType.MARKET, limit_price=10.0,
                               quantity_override=10)
    assert res.status == "executed", res.reason
    snap_before = mgr.snapshot(acc_id)
    cash_before = snap_before.cash
    mv_before = snap_before.market_value

    # bump initial capital by +1000
    mgr.update_account(acc_id, initial_capital=2000.0)
    snap_after = mgr.snapshot(acc_id)
    assert snap_after.initial_capital == pytest.approx(2000.0)
    # cash moved by the delta
    assert snap_after.cash == pytest.approx(cash_before + 1000.0)
    # positions (market value) unchanged
    assert snap_after.market_value == pytest.approx(mv_before)
    # net value changed accordingly
    assert snap_after.total_assets == pytest.approx(snap_after.cash + snap_after.market_value)
