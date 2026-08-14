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
from paper_trading.strategies import RuleEngine, RuleStrategy, Signal, load_strategies_from_dir
from src.config import get_config
from src.utils.exchange_clock import ExchangeClock

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
    now_local = now or ExchangeClock.now(market)
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
    now_local = now or ExchangeClock.now(market)
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
# Timeframe resampling helpers
# ============================================================

_TIMEFRAME_FREQ: Dict[str, str] = {
    "1d": "D",
    "d": "D",
    "day": "D",
    "daily": "D",
    "1w": "W",
    "w": "W",
    "week": "W",
    "weekly": "W",
    "1m": "M",
    "m": "M",
    "month": "M",
    "monthly": "M",
}


def _resample_to_timeframe(df: Any, timeframe: str) -> Any:
    """Resample a daily DataFrame to ``timeframe`` (1d/1w/1m/Nd/etc.).

    The input must be indexed by a pandas DatetimeIndex and contain at
    least a ``close`` column. OHLCV columns are aggregated; other columns
    take the last value of the period.

    Returns None if the timeframe is unsupported or resampling fails.
    """
    import pandas as pd

    tf = timeframe.strip().lower()
    if tf in ("1d", "d", "day", "daily"):
        return df

    # Try simple alias first, then numeric prefix (e.g. "5d", "2w").
    freq = _TIMEFRAME_FREQ.get(tf)
    if freq is None:
        for suffix, base_freq in (("d", "D"), ("w", "W"), ("m", "M")):
            if tf.endswith(suffix):
                try:
                    n = int(tf[:-1])
                    if n > 0:
                        freq = f"{n}{base_freq}"
                        break
                except ValueError:
                    continue
    if freq is None:
        logger.debug("[MarketListener] unsupported timeframe: %s", timeframe)
        return None

    try:
        agg: Dict[str, str] = {}
        for col in df.columns:
            if col in ("open", "high", "low", "close"):
                agg[col] = {"open": "first", "high": "max", "low": "min", "close": "last"}[col]
            elif col == "volume":
                agg[col] = "sum"
            else:
                agg[col] = "last"
        resampled = df.resample(freq).agg(agg).dropna(subset=["close"])
        return resampled
    except Exception as exc:
        logger.debug("[MarketListener] resample failed for %s: %s", timeframe, exc)
        return None


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
        strategy_timeframes: Phase 3: default timeframes to evaluate when a
            strategy does not declare its own. Default ["1d"]. The listener
            fetches/resamples data for each timeframe and requires consensus.
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
    # Phase 3 additions
    strategy_timeframes: List[str] = field(default_factory=lambda: ["1d"])
    # P1-A: Dynamic trailing stop-loss.
    enable_dynamic_sltp: bool = True
    sltp_dynamic_threshold_pct: float = 20.0
    # P0-C: Intraday position review via AgentRiskReviewer.
    enable_position_review: bool = False
    position_review_interval_seconds: float = 1800.0
    # P2-A: Daily report generation after settle.
    enable_daily_report: bool = False


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
        content_generator: Optional[Any] = None,
        notifier: Optional[Any] = None,
        quote_cache: Optional["SharedQuoteCache"] = None,  # ② T12 integration
        signal_fusion: Optional["SignalFusionEngine"] = None,  # ④ T3 integration
        risk_daemon: Optional[Any] = None,  # ⑤ T8 integration
        feature_pipeline: Optional[Any] = None,  # ⑥ T13: feature pipeline
        extreme_market_response: Optional[Any] = None,  # ⑦ T11: extreme market
        drift_detector: Optional[Any] = None,  # ⑧ T10: drift detector
        latency_tracker: Optional[Any] = None,  # ⑨ T-005: tick latency aggregation
    ):
        self.engine = engine
        self.fetcher = data_fetcher
        self.strategies: List[RuleStrategy] = list(strategies or [])
        self.config = config or MarketListenerConfig(account_id=0)
        # P1-C: Optional PM agent / reflection / battle-plan hooks.
        self.pm_agent = pm_agent
        self.reflection_engine = reflection_engine
        self.battle_plan_generator = battle_plan_generator
        # P2-A: Optional daily-report content generator + notifier.
        self.content_generator = content_generator
        self.notifier = notifier

        # ② QuoteCache (T12 integration) — optional dual-channel quote cache.
        self._quote_cache = quote_cache

        # ④ SignalFusionEngine (T3 integration) — optional multi-strategy fusion.
        self._signal_fusion = signal_fusion

        # ⑤ RiskDaemon (T8 integration) — optional real-time risk monitoring.
        self._risk_daemon = risk_daemon

        # ⑥ FeaturePipeline (T13 integration) — post-settle feature computation.
        self._feature_pipeline = feature_pipeline

        # ⑦ ExtremeMarketResponse (T11 integration) — VIX-like volatility response.
        self._extreme_market_response = extreme_market_response
        self._extreme_market_was_active = False

        # ⑧ DriftDetector (T10 integration) — strategy drift monitoring.
        self._drift_detector = drift_detector

        # ⑩ L4 元认知信号闸门（默认启用）— 偏差检测调节信号仓位/过滤。
        # L4 引擎不可用时置 None，提交循环自动跳过（fail-open，不阻断交易）。
        try:
            from paper_trading.meta_cognitive_gate import L4SignalGate

            self._l4_gate = L4SignalGate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("L4SignalGate init failed (disabled): %s", exc)
            self._l4_gate = None

        # ⑨ LatencyTracker (T-005 integration) — optional tick latency aggregator.
        self._latency_tracker = latency_tracker

        self.rule_engine = RuleEngine()
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Caches and dedupe state.
        self._daily_df_cache: Dict[str, Tuple[datetime, Any]] = {}  # code -> (fetched_at, df)
        self._last_signal_at: Dict[Tuple[str, str, str], datetime] = {}  # (code, strat, side) -> ts
        self._last_settle_date: Optional[date] = None
        self._market_was_open: Dict[str, bool] = {}  # market -> was open on previous tick
        # Intraday net-value snapshot cadence (unix ts of last snapshot).
        self._last_intraday_ts: float = 0.0
        # P1-C: PM decision cadence tracking (per-market last-triggered ts).
        self._last_pm_decision_at: Dict[str, datetime] = {}
        # P1-C: Battle plan is generated once per day, per account, after close.
        self._last_battle_plan_date: Optional[date] = None
        self._last_daily_reflection_date: Optional[date] = None
        # P0-C: Position review cadence tracking (last-triggered ts).
        self._last_position_review_ts: Optional[datetime] = None

        # Observability: per-tick data-source health + strategy-output summary
        # (throttled so the console shows the listener is alive without spam).
        self._tick_stats: Dict[str, Any] = {
            "market": "cn",
            "prices_fetched": 0,
            "codes_total": 0,
            "evaluated": 0,
            "signals": 0,
            "ts": 0.0,
        }
        self._last_tick_summary_ts: float = 0.0

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

        # Intraday net-value snapshots while any market is open: gives the
        # net-value curve live shape during trading hours.
        if any_open:
            self._maybe_intraday_snapshot()

        # Fallback: if no markets are open but it's after close on a trading
        # day and we haven't settled, settle anyway (handles listener restart
        # after session close).
        if not any_open:
            for market in self.config.markets:
                self._maybe_daily_settle(market)

    # ------------------------------------------------------------------
    # Intraday net-value snapshots
    # ------------------------------------------------------------------

    def _maybe_intraday_snapshot(self) -> None:
        """Record an intraday net-value point for every active paper account
        while any market is open (rate-limited to the snapshot interval).

        Gives the net-value curve live shape during trading hours instead of
        a single end-of-day point per account.
        """
        interval = float(
            getattr(self.config, "intraday_snapshot_interval_seconds", 300) or 300
        )
        now = time.time()
        if now - self._last_intraday_ts < interval:
            return
        accounts = self.engine.account_mgr.list_accounts(status="active")
        for acc in accounts:
            try:
                self.engine.account_mgr.record_intraday_net_value(acc.id)
            except Exception as exc:
                logger.exception(
                    "[MarketListener] intraday snapshot failed for account=%s: %s",
                    acc.id, exc,
                )
        self._last_intraday_ts = now
        logger.info(
            "[MarketListener] intraday net-value snapshot: accounts=%d",
            len(accounts),
        )

    def _maybe_log_tick_summary(self) -> None:
        """Throttled per-tick observability log (data-source health + strategy output).

        Logs at most once per 60s so the listener stays observable on the
        console without spamming every tick. Picks up the latest ``_tick_stats``
        (prices fetched / total codes, codes evaluated, signals produced), which
        is enough to tell apart "data source down" from "strategies not firing".
        """
        now = time.time()
        if now - self._last_tick_summary_ts < 60.0:
            return
        self._last_tick_summary_ts = now
        s = self._tick_stats
        missing = int(s["codes_total"] - s["prices_fetched"])
        if s["prices_fetched"] <= 0:
            logger.warning(
                "[MarketListener] %s tick: prices 0/%d (行情源未通) "
                "evaluated=%d signals=%d",
                s["market"], s["codes_total"], s["evaluated"], s["signals"],
            )
        elif missing > 0:
            logger.info(
                "[MarketListener] %s tick: prices %d/%d (missing %d) "
                "evaluated=%d signals=%d",
                s["market"], s["prices_fetched"], s["codes_total"], missing,
                s["evaluated"], s["signals"],
            )
        else:
            logger.info(
                "[MarketListener] %s tick: prices %d/%d OK "
                "evaluated=%d signals=%d",
                s["market"], s["prices_fetched"], s["codes_total"],
                s["evaluated"], s["signals"],
            )

    def _tick_market(self, market: str) -> None:
        """Run one tick for a single open market."""
        # T-005: Latency tracking for the full tick cycle.
        from src.utils.latency_tracker import LatencySpan
        import uuid
        span = LatencySpan("tick_market", str(uuid.uuid4())[:8])

        codes = self._codes_for_market(market)
        if not codes:
            return

        latest_prices = self._fetch_latest_prices(codes)
        span.mark("data_fetch", codes=len(latest_prices))
        self._tick_stats.update(
            market=market,
            prices_fetched=len(latest_prices),
            codes_total=len(codes),
            evaluated=0,
            signals=0,
            ts=time.time(),
        )
        if not latest_prices:
            logger.debug("[MarketListener] %s: no prices fetched", market)
            self._maybe_log_tick_summary()
            return

        # 1) Consume AI analysis signals first (before rule-based strategies).
        self._consume_ai_signals(latest_prices)

        # 2) Match pending limit orders.
        matched = self.engine.match_pending_orders(latest_prices)
        for r in matched:
            logger.info(
                "[MarketListener] order matched: %s %s qty=%s price=%s",
                r.side, r.code, r.fill_quantity, r.fill_price,
            )

        # 3) Check stop-loss / take-profit.
        sl_tp = self.engine.check_stop_loss_take_profit(
            latest_prices, account_id=self.config.account_id
        )
        for r in sl_tp:
            logger.info(
                "[MarketListener] SL/TP triggered: %s %s -> %s",
                r.side, r.code, r.status,
            )

        # 3a) P1-A: Dynamic trailing stop-loss for profitable positions.
        self._check_dynamic_sltp(market, latest_prices)

        # 3b) P0-C: Periodic intraday position review via AgentRiskReviewer.
        self._maybe_review_open_positions(market, latest_prices)

        # 3c) T-011: Extreme market — check and gate buy signals before eval.
        em = getattr(self, "_extreme_market_response", None)
        if em is not None:
            em.auto_resume()
            now_active = bool(em.is_active())
            if now_active and not self._extreme_market_was_active:
                self._emit("extreme_market_activated", reason="extreme market activated")
            elif not now_active and self._extreme_market_was_active:
                self._emit("extreme_market_deactivated", reason="extreme market deactivated")
            self._extreme_market_was_active = now_active
            if now_active and em.force_hold_buy():
                logger.debug("[MarketListener] Extreme market: holding buy signals")
            # If market orders are disabled, the engine's RMS/Oms will enforce.

        # 4) Evaluate strategy rules.
        if self.config.enable_strategies and self.strategies:
            self._evaluate_strategies(codes, latest_prices, market)
        span.mark("signal_calc")
        self._maybe_log_tick_summary()

        # T-008: RiskDaemon — per-tick VaR + liquidity + market anomaly check.
        if self._risk_daemon is not None:
            try:
                account = self.engine.account_mgr.snapshot(self.config.account_id)
                positions = self.engine.position_mgr.list_positions(self.config.account_id)
                alerts = self._risk_daemon.tick(account, positions, latest_prices)
                for alert in alerts:
                    logger.warning(
                        "[MarketListener] RiskDaemon alert: type=%s detail=%s",
                        getattr(alert, "alert_type", "?"),
                        getattr(alert, "detail", alert),
                    )
                    self._emit_risk_alert(alert)
                    # VaR breach → feed back into circuit breaker.
                    if (hasattr(alert, "alert_type")
                            and getattr(alert, "alert_type", None) == "var_breach"
                            and self.engine.circuit_breaker is not None):
                        detail = getattr(alert, "detail", None)
                        current_var = getattr(detail, "var_95_pct", None) if detail else None
                        self.engine.circuit_breaker.evaluate(
                            current_pnl=account.total_assets - account.initial_capital,
                            initial_capital=account.initial_capital,
                            current_var=current_var,
                        )
            except Exception:
                logger.exception("[MarketListener] RiskDaemon tick failed")
        span.mark("risk_check")

        # T-005: Record tick latency; warn if > 1000ms.
        result = span.finish()
        if result["total_ms"] > 1000:
            logger.warning("[MarketListener] Slow tick: %.1fms steps=%s",
                           result["total_ms"], result["steps"])
        self._record_tick_latency(result)

        # 5) P1-C: PM agent decision now runs via AISignalWorker (T20)
        # — decoupled from the rule-engine tick to avoid blocking.
        # self._maybe_trigger_pm_decision(market)

    def _record_tick_latency(self, span_result: Dict[str, Any]) -> None:
        """Record a finished tick latency span into the aggregator (T-005).

        Normalizes the raw span ``steps`` (event-name keyed) into the
        document contract's four phases. Safe no-op when no aggregator is
        wired.
        """
        tracker = self._latency_tracker
        if tracker is None:
            return
        steps = span_result.get("steps", {})
        phases = {
            "data_fetch": float(steps.get("data_fetch", 0.0)),
            "signal_calc": float(steps.get("signal_calc", 0.0)),
            "risk_check": float(steps.get("risk_check", 0.0)),
            "order_execute": float(steps.get("tick_market.end", 0.0)),
        }
        try:
            tracker.record(
                {
                    "operation": "tick_market",
                    "total_ms": float(span_result.get("total_ms", 0.0)),
                    "steps": phases,
                    "trace_id": span_result.get("trace_id"),
                }
            )
        except Exception:
            logger.debug("[MarketListener] failed to record tick latency", exc_info=True)

    def _emit(self, event_type: str, **fields: Any) -> None:
        """Emit a paper-trading trade event (best-effort, never raises)."""
        try:
            from paper_trading.events import emit_trade_event

            emit_trade_event(event_type, **fields)
        except Exception:
            logger.debug("[MarketListener] event emission failed", exc_info=True)

    def _emit_risk_alert(self, alert: Any) -> None:
        """Convert a RiskDaemon ``RiskAlert`` to a frontend risk-alert message.

        The event stream ``WS /ws/events`` carries two message shapes; risk
        alerts are discriminated by ``alertType`` and consumed by
        ``RiskAlertToast`` (pending-api §3 type B).
        """
        try:
            from paper_trading.events import emit_risk_alert
            from paper_trading.risk_daemon import RiskAlertType

            alert_type = getattr(alert, "alert_type", None)
            atype = alert_type.value if hasattr(alert_type, "value") else str(alert_type)
            detail_obj = getattr(alert, "detail", None)

            if atype == RiskAlertType.VAR_BREACH.value:
                message = "组合 VaR 超过阈值"
                var_pct = getattr(detail_obj, "var_pct_of_capital", None)
                detail = f"VaR 占资金: {var_pct:.2f}%" if var_pct is not None else None
                level = "danger"
            elif atype == RiskAlertType.LIQUIDITY_WARNING.value:
                code = str(getattr(detail_obj, "code", "") or "")
                days = getattr(detail_obj, "days_to_liquidate", None)
                message = "流动性不足警告"
                detail = f"{code} 清仓需 {days:.1f} 天" if code and days is not None else None
                level = "warning"
            elif atype == RiskAlertType.MARKET_ANOMALY.value:
                ratio = getattr(detail_obj, "ratio", None)
                message = "市场异常"
                detail = f"波动率比: {ratio:.2f}x" if ratio is not None else None
                level = "danger"
            else:
                return

            ts = getattr(alert, "timestamp", None)
            emit_risk_alert(
                atype,
                message=message,
                detail=detail,
                level=level,
                timestamp=ts.isoformat() if ts else None,
            )
        except Exception:
            logger.debug("[MarketListener] risk alert emission failed", exc_info=True)

    def _consume_ai_signals(self, latest_prices: Dict[str, float]) -> None:
        """Consume AI-generated signals from the shared queue and submit them to the trading engine.

        This bridges the main analysis pipeline with the paper trading system.
        """
        from src.paper_trading_signal_queue import get_signal_queue, AIAnalysisSignal
        from paper_trading.strategies import Signal as V2Signal

        signal_q = get_signal_queue()
        if signal_q is None or signal_q.empty():
            return

        cfg = get_config()
        # Only consume AI signals if enabled and we have relevant watched codes
        if not getattr(cfg, "paper_trading_enable_ai_signal_source", False):
            return

        watched_codes_set = set(self.config.watched_codes)
        for ai_signal in signal_q.pop_all():
            # Filter by watched codes
            if ai_signal.code not in watched_codes_set:
                continue

            # Skip if confidence threshold not met
            if ai_signal.confidence < cfg.paper_trading_ai_signal_min_confidence:
                continue

            # Convert to internal Signal type
            signal = V2Signal(
                side=ai_signal.side,
                code=ai_signal.code,
                name=ai_signal.name,
                strategy_name=ai_signal.strategy_name,
                rule_name="ai_analysis_signal",
                trigger_price=ai_signal.trigger_price,
                suggested_quantity=ai_signal.suggested_quantity,
                reason=f"AI分析置信度={ai_signal.confidence:.2f}: {ai_signal.reason}",
            )

            # Submit to engine
            try:
                result = self.engine.submit_signal(
                    account_id=self.config.account_id,
                    signal=signal,
                )
                logger.info(
                    "[MarketListener] AI signal submitted: %s %s (confidence=%.2f) -> %s",
                    ai_signal.side, ai_signal.code, ai_signal.confidence, result.status,
                )
                # 回写来源 PM 决策: status + signal_id (审计闭环)
                if ai_signal.decision_id is not None:
                    try:
                        from src.storage import PaperDecision
                        from sqlalchemy import select
                        with self.engine.db.session_scope() as session:
                            row = session.execute(
                                select(PaperDecision).where(
                                    PaperDecision.id == ai_signal.decision_id
                                )
                            ).scalar_one_or_none()
                            if row is not None and row.status == "pending":
                                row.status = (
                                    "executed" if result.status == "executed" else "rejected"
                                )
                                row.signal_id = result.signal_id
                        logger.info(
                            "[MarketListener] AI decision %s status backfilled -> %s (signal_id=%s)",
                            ai_signal.decision_id, row.status if row is not None else "?",
                            result.signal_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[MarketListener] AI decision backfill failed: %s", exc
                        )
            except Exception as exc:
                logger.warning("[MarketListener] Failed to submit AI signal: %s", exc)

    # ------------------------------------------------------------------
    # P1-A / P0-C: Dynamic SL/TP and intraday position review
    # ------------------------------------------------------------------

    def _check_dynamic_sltp(
        self,
        market: str,
        latest_prices: Dict[str, float],
    ) -> None:
        """Check and adjust stop-loss upward for profitable positions (P1-A trailing stop).

        For each position with profit_ratio >= threshold, recompute SL via
        SLTPCalculator and raise SL if the new value is higher. Only moves
        SL up, never down. Skips positions without an existing stop_loss.
        """
        if not getattr(self.config, "enable_dynamic_sltp", True):
            return
        threshold = getattr(self.config, "sltp_dynamic_threshold_pct", 20.0) / 100.0
        if threshold <= 0:
            return
        try:
            from paper_trading.sltp_calculator import build_sltp_calculator
            calculator = build_sltp_calculator(getattr(self, "data_provider", None))
        except Exception as exc:
            logger.warning("SLTP calculator unavailable for dynamic check: %s", exc)
            return
        try:
            account_id = self.config.account_id
            position_mgr = self.engine.position_mgr
            positions = position_mgr.list_positions(account_id)
        except Exception as exc:
            logger.warning("Failed to list positions for dynamic SLTP: %s", exc)
            return
        for pos in positions or []:
            code = getattr(pos, "code", None)
            if not code or code not in latest_prices:
                continue
            current_sl = getattr(pos, "stop_loss", None)
            if not current_sl or current_sl <= 0:
                continue
            avg_cost = getattr(pos, "avg_cost", None) or getattr(pos, "average_cost", None)
            if not avg_cost or avg_cost <= 0:
                continue
            latest = latest_prices[code]
            if latest <= 0:
                continue
            profit_ratio = (latest - avg_cost) / avg_cost
            if profit_ratio < threshold:
                continue
            try:
                result = calculator.calculate(code, current_price=latest, avg_cost=avg_cost)
                new_sl = getattr(result, "stop_loss", None)
                if not new_sl or new_sl <= current_sl:
                    continue
                position_mgr.update_stop_loss_take_profit(
                    account_id=account_id,
                    code=code,
                    stop_loss=new_sl,
                )
                logger.info(
                    "Dynamic SLTP raised: code=%s old_sl=%.4f new_sl=%.4f profit=%.2f%%",
                    code, current_sl, new_sl, profit_ratio * 100,
                )
            except Exception as exc:
                logger.warning("Dynamic SLTP calc failed for code=%s: %s", code, exc)

    def _maybe_review_open_positions(self, market: str, latest_prices: Dict[str, float]) -> None:
        """Periodically review open positions via AgentRiskReviewer (P0-C).

        Fault-tolerant: failures are logged and never break the tick loop.
        """
        if not getattr(self.config, "enable_position_review", False):
            return
        reviewer = getattr(self.engine, "agent_reviewer", None)
        if reviewer is None:
            return
        now = ExchangeClock.now(market)
        last = getattr(self, "_last_position_review_ts", None)
        interval = getattr(self.config, "position_review_interval_seconds", 1800.0)
        if last is not None and (now - last).total_seconds() < interval:
            return
        self._last_position_review_ts = now
        try:
            account_id = self.config.account_id
            position_mgr = self.engine.position_mgr
            positions = position_mgr.list_positions(account_id)
        except Exception as exc:
            logger.warning("Position review list failed: %s", exc)
            return
        for pos in positions or []:
            code = getattr(pos, "code", None)
            if not code or code not in latest_prices:
                continue
            try:
                account_snap = self.engine.account_mgr.snapshot(account_id)
                from paper_trading.strategies import Signal as V2Signal
                review_signal = V2Signal(
                    side="hold",
                    code=code,
                    strategy_name="position_review",
                    rule_name="intraday_check",
                    trigger_price=latest_prices[code],
                    suggested_quantity=0.0,
                    reason="intraday position review",
                )
                verdict = reviewer.review_signal(
                    signal=review_signal,
                    account_snapshot=account_snap,
                    position=pos,
                )
                self.engine._maybe_trigger_order_action(account_id, verdict, review_signal)
            except Exception as exc:
                logger.warning("Position review failed for code=%s: %s", code, exc)

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
        """Fetch realtime quotes and return {code: latest_price}.

        When quote_cache is configured (② T12 integration), fresh cached
        quotes are returned directly without hitting the data fetcher.
        """
        out: Dict[str, float] = {}

        # ② Try cache first (SharedQuoteCache integration).
        cache = self._quote_cache
        missing: List[str] = []
        for code in codes:
            if cache is not None:
                cached = cache.get(code)
                if cached is not None:
                    out[code] = cached.price
                    continue
            missing.append(code)

        if not missing:
            return out

        # Use bulk prefetch when >=5 codes (populates cache efficiently).
        try:
            if len(missing) >= 5 and hasattr(self.fetcher, "prefetch_realtime_quotes"):
                self.fetcher.prefetch_realtime_quotes(missing)
        except Exception as exc:
            logger.debug("[MarketListener] prefetch failed: %s", exc)

        for code in missing:
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
                # ② Write back to cache.
                if cache is not None:
                    from paper_trading.quote_cache import CachedQuote
                    cache.update(code, CachedQuote(
                        price=float(price),
                        volume=float(getattr(quote, "volume", 0) or 0),
                        change_pct=float(getattr(quote, "change_pct", 0) or 0),
                        high=float(getattr(quote, "high", 0) or 0),
                        low=float(getattr(quote, "low", 0) or 0),
                        open=float(getattr(quote, "open", 0) or 0),
                        pre_close=float(getattr(quote, "pre_close", 0) or 0),
                        timestamp=getattr(quote, "timestamp", ExchangeClock.now("cn")),
                        source=f"poll_{getattr(quote, 'fetcher_name', 'unknown')}",
                    ))
        return out

    def _evaluate_strategies(
        self,
        codes: List[str],
        latest_prices: Dict[str, float],
        market: str,
    ) -> None:
        """Evaluate all strategies for each code; submit signals to engine.

        When signal_fusion is configured (④ T3 integration), signals from
        multiple strategies are fused per-code before submission.
        """
        fusion = self._signal_fusion
        evaluated = 0  # codes actually evaluated by at least one strategy
        produced = 0   # signals passed dedupe and were submitted to the engine
        for code in codes:
            price = latest_prices.get(code)
            if price is None or price <= 0:
                continue

            # Collect signals from all strategies for this code.
            code_signals = []
            for strategy in self.strategies:
                timeframes = strategy.timeframes or self.config.strategy_timeframes or ["1d"]
                data = self._get_strategy_data(code, timeframes)
                if data is None:
                    continue
                evaluated += 1

                try:
                    if len(timeframes) == 1:
                        signal = self.rule_engine.evaluate(
                            strategy=strategy, df=data[timeframes[0]], code=code,
                        )
                    else:
                        signal = self.rule_engine.evaluate_multi_timeframe(
                            strategy=strategy, data=data, code=code,
                        )
                except Exception as exc:
                    logger.warning(
                        "[MarketListener] evaluate failed: %s/%s: %s",
                        code, strategy.name, exc,
                    )
                    continue
                if signal.side not in ("buy", "sell"):
                    continue
                code_signals.append(signal)

            if not code_signals:
                continue

            # ④ Fuse signals if fusion engine is configured.
            targets: list = code_signals
            if fusion is not None:
                fused = fusion.fuse(code, code_signals)
                if fused is None:
                    continue  # no consensus → skip this code
                # Wrap fused result as a signal for engine submission.
                from paper_trading.strategies import Signal as S
                targets = [S(
                    side=fused.side, code=fused.code,
                    name=",".join(fused.supporting_strategies),
                    strategy_name="fusion",
                    rule_name=fused.method.value,
                    trigger_price=price,
                    suggested_quantity=None,
                    reason=f"fused({','.join(fused.supporting_strategies)}): conf={fused.confidence:.2f}",
                )]

            for signal in targets:
                if not self._should_emit_signal(signal, market):
                    continue
                produced += 1
                try:
                    # ── L4 元认知信号闸门：偏差检测 → 调节仓位/过滤 ──
                    # 检测过度自信/确认偏差/锚定等，调节后信号提交。
                    l4 = getattr(self, "_l4_gate", None)
                    if l4 is not None:
                        verdict = l4.evaluate(
                            signal,
                            code=code,
                            price=price,
                            market=market,
                            signals_considered=len(code_signals),
                            signals_dismissed=max(0, len(targets) - len(code_signals)),
                        )
                        if not verdict.allowed:
                            logger.info(
                                "[MarketListener] L4 blocked: %s %s (%s)",
                                signal.side, code, verdict.reason,
                            )
                            continue
                        if verdict.adjusted_signal is not None:
                            signal = verdict.adjusted_signal
                        if verdict.biases:
                            logger.info(
                                "[MarketListener] L4 adjusted %s %s qty x%.1f: %s",
                                signal.side, code, verdict.quantity_factor,
                                verdict.biases,
                            )

                    result = self.engine.submit_signal(
                        account_id=self.config.account_id,
                        signal=signal,
                    )
                    self._record_signal(signal)
                    logger.info(
                        "[MarketListener] signal submitted: %s %s (strat=%s) "
                        "-> status=%s reason=%s",
                        signal.side, code, signal.strategy_name,
                        result.status, result.reason,
                    )
                except Exception as exc:
                    logger.exception(
                        "[MarketListener] submit_signal failed: %s %s: %s",
                        signal.side, code, exc,
                    )

        # Publish per-tick observability counters for the throttled summary.
        self._tick_stats["evaluated"] += evaluated
        self._tick_stats["signals"] += produced

    def _get_strategy_data(
        self,
        code: str,
        timeframes: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Return a {timeframe: DataFrame} dict for ``code``.

        Daily bars are fetched once and cached; higher timeframes are
        resampled from the daily bars. Returns None if any required
        timeframe cannot be produced.
        """
        daily_df = self._get_daily_df(code)
        if daily_df is None or len(daily_df) < 2:
            return None

        out: Dict[str, Any] = {}
        for tf in timeframes:
            tf_clean = str(tf).strip().lower()
            if tf_clean in ("1d", "d", "day", "daily"):
                out[tf] = daily_df
                continue
            df_tf = _resample_to_timeframe(daily_df, tf_clean)
            if df_tf is None or len(df_tf) < 2:
                logger.debug(
                    "[MarketListener] %s: timeframe %s unavailable", code, tf
                )
                return None
            out[tf] = df_tf
        return out

    def _should_emit_signal(self, signal: Signal, market: str = "cn") -> bool:
        """Dedupe: skip if same (code, strategy, side) was emitted recently."""
        key = (signal.code, signal.strategy_name, signal.side)
        last = self._last_signal_at.get(key)
        if last is None:
            return True
        elapsed = (ExchangeClock.now(market) - last).total_seconds()
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
        self._last_signal_at[key] = ExchangeClock.now("cn")

    def _get_daily_df(self, code: str) -> Any:
        """Return the daily-bar DataFrame for ``code``, with caching.

        T-023: Checks local SQLite store first; falls back to remote fetch
        and upserts the result.
        """
        now = ExchangeClock.now("cn")
        cached = self._daily_df_cache.get(code)
        if cached is not None:
            fetched_at, df = cached
            age = (now - fetched_at).total_seconds()
            if age < self.config.daily_df_cache_seconds:
                return df

        # T-023: Try local store first.
        local_store = getattr(self, "_local_store", None)
        if local_store is not None:
            try:
                from datetime import timedelta
                start = (now - timedelta(days=365)).date()
                end = now.date()
                df_local = local_store.get(code, start, end)
                if df_local is not None and len(df_local) >= 2:
                    self._daily_df_cache[code] = (now, df_local)
                    return df_local
            except Exception as exc:
                logger.debug("[MarketListener] local_store read failed for %s: %s", code, exc)

        try:
            # Use unified multi-source data fetcher. Prefer get_daily_historical
            # (MultiSourceDataFetcher), fall back to get_daily_data (stub/test
            # fetchers and older DataFetcherManager).
            if hasattr(self.fetcher, "get_daily_historical"):
                df = self.fetcher.get_daily_historical(code, days=60)
            elif hasattr(self.fetcher, "get_daily_data"):
                df = self.fetcher.get_daily_data(code, days=60)
            else:
                logger.debug(
                    "[MarketListener] fetcher has no get_daily_historical/get_daily_data: %s",
                    type(self.fetcher).__name__,
                )
                return None
            # DataFetcherManager / MultiSourceDataFetcher return
            # (DataFrame, source_name) tuples — unwrap to a plain DataFrame.
            if isinstance(df, tuple):
                df = df[0] if df else None
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

        # T-023: Upsert into local store for next restart.
        if local_store is not None and df is not None and not df.empty:
            try:
                local_store.upsert(code, df, source="fetcher")
            except Exception as exc:
                logger.debug("[MarketListener] local_store write failed for %s: %s", code, exc)

        return df

    # ------------------------------------------------------------------
    # Daily settlement
    # ------------------------------------------------------------------

    def _maybe_daily_settle(self, market: str) -> None:
        """Run daily_settle once per (account, trading day) after session close.

        Settles ALL active paper accounts (not just the listener's bound
        account) so every account accumulates its own net-value curve; a
        single account failure never blocks the others.
        """
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
            now_local = ExchangeClock.now(market)
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

        # Fetch final prices for accurate mark-to-market. Aggregate codes
        # across ALL active paper accounts so a single price fetch covers
        # every account's positions (net-value curve is per-account, not
        # just the listener's bound account).
        accounts = self.engine.account_mgr.list_accounts(status="active")
        codes = set(self._codes_for_market(market))
        for acc in accounts:
            codes.update(
                p["code"] for p in self.engine.position_mgr.list_positions(acc.id)
            )
        codes = sorted(codes)
        latest_prices = self._fetch_latest_prices(codes) if codes else {}

        settled_any = False
        for acc in accounts:
            acc_market = (getattr(acc, "market", None) or "cn").lower()
            try:
                if acc_market == market:
                    # Same-market account: full settle (T+1 roll + net value).
                    result = self.engine.daily_settle(
                        account_id=acc.id,
                        target_date=today,
                        latest_prices=latest_prices or None,
                    )
                else:
                    # Other-market account: valuation only — mark positions to
                    # the latest price and record the net-value point. T+1 roll
                    # is market-specific and must not run early against a
                    # market that has not closed yet (e.g. US still trading at
                    # CN close).
                    if latest_prices:
                        for code, price in latest_prices.items():
                            self.engine.position_mgr.update_last_price(
                                acc.id, code, price
                            )
                    self.engine.account_mgr.record_daily_net_value(
                        acc.id, target_date=today
                    )
                    result = {"positions_rolled": 0, "date": today.isoformat()}
                settled_any = True
                logger.info(
                    "[MarketListener] daily_settle complete: account=%s rolled=%s date=%s market=%s",
                    acc.id,
                    result.get("positions_rolled"),
                    result.get("date"),
                    acc_market,
                )
            except Exception as exc:
                logger.exception(
                    "[MarketListener] daily_settle failed for account=%s: %s",
                    acc.id, exc,
                )
                continue

        if not settled_any:
            return
        self._last_settle_date = today

        # P1-C: post-settle hooks — daily reflection + next-day battle plan.
        # These run once per day, after daily_settle succeeds. They are
        # independently fault-tolerant: a failure in reflection does not
        # block battle plan generation (and vice versa).
        if self.config.enable_daily_reflection:
            self._maybe_run_daily_reflection(today)
        if self.config.enable_battle_plan:
            self._maybe_generate_battle_plan(today)
        # P2-A: daily report generation after settle hooks.
        if self.config.enable_daily_report:
            self._maybe_generate_daily_report(today)

        # T-013: Feature pipeline — recompute after each daily settle.
        self._maybe_run_feature_pipeline(today)

        # T-011: Extreme market auto-resume — check every settle cycle.
        self._maybe_auto_resume_extreme_market()

        # T-010: Drift detector — record daily PnL after settle.
        self._maybe_record_drift_pnl(today)

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
        now = ExchangeClock.now("cn")
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

    # ------------------------------------------------------------------
    # P2-A: Daily report generation
    # ------------------------------------------------------------------

    def _maybe_generate_daily_report(self, today: str) -> None:
        """Generate and optionally push a daily report (P2-A)."""
        if not getattr(self.config, "enable_daily_report", False):
            return
        content_generator = getattr(self, "content_generator", None)
        if content_generator is None:
            return
        try:
            result = content_generator.generate_daily_report(save=True)
            notifier = getattr(self, "notifier", None)
            if notifier is not None and result is not None:
                try:
                    notifier.push_daily_summary(result)
                except Exception as exc:
                    logger.warning("Daily report push failed: %s", exc)
            logger.info("Daily report generated: %s", getattr(result, "report_path", None))
        except Exception as exc:
            logger.warning("Daily report generation failed: %s", exc)

    # ------------------------------------------------------------------
    # T-013: Feature pipeline post-settle trigger
    # ------------------------------------------------------------------

    def _maybe_run_feature_pipeline(self, today: Optional[date] = None) -> None:
        """Run feature computation after daily settle (T-013).

        Activated when ``feature_pipeline`` is configured on the listener.
        Fault-tolerant: failures are logged and never break the settle cycle.
        """
        pipeline = getattr(self, "_feature_pipeline", None)
        if pipeline is None:
            return
        try:
            settle_date = today or date.today()
            codes = list(self._daily_df_cache.keys()) or self._codes_for_market("cn")
            if not codes:
                return
            daily_data: Dict[str, Any] = {}
            for code in codes:
                df_wrapper = self._daily_df_cache.get(code)
                if df_wrapper is not None:
                    _, df = df_wrapper
                    if df is not None and not df.empty:
                        daily_data[code] = df
            if not daily_data:
                return
            features = pipeline.run(list(daily_data.keys()), daily_data)
            if features is not None and not features.empty:
                path = pipeline.save(features, settle_date)
                logger.info("Feature pipeline saved: %s rows → %s", len(features), path)
        except Exception as exc:
            logger.warning("Feature pipeline run failed: %s", exc)

    # ------------------------------------------------------------------
    # T-011: Extreme market auto-resume
    # ------------------------------------------------------------------

    def _maybe_auto_resume_extreme_market(self, auto_resume_minutes: int = 30) -> None:
        """Check and auto-resume from extreme market state (T-011)."""
        response = getattr(self, "_extreme_market_response", None)
        if response is None:
            return
        try:
            if response.auto_resume(auto_resume_minutes):
                logger.info("ExtremeMarketResponse auto-resumed after cooling period")
        except Exception as exc:
            logger.warning("Extreme market auto-resume check failed: %s", exc)

    # ------------------------------------------------------------------
    # T-010: Drift detector — daily PnL record
    # ------------------------------------------------------------------

    def _maybe_record_drift_pnl(self, today: Optional[date] = None) -> None:
        """Record each strategy's daily PnL into the drift detector (T-010).

        Reads today's trades from the engine and computes per-strategy PnL.
        """
        drift = getattr(self, "_drift_detector", None)
        fusion = getattr(self, "_signal_fusion", None)
        if drift is None:
            return
        try:
            account = self.engine.account_mgr.snapshot(self.config.account_id)
            today_pnl = getattr(account, "total_assets", account.cash) - getattr(account, "initial_capital", account.cash)
            # Record for all active strategies — drift detector aggregates per name.
            for strategy in self.strategies:
                drift.record_daily_pnl(strategy.name, today_pnl)
            # Feed drift reports into signal fusion for weight adjustment.
            if fusion is not None and hasattr(fusion, "update_weights_from_drift"):
                reports = {s.name: drift.check(s.name) for s in self.strategies}
                fusion.update_weights_from_drift(reports)
        except Exception as exc:
            logger.warning("Drift PnL record failed: %s", exc)


# ============================================================
# Factory
# ============================================================

def build_default_listener(
    config: Any,
    account_id: int,
    db_manager: Optional[Any] = None,
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
    content_generator: Optional[Any] = None,
    notifier: Optional[Any] = None,
    enable_dynamic_sltp: Optional[bool] = None,
    sltp_dynamic_threshold_pct: Optional[float] = None,
    enable_position_review: Optional[bool] = None,
    position_review_interval_seconds: Optional[float] = None,
    enable_daily_report: Optional[bool] = None,
    circuit_breaker: Optional[Any] = None,  # T-003: circuit breaker injection
    risk_daemon: Optional[Any] = None,  # T-008: risk daemon injection
    signal_fusion: Optional[Any] = None,  # T-009: signal fusion injection
    quote_cache: Optional[Any] = None,  # T-007: quote cache injection
    latency_tracker: Optional[Any] = None,  # T-005: tick latency aggregation
    feature_pipeline: Optional[Any] = None,  # ⑥ T13: feature pipeline injection
    drift_detector: Optional[Any] = None,  # ⑧ T10: drift detector injection
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
        content_generator: P2-A: Pre-built daily-report content generator.
            When set and ``enable_daily_report`` is True, a daily report is
            generated after ``daily_settle``.
        notifier: P2-A: Pre-built notifier. When set, the daily report is
            pushed via ``notifier.push_daily_summary(result)``.
        enable_dynamic_sltp: P1-A: Override dynamic trailing stop-loss. When
            None, falls back to ``config.paper_trading_enable_dynamic_sltp``
            if present, else ``MarketListenerConfig.enable_dynamic_sltp``.
        sltp_dynamic_threshold_pct: P1-A: Override the profit-ratio threshold
            (in percent) above which trailing SL is recomputed. When None,
            falls back to ``config.paper_trading_sltp_dynamic_threshold_pct``
            if present, else ``MarketListenerConfig.sltp_dynamic_threshold_pct``.
        enable_position_review: P0-C: Override intraday position review. When
            None, falls back to ``config.paper_trading_enable_position_review``
            if present, else ``MarketListenerConfig.enable_position_review``.
        position_review_interval_seconds: P0-C: Override the review cadence.
            When None, falls back to
            ``config.paper_trading_position_review_interval_seconds`` if
            present, else ``MarketListenerConfig.position_review_interval_seconds``.
        enable_daily_report: P2-A: Override daily report generation. When
            None, falls back to ``config.paper_trading_enable_daily_report``
            if present, else ``MarketListenerConfig.enable_daily_report``.
    """
    # Lazy imports to keep module import cheap.
    from data_provider import DataFetcherManager
    from paper_trading.agent_risk import AgentRiskReviewer
    from paper_trading import get_watched_codes as get_paper_traded_watchcodes

    if watched_codes is None:
        # Use sync logic: respect paper_trading_sync_stock_list config and stock_list
        watched_codes = get_paper_traded_watchcodes(account_id)

    if strategy_dir is None:
        # Prefer config-driven strategy dir (PAPER_TRADING_STRATEGY_DIR),
        # fall back to the default location under paper_trading/strategies.
        from src.config import get_config as _get_config
        _cfg = _get_config()
        cfg_strategy_dir = getattr(_cfg, "paper_trading_strategy_dir", None)
        if cfg_strategy_dir:
            strategy_dir = str(cfg_strategy_dir)
        else:
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
        from src.data_fetcher import MultiSourceDataFetcher
        from src.config import get_config

        cfg = get_config()
        # Parse realtime_source_priority from config (comma-separated)
        priority_list = [p.strip() for p in getattr(cfg, 'realtime_source_priority', '').split(',') if p.strip()]
        if not priority_list:
            # Use default order
            priority_list = None

        data_fetcher = MultiSourceDataFetcher(source_priority=priority_list, cache_ttl=30)

    if engine is None:
        # T-003: Build CircuitBreaker when not supplied — defaults to env-configurable.
        if circuit_breaker is None:
            from paper_trading.circuit_breaker import BreakerConfig, CircuitBreaker
            breaker_cfg = BreakerConfig(
                soft_threshold_pct=float(getattr(config, "circuit_breaker_soft_threshold_pct", 3.0)),
                hard_threshold_pct=float(getattr(config, "circuit_breaker_hard_threshold_pct", 5.0)),
                liquidation_threshold_pct=float(getattr(config, "circuit_breaker_liquidation_threshold_pct", 8.0)),
            )
            circuit_breaker = CircuitBreaker(config=breaker_cfg, account_id=account_id)
        engine = TradingEngine(
            db_manager=db_manager,
            agent_reviewer=agent_reviewer,
            circuit_breaker=circuit_breaker,
            on_trade_executed=on_trade_executed,
            on_signal_rejected=on_signal_rejected,
        )

    # P1-A / P0-C / P2-A: resolve new config fields with config fallback.
    if enable_dynamic_sltp is None:
        enable_dynamic_sltp = bool(
            getattr(config, "paper_trading_enable_dynamic_sltp", True)
        )
    if sltp_dynamic_threshold_pct is None:
        sltp_dynamic_threshold_pct = float(
            getattr(config, "paper_trading_sltp_dynamic_threshold_pct", 20.0)
        )
    if enable_position_review is None:
        enable_position_review = bool(
            getattr(config, "paper_trading_enable_position_review", False)
        )
    if position_review_interval_seconds is None:
        position_review_interval_seconds = float(
            getattr(config, "paper_trading_position_review_interval_seconds", 1800.0)
        )
    if enable_daily_report is None:
        enable_daily_report = bool(
            getattr(config, "paper_trading_enable_daily_report", False)
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
        strategy_timeframes=list(
            getattr(config, "paper_trading_strategy_timeframes", ["1d"]) or ["1d"]
        ),
        enable_dynamic_sltp=enable_dynamic_sltp,
        sltp_dynamic_threshold_pct=sltp_dynamic_threshold_pct,
        enable_position_review=enable_position_review,
        position_review_interval_seconds=position_review_interval_seconds,
        enable_daily_report=enable_daily_report,
    )

    # 默认装配 SignalFusionEngine（若未注入）+ 从 DB 加载持久化权重
    if signal_fusion is None:
        try:
            from paper_trading.signal_fusion import SignalFusionEngine, FusionMethod

            signal_fusion = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
            try:
                from paper_trading.strategy_backtest_service import load_fusion_weights

                persisted = load_fusion_weights()
                if persisted:
                    signal_fusion.set_weights(persisted)
                    logger.info(
                        "[build_default_listener] fusion weights loaded from DB: %s",
                        persisted,
                    )
                else:
                    logger.info(
                        "[build_default_listener] no persisted fusion weights; using defaults"
                    )
            except Exception as w_exc:  # noqa: BLE001 — 权重加载失败用默认
                logger.warning("load fusion weights failed (defaults): %s", w_exc)
        except Exception as exc:  # noqa: BLE001 — 融合不可用则禁用
            logger.warning("SignalFusionEngine init failed (fusion disabled): %s", exc)
            signal_fusion = None

    return MarketListener(
        engine=engine,
        data_fetcher=data_fetcher,
        strategies=strategies,
        config=listener_config,
        pm_agent=pm_agent,
        reflection_engine=reflection_engine,
        battle_plan_generator=battle_plan_generator,
        content_generator=content_generator,
        notifier=notifier,
        quote_cache=quote_cache,
        signal_fusion=signal_fusion,
        risk_daemon=risk_daemon,
        latency_tracker=latency_tracker,
        feature_pipeline=feature_pipeline,
        drift_detector=drift_detector,
    )


def _resolve_active_account_markets(db_manager: Optional[Any] = None) -> Optional[Set[str]]:
    """Collect market tags (cn/hk/us) of all active paper accounts.

    Used to derive the listener's market coverage (T-14) so hk/us accounts
    are ticked during their own sessions. Returns None on any failure.
    """
    try:
        from src.storage import get_db
        from paper_trading.account import PaperAccountManager

        db = db_manager or get_db()
        accounts = PaperAccountManager(db_manager=db).list_accounts(status="active")
        markets = {
            (getattr(a, "market", None) or "cn").lower()
            for a in accounts
        }
        return markets or None
    except Exception as exc:
        logger.warning("[build_full_listener] resolve account markets failed: %s", exc)
        return None


def _resolve_active_position_codes(db_manager: Optional[Any] = None) -> List[str]:
    """Collect codes of all open positions across active paper accounts."""
    try:
        from src.storage import get_db
        from paper_trading.account import PaperAccountManager
        from paper_trading.position import PositionManager

        db = db_manager or get_db()
        position_mgr = PositionManager(db)
        codes: List[str] = []
        for acc in PaperAccountManager(db_manager=db).list_accounts(status="active"):
            for pos in position_mgr.list_positions(acc.id):
                c = pos.get("code")
                if c:
                    codes.append(c)
        return codes
    except Exception as exc:
        logger.warning("[build_full_listener] resolve position codes failed: %s", exc)
        return []


def build_full_listener(
    config: Any,
    account_id: int,
    *,
    db_manager: Optional[Any] = None,
    watched_codes: Optional[List[str]] = None,
    markets: Optional[Set[str]] = None,
    enable_strategies: bool = True,
    enable_pm_agent: Optional[bool] = None,
    enable_daily_reflection: Optional[bool] = None,
    enable_battle_plan: Optional[bool] = None,
    tick_interval_seconds: Optional[float] = None,
    strategy_dir: Optional[str] = None,
    quote_cache: Optional[Any] = None,
    latency_tracker: Optional[Any] = None,
    pm_decision_interval_seconds: Optional[float] = None,
    on_trade_executed: Optional[Any] = None,
    on_signal_rejected: Optional[Any] = None,
) -> MarketListener:
    """Build a MarketListener with the full self-learning / self-reflection
    wiring (T-08).

    This is the canonical production assembly used by BOTH ``run_listener.py``
    (supervisor / .bat launcher) and the API ``start_listener`` endpoint, so
    the two startup paths no longer drift apart.

    ``quote_cache`` / ``latency_tracker`` may be injected so the API can share
    the same singletons it uses for engine pricing and latency reporting; when
    omitted (standalone listener) fresh instances are created.

    Each optional capability is fault-tolerant: if it cannot be built (e.g.
    missing LLM credentials, broken import), that component degrades to None
    and the listener still runs rule-engine execution.
    """
    # Resolve capability flags from config unless explicitly overridden.
    if enable_pm_agent is None:
        enable_pm_agent = bool(
            getattr(config, "paper_trading_enable_pm_agent", False)
        )
    if enable_daily_reflection is None:
        enable_daily_reflection = bool(
            getattr(config, "paper_trading_listener_enable_daily_reflection", True)
        )
    if enable_battle_plan is None:
        enable_battle_plan = bool(
            getattr(config, "paper_trading_listener_enable_battle_plan", True)
        )

    # Multi-market coverage (T-14): if markets not explicitly given, derive
    # them from the active paper accounts so hk/us accounts get listener
    # coverage during their own sessions (not just cn).
    if not markets:
        markets = _resolve_active_account_markets(db_manager) or {"cn"}
        logger.info(
            "[build_full_listener] derived markets from active accounts: %s",
            sorted(markets),
        )

    # Extend watched_codes with positions held by active accounts across all
    # markets, so hk/us holdings are polled/assessed in their market sessions.
    if not watched_codes:
        from paper_trading import get_watched_codes as _get_watchcodes

        watched_codes = list(_get_watchcodes(account_id))
        try:
            pos_codes = _resolve_active_position_codes(db_manager)
            if pos_codes:
                seen = set(watched_codes)
                for c in pos_codes:
                    if c not in seen:
                        watched_codes.append(c)
                        seen.add(c)
                logger.info(
                    "[build_full_listener] watched codes extended with %d held codes -> %d total",
                    len(pos_codes), len(watched_codes),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[build_full_listener] extend watched codes failed: %s", exc)

    # Shared quote cache + tick latency aggregator (single instances).
    if quote_cache is None:
        try:
            from paper_trading.quote_cache import SharedQuoteCache

            quote_cache = SharedQuoteCache()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[build_full_listener] quote_cache unavailable: %s", exc)

    if latency_tracker is None:
        try:
            from src.utils.latency_tracker import TickLatencyAggregator

            latency_tracker = TickLatencyAggregator(window_size=100)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[build_full_listener] latency_tracker unavailable: %s", exc)

    # PM agent (self-decision) — independent of reflection/battle plan.
    pm_agent = None
    if enable_pm_agent:
        try:
            from src.agent.portfolio_manager_agent import build_portfolio_manager_agent

            pm_agent = build_portfolio_manager_agent(
                config=config, account_id=account_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[build_full_listener] PM agent unavailable: %s", exc)

    # Reflection engine (self-learning).
    reflection_engine = None
    if enable_daily_reflection:
        try:
            from paper_trading.reflection import build_reflection_engine

            reflection_engine = build_reflection_engine(
                config=config, account_id=account_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[build_full_listener] reflection engine unavailable: %s", exc
            )

    # T-10: 成交即复盘 — 默认把 on_trade_executed 接到 reflection engine
    # (调用方显式传入的回调优先)。
    if on_trade_executed is None and reflection_engine is not None:
        def _default_trade_reflection_cb(
            result: Any, trade_id: Optional[int] = None
        ) -> None:
            try:
                if trade_id is not None:
                    reflection_engine.reflect_on_trade(trade_id=trade_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[build_full_listener] post-trade reflection failed: %s", exc
                )

        on_trade_executed = _default_trade_reflection_cb

    # Battle-plan generator (self-reflection).
    battle_plan_generator = None
    if enable_battle_plan:
        try:
            from paper_trading.battle_plan import build_battle_plan_generator

            battle_plan_generator = build_battle_plan_generator(
                config=config, account_id=account_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[build_full_listener] battle plan generator unavailable: %s", exc
            )

    # Drift detector (strategy drift) + feature pipeline (T13).
    drift_detector = None
    try:
        from paper_trading.drift_detector import DriftDetector

        drift_detector = DriftDetector()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[build_full_listener] drift detector unavailable: %s", exc)

    # Signal fusion engine (multi-strategy weighted vote + persisted weights).
    signal_fusion = None
    try:
        from paper_trading.signal_fusion import FusionMethod, SignalFusionEngine

        signal_fusion = SignalFusionEngine(
            method=FusionMethod.WEIGHTED_VOTE,
            consensus_threshold=float(
                getattr(config, "signal_fusion_consensus_threshold", 0.60)
            ),
        )
        try:
            from paper_trading.strategy_backtest_service import load_fusion_weights

            persisted = load_fusion_weights()
            if persisted:
                signal_fusion.set_weights(persisted)
        except Exception as w_exc:  # noqa: BLE001
            logger.warning("load fusion weights failed (defaults): %s", w_exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[build_full_listener] signal_fusion unavailable: %s", exc)

    feature_pipeline = None
    try:
        from paper_trading.features import FeatureConfig, FeaturePipeline

        feature_pipeline = FeaturePipeline(
            [
                FeatureConfig("sma_crossover", "momentum", "sma_crossover", {"fast": 5, "slow": 20}),
                FeatureConfig("rsi", "momentum", "rsi", {"period": 14}),
                FeatureConfig("volume_spike", "volume", "volume_spike", {"multiplier": 2.0}),
                FeatureConfig("ma_alignment", "trend", "ma_alignment", {"short": 5, "long": 20}),
                FeatureConfig("bid_ask_imbalance", "market_microstructure", "bid_ask_imbalance", {}),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[build_full_listener] feature pipeline unavailable: %s", exc)

    return build_default_listener(
        config=config,
        account_id=account_id,
        db_manager=db_manager,
        watched_codes=watched_codes,
        strategy_dir=strategy_dir,
        markets=markets,
        tick_interval_seconds=tick_interval_seconds or 10.0,
        enable_strategies=enable_strategies,
        pm_agent=pm_agent,
        reflection_engine=reflection_engine,
        battle_plan_generator=battle_plan_generator,
        pm_decision_interval_seconds=pm_decision_interval_seconds,
        enable_daily_reflection=enable_daily_reflection,
        enable_battle_plan=enable_battle_plan,
        quote_cache=quote_cache,
        latency_tracker=latency_tracker,
        signal_fusion=signal_fusion,
        feature_pipeline=feature_pipeline,
        drift_detector=drift_detector,
        on_trade_executed=on_trade_executed,
        on_signal_rejected=on_signal_rejected,
    )
