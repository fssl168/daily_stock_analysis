# -*- coding: utf-8 -*-
"""Real-time market listener driving the TradingEngine (Phase 5).

Polls realtime quotes for watched codes at a configurable tick interval,
evaluates strategies_v2 rules on each tick, and feeds signals into the
TradingEngine. Also drives limit-order matching, SL/TP guards, and
end-of-day settlement.

Lifecycle:
- ``start()`` launches a daemon thread and returns immediately.
- ``run_loop()`` is the blocking main loop (runs inside the daemon thread).
- ``stop()`` signals shutdown (does not join — daemon exits with process).

Tick logic (per iteration when market is open):
1. Fetch latest realtime quotes for all watched codes (bulk prefetch).
2. ``TradingEngine.match_pending_orders(latest_prices)`` — fill triggered
   limit orders.
3. ``TradingEngine.check_stop_loss_take_profit(latest_prices)`` — auto-emit
   sell signals for positions breaching SL/TP.
4. ``_evaluate_strategies(latest_prices)`` — for each (code, strategy):
   - Get daily-bar DataFrame (cached to limit data-source load).
   - Run ``RuleEngine.evaluate()`` -> ``Signal``.
   - If side in (buy, sell): submit to engine (with cooldown dedupe).
5. After session close (once per day): ``TradingEngine.daily_settle()``.

Concurrency:
- Runs in a daemon thread so it can coexist with ``--serve`` (FastAPI).
- The TradingEngine is synchronous; all calls are serialized within the
  listener thread. External callers (API handlers) may also call the
  engine concurrently — engine DB operations use short-lived sessions,
  but cross-tick coordination is the caller's responsibility.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from paper_trading.trading_engine import TradeResult, TradingEngine
from strategies_v2 import RuleEngine, RuleStrategy, Signal, load_strategies_from_dir

logger = logging.getLogger(__name__)


# ============================================================
# Market session windows (intraday)
# ============================================================

# Market code -> list of (start, end) session windows in local market time.
# Used in combination with trading_calendar.is_market_open() to determine
# if a market is open *right now* (not just today).
MARKET_SESSIONS: Dict[str, List[Tuple[dtime, dtime]]] = {
    "cn": [
        (dtime(9, 30), dtime(11, 30)),   # morning session
        (dtime(13, 0), dtime(15, 0)),    # afternoon session
    ],
    "hk": [
        (dtime(9, 30), dtime(12, 0)),    # morning session
        (dtime(13, 0), dtime(16, 0)),    # afternoon session
    ],
    "us": [
        (dtime(9, 30), dtime(16, 0)),    # continuous session
    ],
}

# Market code -> IANA timezone for "now" computation.
MARKET_TIMEZONE: Dict[str, str] = {
    "cn": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
}

# Buffer (seconds) after session close before triggering daily_settle.
# Ensures post-close quotes are available and avoids racing the last tick.
POST_CLOSE_SETTLE_BUFFER_SECONDS = 300  # 5 minutes


# ============================================================
# Stock-code market classification (with graceful fallback)
# ============================================================

def _local_classify_market(code: str) -> Optional[str]:
    """Local fallback classifier when trading_calendar's import chain is broken.

    Rules:
    - A-share: 6-digit numeric (e.g. 600519, 000001, 300750)
    - HK: ends with ".HK" / ".hk" or 4-5 digit numeric (e.g. 0700.HK, 09988)
    - US: contains a letter and is not a HK-suffixed code (e.g. AAPL, MSFT)
    - Unknown: None
    """
    if not code or not isinstance(code, str):
        return None
    c = code.strip().upper()
    if not c:
        return None
    # HK suffix first (unambiguous).
    if c.endswith(".HK"):
        return "hk"
    # Pure 6-digit numeric -> A-share.
    if c.isdigit() and len(c) == 6:
        return "cn"
    # 4-5 digit numeric without suffix -> likely HK.
    if c.isdigit() and 4 <= len(c) <= 5:
        return "hk"
    # Contains a letter -> likely US ticker.
    if any(ch.isalpha() for ch in c):
        return "us"
    return None


def _get_market_classifier():
    """Return the canonical get_market_for_stock, or a local fallback.

    The canonical version (in ``src.core.trading_calendar``) imports from
    ``data_provider`` which transitively pulls in LLM dependencies. When
    those are unavailable, we fall back to a local regex-based classifier
    so the listener keeps running.
    """
    try:
        from src.core.trading_calendar import get_market_for_stock
        # Probe a call to ensure imports resolve.
        get_market_for_stock("600519")
        return get_market_for_stock
    except Exception as exc:
        logger.warning(
            "[MarketListener] using local market classifier "
            "(trading_calendar import failed: %s)", exc,
        )
        return _local_classify_market


def is_market_open_now(market: str, now: Optional[datetime] = None) -> bool:
    """Return True if ``market`` is in an active intraday session right now.

    Combines day-level trading-day check (``trading_calendar.is_market_open``)
    with the intraday session windows defined in ``MARKET_SESSIONS``.

    Fail-open: if exchange-calendars is unavailable, only the session-window
    check applies (so the listener still runs on weekdays).
    """
    from src.core.trading_calendar import is_market_open

    tz_name = MARKET_TIMEZONE.get(market)
    if tz_name is None:
        return False
    tz = ZoneInfo(tz_name)
    now_local = now or datetime.now(tz)
    if now_local.tzinfo is None:
        now_local = now_local.replace(tzinfo=tz)

    # Day-level check (trading day or holiday).
    if not is_market_open(market, now_local.date()):
        return False

    # Intraday session check.
    sessions = MARKET_SESSIONS.get(market, [])
    if not sessions:
        return False
    t = now_local.time()
    for start, end in sessions:
        if start <= t < end:
            return True
    return False


def get_market_close_today(market: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Return the local datetime of today's session close for ``market``.

    Returns None if market is unknown or today is not a trading day.
    """
    from src.core.trading_calendar import is_market_open

    tz_name = MARKET_TIMEZONE.get(market)
    if tz_name is None:
        return None
    tz = ZoneInfo(tz_name)
    now_local = now or datetime.now(tz)
    if now_local.tzinfo is None:
        now_local = now_local.replace(tzinfo=tz)
    if not is_market_open(market, now_local.date()):
        return None
    sessions = MARKET_SESSIONS.get(market, [])
    if not sessions:
        return None
    # Use the end of the last session.
    _, last_end = sessions[-1]
    close_dt = datetime.combine(now_local.date(), last_end, tzinfo=tz)
    return close_dt


