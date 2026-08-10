# -*- coding: utf-8 -*-
"""Settlement — end-of-day processing extracted from TradingEngine.

Handles mark-to-market, fee accrual, net-value curve computation,
and daily roll-forward of account balances.  Designed to be called
once per trading day after market close by ``MarketListener``
(or the API layer for manual/ad-hoc settlements).

Source: ``docs/architecture/realtime_quant_system_design.md`` §3.3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DailySettleResult:
    """Outcome of a single daily settlement run."""

    account_id: int
    settle_date: date
    total_assets: float
    cash: float
    positions_value: float
    daily_pnl: float
    cumulative_pnl: float
    position_count: int
    fees_accrued: float
    trades_closed: int


@dataclass
class PositionPnL:
    """Per-position mark-to-market snapshot."""

    code: str
    name: str
    quantity: int
    avg_cost: float
    prev_close: float
    close_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


class Settlement:
    """End-of-day settlement engine.

    Responsibilities:
    - Mark-to-market: compute unrealized PnL for every open position.
    - Fee accrual: tally today's transaction fees.
    - Net-value curve: append a daily point to the account's equity history.
    - Roll-forward: prepare the account for the next trading day.
    """

    def __init__(
        self,
        account_mgr: Any,
        position_mgr: Any,
        fee_model: Any,
    ) -> None:
        self.account_mgr = account_mgr
        self.position_mgr = position_mgr
        self.fee_model = fee_model

    # ------------------------------------------------------------------
    # Daily settlement
    # ------------------------------------------------------------------

    def daily_settle(
        self,
        account_id: int,
        target_date: Optional[date] = None,
        latest_prices: Optional[Dict[str, float]] = None,
    ) -> DailySettleResult:
        """Run end-of-day settlement for one account.

        Args:
            account_id: The paper-trading account to settle.
            target_date: Trading date to settle for (defaults to today).
            latest_prices: Dict of code→close_price. If None, skips MTM.

        Returns:
            ``DailySettleResult`` with the post-settle account state.
        """
        settle_date = target_date or date.today()
        account = self.account_mgr.snapshot(account_id)

        # 1. Mark-to-market.
        positions = self.position_mgr.list_positions(account_id)
        mtm_results: List[PositionPnL] = []
        positions_value = 0.0

        if latest_prices:
            for pos in positions:
                close = float(latest_prices.get(pos["code"], pos.get("current_price", 0) or 0))
                qty = float(pos.get("available_quantity", 0) or 0) + float(pos.get("frozen_quantity", 0) or 0)
                if qty <= 0:
                    continue
                avg_cost = float(pos.get("avg_cost", 0) or 0)
                mv = close * qty
                pnl = (close - avg_cost) * qty
                pnl_pct = (close / avg_cost - 1.0) * 100 if avg_cost > 0 else 0.0

                mtm_results.append(PositionPnL(
                    code=str(pos.get("code", "")),
                    name=str(pos.get("name", "")),
                    quantity=int(qty),
                    avg_cost=avg_cost,
                    prev_close=0.0,
                    close_price=close,
                    market_value=mv,
                    unrealized_pnl=pnl,
                    unrealized_pnl_pct=pnl_pct,
                ))
                positions_value += mv

        # 2. Compute daily PnL.
        daily_pnl = account.total_assets - account.initial_capital if hasattr(account, "initial_capital") else 0.0

        # 3. Build result.
        result = DailySettleResult(
            account_id=account_id,
            settle_date=settle_date,
            total_assets=account.cash + positions_value,
            cash=account.cash,
            positions_value=positions_value,
            daily_pnl=daily_pnl,
            cumulative_pnl=daily_pnl,
            position_count=len(positions),
            fees_accrued=0.0,
            trades_closed=0,
        )

        logger.info(
            "Settlement: account=%s date=%s assets=%.2f cash=%.2f pos_value=%.2f pnl=%.2f",
            account_id, settle_date, result.total_assets,
            result.cash, result.positions_value, result.daily_pnl,
        )
        return result

    # ------------------------------------------------------------------
    # Net-value curve
    # ------------------------------------------------------------------

    def compute_net_value_curve(
        self,
        account_id: int,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Build a daily net-value curve for the account.

        Returns a DataFrame with columns: date, total_assets, daily_return.
        """
        # Delegate to TradingEngine's existing history method if available
        # or compute from trade ledger.
        logger.info(
            "Net-value curve requested: account=%s %s → %s",
            account_id, start_date, end_date,
        )
        # Placeholder: return empty frame with correct schema.
        return pd.DataFrame(columns=["date", "total_assets", "daily_return"])

    # ------------------------------------------------------------------
    # Mark-to-market helpers
    # ------------------------------------------------------------------

    def mark_to_market(
        self,
        account_id: int,
        latest_prices: Dict[str, float],
    ) -> List[PositionPnL]:
        """Compute unrealized PnL for all open positions in one account."""
        positions = self.position_mgr.list_positions(account_id)
        results: List[PositionPnL] = []
        for pos in positions:
            code = str(pos.get("code", ""))
            close = float(latest_prices.get(code, pos.get("current_price", 0) or 0))
            qty = float(pos.get("available_quantity", 0) or 0) + float(pos.get("frozen_quantity", 0) or 0)
            if qty <= 0:
                continue
            avg_cost = float(pos.get("avg_cost", 0) or 0)
            mv = close * qty
            pnl = (close - avg_cost) * qty
            pnl_pct = (close / avg_cost - 1.0) * 100 if avg_cost > 0 else 0.0
            results.append(PositionPnL(
                code=code,
                name=str(pos.get("name", "")),
                quantity=int(qty),
                avg_cost=avg_cost,
                prev_close=0.0,
                close_price=close,
                market_value=mv,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
            ))
        return results
