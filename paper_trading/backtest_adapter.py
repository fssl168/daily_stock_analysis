# -*- coding: utf-8 -*-
"""Backtest-paper trading integration adapter (P3-F).

Closes the loop between strategy backtesting and live paper-trading
performance by:

1. Converting paper-trading account history into a backtest-like scenario.
2. Comparing backtest engine output with actual paper-trading results.
3. Persisting the comparison as a reflection note so the PM agent can learn
   from the delta between simulated and actual performance.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from src.storage import DatabaseManager, PaperNetValue, PaperTrade, get_db

logger = logging.getLogger(__name__)


@dataclass
class PaperTradingScenario:
    """Paper-trading history packaged like a backtest scenario."""

    account_id: int
    strategy_name: str
    base_date: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    initial_capital: float = 1000.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    net_value_curve: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "strategy_name": self.strategy_name,
            "base_date": self.base_date.isoformat() if self.base_date else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "initial_capital": self.initial_capital,
            "total_return_pct": self.total_return_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate": self.win_rate,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "net_value_curve": self.net_value_curve,
            "trades": self.trades,
        }


class PaperTradingToBacktestAdapter:
    """Adapter that maps paper-trading account data to backtest-like scenarios.

    The adapter is read-only: it only inspects persisted PaperNetValue and
    PaperTrade rows.  All write operations (reflection notes) are exposed as
    standalone functions so callers can decide when to persist.
    """

    def __init__(
        self,
        account_id: int,
        db_manager: Optional[DatabaseManager] = None,
        performance_analyzer: Optional[Any] = None,
    ):
        self.account_id = int(account_id)
        self.db = db_manager or get_db()
        self._performance_analyzer = performance_analyzer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_backtest_scenario(
        self,
        strategy_name: str,
        base_date: Optional[date] = None,
    ) -> PaperTradingScenario:
        """Build a backtest-like scenario from paper-trading history."""
        if base_date is None:
            base_date = date.today()

        net_values = self._fetch_net_values()
        trades = self._fetch_trades()

        if not net_values:
            return PaperTradingScenario(
                account_id=self.account_id,
                strategy_name=strategy_name,
                base_date=base_date,
            )

        start_date = net_values[0]["date"]
        end_date = net_values[-1]["date"]
        initial_capital = self._infer_initial_capital(net_values)
        total_return_pct = (net_values[-1]["net_value"] - 1.0) * 100.0
        max_drawdown_pct = self._compute_max_drawdown(net_values)

        win_count, loss_count = self._count_wins_losses(trades)
        trade_count = win_count + loss_count
        win_rate = (win_count / trade_count * 100.0) if trade_count > 0 else 0.0

        return PaperTradingScenario(
            account_id=self.account_id,
            strategy_name=strategy_name,
            base_date=base_date,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            total_return_pct=total_return_pct,
            max_drawdown_pct=max_drawdown_pct,
            win_rate=win_rate,
            trade_count=trade_count,
            win_count=win_count,
            loss_count=loss_count,
            net_value_curve=net_values,
            trades=trades,
        )

    def evaluate_strategy_vs_paper(
        self,
        backtest_summary: Dict[str, Any],
        strategy_name: str,
    ) -> Dict[str, Any]:
        """Compare a backtest summary with the actual paper-trading record.

        ``backtest_summary`` can be either a dict from
        :meth:`BacktestEngine.compute_summary` or the ``summary`` field returned
        by :class:`BacktestService`.
        """
        scenario = self.generate_backtest_scenario(strategy_name)

        bt_win_rate = self._float_or_none(backtest_summary.get("win_rate_pct"))
        bt_direction = self._float_or_none(
            backtest_summary.get("direction_accuracy_pct")
        )
        bt_avg_return = self._float_or_none(backtest_summary.get("avg_stock_return_pct"))
        bt_long_count = backtest_summary.get("long_count") or 0
        bt_completed = backtest_summary.get("completed_count") or 0

        paper_win_rate = scenario.win_rate if scenario.trade_count > 0 else None
        paper_return = scenario.total_return_pct if scenario.net_value_curve else None
        paper_drawdown = (
            scenario.max_drawdown_pct if scenario.net_value_curve else None
        )

        comparison = {
            "account_id": self.account_id,
            "strategy_name": strategy_name,
            "paper_scenario": scenario.to_dict(),
            "backtest_summary": backtest_summary,
            "metrics": {
                "win_rate_pct": {
                    "backtest": bt_win_rate,
                    "paper": paper_win_rate,
                    "delta": (
                        (paper_win_rate - bt_win_rate)
                        if bt_win_rate is not None and paper_win_rate is not None
                        else None
                    ),
                },
                "total_return_pct": {
                    "backtest": bt_avg_return,
                    "paper": paper_return,
                    "delta": (
                        (paper_return - bt_avg_return)
                        if bt_avg_return is not None and paper_return is not None
                        else None
                    ),
                },
                "max_drawdown_pct": {
                    "paper": paper_drawdown,
                },
                "sample_size": {
                    "backtest_completed": bt_completed,
                    "backtest_long_signals": bt_long_count,
                    "paper_trades": scenario.trade_count,
                },
            },
            "generated_at": datetime.now().isoformat(),
        }

        comparison["interpretation"] = self._interpret_comparison(comparison)
        return comparison

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_net_values(self) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = (
                select(PaperNetValue)
                .where(PaperNetValue.account_id == self.account_id)
                .order_by(PaperNetValue.date)
            )
            return [
                {
                    "date": row.date,
                    "net_value": float(row.net_value),
                    "total_assets": float(row.total_assets),
                    "cash": float(row.cash),
                    "market_value": float(row.market_value),
                    "return_pct": float(row.return_pct or 0.0),
                }
                for row in session.execute(stmt).scalars().all()
            ]

    def _fetch_trades(self) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = (
                select(PaperTrade)
                .where(PaperTrade.account_id == self.account_id)
                .order_by(PaperTrade.traded_at)
            )
            return [
                {
                    "code": row.code,
                    "name": row.name,
                    "side": row.side,
                    "price": float(row.price),
                    "quantity": float(row.quantity),
                    "amount": float(row.amount),
                    "fee": float(row.fee or 0.0),
                    "traded_at": row.traded_at.isoformat() if row.traded_at else None,
                }
                for row in session.execute(stmt).scalars().all()
            ]

    def _infer_initial_capital(self, net_values: List[Dict[str, Any]]) -> float:
        if not net_values:
            return 1000.0
        first_total = net_values[0]["total_assets"]
        first_nv = net_values[0]["net_value"]
        if first_nv > 0:
            return round(first_total / first_nv, 4)
        return first_total

    def _compute_max_drawdown(self, net_values: List[Dict[str, Any]]) -> float:
        peak = 1.0
        max_dd = 0.0
        for point in net_values:
            nv = point["net_value"]
            if nv > peak:
                peak = nv
            dd = (peak - nv) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd * 100.0

    def _count_wins_losses(self, trades: List[Dict[str, Any]]) -> tuple[int, int]:
        """Count realized winning/losing sell trades using FIFO matching."""
        from collections import deque

        lots: Dict[str, deque] = {}
        wins = 0
        losses = 0

        for trade in trades:
            code = trade.get("code") or ""
            side = str(trade.get("side") or "").lower()
            price = float(trade.get("price") or 0.0)
            qty = float(trade.get("quantity") or 0.0)
            fee = float(trade.get("fee") or 0.0)
            if qty <= 0 or price <= 0:
                continue

            if side == "buy":
                lots.setdefault(code, deque()).append((price, qty, fee))
            elif side == "sell":
                realized = self._match_sell(lots.get(code, deque()), qty, price, fee)
                if realized is not None:
                    if realized > 1e-9:
                        wins += 1
                    elif realized < -1e-9:
                        losses += 1

        return wins, losses

    @staticmethod
    def _match_sell(
        lots: deque,
        sell_qty: float,
        sell_price: float,
        sell_fee: float,
    ) -> Optional[float]:
        if not lots:
            return None
        qty_left = sell_qty
        total_cost = 0.0
        total_qty = 0.0
        while qty_left > 1e-9 and lots:
            buy_price, buy_qty, buy_fee = lots[0]
            use = min(buy_qty, qty_left)
            total_cost += use * buy_price + buy_fee * (use / buy_qty if buy_qty else 0)
            total_qty += use
            qty_left -= use
            if use >= buy_qty - 1e-9:
                lots.popleft()
            else:
                lots[0] = (buy_price, buy_qty - use, buy_fee)
        if total_qty <= 0:
            return None
        revenue = total_qty * sell_price - sell_fee
        return revenue - total_cost

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _interpret_comparison(comparison: Dict[str, Any]) -> str:
        metrics = comparison.get("metrics", {})
        win_delta = metrics.get("win_rate_pct", {}).get("delta")
        return_delta = metrics.get("total_return_pct", {}).get("delta")

        parts = []
        if win_delta is not None:
            parts.append(
                f"paper win-rate vs backtest delta: {win_delta:+.1f} pct points"
            )
        if return_delta is not None:
            parts.append(
                f"paper return vs backtest avg return delta: {return_delta:+.2f}%"
            )
        if not parts:
            return "insufficient data for comparison"
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------

def update_paper_trading_from_backtest(
    backtest_summary: Dict[str, Any],
    strategy_name: str,
    account_id: int,
    db_manager: Optional[DatabaseManager] = None,
    reflection_engine: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Persist a backtest-vs-paper comparison as a reflection note.

    Creates a ``PaperReflection`` row scoped ``backtest`` so the PM agent and
    reflection system can learn from the delta between simulated and actual
    performance.  The note is persisted directly via the ORM to avoid depending
    on optional reflection-engine internals.
    """
    try:
        from paper_trading.reflection import ReflectionNote
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[backtest_adapter] reflection import failed: %s", exc)
        return None

    db = db_manager or get_db()
    try:
        adapter = PaperTradingToBacktestAdapter(account_id, db_manager=db)
        comparison = adapter.evaluate_strategy_vs_paper(backtest_summary, strategy_name)
        paper = comparison["paper_scenario"]
        interp = comparison["interpretation"]

        note = ReflectionNote(
            scope="backtest",
            account_id=account_id,
            subject=f"策略回测评估: {strategy_name}",
            summary=(
                f"回测胜率 {comparison['metrics']['win_rate_pct']['backtest']:.1f}% "
                f"vs 纸面胜率 {paper['win_rate']:.1f}%; "
                f"纸面总收益 {paper['total_return_pct']:.2f}%, "
                f"最大回撤 {paper['max_drawdown_pct']:.2f}%. "
                f"{interp}"
            ),
            takeaway=(
                "当回测与纸面交易出现显著偏差时，优先检查滑点、手续费、"
                "信号执行延迟和仓位管理差异。"
            ),
            lessons=[
                f"差异解释: {interp}",
                f"纸面交易次数: {paper['trade_count']} (胜 {paper['win_count']} / 负 {paper['loss_count']})",
                f"回测完成样本: {comparison['metrics']['sample_size']['backtest_completed']}",
            ],
            tags=["backtest", "performance-analysis", strategy_name],
            mood="neutral",
        )

        row_id = _persist_reflection_note(note, db)
        note.row_id = row_id

        logger.info(
            "[backtest_adapter] persisted backtest reflection for account=%s strategy=%s row_id=%s",
            account_id,
            strategy_name,
            note.row_id,
        )
        return {"note": note.to_dict(), "comparison": comparison}
    except Exception as exc:
        logger.warning(
            "[backtest_adapter] failed to update paper trading from backtest: %s", exc
        )
        return None