# ============================================================
# Listener config
# ============================================================

@dataclass
class MarketListenerConfig:
    """Configuration for the MarketListener.

    Attributes:
        account_id: Paper trading account to drive.
        watched_codes: Stock codes to poll. If empty, listener does nothing.
        strategy_dir: Directory of strategies_v2 YAML files. May be empty
            or non-existent — listener will run with no strategies (SL/TP
            and pending-order matching still work).
        tick_interval_seconds: Polling interval. Default 10s.
        daily_df_cache_seconds: How long to cache daily-bar DataFrames per
            code before re-fetching. Default 300s (5 min).
        signal_cooldown_seconds: Skip re-emitting the same (code, strategy,
            side) signal within this window. Default 900s (15 min).
        markets: Set of markets to monitor. Codes are routed to markets via
            ``get_market_for_stock``. Default {"cn"}.
        enable_strategies: If False, skip strategy evaluation entirely
            (only order matching + SL/TP). Default True.
        enable_agent_review: If True, build an AgentRiskReviewer and inject
            it into the TradingEngine. Default False (configured at engine
            build time, not here, but kept for future wiring).
        pm_decision_interval_seconds: P1-C: If > 0 and ``pm_agent`` is
            configured, the listener triggers a PM decision cycle every
            N seconds during market hours. Default 600s (10 min). Set to 0
            to disable PM auto-cycling.
        enable_daily_reflection: P1-C: If True, trigger
            ``ReflectionEngine.reflect_on_daily`` after ``daily_settle``.
            Default True.
        enable_battle_plan: P1-C: If True, generate a next-day battle plan
            after ``daily_settle`` (requires ``battle_plan_generator``).
            Default True.
    """

    account_id: int
    watched_codes: List[str] = field(default_factory=list)
    strategy_dir: Optional[str] = None
    tick_interval_seconds: float = 10.0
    daily_df_cache_seconds: float = 300.0
    signal_cooldown_seconds: float = 900.0
    markets: Set[str] = field(default_factory=lambda: {"cn"})
    enable_strategies: bool = True
    enable_agent_review: bool = False
    # P1-C additions
    pm_decision_interval_seconds: float = 600.0
    enable_daily_reflection: bool = True
    enable_battle_plan: bool = True


