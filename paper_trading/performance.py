# -*- coding: utf-8 -*-
"""Performance analytics for paper-trading accounts.

Computes account-level risk/return metrics from ``PaperNetValue`` and
``PaperTrade`` rows. All calculations are deterministic and side-effect free;
they only read persisted state.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from src.storage import DatabaseManager, PaperNetValue, PaperTrade, get_db

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


@dataclass
class DrawdownRecord:
    """A single point on the drawdown curve."""

    date: date
    net_value: float
    peak_net_value: float
    drawdown_pct: float


@dataclass
class PerformanceMetrics:
    """Account performance summary."""

    account_id: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    max_drawdown_start_date: Optional[date] = None
    max_drawdown_end_date: Optional[date] = None
    volatility_annualized: Optional[float] = None
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
    calmar_ratio: Optional[float] = None
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "total_return_pct": self.total_return_pct,
            "annualized_return_pct": self.annualized_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_drawdown_start_date": (
                self.max_drawdown_start_date.isoformat()
                if self.max_drawdown_start_date
                else None
            ),
            "max_drawdown_end_date": (
                self.max_drawdown_end_date.isoformat()
                if self.max_drawdown_end_date
                else None
            ),
            "volatility_annualized": self.volatility_annualized,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "calmar_ratio": self.calmar_ratio,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
        }


@dataclass
class PerformanceConfig:
    """Tunable assumptions for performance calculations."""

    risk_free_rate_annual: float = 0.03  # 3% annual risk-free rate
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR


class PerformanceAnalyzer:
    """Read-only performance analyzer backed by the ORM."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        config: Optional[PerformanceConfig] = None,
    ):
        self.db = db_manager or get_db()
        self.config = config or PerformanceConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        account_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> PerformanceMetrics:
        """Compute performance metrics for an account over a date range."""
        net_values = self._fetch_net_values(account_id, start_date, end_date)
        trades = self._fetch_trades(account_id, start_date, end_date)

        if not net_values:
            return PerformanceMetrics(account_id=account_id)

        nv_dates = [row[0] for row in net_values]
        nv_values = [row[1] for row in net_values]

        start_date = nv_dates[0]
        end_date = nv_dates[-1]

        total_return_pct = (nv_values[-1] - 1.0) * 100.0
        days = max(1, (end_date - start_date).days)
        annualized_return_pct = (
            (nv_values[-1] / max(nv_values[0], 1e-12)) ** (365.0 / days) - 1.0
        ) * 100.0

        drawdown_curve = self._compute_drawdown_curve(nv_dates, nv_values)
        max_dd, max_dd_start, max_dd_end = self._max_drawdown_from_curve(
            drawdown_curve
        )

        daily_returns = self._daily_returns_from_net_values(nv_values)
        volatility = self._annualized_volatility(daily_returns)
        sharpe = self._sharpe_ratio(annualized_return_pct, volatility)
        calmar = self._calmar_ratio(annualized_return_pct, max_dd)

        trade_stats = self._compute_trade_stats(trades)

        return PerformanceMetrics(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            total_return_pct=total_return_pct,
            annualized_return_pct=annualized_return_pct,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            max_drawdown_start_date=max_dd_start,
            max_drawdown_end_date=max_dd_end,
            volatility_annualized=volatility,
            win_rate=trade_stats.win_rate,
            profit_factor=trade_stats.profit_factor,
            avg_win=trade_stats.avg_win,
            avg_loss=trade_stats.avg_loss,
            calmar_ratio=calmar,
            trade_count=trade_stats.trade_count,
            win_count=trade_stats.win_count,
            loss_count=trade_stats.loss_count,
        )

    def get_drawdown_curve(
        self,
        account_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[DrawdownRecord]:
        """Return the drawdown curve as a list of records."""
        net_values = self._fetch_net_values(account_id, start_date, end_date)
        if not net_values:
            return []
        dates = [row[0] for row in net_values]
        values = [row[1] for row in net_values]
        return self._compute_drawdown_curve(dates, values)

    def get_current_drawdown(self, account_id: int) -> float:
        """Return the current drawdown percentage (>= 0)."""
        net_values = self._fetch_net_values(account_id)
        if not net_values:
            return 0.0
        values = [row[1] for row in net_values]
        peak = values[0]
        current_dd = 0.0
        for value in values:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0.0
            if dd > current_dd:
                current_dd = dd
        return current_dd * 100.0

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_net_values(
        self,
        account_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Tuple[date, float]]:
        """Return (date, net_value) pairs; avoids detached ORM instances."""
        with self.db.session_scope() as session:
            stmt = (
                select(PaperNetValue.date, PaperNetValue.net_value)
                .where(PaperNetValue.account_id == account_id)
                .order_by(PaperNetValue.date)
            )
            if start_date is not None:
                stmt = stmt.where(PaperNetValue.date >= start_date)
            if end_date is not None:
                stmt = stmt.where(PaperNetValue.date <= end_date)
            return [
                (row.date, float(row.net_value))
                for row in session.execute(stmt).all()
            ]

    def _fetch_trades(
        self,
        account_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Return trade dicts; avoids detached ORM instances."""
        with self.db.session_scope() as session:
            stmt = (
                select(PaperTrade)
                .where(PaperTrade.account_id == account_id)
                .order_by(PaperTrade.traded_at)
            )
            if start_date is not None:
                stmt = stmt.where(
                    PaperTrade.traded_at
                    >= datetime.combine(start_date, datetime.min.time())
                )
            if end_date is not None:
                stmt = stmt.where(
                    PaperTrade.traded_at
                    <= datetime.combine(end_date, datetime.max.time())
                )
            return [
                {
                    "code": t.code,
                    "side": t.side,
                    "price": float(t.price or 0.0),
                    "quantity": float(t.quantity or 0.0),
                    "fee": float(t.fee or 0.0),
                }
                for t in session.execute(stmt).scalars().all()
            ]

    # ------------------------------------------------------------------
    # Calculations
    # ------------------------------------------------------------------

    def _compute_drawdown_curve(
        self, dates: List[date], values: List[float]
    ) -> List[DrawdownRecord]:
        records: List[DrawdownRecord] = []
        peak = values[0] if values else 1.0
        for d, value in zip(dates, values):
            if value > peak:
                peak = value
            dd_pct = ((peak - value) / peak) * 100.0 if peak > 0 else 0.0
            records.append(
                DrawdownRecord(
                    date=d,
                    net_value=value,
                    peak_net_value=peak,
                    drawdown_pct=dd_pct,
                )
            )
        return records

    def _max_drawdown_from_curve(
        self, curve: List[DrawdownRecord]
    ) -> Tuple[float, Optional[date], Optional[date]]:
        if not curve:
            return 0.0, None, None
        max_record = max(curve, key=lambda r: r.drawdown_pct)
        return max_record.drawdown_pct, max_record.date, max_record.date

    def _daily_returns_from_net_values(self, values: List[float]) -> List[float]:
        if len(values) < 2:
            return []
        return [
            (values[i] / values[i - 1]) - 1.0
            for i in range(1, len(values))
            if values[i - 1] > 0
        ]

    def _annualized_volatility(self, daily_returns: List[float]) -> Optional[float]:
        if len(daily_returns) < 2:
            return None
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean) ** 2 for r in daily_returns) / (
            len(daily_returns) - 1
        )
        std = math.sqrt(variance)
        return std * math.sqrt(self.config.trading_days_per_year) * 100.0

    def _sharpe_ratio(
        self, annualized_return_pct: float, volatility: Optional[float]
    ) -> Optional[float]:
        if volatility is None or volatility <= 0:
            return None
        return (
            annualized_return_pct - self.config.risk_free_rate_annual * 100.0
        ) / volatility

    def _calmar_ratio(
        self, annualized_return_pct: float, max_drawdown_pct: float
    ) -> Optional[float]:
        if max_drawdown_pct <= 0:
            return None
        return annualized_return_pct / max_drawdown_pct

    # ------------------------------------------------------------------
    # Trade stats (FIFO realized PnL)
    # ------------------------------------------------------------------

    def _compute_trade_stats(self, trades: List[Dict[str, Any]]) -> "_TradeStats":
        """Compute win/loss statistics using FIFO lot matching."""
        wins: List[float] = []
        losses: List[float] = []

        # Per-code FIFO buy lots: deque of (price, quantity)
        lots: dict[str, deque] = {}

        for trade in trades:
            code = trade.get("code") or ""
            side = (trade.get("side") or "").lower()
            price = float(trade.get("price") or 0.0)
            qty = float(trade.get("quantity") or 0.0)
            fee = float(trade.get("fee") or 0.0)
            if qty <= 0 or price <= 0:
                continue

            if side == "buy":
                lots.setdefault(code, deque()).append((price, qty, fee))
            elif side == "sell":
                realized = self._match_sell_against_lots(
                    lots.get(code, deque()), qty, price, fee
                )
                if realized is not None:
                    if realized > 1e-9:
                        wins.append(realized)
                    elif realized < -1e-9:
                        losses.append(abs(realized))
                    # trades with zero realized PnL are ignored for win/loss stats

        total_wins = sum(wins)
        total_losses = sum(losses)
        win_count = len(wins)
        loss_count = len(losses)
        trade_count = win_count + loss_count

        return _TradeStats(
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=(win_count / trade_count * 100.0) if trade_count > 0 else 0.0,
            profit_factor=(total_wins / total_losses) if total_losses > 1e-9 else None,
            avg_win=(total_wins / win_count) if win_count > 0 else 0.0,
            avg_loss=(total_losses / loss_count) if loss_count > 0 else 0.0,
        )

    def _match_sell_against_lots(
        self,
        lots: deque,
        sell_qty: float,
        sell_price: float,
        sell_fee: float,
    ) -> Optional[float]:
        """Match a sell against FIFO buy lots and return realized PnL."""
        if not lots:
            return None

        remaining = sell_qty
        cost_basis = 0.0
        total_buy_fee = 0.0

        while remaining > 1e-9 and lots:
            buy_price, buy_qty, buy_fee = lots[0]
            use = min(buy_qty, remaining)
            cost_basis += buy_price * use
            # Attribute a proportional share of the buy fee.
            total_buy_fee += buy_fee * (use / buy_qty) if buy_qty > 0 else 0.0
            remaining -= use
            if use >= buy_qty - 1e-9:
                lots.popleft()
            else:
                lots[0] = (buy_price, buy_qty - use, buy_fee)

        if sell_qty <= 0:
            return None

        revenue = sell_price * sell_qty
        proportional_sell_fee = sell_fee  # already the fee for this trade
        realized = revenue - cost_basis - total_buy_fee - proportional_sell_fee
        return realized


@dataclass
class _TradeStats:
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