def _persist_reflection_note(note: Any, db: DatabaseManager) -> Optional[int]:
    """Persist a ReflectionNote-like object directly to ``PaperReflection``."""
    from src.storage import PaperReflection

    with db.session_scope() as session:
        row = PaperReflection(
            account_id=int(note.account_id or 0),
            scope=str(note.scope or "adhoc"),
            subject=str(note.subject or "")[:255],
            summary=str(note.summary or "")[:5000],
            takeaway=str(note.takeaway or "")[:5000],
            lessons_json=json.dumps(note.lessons or [], ensure_ascii=False),
            tags=",".join(note.tags or []),
            mood=str(note.mood or "neutral")[:16],
            trade_id=note.trade_id,
            order_id=note.order_id,
            signal_id=note.signal_id,
            raw_response=note.raw_response,
        )
        session.add(row)
        session.flush()
        return int(row.id)


def run_with_paper_validation(
    backtest_summary: Dict[str, Any],
    strategy_name: str,
    account_id: int,
    db_manager: Optional[DatabaseManager] = None,
    reflection_engine: Optional[Any] = None,
    persist_reflection: bool = True,
) -> Dict[str, Any]:
    """Run backtest-paper comparison and optionally persist a reflection note.

    This is the high-level entry point for the P3-F loop.  It compares the
    backtest engine output with the paper-trading account history and, by
    default, writes the result into the reflection system.
    """
    adapter = PaperTradingToBacktestAdapter(account_id, db_manager=db_manager)
    comparison = adapter.evaluate_strategy_vs_paper(backtest_summary, strategy_name)

    result = {
        "account_id": account_id,
        "strategy_name": strategy_name,
        "paper_scenario": comparison["paper_scenario"],
        "backtest_summary": comparison["backtest_summary"],
        "metrics": comparison["metrics"],
        "interpretation": comparison["interpretation"],
        "generated_at": comparison["generated_at"],
        "reflection_persisted": False,
    }

    if persist_reflection:
        reflection_result = update_paper_trading_from_backtest(
            backtest_summary=backtest_summary,
            strategy_name=strategy_name,
            account_id=account_id,
            db_manager=db_manager,
            reflection_engine=reflection_engine,
        )
        result["reflection_persisted"] = reflection_result is not None
        result["reflection"] = reflection_result

    return result


