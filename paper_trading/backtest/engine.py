# -*- coding: utf-8 -*-
"""Bar-by-bar backtest engine for rule strategies (T5).

Reuses the existing ``RuleEngine`` (``paper_trading/strategies/engine/rule_engine.py``)
and ``FeeModel`` (``paper_trading/fees.py``) **without modifying them**.

Look-ahead prevention: every strategy evaluation on bar ``i`` only receives
``df.iloc[:i + 1]`` — the history that was actually available up to that bar.
Slippage, A-share limit up/down and transaction fees are applied in
:meth:`BacktestEngine._simulate_fill` so simulated fills stay realistic.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from paper_trading.fees import FeeModel
from paper_trading.strategies.engine.rule_engine import RuleEngine
from paper_trading.strategies.engine.schema import RuleStrategy

logger = logging.getLogger(__name__)

#: Trading days used to annualize daily Sharpe / returns (A-share convention).
TRADING_DAYS_PER_YEAR = 242
#: A-share daily price limit (10% for normal stocks).
LIMIT_RATIO = 0.10


@dataclass
class BacktestConfig:
    """Backtest configuration — mirrors ``paper_trading/risk.py:RiskConfig``."""

    initial_cash: float = 100_000.0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    benchmark_code: str = "000300"  # CSI 300
    slippage_bps: float = 5.0       # slippage 5bp (0.05%)
    commission_bps: float = 2.5     # commission 2.5bp
    stamp_duty_bps: float = 10.0    # stamp duty 10bp (sell side only)
    min_commission: float = 5.0     # minimum commission (CNY)
    lot_size: int = 100             # shares per lot
    enable_limit_up_down: bool = True
    max_position_pct: float = 0.30  # max single-stock position as fraction of assets


@dataclass
class DailySnapshot:
    """End-of-day account state during the backtest."""

    date: date
    cash: float
    total_assets: float
    positions: Dict[str, float]  # code -> market value
    daily_return: float
    cumulative_return: float
    benchmark_return: float       # same-period benchmark cumulative return


@dataclass
class BacktestResult:
    """Result of a backtest run: snapshots, trades and performance metrics."""

    config: BacktestConfig
    snapshots: List[DailySnapshot]
    trades: List[Dict[str, Any]]  # aligned with TradingEngine.TradeResult.to_dict()
    # --- performance metrics ---
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration: int
    win_rate: float
    profit_loss_ratio: float
    avg_hold_days: float
    calmar_ratio: float
    benchmark_return: float
    excess_return: float


class BacktestEngine:
    """Bar-by-bar historical backtest engine reusing RuleEngine + FeeModel.

    Dependency injection: ``rule_engine`` / ``fee_model`` are optional and can
    be replaced with mocks in tests.
    """

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        rule_engine: Optional[RuleEngine] = None,
        fee_model: Optional[FeeModel] = None,
    ):
        self.config = config if config is not None else BacktestConfig()
        self.rule_engine = rule_engine if rule_engine is not None else RuleEngine()
        self.fee_model = (
            fee_model if fee_model is not None else self._build_fee_model(self.config)
        )
        self._reset_state()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_fee_model(config: BacktestConfig) -> FeeModel:
        """Build a FeeModel from a BacktestConfig (bps -> fractions)."""
        return FeeModel(
            commission_rate=config.commission_bps / 10000.0,
            commission_min=config.min_commission,
            stamp_duty_rate=config.stamp_duty_bps / 10000.0,
            transfer_fee_rate=0.0,
            slippage_bps=config.slippage_bps,
        )

    def _reset_state(self) -> None:
        """Reset all mutable run state (safe to call multiple times)."""
        self._cash = float(self.config.initial_cash)
        self._positions: Dict[str, Dict[str, Any]] = {}  # code -> {qty, avg_cost, entry_date}
        self._snapshots: List[DailySnapshot] = []
        self._trades: List[Dict[str, Any]] = []
        self._realized: List[float] = []
        self._hold_days: List[int] = []
        self._prev_close: Dict[str, float] = {}
        self._last_close: Dict[str, float] = {}
        self._benchmark_cumret: Optional[pd.Series] = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(
        self,
        codes: Sequence[str],
        strategies: Sequence[RuleStrategy],
        daily_data: Dict[str, pd.DataFrame],
        benchmark_df: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """Run a bar-by-bar backtest over ``daily_data``.

        Args:
            codes: Stock codes to backtest.
            strategies: Either a single strategy (applied to every code) or one
                strategy per code (must match ``len(codes)``).
            daily_data: code -> DataFrame indexed by date (ascending) with
                columns open/high/low/close/volume.
            benchmark_df: Optional benchmark DataFrame with a ``close`` column;
                used for same-period benchmark return.

        Returns:
            A :class:`BacktestResult` with per-day snapshots and metrics.
        """
        self._reset_state()
        strategy_map = self._resolve_strategies(codes, strategies)
        if not codes or not daily_data or not strategy_map:
            return self._empty_result()

        code_dfs: Dict[str, pd.DataFrame] = {}
        all_dates = set()
        for code in codes:
            df = daily_data.get(code)
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue
            df = df.sort_index()
            if "close" not in df.columns:
                continue
            code_dfs[code] = df
            all_dates.update(pd.Timestamp(idx) for idx in df.index)
        if not code_dfs or not all_dates:
            return self._empty_result()

        start_ts = pd.Timestamp(self.config.start_date) if self.config.start_date is not None else None
        end_ts = pd.Timestamp(self.config.end_date) if self.config.end_date is not None else None
        calendar = sorted(
            d for d in all_dates
            if (start_ts is None or d >= start_ts) and (end_ts is None or d <= end_ts)
        )
        if not calendar:
            return self._empty_result()

        self._benchmark_cumret = self._prepare_benchmark(benchmark_df, start_ts, end_ts)

        positions_map = {
            code: {pd.Timestamp(idx): i for i, idx in enumerate(df.index)}
            for code, df in code_dfs.items()
        }

        for bar_ts in calendar:
            bar_date = bar_ts.date()
            for code, df in code_dfs.items():
                row = positions_map[code].get(bar_ts)
                if row is None:
                    continue
                bar = df.iloc[row]
                strategy = strategy_map[code]
                signal = self.rule_engine.evaluate(
                    strategy, self._ensure_no_lookahead(df, row), code, name=code
                )
                if signal.side == "buy":
                    fill = self._simulate_fill(
                        code, "buy", float(signal.trigger_price),
                        float(signal.suggested_quantity or 0.0), bar, bar_date,
                        strategy_name=signal.strategy_name,
                    )
                elif signal.side == "sell":
                    qty = float(self._positions.get(code, {}).get("qty", 0.0))
                    fill = self._simulate_fill(
                        code, "sell", float(signal.trigger_price), qty, bar, bar_date,
                        strategy_name=signal.strategy_name,
                    ) if qty > 0 else None
                else:
                    fill = None

                # Record every attempt (executed or rejected) for the audit trail.
                if fill is not None:
                    self._trades.append(fill)
                    if fill["status"] == "executed":
                        self._apply_fill(fill, bar_date)

                # Update close trackers AFTER fill simulation so limit checks on
                # the next bar use today's close (i.e. the "previous" close then).
                self._prev_close[code] = float(bar["close"])
                self._last_close[code] = float(bar["close"])

            self._mark_to_market(bar_date)

        return self._compute_result()

    # ------------------------------------------------------------------
    # Fill simulation
    # ------------------------------------------------------------------

    def _simulate_fill(
        self,
        code: str,
        side: str,
        signal_price: float,
        quantity: float,
        bar: pd.Series,
        bar_date: date,
        strategy_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Simulate one fill: slippage + limit up/down + bar-range + fees.

        Returns a trade dict with ``status`` ``executed`` / ``rejected``, or
        ``None`` when there is nothing to attempt (e.g. selling without a
        position).
        """
        if side not in ("buy", "sell"):
            return None
        if side == "sell" and (quantity is None or quantity <= 0):
            return None

        close = float(bar["close"])
        prev_close = self._prev_close.get(code)
        if self.config.enable_limit_up_down and prev_close is not None and prev_close > 0:
            if side == "buy" and close >= prev_close * (1.0 + LIMIT_RATIO) - 1e-9:
                return self._rejected_trade(code, side, bar_date, strategy_name, "limit_up")
            if side == "sell" and close <= prev_close * (1.0 - LIMIT_RATIO) + 1e-9:
                return self._rejected_trade(code, side, bar_date, strategy_name, "limit_down")

        fill_price = self.fee_model.apply_slippage(float(signal_price), side)
        high = float(bar["high"])
        low = float(bar["low"])
        if fill_price < low - 1e-9 or fill_price > high + 1e-9:
            return self._rejected_trade(code, side, bar_date, strategy_name, "out_of_range")

        if side == "buy":
            qty = self._compute_buy_quantity(code, fill_price)
            if qty <= 0:
                return self._rejected_trade(code, side, bar_date, strategy_name, "insufficient_cash")
            fee = self.fee_model.compute_fee("buy", fill_price, qty)
            return self._trade_dict(
                code, side, bar_date, fill_price, qty, fee, "executed", strategy_name, "filled"
            )

        held = float(self._positions.get(code, {}).get("qty", 0.0))
        qty = min(float(quantity), held)
        if qty <= 0:
            return None
        fee = self.fee_model.compute_fee("sell", fill_price, qty)
        return self._trade_dict(
            code, side, bar_date, fill_price, qty, fee, "executed", strategy_name, "filled"
        )

    def _compute_buy_quantity(self, code: str, price: float) -> int:
        """Size a buy: lot-multiple, capped by cash and max position pct."""
        if price <= 0:
            return 0
        lot = int(self.config.lot_size)
        if lot <= 0:
            lot = 100
        total_assets = self._current_assets()
        max_value = total_assets * self.config.max_position_pct
        held = float(self._positions.get(code, {}).get("qty", 0.0))
        budget = min(self._cash, max(0.0, max_value - held * price))
        qty = int(budget / price / lot) * lot
        while qty > 0:
            if self.fee_model.estimate_buy_cost(price, qty) <= budget + 1e-6:
                return qty
            qty -= lot
        return 0

    def _apply_fill(self, fill: Dict[str, Any], bar_date: date) -> None:
        """Apply an executed fill to cash / positions."""
        code = fill["code"]
        side = fill["side"]
        qty = float(fill["quantity"])
        price = float(fill["price"])
        fee = float(fill["fee"])

        if side == "buy":
            cost = price * qty
            self._cash -= cost + fee
            pos = self._positions.get(code)
            if pos is None:
                self._positions[code] = {
                    "qty": qty,
                    "avg_cost": (cost + fee) / qty,
                    "entry_date": bar_date,
                }
            else:
                total_qty = float(pos["qty"]) + qty
                pos["avg_cost"] = (float(pos["avg_cost"]) * float(pos["qty"]) + cost + fee) / total_qty
                pos["qty"] = total_qty
        else:
            proceeds = price * qty
            self._cash += proceeds - fee
            pos = self._positions.get(code)
            if pos is None:
                return
            realized = proceeds - fee - float(pos["avg_cost"]) * qty
            self._realized.append(realized)
            pos["qty"] = float(pos["qty"]) - qty
            if pos["qty"] <= 1e-9:
                entry = pos.get("entry_date")
                hold_days = (bar_date - entry).days if entry is not None else 0
                self._hold_days.append(hold_days)
                del self._positions[code]

    def _trade_dict(
        self,
        code: str,
        side: str,
        bar_date: date,
        price: float,
        qty: float,
        fee: float,
        status: str,
        strategy_name: Optional[str],
        reason: str,
    ) -> Dict[str, Any]:
        """Trade record aligned with ``TradingEngine.TradeResult.to_dict()``."""
        return {
            "date": bar_date,
            "side": side,
            "code": code,
            "quantity": float(qty),
            "price": float(price),
            "fee": float(fee),
            "status": status,
            "signal_id": None,
            "order_id": None,
            "fill_price": float(price),
            "fill_quantity": float(qty),
            "reason": reason,
            "risk_decisions": [],
            "agent_review": None,
            "strategy_name": strategy_name,
        }

    def _rejected_trade(
        self,
        code: str,
        side: str,
        bar_date: date,
        strategy_name: Optional[str],
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "date": bar_date,
            "side": side,
            "code": code,
            "quantity": 0.0,
            "price": 0.0,
            "fee": 0.0,
            "status": "rejected",
            "signal_id": None,
            "order_id": None,
            "fill_price": None,
            "fill_quantity": None,
            "reason": reason,
            "risk_decisions": [],
            "agent_review": None,
            "strategy_name": strategy_name,
        }

    # ------------------------------------------------------------------
    # Accounting / snapshots
    # ------------------------------------------------------------------

    def _current_assets(self) -> float:
        mv = 0.0
        for code, pos in self._positions.items():
            price = self._last_close.get(code, float(pos.get("avg_cost", 0.0)))
            mv += float(pos["qty"]) * price
        return self._cash + mv

    def _mark_to_market(self, bar_date: date) -> None:
        total_assets = self._current_assets()
        positions_value = {}
        for code, pos in self._positions.items():
            price = self._last_close.get(code, float(pos.get("avg_cost", 0.0)))
            positions_value[code] = round(float(pos["qty"]) * price, 6)

        if self._snapshots:
            prev_assets = self._snapshots[-1].total_assets
            daily_return = total_assets / prev_assets - 1.0 if prev_assets > 0 else 0.0
        else:
            daily_return = 0.0
        cumulative_return = (
            total_assets / self.config.initial_cash - 1.0
            if self.config.initial_cash > 0
            else 0.0
        )
        self._snapshots.append(
            DailySnapshot(
                date=bar_date,
                cash=round(self._cash, 6),
                total_assets=round(total_assets, 6),
                positions=positions_value,
                daily_return=daily_return,
                cumulative_return=cumulative_return,
                benchmark_return=self._benchmark_return_asof(bar_date),
            )
        )

    # ------------------------------------------------------------------
    # Benchmark
    # ------------------------------------------------------------------

    def _prepare_benchmark(
        self,
        benchmark_df: Optional[pd.DataFrame],
        start_ts: Optional[pd.Timestamp],
        end_ts: Optional[pd.Timestamp],
    ) -> Optional[pd.Series]:
        """Return cumulative benchmark return series (same period as backtest)."""
        if (
            benchmark_df is None
            or not isinstance(benchmark_df, pd.DataFrame)
            or benchmark_df.empty
            or "close" not in benchmark_df.columns
        ):
            return None
        bm = benchmark_df[["close"]].dropna().sort_index()
        if bm.empty:
            return None
        bm.index = pd.to_datetime(bm.index)
        if start_ts is not None:
            bm = bm[bm.index >= start_ts]
        if end_ts is not None:
            bm = bm[bm.index <= end_ts]
        if bm.empty:
            return None
        first_close = float(bm["close"].iloc[0])
        if first_close <= 0:
            return None
        return bm["close"].astype(float) / first_close - 1.0

    def _benchmark_return_asof(self, d: date) -> float:
        if self._benchmark_cumret is None:
            return 0.0
        ts = pd.Timestamp(d)
        before = self._benchmark_cumret[self._benchmark_cumret.index <= ts]
        if before.empty:
            return 0.0
        return float(before.iloc[-1])

    # ------------------------------------------------------------------
    # Performance metrics
    # ------------------------------------------------------------------

    def _compute_result(self) -> BacktestResult:
        snapshots = self._snapshots
        if not snapshots:
            return self._empty_result()

        total_return = snapshots[-1].total_assets / self.config.initial_cash - 1.0
        n = len(snapshots)
        annual_return = 0.0
        if total_return > -1.0 and n > 0:
            annual_return = (1.0 + total_return) ** (TRADING_DAYS_PER_YEAR / n) - 1.0

        daily = np.array([s.daily_return for s in snapshots], dtype=float)
        sharpe = 0.0
        if len(daily) >= 2:
            std = float(np.std(daily, ddof=1))
            if std > 0:
                sharpe = float(np.mean(daily)) / std * math.sqrt(TRADING_DAYS_PER_YEAR)

        equity = np.array([s.total_assets for s in snapshots], dtype=float)
        max_dd, max_dd_duration = self._max_drawdown(equity)
        win_rate, profit_loss_ratio = self._win_stats()
        avg_hold_days = float(np.mean(self._hold_days)) if self._hold_days else 0.0
        calmar_ratio = annual_return / max_dd if max_dd > 0 else 0.0
        benchmark_return = snapshots[-1].benchmark_return
        excess_return = total_return - benchmark_return

        return BacktestResult(
            config=self.config,
            snapshots=snapshots,
            trades=self._trades,
            total_return=total_return,
            annual_return=annual_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_duration=max_dd_duration,
            win_rate=win_rate,
            profit_loss_ratio=profit_loss_ratio,
            avg_hold_days=avg_hold_days,
            calmar_ratio=calmar_ratio,
            benchmark_return=benchmark_return,
            excess_return=excess_return,
        )

    @staticmethod
    def _max_drawdown(equity: np.ndarray) -> tuple[float, int]:
        """Return (max_drawdown_fraction, duration_in_bars)."""
        if len(equity) == 0:
            return 0.0, 0
        peak = float(equity[0])
        peak_idx = 0
        max_dd = 0.0
        max_dd_duration = 0
        for i in range(1, len(equity)):
            if equity[i] > peak:
                peak = float(equity[i])
                peak_idx = i
            if peak > 0:
                dd = 1.0 - float(equity[i]) / peak
                if dd > max_dd:
                    max_dd = dd
                    max_dd_duration = i - peak_idx
        return float(max_dd), int(max_dd_duration)

    def _win_stats(self) -> tuple[float, float]:
        wins = [r for r in self._realized if r > 1e-9]
        losses = [r for r in self._realized if r < -1e-9]
        total = len(wins) + len(losses)
        win_rate = len(wins) / total if total > 0 else 0.0
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean([abs(x) for x in losses])) if losses else 0.0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        return win_rate, profit_loss_ratio

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_no_lookahead(df: pd.DataFrame, bar_index: int) -> pd.DataFrame:
        """Critical anti-cheat: truncate to ``bar_index`` so no future data leaks."""
        return df.iloc[: bar_index + 1]

    @staticmethod
    def _resolve_strategies(
        codes: Sequence[str], strategies: Sequence[RuleStrategy]
    ) -> Dict[str, RuleStrategy]:
        """Map each code to the strategy that evaluates it."""
        if isinstance(strategies, RuleStrategy):
            strategies = [strategies]
        strategy_list = list(strategies)
        if not strategy_list:
            return {}
        if len(strategy_list) == 1:
            return {code: strategy_list[0] for code in codes}
        if len(strategy_list) == len(codes):
            return dict(zip(codes, strategy_list))
        raise ValueError(
            "strategies must be a single strategy or one per code "
            f"(got {len(strategy_list)} strategies for {len(codes)} codes)"
        )

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            config=self.config,
            snapshots=[],
            trades=[],
            total_return=0.0,
            annual_return=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_duration=0,
            win_rate=0.0,
            profit_loss_ratio=0.0,
            avg_hold_days=0.0,
            calmar_ratio=0.0,
            benchmark_return=0.0,
            excess_return=0.0,
        )