# ============================================================
# Market listener
# ============================================================

class MarketListener:
    """Drives TradingEngine via periodic realtime-quote polling.

    Usage::

        listener = MarketListener(engine, fetcher, strategies, config)
        listener.start()  # non-blocking — daemon thread
        # ... later ...
        listener.stop()   # signal shutdown
    """

    def __init__(
        self,
        engine: TradingEngine,
        data_fetcher: Any,
        strategies: Optional[List[RuleStrategy]] = None,
        config: Optional[MarketListenerConfig] = None,
        pm_agent: Optional[Any] = None,
        reflection_engine: Optional[Any] = None,
        battle_plan_generator: Optional[Any] = None,
    ):
        self.engine = engine
        self.fetcher = data_fetcher
        self.strategies: List[RuleStrategy] = list(strategies or [])
        self.config = config or MarketListenerConfig(account_id=0)
        # P1-C: Optional PM agent / reflection / battle-plan hooks.
        self.pm_agent = pm_agent
        self.reflection_engine = reflection_engine
        self.battle_plan_generator = battle_plan_generator

        self.rule_engine = RuleEngine()
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Caches and dedupe state.
        self._daily_df_cache: Dict[str, Tuple[datetime, Any]] = {}  # code -> (fetched_at, df)
        self._last_signal_at: Dict[Tuple[str, str, str], datetime] = {}  # (code, strat, side) -> ts
        self._last_settle_date: Optional[date] = None
        self._market_was_open: Dict[str, bool] = {}  # market -> was open on previous tick
        # P1-C: PM decision cadence tracking (per-market last-triggered ts).
        self._last_pm_decision_at: Dict[str, datetime] = {}
        # P1-C: Battle plan is generated once per day, per account, after close.
        self._last_battle_plan_date: Optional[date] = None
        self._last_daily_reflection_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch the listener loop in a daemon thread (non-blocking)."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("[MarketListener] already running")
            return
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._run_safely,
            name="paper-trading-listener",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[MarketListener] started: codes=%d strategies=%d tick=%ss markets=%s",
            len(self.config.watched_codes),
            len(self.strategies),
            self.config.tick_interval_seconds,
            sorted(self.config.markets),
        )

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal shutdown. Optionally join the thread."""
        self._shutdown.set()
        if self._thread is not None and timeout is not None:
            self._thread.join(timeout=timeout)
        logger.info("[MarketListener] stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_safely(self) -> None:
        try:
            self.run_loop()
        except Exception as exc:
            logger.exception("[MarketListener] fatal error: %s", exc)

    def run_loop(self) -> None:
        """Blocking main loop. Exits when ``stop()`` is called."""
        logger.info("[MarketListener] entering main loop")
        while not self._shutdown.is_set():
            try:
                self._tick_all_markets()
            except Exception as exc:
                logger.exception("[MarketListener] tick error: %s", exc)
            # Wait for the next tick interval, but wake up early on shutdown.
            self._shutdown.wait(timeout=self.config.tick_interval_seconds)
        logger.info("[MarketListener] main loop exited")

    # ------------------------------------------------------------------
    # Tick logic
    # ------------------------------------------------------------------

    def _tick_all_markets(self) -> None:
        """Run one tick: evaluate each configured market, settle if needed."""
        any_open = False
        for market in self.config.markets:
            open_now = is_market_open_now(market)
            was_open = self._market_was_open.get(market, False)

            if open_now:
                any_open = True
                self._tick_market(market)
            elif was_open and not open_now:
                # Transition: open -> closed. Maybe run daily_settle.
                logger.info(
                    "[MarketListener] market %s transitioned to closed", market
                )
                self._maybe_daily_settle(market)

            self._market_was_open[market] = open_now

        # Fallback: if no markets are open but it's after close on a trading
        # day and we haven't settled, settle anyway (handles listener restart
        # after session close).
        if not any_open:
            for market in self.config.markets:
                self._maybe_daily_settle(market)

    def _tick_market(self, market: str) -> None:
        """Run one tick for a single open market."""
        codes = self._codes_for_market(market)
        if not codes:
            return

        latest_prices = self._fetch_latest_prices(codes)
        if not latest_prices:
            logger.debug("[MarketListener] %s: no prices fetched", market)
            return

        # 1) Match pending limit orders.
        matched = self.engine.match_pending_orders(latest_prices)
        for r in matched:
            logger.info(
                "[MarketListener] order matched: %s %s qty=%s price=%s",
                r.side, r.code, r.fill_quantity, r.fill_price,
            )

        # 2) Check stop-loss / take-profit.
        sl_tp = self.engine.check_stop_loss_take_profit(
            latest_prices, account_id=self.config.account_id
        )
        for r in sl_tp:
            logger.info(
                "[MarketListener] SL/TP triggered: %s %s -> %s",
                r.side, r.code, r.status,
            )

        # 3) Evaluate strategies.
        if self.config.enable_strategies and self.strategies:
            self._evaluate_strategies(codes, latest_prices, market)

        # 4) P1-C: Periodically trigger PM agent decision cycle.
        self._maybe_trigger_pm_decision(market)

    def _codes_for_market(self, market: str) -> List[str]:
        """Filter watched codes to those belonging to ``market``.

        Uses ``trading_calendar.get_market_for_stock`` when available;
        falls back to a local classifier if the data_provider import chain
        is broken (e.g., missing optional LLM dependencies).
        """
        get_market_for_stock = _get_market_classifier()
        out: List[str] = []
        for code in self.config.watched_codes:
            m = get_market_for_stock(code)
            # Unknown market codes are included only if "cn" is the target
            # (fail-open for unrecognized formats like indices).
            if m == market or (m is None and market == "cn"):
                out.append(code)
        return out

    def _fetch_latest_prices(self, codes: List[str]) -> Dict[str, float]:
        """Fetch realtime quotes and return {code: latest_price}."""
        out: Dict[str, float] = {}
        # Use bulk prefetch when >=5 codes (populates cache efficiently).
        try:
            if len(codes) >= 5 and hasattr(self.fetcher, "prefetch_realtime_quotes"):
                self.fetcher.prefetch_realtime_quotes(codes)
        except Exception as exc:
            logger.debug("[MarketListener] prefetch failed: %s", exc)

        for code in codes:
            try:
                quote = self.fetcher.get_realtime_quote(code)
            except Exception as exc:
                logger.debug(
                    "[MarketListener] get_realtime_quote failed for %s: %s",
                    code, exc,
                )
                continue
            if quote is None:
                continue
            price = getattr(quote, "price", None)
            if price is not None and float(price) > 0:
                out[code] = float(price)
        return out

    def _evaluate_strategies(
        self,
        codes: List[str],
        latest_prices: Dict[str, float],
        market: str,
    ) -> None:
        """Evaluate all strategies for each code; submit signals to engine."""
        for code in codes:
            price = latest_prices.get(code)
            if price is None or price <= 0:
                continue
            df = self._get_daily_df(code)
            if df is None or len(df) < 2:
                continue
            for strategy in self.strategies:
                try:
                    signal = self.rule_engine.evaluate(
                        strategy=strategy, df=df, code=code,
                    )
                except Exception as exc:
                    logger.warning(
                        "[MarketListener] evaluate failed: %s/%s: %s",
                        code, strategy.name, exc,
                    )
                    continue
                if signal.side not in ("buy", "sell"):
                    continue
                if not self._should_emit_signal(signal):
                    continue
                try:
                    result = self.engine.submit_signal(
                        account_id=self.config.account_id,
                        signal=signal,
                    )
                    self._record_signal(signal)
                    logger.info(
                        "[MarketListener] signal submitted: %s %s (strat=%s) "
                        "-> status=%s reason=%s",
                        signal.side, code, strategy.name,
                        result.status, result.reason,
                    )
                except Exception as exc:
                    logger.exception(
                        "[MarketListener] submit_signal failed: %s %s: %s",
                        signal.side, code, exc,
                    )

    def _should_emit_signal(self, signal: Signal) -> bool:
        """Dedupe: skip if same (code, strategy, side) was emitted recently."""
        key = (signal.code, signal.strategy_name, signal.side)
        last = self._last_signal_at.get(key)
        if last is None:
            return True
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed < self.config.signal_cooldown_seconds:
            logger.debug(
                "[MarketListener] dedupe skip: %s %s %s (last=%ss ago)",
                signal.side, signal.code, signal.strategy_name,
                int(elapsed),
            )
            return False
        return True

    def _record_signal(self, signal: Signal) -> None:
        key = (signal.code, signal.strategy_name, signal.side)
        self._last_signal_at[key] = datetime.now()

    def _get_daily_df(self, code: str) -> Any:
        """Return the daily-bar DataFrame for ``code``, with caching."""
        now = datetime.now()
        cached = self._daily_df_cache.get(code)
        if cached is not None:
            fetched_at, df = cached
            age = (now - fetched_at).total_seconds()
            if age < self.config.daily_df_cache_seconds:
                return df

        try:
            df = self.fetcher.get_daily_data(code, days=60)
        except Exception as exc:
            logger.debug(
                "[MarketListener] get_daily_data failed for %s: %s", code, exc
            )
            return None
        if df is None or len(df) < 2:
            return None

        # Ensure index is date ascending (rule engine requires this).
        try:
            import pandas as pd
            if "date" in df.columns:
                df = df.set_index("date")
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            df = df.sort_index()
        except Exception as exc:
            logger.debug(
                "[MarketListener] df index fix failed for %s: %s", code, exc
            )

        self._daily_df_cache[code] = (now, df)
        return df

    # ------------------------------------------------------------------
    # Daily settlement
    # ------------------------------------------------------------------

    def _maybe_daily_settle(self, market: str) -> None:
        """Run daily_settle once per (account, trading day) after session close."""
        today = date.today()
        if self._last_settle_date == today:
            return

        # Only settle if today is a trading day for this market.
        from src.core.trading_calendar import is_market_open
        if not is_market_open(market, today):
            return

        # Ensure we're past the session close + buffer.
        close_dt = get_market_close_today(market)
        if close_dt is not None:
            tz = close_dt.tzinfo
            now_local = datetime.now(tz)
            elapsed_since_close = (now_local - close_dt).total_seconds()
            if elapsed_since_close < 0:
                # Market still open (shouldn't happen here, but defensive).
                return
            if elapsed_since_close < POST_CLOSE_SETTLE_BUFFER_SECONDS:
                logger.debug(
                    "[MarketListener] %s: waiting %ss after close before settle",
                    market,
                    int(POST_CLOSE_SETTLE_BUFFER_SECONDS - elapsed_since_close),
                )
                return

        # Fetch final prices for accurate mark-to-market.
        codes = self._codes_for_market(market)
        latest_prices = self._fetch_latest_prices(codes) if codes else {}

        try:
            result = self.engine.daily_settle(
                account_id=self.config.account_id,
                target_date=today,
                latest_prices=latest_prices or None,
            )
            self._last_settle_date = today
            logger.info(
                "[MarketListener] daily_settle complete: account=%s rolled=%s date=%s",
                self.config.account_id,
                result.get("positions_rolled"),
                result.get("date"),
            )
        except Exception as exc:
            logger.exception(
                "[MarketListener] daily_settle failed for account=%s: %s",
                self.config.account_id, exc,
            )
            return

        # P1-C: post-settle hooks — daily reflection + next-day battle plan.
        # These run once per day, after daily_settle succeeds. They are
        # independently fault-tolerant: a failure in reflection does not
        # block battle plan generation (and vice versa).
        if self.config.enable_daily_reflection:
            self._maybe_run_daily_reflection(today)
        if self.config.enable_battle_plan:
            self._maybe_generate_battle_plan(today)

    # ------------------------------------------------------------------
    # P1-C: PM agent / reflection / battle-plan triggers
    # ------------------------------------------------------------------

    def _maybe_trigger_pm_decision(self, market: str) -> None:
        """Trigger a PM agent decision cycle on a configured cadence.

        Skipped when:
        - ``pm_agent`` is None.
        - ``pm_decision_interval_seconds`` <= 0.
        - Not enough time has elapsed since the last decision for ``market``.
        """
        if self.pm_agent is None:
            return
        interval = float(self.config.pm_decision_interval_seconds or 0.0)
        if interval <= 0:
            return
        last = self._last_pm_decision_at.get(market)
        now = datetime.now()
        if last is not None and (now - last).total_seconds() < interval:
            return
        self._last_pm_decision_at[market] = now
        try:
            decision = self.pm_agent.make_decision(
                account_id=self.config.account_id,
                extra_context={
                    "trigger": "market_listener_tick",
                    "market": market,
                    "ts": now.isoformat(),
                },
            )
            logger.info(
                "[MarketListener] PM decision: action=%s code=%s confidence=%.2f "
                "fallback=%s (%.1fs)",
                decision.action, decision.code, decision.confidence,
                decision.used_fallback, decision.elapsed_seconds,
            )
        except Exception as exc:
            logger.warning(
                "[MarketListener] PM decision failed for market=%s: %s",
                market, exc,
            )

    def _maybe_run_daily_reflection(self, today: date) -> None:
        """Run ``reflect_on_daily`` once per day after settle."""
        if self.reflection_engine is None:
            return
        if self._last_daily_reflection_date == today:
            return
        try:
            note = self.reflection_engine.reflect_on_daily(
                account_id=self.config.account_id,
                review_date=today,
            )
            self._last_daily_reflection_date = today
            logger.info(
                "[MarketListener] daily reflection complete: subject=%s mood=%s",
                getattr(note, "subject", ""),
                getattr(note, "mood", ""),
            )
        except Exception as exc:
            logger.warning(
                "[MarketListener] daily reflection failed: %s", exc,
            )

    def _maybe_generate_battle_plan(self, today: date) -> None:
        """Generate the next-trading-day battle plan once per day after settle."""
        if self.battle_plan_generator is None:
            return
        if self._last_battle_plan_date == today:
            return
        try:
            from datetime import timedelta

            # Battle plan applies to the *next* trading day.
            target_date = today + timedelta(days=1)
            plan = self.battle_plan_generator.generate(
                account_id=self.config.account_id,
                target_date=target_date,
            )
            self._last_battle_plan_date = today
            logger.info(
                "[MarketListener] battle plan generated: plan_id=%s date=%s "
                "holdings=%d candidates=%d fallback=%s",
                plan.plan_id, plan.date,
                len(plan.holdings_plans), len(plan.candidates),
                plan.used_fallback,
            )
        except Exception as exc:
            logger.warning(
                "[MarketListener] battle plan generation failed: %s", exc,
            )


# ============================================================
# Factory
# ============================================================

def build_default_listener(
    config: Any,
    account_id: int,
    watched_codes: Optional[List[str]] = None,
    strategy_dir: Optional[str] = None,
    data_fetcher: Optional[Any] = None,
    engine: Optional[TradingEngine] = None,
    agent_reviewer: Optional[Any] = None,
    markets: Optional[Set[str]] = None,
    tick_interval_seconds: float = 10.0,
    enable_strategies: bool = True,
    pm_agent: Optional[Any] = None,
    reflection_engine: Optional[Any] = None,
    battle_plan_generator: Optional[Any] = None,
    pm_decision_interval_seconds: Optional[float] = None,
    enable_daily_reflection: bool = True,
    enable_battle_plan: bool = True,
    on_trade_executed: Optional[Any] = None,
    on_signal_rejected: Optional[Any] = None,
) -> MarketListener:
    """Build a MarketListener wired to project defaults.

    Args:
        config: Application config (``src.config.Config``). Used to source
            default ``watched_codes`` (``config.stock_list``) and to build
            the agent executor if ``agent_reviewer`` is requested.
        account_id: Paper trading account to drive.
        watched_codes: Override list of codes. Defaults to ``config.stock_list``.
        strategy_dir: Directory of strategies_v2 YAML files. If None,
            defaults to ``paper_trading/strategies/`` (may not exist).
        data_fetcher: Pre-built ``DataFetcherManager``. If None, builds one.
        engine: Pre-built ``TradingEngine``. If None, builds one with the
            optional ``agent_reviewer`` injected. P1-C: when ``on_trade_executed``
            / ``on_signal_rejected`` are provided, they are injected into the
            engine (only effective when ``engine`` is None; if you supply
            your own engine, wire the callbacks to it directly).
        agent_reviewer: Pre-built ``AgentRiskReviewer``. Pass None to skip
            agent review (signals flow through after risk checks).
        markets: Set of markets to monitor. Defaults to {"cn"}.
        tick_interval_seconds: Polling interval.
        enable_strategies: If False, skip strategy evaluation.
        pm_agent: P1-C: Pre-built ``PortfolioManagerAgent``. When set, the
            listener triggers a decision cycle every
            ``pm_decision_interval_seconds`` during market hours.
        reflection_engine: P1-C: Pre-built ``ReflectionEngine``. When set,
            ``reflect_on_daily`` runs after ``daily_settle`` (if
            ``enable_daily_reflection`` is True).
        battle_plan_generator: P1-C: Pre-built ``BattlePlanGenerator``. When
            set, a next-day battle plan is generated after ``daily_settle``
            (if ``enable_battle_plan`` is True).
        pm_decision_interval_seconds: Override the PM decision cadence.
            Defaults to ``MarketListenerConfig.pm_decision_interval_seconds``
            (600s). Set to 0 to disable.
        enable_daily_reflection: Whether to auto-run daily reflection.
        enable_battle_plan: Whether to auto-generate the next-day plan.
        on_trade_executed: P1-C: Callback injected into TradingEngine (only
            when ``engine`` is None). Receives ``(TradeResult, trade_id=...)``.
        on_signal_rejected: P1-C: Callback injected into TradingEngine (only
            when ``engine`` is None). Receives ``TradeResult``.
    """
    # Lazy imports to keep module import cheap.
    from data_provider import DataFetcherManager
    from paper_trading.agent_risk import AgentRiskReviewer

    if watched_codes is None:
        watched_codes = list(getattr(config, "stock_list", []) or [])

    if strategy_dir is None:
        # Default location — caller may create strategies here.
        strategy_dir = str(Path(__file__).parent / "strategies")

    strategies: List[RuleStrategy] = []
    if enable_strategies and strategy_dir:
        try:
            strategies = load_strategies_from_dir(strategy_dir)
            logger.info(
                "[MarketListener] loaded %d strategies from %s",
                len(strategies), strategy_dir,
            )
        except Exception as exc:
            logger.warning(
                "[MarketListener] failed to load strategies from %s: %s",
                strategy_dir, exc,
            )

    if data_fetcher is None:
        data_fetcher = DataFetcherManager()

    if engine is None:
        engine = TradingEngine(
            agent_reviewer=agent_reviewer,
            on_trade_executed=on_trade_executed,
            on_signal_rejected=on_signal_rejected,
        )

    listener_config = MarketListenerConfig(
        account_id=account_id,
        watched_codes=watched_codes,
        strategy_dir=strategy_dir,
        tick_interval_seconds=tick_interval_seconds,
        markets=markets or {"cn"},
        enable_strategies=enable_strategies,
        pm_decision_interval_seconds=(
            float(pm_decision_interval_seconds)
            if pm_decision_interval_seconds is not None
            else 600.0
        ),
        enable_daily_reflection=enable_daily_reflection,
        enable_battle_plan=enable_battle_plan,
    )

    return MarketListener(
        engine=engine,
        data_fetcher=data_fetcher,
        strategies=strategies,
        config=listener_config,
        pm_agent=pm_agent,
        reflection_engine=reflection_engine,
        battle_plan_generator=battle_plan_generator,
    )
