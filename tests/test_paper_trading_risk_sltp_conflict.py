# -*- coding: utf-8 -*-
"""T-09 regression tests: risk-forced exits (stop-loss / liquidation) must
NOT be blocked by the daily-loss limit.

Root cause fixed: ``_check_daily_loss_limit`` previously used the position's
cumulative ``avg_cost`` drawdown as "today's estimated loss", so a deep-
underwater position's stop-loss sell was always rejected
(``estimated daily loss 75393 exceeds limit 46322``) and could never be closed.

Fixes verified here:
- ``Signal.risk_mandated`` flag flows SL/TP/liquidation exits through the RMS
  to ``check_sell(skip_daily_loss=True)``.
- ``_check_daily_loss_limit`` counts only same-day-opened positions as today's
  loss; historical drawdown is realized loss, not today's new loss.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from paper_trading.account import PaperAccountManager
from paper_trading.risk import RiskChecker, RiskConfig
from paper_trading.rms_mgmt import RiskManagementSystem
from paper_trading.trading_engine import TradingEngine
from src.storage import Account, DatabaseManager, PaperPosition


def _create_account(db: DatabaseManager, name: str, capital: float = 1000.0) -> int:
    mgr = PaperAccountManager(db_manager=db)
    mgr.get_or_create_account(name=name, initial_capital=capital)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == name, Account.account_type == "paper")
        ).scalar_one()
        return int(acc.id)


def _build_checker(db: DatabaseManager, capital: float = 1000.0) -> RiskChecker:
    return RiskChecker(
        db_manager=db,
        config=RiskConfig(max_daily_loss_pct=0.02),  # 2% of 1000 = 20
    )


def _buy(db: DatabaseManager, account_id: int, code="600000", qty=50, price=10.0) -> None:
    checker = _build_checker(db)
    checker.position_mgr.apply_buy(account_id, code, qty, price, name="浦发银行")
    checker.position_mgr.daily_roll_available(account_id)


def _backdate_position(db: DatabaseManager, account_id: int, code: str, days: int = 1) -> None:
    """Move a position's created_at into the past so it is not "today-opened"."""
    with db.session_scope() as session:
        pos = session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.code == code,
            )
        ).scalar_one()
        pos.created_at = datetime.now() - timedelta(days=days)


def _daily_loss_decision(decisions):
    return next(d for d in decisions if d.check_name == "daily_loss_limit")


# ---------------------------------------------------------------------------
# AC-901: stop-loss / risk-mandated exit is not blocked by the daily-loss limit
# ---------------------------------------------------------------------------

def test_stop_loss_exit_skips_daily_loss_limit(temp_db):
    acc = _create_account(temp_db, "sltp_901", capital=1000.0)
    _buy(temp_db, acc)  # 50 x 10 = 500; selling at 5 realizes 250 (>> 20 limit)

    checker = _build_checker(temp_db)
    decisions = checker.check_sell(acc, "600000", price=5.0, quantity=50.0,
                                   skip_daily_loss=True)

    assert _daily_loss_decision(decisions).passed


# ---------------------------------------------------------------------------
# AC-902: regular (non-risk-mandated) sells still respect the daily-loss limit
# ---------------------------------------------------------------------------

def test_regular_sell_still_blocked_by_daily_loss(temp_db):
    acc = _create_account(temp_db, "sltp_902", capital=1000.0)
    _buy(temp_db, acc)  # today-opened, 50 x 10

    checker = _build_checker(temp_db)
    decisions = checker.check_sell(acc, "600000", price=5.0, quantity=50.0)
    # same-day position: (10 - 5) * 50 = 250 > 20 limit -> blocked
    assert not _daily_loss_decision(decisions).passed


# ---------------------------------------------------------------------------
# Semantic fix: historical drawdown is NOT today's loss
# ---------------------------------------------------------------------------

def test_historical_position_drawdown_not_counted_as_daily_loss(temp_db):
    acc = _create_account(temp_db, "sltp_hist", capital=1000.0)
    _buy(temp_db, acc)
    _backdate_position(temp_db, acc, "600000", days=1)  # opened yesterday

    checker = _build_checker(temp_db)
    decisions = checker.check_sell(acc, "600000", price=5.0, quantity=50.0)
    # not same-day -> estimated_additional_loss = 0 -> passes
    assert _daily_loss_decision(decisions).passed


# ---------------------------------------------------------------------------
# RMS layer: risk_mandated flag is threaded through pre_trade_check
# ---------------------------------------------------------------------------

def test_rms_pretrade_passes_risk_mandated_sell(temp_db):
    acc = _create_account(temp_db, "rms_901", capital=1000.0)
    _buy(temp_db, acc)

    checker = _build_checker(temp_db)
    rms = RiskManagementSystem(risk_checker=checker)
    result = rms.pre_trade_check(acc, "600000", 5.0, 50.0, "sell", risk_mandated=True)
    assert result.passed
    dloss = next(d for d in result.risk_decisions if d["check_name"] == "daily_loss_limit")
    assert dloss["passed"]


def test_rms_pretrade_still_blocks_regular_sell(temp_db):
    acc = _create_account(temp_db, "rms_902", capital=1000.0)
    _buy(temp_db, acc)

    checker = _build_checker(temp_db)
    rms = RiskManagementSystem(risk_checker=checker)
    result = rms.pre_trade_check(acc, "600000", 5.0, 50.0, "sell")  # risk_mandated=False
    assert not result.passed
    assert "daily loss" in result.reason


# ---------------------------------------------------------------------------
# AC-903: end-to-end — SL/TP trigger submits a risk_mandated sell that fills
# ---------------------------------------------------------------------------

def test_engine_sltp_exit_executes_despite_daily_loss(temp_db):
    acc = _create_account(temp_db, "engine_sltp", capital=1000.0)
    engine = TradingEngine(db_manager=temp_db, risk_checker=_build_checker(temp_db))
    engine.position_mgr.apply_buy(acc, "600000", 50, 10.0, name="浦发银行")
    engine.position_mgr.daily_roll_available(acc)
    engine.position_mgr.update_stop_loss_take_profit(acc, "600000", stop_loss=9.0)

    # Price drops to 8.0 < SL 9.0 -> SL triggers a risk-mandated sell.
    results = engine.check_stop_loss_take_profit({"600000": 8.0}, account_id=acc)
    assert len(results) == 1
    r = results[0]
    assert r.status not in ("rejected", "canceled"), r.reason
    assert r.status in ("filled", "executed"), f"unexpected status {r.status}: {r.reason}"
    assert r.side == "sell"
