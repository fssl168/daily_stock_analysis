# -*- coding: utf-8 -*-
"""pytest tests for Phase 2 performance analytics and risk metrics."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.performance import PerformanceAnalyzer
from paper_trading.risk import RiskChecker, RiskConfig
from src.storage import DatabaseManager, PaperAccount, PaperNetValue, PaperTrade


def _create_account(db: DatabaseManager, name: str, capital: float = 1000.0) -> int:
    mgr = PaperAccountManager(db_manager=db)
    mgr.get_or_create_account(name=name, initial_capital=capital)
    with db.session_scope() as session:
        acc = session.execute(
            select(PaperAccount).where(PaperAccount.name == name)
        ).scalar_one()
        return int(acc.id)


def _insert_net_values(
    db: DatabaseManager,
    account_id: int,
    values: list[float],
    start_date: date | None = None,
) -> None:
    start = start_date or date(2026, 1, 5)
    with db.session_scope() as session:
        for i, nv in enumerate(values):
            d = start + timedelta(days=i)
            session.add(
                PaperNetValue(
                    account_id=account_id,
                    date=d,
                    total_assets=nv * 1000.0,
                    cash=0.0,
                    market_value=nv * 1000.0,
                    net_value=nv,
                    return_pct=(nv - 1.0) * 100.0,
                    daily_return_pct=0.0,
                    created_at=datetime.combine(d, datetime.min.time()),
                )
            )


def _insert_trade(
    db: DatabaseManager,
    account_id: int,
    code: str,
    side: str,
    price: float,
    quantity: float,
    fee: float = 0.0,
    traded_at: datetime | None = None,
) -> None:
    if traded_at is None:
        traded_at = datetime.now()
    with db.session_scope() as session:
        session.add(
            PaperTrade(
                account_id=account_id,
                order_id=0,  # tests don't need a real order linkage
                code=code,
                side=side,
                price=price,
                quantity=quantity,
                amount=price * quantity,
                fee=fee,
                traded_at=traded_at,
            )
        )


@pytest.fixture
def analyzer_account(temp_db):
    acc_id = _create_account(temp_db, "perf_analyzer", capital=1000.0)
    return PerformanceAnalyzer(db_manager=temp_db), acc_id


class TestPerformanceMetrics:
    """Performance metrics computed from known net-value sequences."""

    def test_total_return_and_drawdown(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        # 1000 -> 1100 -> 1050 -> 1200
        _insert_net_values(analyzer.db, acc_id, [1.0, 1.1, 1.05, 1.2])

        metrics = analyzer.calculate(acc_id)
        assert metrics.account_id == acc_id
        assert metrics.total_return_pct == pytest.approx(20.0, abs=1e-6)
        assert metrics.max_drawdown_pct == pytest.approx(
            (1.1 - 1.05) / 1.1 * 100.0, abs=1e-6
        )

    def test_annualized_return(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        # Flat 20% over exactly 365 days -> ~20% annualized
        start = date(2025, 1, 1)
        _insert_net_values(
            analyzer.db, acc_id, [1.0, 1.2],
            start_date=start,
        )
        # Overwrite the second point to be exactly one year later so the
        # annualization factor becomes 365/365 = 1.
        from datetime import timedelta
        with analyzer.db.session_scope() as session:
            row = session.execute(
                select(PaperNetValue).where(
                    PaperNetValue.account_id == acc_id,
                    PaperNetValue.net_value == 1.2,
                )
            ).scalar_one()
            row.date = start + timedelta(days=365)

        metrics = analyzer.calculate(acc_id)
        assert metrics.annualized_return_pct == pytest.approx(20.0, abs=1e-6)

    def test_volatility_and_sharpe(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        # 10 days of small daily gains
        values = [1.0 * (1.001**i) for i in range(10)]
        _insert_net_values(analyzer.db, acc_id, values)

        metrics = analyzer.calculate(acc_id)
        assert metrics.volatility_annualized is not None
        assert metrics.volatility_annualized > 0
        assert metrics.sharpe_ratio is not None

    def test_empty_net_values_returns_defaults(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        metrics = analyzer.calculate(acc_id)
        assert metrics.account_id == acc_id
        assert metrics.total_return_pct == 0.0
        assert metrics.trade_count == 0


class TestTradeStats:
    """Win/loss statistics using FIFO lot matching."""

    def test_three_wins_two_losses(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        _insert_net_values(analyzer.db, acc_id, [1.0, 1.01])

        # Buy 10 @ 10, sell 10 @ 13 -> win 30
        _insert_trade(analyzer.db, acc_id, "A", "buy", 10.0, 10.0, fee=0.0)
        _insert_trade(analyzer.db, acc_id, "A", "sell", 13.0, 10.0, fee=0.0)

        # Buy 10 @ 20, sell 10 @ 25 -> win 50
        _insert_trade(analyzer.db, acc_id, "B", "buy", 20.0, 10.0, fee=0.0)
        _insert_trade(analyzer.db, acc_id, "B", "sell", 25.0, 10.0, fee=0.0)

        # Buy 10 @ 30, sell 10 @ 28 -> loss 20
        _insert_trade(analyzer.db, acc_id, "C", "buy", 30.0, 10.0, fee=0.0)
        _insert_trade(analyzer.db, acc_id, "C", "sell", 28.0, 10.0, fee=0.0)

        # Buy 10 @ 40, sell 10 @ 45 -> win 50
        _insert_trade(analyzer.db, acc_id, "D", "buy", 40.0, 10.0, fee=0.0)
        _insert_trade(analyzer.db, acc_id, "D", "sell", 45.0, 10.0, fee=0.0)

        # Buy 10 @ 50, sell 10 @ 48 -> loss 20
        _insert_trade(analyzer.db, acc_id, "E", "buy", 50.0, 10.0, fee=0.0)
        _insert_trade(analyzer.db, acc_id, "E", "sell", 48.0, 10.0, fee=0.0)

        metrics = analyzer.calculate(acc_id)
        assert metrics.win_count == 3
        assert metrics.loss_count == 2
        assert metrics.win_rate == pytest.approx(60.0, abs=1e-6)
        assert metrics.profit_factor == pytest.approx(130.0 / 40.0, abs=1e-6)
        assert metrics.avg_win == pytest.approx(130.0 / 3, abs=1e-6)
        assert metrics.avg_loss == pytest.approx(40.0 / 2, abs=1e-6)

    def test_fifo_partial_lots(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        _insert_net_values(analyzer.db, acc_id, [1.0, 1.0])

        _insert_trade(analyzer.db, acc_id, "A", "buy", 10.0, 10.0, fee=0.0)
        _insert_trade(analyzer.db, acc_id, "A", "buy", 12.0, 10.0, fee=0.0)
        # Sell 15: 10 @ 10 + 5 @ 12, cost = 100 + 60 = 160
        # Revenue = 15 * 15 = 225, realized = 65
        _insert_trade(analyzer.db, acc_id, "A", "sell", 15.0, 15.0, fee=0.0)

        metrics = analyzer.calculate(acc_id)
        assert metrics.win_count == 1
        assert metrics.avg_win == pytest.approx(65.0, abs=1e-6)


class TestDrawdownCurve:
    """Drawdown curve records."""

    def test_drawdown_curve_length_and_values(self, analyzer_account):
        analyzer, acc_id = analyzer_account
        _insert_net_values(analyzer.db, acc_id, [1.0, 1.1, 1.05, 1.2])

        curve = analyzer.get_drawdown_curve(acc_id)
        assert len(curve) == 4
        assert curve[0].drawdown_pct == pytest.approx(0.0, abs=1e-6)
        assert curve[2].drawdown_pct == pytest.approx(
            (1.1 - 1.05) / 1.1 * 100.0, abs=1e-6
        )
        assert curve[-1].drawdown_pct == pytest.approx(0.0, abs=1e-6)


class TestRiskMetrics:
    """RiskChecker snapshot and daily loss limit."""

    def test_risk_snapshot_no_positions(self, temp_db):
        acc_id = _create_account(temp_db, "risk_empty", capital=1000.0)
        checker = RiskChecker(db_manager=temp_db)
        snapshot = checker.get_risk_snapshot(acc_id)
        assert snapshot["account_id"] == acc_id
        assert snapshot["current_open_positions"] == 0
        assert snapshot["max_single_stock_concentration_pct"] == 0.0

    def test_risk_snapshot_with_position(self, temp_db):
        acc_id = _create_account(temp_db, "risk_with_pos", capital=1000.0)
        from paper_trading.position import PositionManager

        pos_mgr = PositionManager(temp_db)
        pos_mgr.apply_buy(acc_id, "600000", 50.0, 10.0, name="浦发银行")
        pos_mgr.update_last_price(acc_id, "600000", 12.0)

        checker = RiskChecker(db_manager=temp_db)
        snapshot = checker.get_risk_snapshot(acc_id)
        # PositionManager.apply_buy does not debit cash in this subsystem;
        # cash remains 1000, market value = 50 * 12 = 600, total assets = 1600.
        assert snapshot["max_single_stock_concentration_pct"] == pytest.approx(
            600.0 / 1600.0 * 100.0, abs=1e-6
        )
        assert snapshot["current_open_positions"] == 1

    def test_daily_loss_limit_blocks_large_sell(self, temp_db):
        acc_id = _create_account(temp_db, "risk_daily_loss", capital=1000.0)
        from paper_trading.position import PositionManager

        pos_mgr = PositionManager(temp_db)
        pos_mgr.apply_buy(acc_id, "600000", 50.0, 10.0, name="浦发银行")
        pos_mgr.daily_roll_available(acc_id)

        checker = RiskChecker(
            db_manager=temp_db,
            position_manager=pos_mgr,
            config=RiskConfig(max_daily_loss_pct=0.02),
        )
        # Selling at 5.0 would realize ~250 loss, exceeding 2% of 1000 = 20.
        decisions = checker.check_sell(acc_id, "600000", price=5.0, quantity=50.0)
        daily_loss = next(
            (d for d in decisions if d.check_name == "daily_loss_limit"), None
        )
        assert daily_loss is not None
        assert not daily_loss.passed

    def test_sector_concentration_placeholder_passes(self, temp_db):
        acc_id = _create_account(temp_db, "risk_sector", capital=1000.0)
        checker = RiskChecker(db_manager=temp_db)
        decisions = checker.check_buy(acc_id, "600000", price=10.0, quantity=1.0)
        sector = next(
            (d for d in decisions if d.check_name == "sector_concentration"), None
        )
        assert sector is not None
        assert sector.passed
        assert "unavailable" in sector.reason
