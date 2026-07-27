# -*- coding: utf-8 -*-
"""Market monitoring and event-driven action triggers for paper trading.

Real-time market monitoring with strategy evaluation and action triggering.
Supports PM decision generation, daily reflections, battle plan creation,
and dynamic SL/TP adjustments (P1-A).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Set

# NOTE: We avoid importing here to prevent circular imports;
# actual engine imports are done inside methods where needed.

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market session definitions
# ---------------------------------------------------------------------------

MARKET_SESSIONS = {
    "cn": [dt_time(9, 30), dt_time(11, 30), dt_time(13, 0), dt_time(15, 0)],
}


# ---------------------------------------------------------------------------
# Utility functions (top-level for re-export)
# ---------------------------------------------------------------------------

def is_market_open_now(market: str, now: Optional[datetime] = None) -> bool:
    """Check if the given market is open at the current time.

    Uses simple time-of-day checks against configured MARKET_SESSIONS.
    Returns True if within any configured session window.
    """
    if now is None:
        now = datetime.now()

    if market not in MARKET_SESSIONS:
        return False

    sessions = MARKET_SESSIONS[market]
    current_time = now.time()

    # Check each session window
    for i in range(0, len(sessions), 2):
        start = sessions[i]
        end = sessions[i + 1]
        if start <= current_time < end:
            return True

    return False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class MarketListenerConfig:
    """Configuration for MarketListener."""

    watched_codes: List[str] = field(default_factory=list)
    markets: Set[str] = field(default_factory=lambda: {"cn"})
    strategy_timeframes: List[str] = field(default_factory=lambda: ["1d"])
    tick_interval_seconds: float = 60.0


# ---------------------------------------------------------------------------
# MarketListener class
# ---------------------------------------------------------------------------

class MarketListener:
    """Monitored market ticker with action triggering hooks."""

    def __init__(
        self,
        config: Optional[MarketListenerConfig] = None,
        trading_engine=None,  # Avoid hard import to prevent circular deps
        battle_plan_generator=None,
        reflection_engine=None,
        default_account_id: int = 0,
    ):
        """Initialize the listener."""
        self.config = config or MarketListenerConfig()
        self.trading_engine = trading_engine
        self.battle_plan_generator = battle_plan_generator
        self.reflection_engine = reflection_engine
        self.default_account_id = default_account_id
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()

        # P1-A: Dynamic SL/TP threshold percentage (default 20%)
        self.sltp_dynamic_threshold_pct: float = 20.0

    def start(self) -> None:
        """Start the listener loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_safely, daemon=True)
        self._thread.start()
        logger.info("MarketListener started for markets=%s", self.config.markets)

    def stop(self, timeout: Optional[float] = None) -> None:
        """Stop the listener gracefully."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            logger.info("MarketListener stopped")

    def is_running(self) -> bool:
        """Check if the listener is active."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def _run_safely(self) -> None:
        """Run the tick loop with safe exception handling."""
        while self._running and not self._stop_event.is_set():
            try:
                self.run_loop()
            except Exception as exc:
                logger.exception("MarketListener loop error: %s", exc)
            time.sleep(1)

    def run_loop(self) -> None:
        """Tick all registered markets once."""
        for market in self.config.markets:
            if self._running and not self._stop_event.is_set():
                self._tick_market(market)

    def _tick_market(self, market: str) -> None:
        """Process all codes for a given market."""
        # Fetch prices and evaluate strategies
        for code in self._codes_for_market(market):
            price = self._get_latest_price(code, market)
            # Price handling would go here (update engine, etc.)

        self._evaluate_strategies(market)
        self._maybe_daily_settle(market)
        self._maybe_generate_battle_plan(date.today())
        self._maybe_trigger_pm_decision(market)
        self._maybe_run_daily_reflection(date.today())
        self._check_dynamic_sltp(market)  # P1-A

    def _codes_for_market(self, market: str) -> List[str]:
        """Get codes to monitor for this market."""
        if self.config.watched_codes:
            return self.config.watched_codes
        return []

    def _get_latest_price(self, code: str, market: str) -> Optional[float]:
        """Get latest price from trading engine if available."""
        if self.trading_engine:
            try:
                # Placeholder - actual implementation depends on engine interface
                return None
            except Exception:
                return None
        return None

    def _evaluate_strategies(self, market: str) -> None:
        """Evaluate trading strategies for the market."""
        pass

    def _maybe_daily_settle(self, market: str) -> None:
        """Perform daily settlement tasks if needed."""
        pass

    def _maybe_generate_battle_plan(self, today: date) -> None:
        """Generate battle plan for today if needed."""
        if self.battle_plan_generator and self.default_account_id > 0:
            try:
                self.battle_plan_generator.generate(today, self.default_account_id)
                logger.info("Battle plan generated for %s", today)
            except Exception as exc:
                logger.warning("Battle plan generation failed: %s", exc)

    def _maybe_trigger_pm_decision(self, market: str) -> None:
        """Trigger PM decision if conditions warrant."""
        pass

    def _maybe_run_daily_reflection(self, today: date) -> None:
        """Run daily reflection if needed."""
        if self.reflection_engine and self.default_account_id > 0:
            try:
                self.reflection_engine.reflect_on_daily(account_id=self.default_account_id, date=today)
                logger.info("Daily reflection completed for %s", today)
            except Exception as exc:
                logger.warning("Daily reflection failed: %s", exc)

    # -----------------------------------------------------------------------
    # P1-A: Dynamic SL/TP check hook
    # -----------------------------------------------------------------------

    def _check_dynamic_sltp(self, market: str) -> None:
        """Check positions for dynamic SL/TP adjustment."""
        if self.trading_engine is None:
            return

        acct_id = self.default_account_id
        if acct_id <= 0:
            return

        try:
            positions = self.trading_engine.position_mgr.list_positions(acct_id)
            for pos in positions:
                if getattr(pos, "stop_loss", None) is None:
                    continue

                latest = self._get_latest_price_for_code(pos.code, market)
                if latest is None or latest <= 0:
                    continue

                avg_cost = getattr(pos, "avg_cost", 0)
                if avg_cost <= 0:
                    continue

                profit_ratio = (latest - avg_cost) / avg_cost
                threshold = self.sltp_dynamic_threshold_pct / 100.0

                if profit_ratio >= threshold:
                    try:
                        # Compute new SL using fresh data
                        from paper_trading.sltp_calculator import build_sltp_calculator

                        calc = build_sltp_calculator(data_provider=None)
                        result = calc.compute(code=pos.code, entry_price=avg_cost)
                        new_stop_loss = result.stop_loss

                        if new_stop_loss > pos.stop_loss:
                            self.trading_engine.position_mgr.update_stop_loss_take_profit(
                                account_id=acct_id,
                                code=pos.code,
                                stop_loss=new_stop_loss,
                                take_profit=getattr(pos, "take_profit", None),
                            )
                            logger.info(
                                "Dynamic SL updated: %s cost=%.4f current=%.4f SL=%f->%f profit=%+.1f%%",
                                pos.code, avg_cost, latest, pos.stop_loss, new_stop_loss, profit_ratio * 100,
                            )
                    except Exception as e:
                        logger.debug("SLTP calculation failed for %s: %s", pos.code, e)
        except Exception as exc:
            logger.error("Dynamic SL/TP check failed: %s", exc)

    def _get_latest_price_for_code(self, code: str, market: str) -> Optional[float]:
        """Helper to get latest price."""
        return self._get_latest_price(code, market)


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def build_default_listener(
    trading_engine=None,
    account_id: int = 0,
) -> MarketListener:
    """Build a MarketListener wired to project defaults."""
    from src.config import get_config

    config = get_config()
    cfg = MarketListenerConfig(
        watched_codes=config.stock_list[:10],
        markets={"cn"},
        default_account_id=account_id,
    )

    # Attach components lazily to avoid circular imports
    listener = MarketListener(
        config=cfg,
        trading_engine=trading_engine,
        default_account_id=account_id,
    )

    # P1-A: Read threshold from config if available
    try:
        from paper_trading.battle_plan import BattlePlanGenerator
        bp_gen = BattlePlanGenerator(
            db_manager=getattr(trading_engine, "db", None),
            account_id=account_id,
        )
        listener.battle_plan_generator = bp_gen
    except Exception:
        pass

    try:
        from paper_trading.reflection import ReflectionEngine
        refl_engine = ReflectionEngine(db_manager=getattr(trading_engine, "db", None))
        listener.reflection_engine = refl_engine
    except Exception:
        pass

    # Set threshold from config
    listener.sltp_dynamic_threshold_pct = float(
        getattr(config, "paper_trading_sltp_dynamic_threshold_pct", 20.0)
    )

    return listener