# ---------------------------------------------------------------------------
# T-017: Passthrough backtest from paper account
# ---------------------------------------------------------------------------


def backtest_from_paper_account(
    account_id: int,
    strategies: List[Any],
    start_date: date,
    end_date: date,
    db_manager: Optional[DatabaseManager] = None,
) -> Any:
    """Run a full backtest for a paper-trading account (passthrough entry point).

    Pulls the account's watchlist, fetches historical daily data via
    ``DataFetcherManager``, and delegates to ``BacktestEngine.run()``.

    This is the UI-facing counterpart of ``PaperTradingToBacktestAdapter``
    — it answers "what would this account's strategies have returned in
    the past?" without requiring the caller to manually assemble
    configurations and data.

    Returns a ``BacktestResult`` or None if data is insufficient.
    """
    from data_provider.base import DataFetcherManager
    from paper_trading.account import PaperAccountManager
    from paper_trading.backtest.engine import BacktestConfig, BacktestEngine

    fetcher = DataFetcherManager()
    account_mgr = PaperAccountManager(db_manager)
    account = account_mgr.snapshot(account_id)

    # Resolve watched codes from config (or all positions as fallback).
    codes: List[str] = []
    config_json = getattr(account, "config_json", None)
    if config_json:
        import json
        cfg = json.loads(config_json) if isinstance(config_json, str) else config_json
        codes = cfg.get("watched_codes", [])
    if not codes:
        from paper_trading.position import PositionManager
        pm = PositionManager(db_manager)
        positions = pm.list_positions(account_id)
        codes = [p.get("code", "") for p in positions if p.get("code")]

    if not codes:
        logger.warning("backtest_from_paper_account: no codes found for account %s", account_id)
        return None

    # Pull daily data.
    daily_data: Dict[str, Any] = {}
    for code in codes:
        df, _ = fetcher.get_daily_data(
            code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            days=9999,
        )
        if df is not None and not df.empty:
            daily_data[code] = df

    if not daily_data:
        logger.warning("backtest_from_paper_account: no daily data for account %s", account_id)
        return None

    engine = BacktestEngine(BacktestConfig(
        initial_cash=float(getattr(account, "initial_capital", account.cash)),
        start_date=start_date,
        end_date=end_date,
    ))
    return engine.run(codes=list(daily_data.keys()), strategies=strategies, daily_data=daily_data)
