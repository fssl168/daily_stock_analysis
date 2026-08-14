# -*- coding: utf-8 -*-
"""Paper trading API endpoints (P3-A).

Exposes the full paper-trading subsystem to the WebUI / external callers:

- Account: create / snapshot / net-value curve
- Orders: manual submit / cancel / modify / list
- Positions / Trades / Signals: read-only listings
- Reflection notes: list + manual daily reflection trigger
- Battle plans: generate / list / fetch / markdown
- PM decisions: trigger / list
- MarketListener: status / start / stop

State management:
- A single :class:`PaperTradingService` is attached to ``app.state`` by the
  FastAPI lifespan. It lazily builds and caches the TradingEngine, optional
  agent reviewer, reflection engine, battle-plan generator, PM agent, and
  the MarketListener.
- Read-only endpoints can also run stateless (they construct lightweight
  managers per-request). Stateful endpoints (listener start/stop, PM
  decision trigger) go through the shared service.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select

from api.deps import get_config_dep, get_database_manager
from api.v1.schemas.common import ErrorResponse
from starlette.requests import Request
from src.permissions import (
    check_paper_trading_account_access,
    require_login,
    verify_account_ownership,
    verify_ws_account_ownership,
)
from api.v1.schemas.paper_trading import (
    AccountCreateRequest,
    AccountListItem,
    AccountListResponse,
    AccountSnapshotResponse,
    AccountUpdateRequest,
    BacktestPaperComparisonRequest,
    BacktestPaperComparisonResponse,
    BatchOrderCreateRequest,
    BatchOrderResponse,
    BattlePlanGenerateRequest,
    BattlePlanItem,
    BattlePlanMarkdownResponse,
    BreakerStatusResponse,
    ConditionalOrderCreateRequest,
    ConditionalOrderItem,
    DailyBarItem,
    DailyBarsResponse,
    DailyReflectionRequest,
    DailyReportResponse,
    DrawdownItem,
    DriftReportItem,
    ExtremeMarketStatusResponse,
    FeatureRecomputeResponse,
    FeatureRowItem,
    FeatureSnapshotResponse,
    HoldingPlanItem,
    L2DepthLevel,
    L2DepthResponse,
    LatencyReportResponse,
    ListenerControlResponse,
    ListenerStartRequest,
    ListenerStatusResponse,
    NetValueCurveResponse,
    NetValuePoint,
    OrderCancelRequest,
    OrderCreateRequest,
    OrderItem,
    OrderListFilterParams,
    OrderListResponse,
    OrderModifyRequest,
    PaperTradingScenario,
    PMDecisionExecuteResponse,
    PMDecisionIgnoreRequest,
    PMDecisionItem,
    PMDecisionListResponse,
    PMDecisionTriggerRequest,
    PerformanceMetricsResponse,
    PositionItem,
    PositionListResponse,
    ReflectionListResponse,
    ReflectionNoteItem,
    RiskMetricsResponse,
    SignalItem,
    SignalListResponse,
    StrategyLifecycleItem,
    StrategyLifecycleListResponse,
    StrategyPerformanceItem,
    StrategyTransitionRequest,
    StrategyTransitionResponse,
    TradeItem,
    TradeListResponse,
    TradeResultResponse,
)
from paper_trading import (
    DEFAULT_FEE_MODEL,
    AgentRiskReviewer,
    BattlePlanGenerator,
    MarketListener,
    MarketListenerConfig,
    OrderManager,
    OrderRequest,
    OrderSide,
    OrderType,
    PaperAccountManager,
    PerformanceAnalyzer,
    PositionManager,
    ReflectionEngine,
    RiskChecker,
    SLTPCalculator,
    TradeResult,
    TradingEngine,
    build_battle_plan_generator,
    build_portfolio_manager_agent,
    build_reflection_engine,
    build_sltp_calculator,
)
from src.config import Config
from src.storage import (
    DatabaseManager,
    PaperBattlePlan,
    PaperDecision,
    PaperReflection,
    PaperSignal,
)
from paper_trading.strategies import Signal

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_login)])

# WebSocket router WITHOUT the router-level require_login dependency.
# FastAPI applies router-level dependencies to websocket routes too, and
# require_login(request: Request) fails during websocket dependency
# resolution ("missing 1 required positional argument: 'request'"), turning
# every ws handshake into a 500. The ws endpoints below authenticate via
# verify_ws_account_ownership() (cookie-based) themselves, so this is safe.
ws_router = APIRouter()


# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------


class PaperTradingService:
    """Lazy singleton holding expensive paper-trading components.

    Built once per FastAPI app (stored on ``app.state.paper_trading_service``).
    The TradingEngine and optional sub-components (agent reviewer, reflection
    engine, battle-plan generator, PM agent) are constructed lazily on first
    use so the API still boots if e.g. LLM credentials are missing.
    """

    def __init__(self, config: Config, db_manager: DatabaseManager):
        self.config = config
        self.db = db_manager
        self._engine: Optional[TradingEngine] = None
        self._account_mgr: Optional[PaperAccountManager] = None
        self._order_mgr: Optional[OrderManager] = None
        self._position_mgr: Optional[PositionManager] = None
        self._risk_checker: Optional[RiskChecker] = None
        self._performance_analyzer: Optional[PerformanceAnalyzer] = None
        self._sltp_calculator: Optional[SLTPCalculator] = None
        self._agent_reviewer: Optional[AgentRiskReviewer] = None
        self._reflection_engine: Optional[ReflectionEngine] = None
        self._battle_plan_generator: Optional[BattlePlanGenerator] = None
        self._pm_agent: Optional[Any] = None
        self._listener: Optional[MarketListener] = None
        self._data_fetcher: Optional[Any] = None
        self._tick_latency: Optional[Any] = None  # T-005: tick latency aggregator
        self._drift_detector: Optional[Any] = None  # T-010: drift detector
        self._signal_fusion: Optional[Any] = None  # T-009: signal fusion
        self._feature_pipeline: Optional[Any] = None  # T-013: feature pipeline
        self._quote_cache: Optional[Any] = None  # T-02: shared live-quote cache

    # ------------------------------------------------------------------
    # Managers (lightweight)
    # ------------------------------------------------------------------

    def account_mgr(self) -> PaperAccountManager:
        if self._account_mgr is None:
            self._account_mgr = PaperAccountManager(self.db)
        return self._account_mgr

    def order_mgr(self) -> OrderManager:
        if self._order_mgr is None:
            self._order_mgr = OrderManager(self.db)
        return self._order_mgr

    def position_mgr(self) -> PositionManager:
        if self._position_mgr is None:
            self._position_mgr = PositionManager(self.db)
        return self._position_mgr

    def risk_checker(self) -> RiskChecker:
        if self._risk_checker is None:
            from paper_trading.risk import RiskConfig

            risk_config = RiskConfig(
                max_daily_loss_pct=float(
                    getattr(self.config, "paper_trading_max_daily_loss_pct", 0.05)
                ),
            )
            self._risk_checker = RiskChecker(
                db_manager=self.db,
                account_manager=self.account_mgr(),
                position_manager=self.position_mgr(),
                fee_model=DEFAULT_FEE_MODEL,
                config=risk_config,
            )
        return self._risk_checker

    def performance_analyzer(self) -> PerformanceAnalyzer:
        if self._performance_analyzer is None:
            self._performance_analyzer = PerformanceAnalyzer(db_manager=self.db)
        return self._performance_analyzer

    def sltp_calculator(self) -> Optional[SLTPCalculator]:
        if self._sltp_calculator is None:
            try:
                self._sltp_calculator = build_sltp_calculator(
                    data_provider=self._get_data_fetcher()
                )
            except Exception as exc:
                logger.warning(
                    "[PaperTradingService] SLTPCalculator build failed: %s", exc
                )
                self._sltp_calculator = None
        return self._sltp_calculator

    # ------------------------------------------------------------------
    # Trading engine (with optional agent reviewer + SLTP)
    # ------------------------------------------------------------------

    def engine(self) -> TradingEngine:
        if self._engine is None:
            self._engine = TradingEngine(
                db_manager=self.db,
                account_manager=self.account_mgr(),
                order_manager=self.order_mgr(),
                position_manager=self.position_mgr(),
                fee_model=DEFAULT_FEE_MODEL,
                risk_checker=self.risk_checker(),
                agent_reviewer=self._build_agent_reviewer(),
                sltp_calculator=self.sltp_calculator(),
                enable_auto_sltp=bool(
                    getattr(self.config, "paper_trading_enable_auto_sltp", True)
                ),
                on_trade_executed=self._on_trade_executed,
                on_signal_rejected=self._on_signal_rejected,
                quote_cache=self.quote_cache(),
            )
        return self._engine

    def _build_agent_reviewer(self) -> Optional[AgentRiskReviewer]:
        if self._agent_reviewer is not None:
            return self._agent_reviewer
        enabled = bool(
            getattr(self.config, "paper_trading_enable_agent_review", False)
        )
        if not enabled:
            return None
        try:
            from paper_trading.agent_risk import AgentRiskReviewer

            self._agent_reviewer = AgentRiskReviewer(config=self.config)
            logger.info("[PaperTradingService] AgentRiskReviewer enabled")
            return self._agent_reviewer
        except Exception as exc:
            logger.warning(
                "[PaperTradingService] AgentRiskReviewer build failed: %s", exc
            )
            return None

    # ------------------------------------------------------------------
    # Reflection engine (lazy)
    # ------------------------------------------------------------------

    def reflection_engine(self) -> ReflectionEngine:
        if self._reflection_engine is None:
            self._reflection_engine = build_reflection_engine(
                config=self.config,
                trading_engine=self.engine(),
            )
        return self._reflection_engine

    # ------------------------------------------------------------------
    # Battle plan generator (lazy)
    # ------------------------------------------------------------------

    def battle_plan_generator(self) -> BattlePlanGenerator:
        if self._battle_plan_generator is None:
            self._battle_plan_generator = build_battle_plan_generator(
                config=self.config,
                trading_engine=self.engine(),
                data_provider=self._get_data_fetcher(),
            )
        return self._battle_plan_generator

    # ------------------------------------------------------------------
    # PM agent (lazy)
    # ------------------------------------------------------------------

    def pm_agent(self) -> Any:
        if self._pm_agent is None:
            self._pm_agent = build_portfolio_manager_agent(
                config=self.config,
                trading_engine=self.engine(),
                reflection_engine=self.reflection_engine(),
            )
        return self._pm_agent

    # ------------------------------------------------------------------
    # MarketListener (stateful, single instance)
    # ------------------------------------------------------------------

    def get_listener(self) -> Optional[MarketListener]:
        return self._listener

    def start_listener(self, request: ListenerStartRequest) -> MarketListener:
        # Stop existing listener first if running.
        if self._listener is not None and self._listener.is_running():
            self._listener.stop(timeout=2.0)

        # T-08: 统一生产装配。与 run_listener.py 共用 build_full_listener，
        # 消除两条启动路径的能力漂移；PM/复盘/作战卡/漂移/延迟按 flag 注入。
        from paper_trading.market_listener import build_full_listener

        # Reuse the shared quote cache (the same instance the engine prices
        # off) and tune freshness to the listener tick.
        quote_cache = self.quote_cache()
        quote_cache._max_age = max(float(request.tick_interval_seconds or 10.0) * 2.0, 10.0)

        watched_codes = request.watched_codes or list(
            getattr(self.config, "stock_list", []) or []
        )
        markets = set(request.markets) if request.markets else {"cn"}

        listener = build_full_listener(
            config=self.config,
            account_id=request.account_id,
            watched_codes=watched_codes,
            markets=markets,
            tick_interval_seconds=request.tick_interval_seconds or 10.0,
            enable_strategies=request.enable_strategies,
            enable_daily_reflection=request.enable_daily_reflection,
            enable_battle_plan=request.enable_battle_plan,
            pm_decision_interval_seconds=request.pm_decision_interval_seconds,
            quote_cache=quote_cache,
            latency_tracker=self.tick_latency(),
            # T-10: 成交/拒单回调——成交即触发复盘（reflect_on_trade）。
            on_trade_executed=self._on_trade_executed,
            on_signal_rejected=self._on_signal_rejected,
        )
        listener.start()
        self._listener = listener
        return listener

    def stop_listener(self) -> bool:
        if self._listener is None:
            return False
        self._listener.stop(timeout=2.0)
        return True

    # ------------------------------------------------------------------
    # Reflection hook callbacks (P1-C)
    # ------------------------------------------------------------------

    def _on_trade_executed(self, result: TradeResult, trade_id: Optional[int] = None) -> None:
        """Trigger a post-trade reflection when LLM is available."""
        try:
            if trade_id is None:
                return
            engine = self.reflection_engine()
            engine.reflect_on_trade(trade_id=trade_id)
        except Exception as exc:
            logger.warning(
                "[PaperTradingService] post-trade reflection failed: %s", exc
            )

    def _on_signal_rejected(self, result: TradeResult) -> None:
        """No-op for now; rejected signals are already persisted for audit."""
        return

    # ------------------------------------------------------------------
    # Data fetcher (lazy)
    # ------------------------------------------------------------------

    def _get_data_fetcher(self) -> Any:
        if self._data_fetcher is None:
            try:
                from data_provider import DataFetcherManager

                self._data_fetcher = DataFetcherManager()
            except Exception as exc:
                logger.warning(
                    "[PaperTradingService] DataFetcherManager build failed: %s", exc
                )
                self._data_fetcher = None
        return self._data_fetcher

    # ------------------------------------------------------------------
    # Tick latency aggregator (T-005 / pending-api §1)
    # ------------------------------------------------------------------

    def tick_latency(self) -> Any:
        """Return the shared tick-latency aggregator (lazy singleton)."""
        if self._tick_latency is None:
            from src.utils.latency_tracker import TickLatencyAggregator

            self._tick_latency = TickLatencyAggregator(window_size=100)
        return self._tick_latency

    def quote_cache(self) -> Any:
        """Return the shared live-quote cache (lazy singleton, T-02)."""
        if self._quote_cache is None:
            from paper_trading.quote_cache import SharedQuoteCache

            self._quote_cache = SharedQuoteCache()
        return self._quote_cache

    def latency_report(self) -> Dict[str, Any]:
        """Return the doc-contract latency report (zeros when no samples)."""
        try:
            return self.tick_latency().report()
        except Exception as exc:
            logger.error("[PaperTradingService] latency report failed: %s", exc, exc_info=True)
            return {
                "tick_total_ms": {"p50": 0.0, "p95": 0.0, "p99": 0.0},
                "steps": [],
            }

    # ------------------------------------------------------------------
    # Drift detector / signal fusion / feature pipeline (T10 / T9 / T13)
    # ------------------------------------------------------------------

    def drift_detector(self) -> Any:
        """Return the shared DriftDetector (lazy singleton)."""
        if self._drift_detector is None:
            from paper_trading.drift_detector import DriftDetector

            self._drift_detector = DriftDetector()
        return self._drift_detector

    def signal_fusion(self) -> Any:
        """Return the shared SignalFusionEngine (lazy singleton)."""
        if self._signal_fusion is None:
            from paper_trading.signal_fusion import FusionMethod, SignalFusionEngine

            self._signal_fusion = SignalFusionEngine(
                method=FusionMethod.WEIGHTED_VOTE,
                consensus_threshold=float(
                    getattr(self.config, "signal_fusion_consensus_threshold", 0.60)
                ),
            )
        return self._signal_fusion

    def feature_pipeline(self) -> Any:
        """Return the shared FeaturePipeline with default configs (lazy)."""
        if self._feature_pipeline is None:
            from paper_trading.features import FeatureConfig, FeaturePipeline

            self._feature_pipeline = FeaturePipeline(
                [
                    FeatureConfig("sma_crossover", "momentum", "sma_crossover", {"fast": 5, "slow": 20}),
                    FeatureConfig("rsi", "momentum", "rsi", {"period": 14}),
                    FeatureConfig("volume_spike", "volume", "volume_spike", {"multiplier": 2.0}),
                    FeatureConfig("ma_alignment", "trend", "ma_alignment", {"short": 5, "long": 20}),
                    FeatureConfig("bid_ask_imbalance", "market_microstructure", "bid_ask_imbalance", {}),
                ]
            )
        return self._feature_pipeline


# ---------------------------------------------------------------------------
# Dependency: paper_trading_service
# ---------------------------------------------------------------------------


def get_paper_trading_service(request: Request) -> PaperTradingService:
    """Return the app-lifecycle PaperTradingService, building it on first use."""
    service = getattr(request.app.state, "paper_trading_service", None)
    if service is None:
        config = get_config_dep()
        db_manager = get_database_manager()
        service = PaperTradingService(config=config, db_manager=db_manager)
        request.app.state.paper_trading_service = service
    return service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _order_to_item(order: Any) -> OrderItem:
    """Serialize a PaperOrder ORM row to OrderItem."""
    return OrderItem(
        id=int(order.id),
        account_id=int(order.account_id),
        code=str(order.code),
        name=order.name,
        side=str(order.side),
        order_type=str(order.order_type),
        price=order.price,
        quantity=float(order.quantity or 0.0),
        filled_quantity=float(order.filled_quantity or 0.0),
        filled_price_avg=float(order.filled_price_avg or 0.0),
        status=str(order.status),
        strategy_name=order.strategy_name,
        signal_id=order.signal_id,
        reason=order.reason,
        reject_reason=order.reject_reason,
        created_at=order.created_at.isoformat() if order.created_at else None,
        filled_at=order.filled_at.isoformat() if order.filled_at else None,
    )


def _conditional_order_to_item(order: Any) -> ConditionalOrderItem:
    """Serialize a PaperOrder ORM row to ConditionalOrderItem."""
    return ConditionalOrderItem(
        id=int(order.id),
        account_id=int(order.account_id),
        code=str(order.code),
        name=order.name,
        side=str(order.side),
        order_type=str(order.order_type),
        price=order.price,
        quantity=float(order.quantity or 0.0),
        filled_quantity=float(order.filled_quantity or 0.0),
        filled_price_avg=float(order.filled_price_avg or 0.0),
        status=str(order.status),
        strategy_name=order.strategy_name,
        signal_id=order.signal_id,
        reason=order.reason,
        reject_reason=order.reject_reason,
        created_at=order.created_at.isoformat() if order.created_at else None,
        filled_at=order.filled_at.isoformat() if order.filled_at else None,
        trigger_price=order.trigger_price,
        linked_order_id=order.linked_order_id,
        triggered_at=order.triggered_at.isoformat() if order.triggered_at else None,
    )


def _row_to_decision_dict(row: PaperDecision) -> Dict[str, Any]:
    import json as _json

    params: Dict[str, Any] = {}
    if row.params_json:
        try:
            parsed = _json.loads(row.params_json)
            if isinstance(parsed, dict):
                params = parsed
        except (ValueError, TypeError):
            params = {}
    return {
        "id": row.id,
        "account_id": row.account_id,
        "action": row.action,
        "code": row.code,
        "name": row.name,
        "params": params,
        "reason": row.reason or "",
        "confidence": float(row.confidence or 0.0),
        "elapsed_seconds": 0.0,  # not stored on the row
        "used_fallback": bool(row.status == "skipped" and row.action == "hold"),
        "error": row.reject_reason,
        "status": row.status or "pending",
        "signal_id": row.signal_id,
        "order_id": row.order_id,
        "parse_ok": bool(getattr(row, "parse_ok", False)),
        "quality_score": float(getattr(row, "quality_score", 0.0) or 0.0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _row_to_signal_dict(row: PaperSignal) -> Dict[str, Any]:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "code": row.code,
        "name": row.name,
        "side": row.side,
        "trigger_price": float(row.trigger_price or 0.0),
        "suggested_quantity": (
            float(row.suggested_quantity) if row.suggested_quantity is not None else None
        ),
        "strategy_name": row.strategy_name,
        "rule_name": row.rule_name,
        "reason": row.reason,
        "status": row.status,
        "agent_confirmed": row.agent_confirmed,
        "agent_reason": row.agent_reason,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _row_to_reflection_dict(row: PaperReflection) -> Dict[str, Any]:
    import json as _json

    lessons: List[str] = []
    if row.lessons_json:
        try:
            parsed = _json.loads(row.lessons_json)
            if isinstance(parsed, list):
                lessons = [str(s) for s in parsed]
        except (ValueError, TypeError):
            lessons = []
    return {
        "id": row.id,
        "account_id": row.account_id,
        "scope": row.scope or "adhoc",
        "subject": row.subject or "",
        "summary": row.summary or "",
        "takeaway": row.takeaway or "",
        "lessons": lessons,
        "tags": row.tags or "",
        "mood": row.mood or "neutral",
        "trade_id": row.trade_id,
        "order_id": row.order_id,
        "code": row.code,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _plan_to_item(row: PaperBattlePlan) -> BattlePlanItem:
    import json as _json

    try:
        holdings_raw = _json.loads(row.holdings_plans_json or "[]")
    except (ValueError, TypeError):
        holdings_raw = []
    try:
        candidates_raw = _json.loads(row.candidates_json or "[]")
    except (ValueError, TypeError):
        candidates_raw = []

    holdings = [
        HoldingPlanItem(**h) for h in holdings_raw if isinstance(h, dict)
    ]
    candidates = [
        _candidate_dict_to_item(c) for c in candidates_raw if isinstance(c, dict)
    ]
    return BattlePlanItem(
        plan_id=int(row.id),
        account_id=int(row.account_id),
        date=row.date.isoformat() if row.date else "",
        holdings_plans=holdings,
        candidates=candidates,
        market_review=row.market_review or "",
        sentiment_score=int(row.sentiment_score or 50),
        main_theme=row.main_theme or "",
        used_fallback=bool(row.used_fallback),
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


def _candidate_dict_to_item(c: Dict[str, Any]) -> Any:
    from api.v1.schemas.paper_trading import CandidatePlanItem

    return CandidatePlanItem(
        code=str(c.get("code") or ""),
        name=str(c.get("name") or ""),
        auction_condition=str(c.get("auction_condition") or ""),
        intraday_trigger=str(c.get("intraday_trigger") or ""),
        position_ratio=float(c.get("position_ratio") or 0.0),
        stop_loss=c.get("stop_loss"),
        take_profit_1=c.get("take_profit_1"),
        take_profit_2=c.get("take_profit_2"),
        technical_score=float(c.get("technical_score") or 0.0),
    )


# ---------------------------------------------------------------------------
# Account endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/accounts",
    response_model=AccountListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List paper trading accounts",
)
def list_accounts(service: PaperTradingService = Depends(get_paper_trading_service)):
    try:
        mgr = service.account_mgr()
        rows = mgr.list_accounts()
        accounts: List[AccountListItem] = []
        for account in rows:
            snap = mgr.snapshot(account.id)
            position_count = len(service.position_mgr().list_positions(account.id))
            accounts.append(
                AccountListItem(
                    account_id=snap.id,
                    name=snap.name,
                    initial_capital=snap.initial_capital,
                    cash=snap.cash,
                    frozen_cash=snap.frozen_cash,
                    total_market_value=snap.market_value,
                    net_value=snap.total_assets,
                    return_pct=snap.pnl_pct,
                    position_count=position_count,
                    status=snap.status,
                )
            )
        return AccountListResponse(accounts=accounts, total=len(accounts))
    except Exception as exc:
        logger.error("[paper_trading] list_accounts failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"list_accounts failed: {exc}")


@router.post(
    "/accounts",
    response_model=AccountSnapshotResponse,
    responses={
        200: {"description": "Account created / reset"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Create or reset a paper trading account",
)
def create_account(
    request: AccountCreateRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> AccountSnapshotResponse:
    try:
        mgr = service.account_mgr()
        existing = mgr.get_account(name=request.name)
        if existing is not None:
            if request.reset_if_exists:
                mgr.reset_account(existing.id, new_capital=request.initial_capital)
                account_id = existing.id
            else:
                account_id = existing.id
        else:
            account = mgr.get_or_create_account(
                name=request.name, initial_capital=request.initial_capital
            )
            account_id = account.id

        snap = mgr.snapshot(account_id)
        return AccountSnapshotResponse(
            account_id=snap.id,
            name=snap.name,
            initial_capital=snap.initial_capital,
            cash=snap.cash,
            frozen_cash=snap.frozen_cash,
            total_market_value=snap.market_value,
            net_value=snap.total_assets,
            return_pct=snap.pnl_pct,
            position_count=0,
            status=snap.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] create_account failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"create_account failed: {exc}")


@router.put(
    "/accounts/{account_id}",
    response_model=AccountSnapshotResponse,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
        409: {"description": "Name conflict", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Update paper trading account metadata",
    dependencies=[Depends(verify_account_ownership)],
)
def update_account(
    account_id: int,
    request: AccountUpdateRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> AccountSnapshotResponse:
    try:
        mgr = service.account_mgr()
        account = mgr.update_account(
            account_id,
            name=request.name,
            initial_capital=request.initial_capital,
        )
        snap = mgr.snapshot(account_id)
        positions = service.position_mgr().list_positions(account_id)
        return AccountSnapshotResponse(
            account_id=snap.id,
            name=snap.name,
            initial_capital=snap.initial_capital,
            cash=snap.cash,
            frozen_cash=snap.frozen_cash,
            total_market_value=snap.market_value,
            net_value=snap.total_assets,
            return_pct=snap.pnl_pct,
            position_count=len(positions),
            status=snap.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] update_account failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"update_account failed: {exc}")


# NOTE: Temporarily changed from status_code=204 to 200 as a workaround for
# FastAPI's assertion error "Status code 204 must not have a response body"
# when using response_description with DELETE/POST endpoints. This is a known
# limitation in the current FastAPI version and will be reverted once a proper
# fix or migration path is available. The endpoint functionality remains correct.
@router.delete(
    "/accounts/{account_id}",
    status_code=200,
    response_model=None,
    summary="Delete a paper trading account and all its data",
    dependencies=[Depends(verify_account_ownership)],
)
def delete_account(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> None:
    try:
        service.account_mgr().delete_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] delete_account failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"delete_account failed: {exc}")


@router.get(
    "/accounts/{account_id}",
    response_model=AccountSnapshotResponse,
    responses={
        404: {"description": "Account not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get account snapshot",
    dependencies=[Depends(verify_account_ownership)],
)
def get_account_snapshot(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> AccountSnapshotResponse:
    try:
        mgr = service.account_mgr()
        snap = mgr.snapshot(account_id)
        positions = service.position_mgr().list_positions(account_id)
        return AccountSnapshotResponse(
            account_id=snap.id,
            name=snap.name,
            initial_capital=snap.initial_capital,
            cash=snap.cash,
            frozen_cash=snap.frozen_cash,
            total_market_value=snap.market_value,
            net_value=snap.total_assets,
            return_pct=snap.pnl_pct,
            position_count=len(positions),
            status=snap.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] get_account_snapshot failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/net-value",
    response_model=NetValueCurveResponse,
    responses={
        404: {"description": "Account not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get net value curve",
    dependencies=[Depends(verify_account_ownership)],
)
def get_net_value_curve(
    account_id: int,
    limit: int = Query(90, ge=1, le=365, description="Number of points"),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> NetValueCurveResponse:
    try:
        points_raw = service.account_mgr().get_net_value_series(account_id, limit=limit)
        points = [
            NetValuePoint(
                date=p["date"] or "",
                net_value=float(p.get("net_value") or 0.0),
                cash=float(p.get("cash") or 0.0),
                market_value=float(p.get("market_value") or 0.0),
                return_pct=float(p.get("return_pct") or 0.0),
            )
            for p in points_raw
        ]
        return NetValueCurveResponse(account_id=account_id, points=points)
    except Exception as exc:
        logger.error("[paper_trading] get_net_value_curve failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/performance",
    response_model=PerformanceMetricsResponse,
    responses={
        404: {"description": "Account not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get account performance metrics",
    dependencies=[Depends(verify_account_ownership)],
)
def get_account_performance(
    account_id: int,
    start_date: Optional[str] = Query(None, description="ISO date (inclusive)"),
    end_date: Optional[str] = Query(None, description="ISO date (inclusive)"),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> PerformanceMetricsResponse:
    try:
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
        metrics = service.performance_analyzer().calculate(
            account_id, start_date=start, end_date=end
        )
        return PerformanceMetricsResponse(**metrics.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] get_account_performance failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/drawdown",
    response_model=List[DrawdownItem],
    responses={
        404: {"description": "Account not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get account drawdown curve",
    dependencies=[Depends(verify_account_ownership)],
)
def get_account_drawdown(
    account_id: int,
    start_date: Optional[str] = Query(None, description="ISO date (inclusive)"),
    end_date: Optional[str] = Query(None, description="ISO date (inclusive)"),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> List[DrawdownItem]:
    try:
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
        records = service.performance_analyzer().get_drawdown_curve(
            account_id, start_date=start, end_date=end
        )
        return [
            DrawdownItem(
                date=r.date.isoformat(),
                net_value=r.net_value,
                peak_net_value=r.peak_net_value,
                drawdown_pct=r.drawdown_pct,
            )
            for r in records
        ]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] get_account_drawdown failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/risk-metrics",
    response_model=RiskMetricsResponse,
    responses={
        404: {"description": "Account not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get current risk metrics",
    dependencies=[Depends(verify_account_ownership)],
)
def get_account_risk_metrics(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> RiskMetricsResponse:
    try:
        snapshot = service.risk_checker().get_risk_snapshot(account_id)
        return RiskMetricsResponse(**snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] get_account_risk_metrics failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/latency",
    response_model=LatencyReportResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get account tick latency statistics (p50/p95/p99)",
    dependencies=[Depends(verify_account_ownership)],
)
def get_account_latency(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> LatencyReportResponse:
    """Return aggregated tick latency for an account (pending-api §1).

    Matches the frontend LatencyPanel polling contract: returns HTTP 200 with
    zeroed ``tick_total_ms`` and empty ``steps`` when the MarketListener has
    not produced any ticks yet — never 404.
    """
    try:
        report = service.latency_report()
        return LatencyReportResponse(**report)
    except Exception as exc:
        logger.error("[paper_trading] get_account_latency failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Order endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/orders",
    response_model=TradeResultResponse,
    responses={
        200: {"description": "Order submitted"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Submit a manual paper-trading order",
    description=(
        "Submits a manual order through the full pipeline (risk checks, "
        "optional agent review, fee model, settlement). For strategy-driven "
        "orders, use the signal endpoint instead."
    ),
)
def submit_manual_order(
    request: OrderCreateRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> TradeResultResponse:
    try:
        order_type = (
            OrderType.LIMIT if request.order_type == "limit" else OrderType.MARKET
        )
        if request.side not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail=f"invalid side: {request.side}")
        signal = Signal(
            side=request.side,
            code=request.code,
            name=request.name,
            strategy_name=request.strategy_name or "manual",
            rule_name="manual_order",
            trigger_price=(
                request.limit_price
                if order_type == OrderType.LIMIT and request.limit_price
                else 0.0
            ),
            suggested_quantity=request.quantity,
            reason=request.reason or "manual order",
        )
        # Need a non-zero trigger price for risk checks.
        if signal.trigger_price <= 0 and request.limit_price:
            signal.trigger_price = float(request.limit_price)
        result = service.engine().submit_signal(
            account_id=request.account_id,
            signal=signal,
            order_type=order_type,
            limit_price=request.limit_price,
            quantity_override=request.quantity,
        )
        return TradeResultResponse(**result.to_dict())
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] submit_manual_order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/signals/{signal_id}/cancel",
    response_model=TradeResultResponse,
    responses={
        200: {"description": "Signal canceled"},
        404: {"description": "Signal not found", "model": ErrorResponse},
        400: {"description": "Signal not cancellable", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Cancel a pending signal and its order",
)
def cancel_signal(
    signal_id: int,
    request: OrderCancelRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> TradeResultResponse:
    try:
        result = service.engine().cancel_signal(
            signal_id=signal_id, reason=request.reason
        )
        return TradeResultResponse(**result.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] cancel_signal failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/signals/{signal_id}/modify",
    response_model=TradeResultResponse,
    responses={
        200: {"description": "Signal modified"},
        404: {"description": "Signal not found", "model": ErrorResponse},
        400: {"description": "Signal not modifiable", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Modify a pending limit order's price/quantity",
)
def modify_signal(
    signal_id: int,
    request: OrderModifyRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> TradeResultResponse:
    try:
        result = service.engine().modify_signal(
            signal_id=signal_id,
            new_price=request.new_limit_price,
            new_quantity=request.new_quantity,
            reason=request.reason,
        )
        return TradeResultResponse(**result.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] modify_signal failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/orders/{order_id}/cancel",
    response_model=TradeResultResponse,
    responses={
        200: {"description": "Order canceled"},
        404: {"description": "Order not found", "model": ErrorResponse},
        400: {"description": "Order not cancellable", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Cancel a pending order by order id",
)
def cancel_order(
    order_id: int,
    request: OrderCancelRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> TradeResultResponse:
    try:
        result = service.engine().cancel_order(
            order_id=order_id, reason=request.reason
        )
        return TradeResultResponse(**result.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] cancel_order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/orders/{order_id}/modify",
    response_model=TradeResultResponse,
    responses={
        200: {"description": "Order modified"},
        404: {"description": "Order not found", "model": ErrorResponse},
        400: {"description": "Order not modifiable", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Modify a pending limit order's price/quantity by order id",
)
def modify_order(
    order_id: int,
    request: OrderModifyRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> TradeResultResponse:
    try:
        result = service.engine().modify_order(
            order_id=order_id,
            new_price=request.new_limit_price,
            new_quantity=request.new_quantity,
            reason=request.reason,
        )
        return TradeResultResponse(**result.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] modify_order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/orders/batch",
    response_model=BatchOrderResponse,
    responses={
        200: {"description": "Batch orders created"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Submit a batch of paper-trading orders",
    description=(
        "Creates multiple orders atomically. Market orders are filled "
        "immediately using ``limit_price`` as the reference fill price. "
        "Limit orders are left pending for the matcher."
    ),
)
def create_batch_orders(
    request: BatchOrderCreateRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> BatchOrderResponse:
    try:
        engine = service.engine()
        order_mgr = engine.order_mgr

        # Build OrderRequest objects and validate order types up front.
        order_requests: List[OrderRequest] = []
        for item in request.orders:
            side = str(item.side).lower()
            if side not in ("buy", "sell"):
                raise ValueError(f"invalid side: {item.side}")

            order_type_str = str(item.order_type).lower()
            if order_type_str == "limit":
                order_type = OrderType.LIMIT
                if item.limit_price is None or item.limit_price <= 0:
                    raise ValueError("limit order requires a positive limit_price")
            elif order_type_str == "market":
                order_type = OrderType.MARKET
                if item.limit_price is None or item.limit_price <= 0:
                    raise ValueError(
                        "market order in batch requires limit_price as reference fill price"
                    )
            else:
                raise ValueError(f"unsupported batch order_type: {item.order_type}")

            order_requests.append(
                OrderRequest(
                    account_id=request.account_id,
                    code=item.code,
                    side=OrderSide(side),
                    quantity=float(item.quantity),
                    order_type=order_type,
                    price=item.limit_price,
                    name=item.name,
                    strategy_name=item.strategy_name or "manual_batch",
                    reason=item.reason or "batch order",
                )
            )

        # Atomically create all orders.
        created = order_mgr.create_batch_orders(request.account_id, order_requests)

        # Execute market orders immediately; leave limit orders pending.
        results: List[TradeResultResponse] = []
        for order in created:
            if order.order_type == OrderType.MARKET.value:
                order_dict = order_mgr._order_to_dict(order)
                live = engine._live_price(order.code)
                fill_price = live if live is not None else float(order.price or 0.0)
                result = engine._execute_triggered_market_order(
                    order_dict, fill_price=fill_price
                )
                results.append(TradeResultResponse(**result.to_dict()))
            else:
                results.append(
                    TradeResultResponse(
                        signal_id=order.signal_id or 0,
                        order_id=order.id,
                        side=order.side,
                        code=order.code,
                        status="pending",
                        fill_price=None,
                        fill_quantity=None,
                        fee=None,
                        reason="limit order pending",
                    )
                )

        return BatchOrderResponse(
            account_id=request.account_id, total=len(results), results=results
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] create_batch_orders failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/orders/conditional",
    response_model=ConditionalOrderItem,
    responses={
        200: {"description": "Conditional order created"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Create a conditional order (stop-loss / take-profit / OCO)",
)
def create_conditional_order(
    request: ConditionalOrderCreateRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> ConditionalOrderItem:
    try:
        side = str(request.side).lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {request.side}")

        order_type_str = str(request.order_type).lower()
        conditional_types = {
            "stop_loss": OrderType.STOP_LOSS,
            "take_profit": OrderType.TAKE_PROFIT,
            "oco_primary": OrderType.OCO_PRIMARY,
            "oco_secondary": OrderType.OCO_SECONDARY,
        }
        if order_type_str not in conditional_types:
            raise ValueError(
                f"invalid conditional order_type: {request.order_type}; "
                f"expected one of {list(conditional_types.keys())}"
            )

        order = service.order_mgr().create_conditional_order(
            account_id=request.account_id,
            code=request.code,
            side=OrderSide(side),
            quantity=float(request.quantity),
            order_type=conditional_types[order_type_str],
            trigger_price=float(request.trigger_price),
            price=request.limit_price,
            linked_order_id=request.linked_order_id,
            name=request.name,
            strategy_name=request.strategy_name or "manual_conditional",
            reason=request.reason or "conditional order",
        )
        return _conditional_order_to_item(order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] create_conditional_order failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/orders",
    response_model=OrderListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List orders for an account",
    dependencies=[Depends(verify_account_ownership)],
)
def list_orders(
    account_id: int,
    filters: OrderListFilterParams = Depends(),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> OrderListResponse:
    try:
        rows = service.order_mgr().list_orders(
            account_id=account_id,
            status=filters.status,
            side=filters.side,
            code=filters.code,
            from_date=_parse_iso_datetime(filters.from_date),
            to_date=_parse_iso_datetime(filters.to_date),
            limit=filters.limit,
            offset=filters.offset,
        )
        items = [OrderItem(**row) for row in rows]
        return OrderListResponse(
            account_id=account_id, total=len(items), items=items
        )
    except Exception as exc:
        logger.error("[paper_trading] list_orders failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/trades",
    response_model=TradeListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List filled trades for an account",
    dependencies=[Depends(verify_account_ownership)],
)
def list_trades(
    account_id: int,
    code: Optional[str] = Query(None, description="Filter by stock code"),
    limit: int = Query(100, ge=1, le=500),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> TradeListResponse:
    try:
        rows = service.order_mgr().list_trades(
            account_id=account_id, code=code, limit=limit
        )
        items = [
            TradeItem(
                id=int(r["id"]),
                order_id=int(r["order_id"]),
                account_id=account_id,
                code=r["code"],
                name=r.get("name"),
                side=r["side"],
                fill_price=float(r["price"]),
                fill_quantity=float(r["quantity"]),
                fee=float(r["fee"]),
                realized_pnl=None,  # not stored on the trade row
                traded_at=r.get("traded_at") or "",
            )
            for r in rows
        ]
        return TradeListResponse(
            account_id=account_id, total=len(items), items=items
        )
    except Exception as exc:
        logger.error("[paper_trading] list_trades failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def _apply_live_valuation(
    rows: List[Dict[str, Any]], quote_cache: Optional[Any]
) -> List[Dict[str, Any]]:
    """Overlay fresh live prices on position rows for PnL display (T-02).

    When a fresh quote exists for a held code, recompute last_price /
    market_value / floating_pnl from the live price so displayed PnL is
    consistent with the market rather than the fill reference price.
    """
    out: List[Dict[str, Any]] = []
    for r in rows:
        live: Optional[float] = None
        if quote_cache is not None:
            try:
                q = quote_cache.get(r["code"])
                if q is not None and float(getattr(q, "price", 0.0) or 0.0) > 0:
                    live = float(q.price)
            except Exception:
                live = None
        if live is not None:
            qty = float(r.get("quantity") or 0.0)
            avg_cost = float(r.get("avg_cost") or 0.0)
            r = dict(r)
            r["last_price"] = live
            r["market_value"] = qty * live
            r["floating_pnl"] = (live - avg_cost) * qty
            r["floating_pnl_pct"] = (
                ((live - avg_cost) / avg_cost * 100.0) if avg_cost > 0 else 0.0
            )
        out.append(r)
    return out


@router.get(
    "/accounts/{account_id}/positions",
    response_model=PositionListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List open positions",
    dependencies=[Depends(verify_account_ownership)],
)
def list_positions(
    account_id: int,
    include_zero: bool = Query(False, description="Include zero-quantity rows"),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> PositionListResponse:
    try:
        rows = service.position_mgr().list_positions(
            account_id=account_id, include_zero=include_zero
        )
        rows = _apply_live_valuation(rows, service.quote_cache())
        items = [
            PositionItem(
                account_id=account_id,
                code=r["code"],
                name=r.get("name"),
                quantity=float(r["quantity"]),
                available_quantity=float(r["available_quantity"]),
                avg_cost=float(r["avg_cost"]),
                last_price=float(r["last_price"]),
                stop_loss=r.get("stop_loss"),
                take_profit=r.get("take_profit"),
                take_profit_2=r.get("take_profit_2"),
                sltp_reasoning=r.get("sltp_reasoning"),
                floating_pnl=float(r.get("floating_pnl") or 0.0),
                floating_pnl_pct=float(r.get("floating_pnl_pct") or 0.0),
            )
            for r in rows
        ]
        total_mv = sum(float(r.get("market_value") or 0.0) for r in rows)
        return PositionListResponse(
            account_id=account_id,
            positions=items,
            total_market_value=total_mv,
        )
    except Exception as exc:
        logger.error("[paper_trading] list_positions failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@router.get(
    "/accounts/{account_id}/signals",
    response_model=SignalListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List signals (audit trail)",
    dependencies=[Depends(verify_account_ownership)],
)
def list_signals(
    account_id: int,
    status: Optional[str] = Query(None, description="Filter by status"),
    code: Optional[str] = Query(None, description="Filter by stock code"),
    limit: int = Query(100, ge=1, le=500),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> SignalListResponse:
    try:
        with db_manager.session_scope() as session:
            stmt = select(PaperSignal).where(PaperSignal.account_id == account_id)
            if status:
                stmt = stmt.where(PaperSignal.status == status)
            if code:
                stmt = stmt.where(PaperSignal.code == code)
            stmt = stmt.order_by(desc(PaperSignal.created_at)).limit(limit)
            rows = session.execute(stmt).scalars().all()
            items = [SignalItem(**_row_to_signal_dict(r)) for r in rows]
        return SignalListResponse(
            account_id=account_id, total=len(items), items=items
        )
    except Exception as exc:
        logger.error("[paper_trading] list_signals failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Reflection notes
# ---------------------------------------------------------------------------


@router.get(
    "/accounts/{account_id}/reflections",
    response_model=ReflectionListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List reflection notes",
    dependencies=[Depends(verify_account_ownership)],
)
def list_reflections(
    account_id: int,
    scope: Optional[str] = Query(None, description="Filter by scope (trade|daily|weekly|adhoc)"),
    code: Optional[str] = Query(None, description="Filter by stock code"),
    limit: int = Query(50, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> ReflectionListResponse:
    try:
        with db_manager.session_scope() as session:
            stmt = select(PaperReflection).where(
                PaperReflection.account_id == account_id
            )
            if scope:
                stmt = stmt.where(PaperReflection.scope == scope)
            if code:
                stmt = stmt.where(PaperReflection.code == code)
            stmt = stmt.order_by(desc(PaperReflection.created_at)).limit(limit)
            rows = session.execute(stmt).scalars().all()
            items = [ReflectionNoteItem(**_row_to_reflection_dict(r)) for r in rows]
        return ReflectionListResponse(
            account_id=account_id, total=len(items), items=items
        )
    except Exception as exc:
        logger.error("[paper_trading] list_reflections failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/accounts/{account_id}/reflections/daily",
    response_model=ReflectionNoteItem,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Trigger a daily reflection manually",
    description=(
        "Runs the AI reflection agent on today's (or specified date's) trades "
        "and persists the resulting note. Returns the note."
    ),
    dependencies=[Depends(verify_account_ownership)],
)
def trigger_daily_reflection(
    account_id: int,
    request: DailyReflectionRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> ReflectionNoteItem:
    try:
        review_date = _parse_iso_date(request.review_date)
        engine = service.reflection_engine()
        note = engine.reflect_on_daily(
            account_id=account_id, review_date=review_date
        )
        return ReflectionNoteItem(
            id=note.row_id or 0,
            account_id=account_id,
            scope=note.scope,
            subject=note.subject,
            summary=note.summary,
            takeaway=note.takeaway,
            lessons=note.lessons,
            tags=",".join(note.tags),
            mood=note.mood,
            trade_id=note.trade_id,
            order_id=note.order_id,
            code=note.code,
            created_at=(
                note.created_at.isoformat() if note.created_at else None
            ),
        )
    except Exception as exc:
        logger.error("[paper_trading] trigger_daily_reflection failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Battle plans
# ---------------------------------------------------------------------------


@router.post(
    "/accounts/{account_id}/battle-plans/generate",
    response_model=BattlePlanItem,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Generate (and persist) the next-trading-day battle plan",
    dependencies=[Depends(verify_account_ownership)],
)
def generate_battle_plan(
    account_id: int,
    request: BattlePlanGenerateRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> BattlePlanItem:
    try:
        target_date = _parse_iso_date(request.target_date)
        gen = service.battle_plan_generator()
        plan = gen.generate(
            account_id=account_id,
            target_date=target_date,
            watched_codes=request.watched_codes,
        )
        # Re-fetch the persisted row to ensure consistent serialization.
        if plan.plan_id is None:
            raise HTTPException(
                status_code=500, detail="battle plan generation produced no row id"
            )
        with service.db.session_scope() as session:
            row = session.execute(
                select(PaperBattlePlan).where(PaperBattlePlan.id == plan.plan_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=500, detail="battle plan not found after generation"
                )
            return _plan_to_item(row)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[paper_trading] generate_battle_plan failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/battle-plans",
    response_model=List[BattlePlanItem],
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List recent battle plans",
    dependencies=[Depends(verify_account_ownership)],
)
def list_battle_plans(
    account_id: int,
    limit: int = Query(10, ge=1, le=60),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> List[BattlePlanItem]:
    try:
        with db_manager.session_scope() as session:
            rows = session.execute(
                select(PaperBattlePlan)
                .where(PaperBattlePlan.account_id == account_id)
                .order_by(desc(PaperBattlePlan.date))
                .limit(limit)
            ).scalars().all()
            return [_plan_to_item(r) for r in rows]
    except Exception as exc:
        logger.error("[paper_trading] list_battle_plans failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/battle-plans/{plan_id}",
    response_model=BattlePlanItem,
    responses={
        404: {"description": "Plan not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get a battle plan by id",
)
def get_battle_plan(
    plan_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> BattlePlanItem:
    try:
        with db_manager.session_scope() as session:
            row = session.execute(
                select(PaperBattlePlan).where(PaperBattlePlan.id == plan_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"battle plan {plan_id} not found"
                )
            return _plan_to_item(row)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[paper_trading] get_battle_plan failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/battle-plans/{plan_id}/markdown",
    response_model=BattlePlanMarkdownResponse,
    responses={
        404: {"description": "Plan not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Render a battle plan as Markdown (for Lark/DingTalk push)",
)
def get_battle_plan_markdown(
    plan_id: int,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> BattlePlanMarkdownResponse:
    try:
        with db_manager.session_scope() as session:
            row = session.execute(
                select(PaperBattlePlan).where(PaperBattlePlan.id == plan_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"battle plan {plan_id} not found"
                )
            # Reuse BattlePlanGenerator's row_to_plan for the markdown render.
            plan = BattlePlanGenerator._row_to_plan(row)
            return BattlePlanMarkdownResponse(
                plan_id=int(row.id),
                date=row.date.isoformat() if row.date else "",
                markdown=plan.to_markdown(),
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[paper_trading] get_battle_plan_markdown failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# PM decisions
# ---------------------------------------------------------------------------


@router.post(
    "/accounts/{account_id}/pm-decisions/trigger",
    response_model=PMDecisionItem,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Manually trigger one PM agent decision cycle",
    dependencies=[Depends(verify_account_ownership)],
)
def trigger_pm_decision(
    account_id: int,
    request: PMDecisionTriggerRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> PMDecisionItem:
    try:
        agent = service.pm_agent()
        decision = agent.make_decision(
            account_id=account_id, extra_context=request.extra_context
        )
        # Persisted inside make_decision; fetch the latest row for this account.
        with service.db.session_scope() as session:
            row = session.execute(
                select(PaperDecision)
                .where(PaperDecision.account_id == account_id)
                .order_by(desc(PaperDecision.id))
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                # Fallback: build item from decision object directly.
                return PMDecisionItem(
                    id=0,
                    account_id=account_id,
                    action=decision.action,
                    code=decision.code,
                    name=decision.name,
                    params=decision.params,
                    reason=decision.reason,
                    confidence=decision.confidence,
                    elapsed_seconds=decision.elapsed_seconds,
                    used_fallback=decision.used_fallback,
                    error=decision.error,
                    created_at=datetime.now().isoformat(),
                )
            return PMDecisionItem(**_row_to_decision_dict(row))
    except Exception as exc:
        logger.error("[paper_trading] trigger_pm_decision failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/pm-decisions",
    response_model=PMDecisionListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List PM agent decisions",
    dependencies=[Depends(verify_account_ownership)],
)
def list_pm_decisions(
    account_id: int,
    action: Optional[str] = Query(None, description="Filter by action"),
    limit: int = Query(50, ge=1, le=200),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PMDecisionListResponse:
    try:
        with db_manager.session_scope() as session:
            stmt = select(PaperDecision).where(
                PaperDecision.account_id == account_id
            )
            if action:
                stmt = stmt.where(PaperDecision.action == action)
            stmt = stmt.order_by(desc(PaperDecision.created_at)).limit(limit)
            rows = session.execute(stmt).scalars().all()
            items = [PMDecisionItem(**_row_to_decision_dict(r)) for r in rows]
        return PMDecisionListResponse(
            account_id=account_id, total=len(items), items=items
        )
    except Exception as exc:
        logger.error("[paper_trading] list_pm_decisions failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/accounts/{account_id}/pm-decisions/{decision_id}/execute",
    response_model=PMDecisionExecuteResponse,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        404: {"description": "Decision not found", "model": ErrorResponse},
        409: {"description": "Decision not executable", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Execute a pending PM decision",
    dependencies=[Depends(verify_account_ownership)],
)
def execute_pm_decision(
    account_id: int,
    decision_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> PMDecisionExecuteResponse:
    try:
        agent = service.pm_agent()
        result = agent.execute_decision(decision_id, account_id=account_id)
        return PMDecisionExecuteResponse(
            decision_id=decision_id,
            account_id=account_id,
            signal_id=int(result.get("signal_id") or 0),
            order_id=result.get("order_id"),
            side=str(result.get("side") or ""),
            code=str(result.get("code") or ""),
            status=str(result.get("status") or ""),
            fill_price=result.get("fill_price"),
            fill_quantity=result.get("fill_quantity"),
            fee=result.get("fee"),
            reason=str(result.get("reason") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] execute_pm_decision failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"execute_pm_decision failed: {exc}")


# NOTE: Temporarily changed from status_code=204 to 200 as a workaround for
# FastAPI's assertion error "Status code 204 must not have a response body"
# when using response_description with DELETE/POST endpoints. This is a known
# limitation in the current FastAPI version and will be reverted once a proper
# fix or migration path is available. The endpoint functionality remains correct.
@router.post(
    "/accounts/{account_id}/pm-decisions/{decision_id}/ignore",
    status_code=200,
    response_model=None,
    summary="Ignore / skip a pending PM decision",
    dependencies=[Depends(verify_account_ownership)],
)
def ignore_pm_decision(
    account_id: int,
    decision_id: int,
    request: PMDecisionIgnoreRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> None:
    try:
        agent = service.pm_agent()
        agent.ignore_decision(decision_id, account_id=account_id, reason=request.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("[paper_trading] ignore_pm_decision failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"ignore_pm_decision failed: {exc}")


# ---------------------------------------------------------------------------
# MarketListener control
# ---------------------------------------------------------------------------


@router.get(
    "/listener/status",
    response_model=ListenerStatusResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get MarketListener status",
)
def get_listener_status(
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> ListenerStatusResponse:
    listener = service.get_listener()
    if listener is None:
        return ListenerStatusResponse(running=False)
    cfg: MarketListenerConfig = listener.config
    last_pm = {
        k: v.isoformat() for k, v in listener._last_pm_decision_at.items()
    } if listener._last_pm_decision_at else None
    return ListenerStatusResponse(
        running=listener.is_running(),
        account_id=cfg.account_id,
        watched_codes_count=len(cfg.watched_codes),
        strategies_count=len(listener.strategies),
        markets=sorted(cfg.markets),
        last_settle_date=(
            listener._last_settle_date.isoformat()
            if listener._last_settle_date
            else None
        ),
        last_battle_plan_date=(
            listener._last_battle_plan_date.isoformat()
            if listener._last_battle_plan_date
            else None
        ),
        last_daily_reflection_date=(
            listener._last_daily_reflection_date.isoformat()
            if listener._last_daily_reflection_date
            else None
        ),
        last_pm_decision_at=last_pm,
    )


@router.post(
    "/listener/start",
    response_model=ListenerControlResponse,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Start the MarketListener",
)
def start_listener(
    request: ListenerStartRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> ListenerControlResponse:
    try:
        listener = service.start_listener(request)
        cfg = listener.config
        return ListenerControlResponse(
            running=listener.is_running(),
            message=(
                f"listener started: account={cfg.account_id} "
                f"codes={len(cfg.watched_codes)} markets={sorted(cfg.markets)}"
            ),
        )
    except Exception as exc:
        logger.error("[paper_trading] start_listener failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/listener/stop",
    response_model=ListenerControlResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Stop the MarketListener",
)
def stop_listener(
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> ListenerControlResponse:
    try:
        stopped = service.stop_listener()
        return ListenerControlResponse(
            running=False,
            message="listener stopped" if stopped else "listener was not running",
        )
    except Exception as exc:
        logger.error("[paper_trading] stop_listener failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Daily report (P2-A)
# ---------------------------------------------------------------------------


@router.post(
    "/accounts/{account_id}/daily-report/generate",
    response_model=DailyReportResponse,
    tags=["daily-report"],
    dependencies=[Depends(verify_account_ownership)],
)
async def generate_daily_report(
    account_id: int,
    save: bool = True,
) -> DailyReportResponse:
    """Generate a daily trading report (P2-A)."""
    from datetime import date as date_cls
    from paper_trading.content_generator import build_content_generator
    try:
        generator = build_content_generator(account_id=account_id)
        result = generator.generate_daily_report(save=save)
        return DailyReportResponse(
            date=date_cls.today().isoformat(),
            markdown=getattr(result, "markdown", None),
            report_path=str(getattr(result, "report_path", None)) if getattr(result, "report_path", None) else None,
            voice_path=str(getattr(result, "voice_path", None)) if getattr(result, "voice_path", None) else None,
            used_fallback=getattr(result, "used_fallback", False),
            error=getattr(result, "error", None),
        )
    except Exception as exc:
        return DailyReportResponse(
            date=date_cls.today().isoformat(),
            error=str(exc),
        )


@router.get(
    "/accounts/{account_id}/daily-report/{report_date}",
    response_model=DailyReportResponse,
    tags=["daily-report"],
    dependencies=[Depends(verify_account_ownership)],
)
async def get_daily_report(
    account_id: int,
    report_date: str,
) -> DailyReportResponse:
    """Retrieve a saved daily report by date (P2-A)."""
    from pathlib import Path
    try:
        report_dir = Path("data/paper_trading/reports")
        filepath = report_dir / f"daily_report_{report_date}.md"
        if not filepath.exists():
            return DailyReportResponse(date=report_date, error="Report not found")
        markdown = filepath.read_text(encoding="utf-8")
        return DailyReportResponse(
            date=report_date,
            markdown=markdown,
            report_path=str(filepath),
        )
    except Exception as exc:
        return DailyReportResponse(date=report_date, error=str(exc))


# ---------------------------------------------------------------------------
# Backtest comparison (P3-F)
# ---------------------------------------------------------------------------


def _fmt_iso_date_value(value: Any) -> Optional[str]:
    """Format a date/datetime/str to ISO string (adapter may yield either)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _paper_scenario_to_schema(scenario: Any) -> PaperTradingScenario:
    """Serialize a PaperTradingScenario dataclass to the API schema."""
    return PaperTradingScenario(
        account_id=scenario.account_id,
        strategy_name=scenario.strategy_name,
        base_date=_fmt_iso_date_value(scenario.base_date),
        start_date=_fmt_iso_date_value(scenario.start_date),
        end_date=_fmt_iso_date_value(scenario.end_date),
        initial_capital=scenario.initial_capital,
        total_return_pct=scenario.total_return_pct,
        max_drawdown_pct=scenario.max_drawdown_pct,
        win_rate=scenario.win_rate,
        trade_count=scenario.trade_count,
        win_count=scenario.win_count,
        loss_count=scenario.loss_count,
        net_value_curve=scenario.net_value_curve,
        trades=scenario.trades,
    )


@router.get(
    "/accounts/{account_id}/backtest-scenario",
    response_model=PaperTradingScenario,
    responses={
        200: {"description": "纸面交易场景"},
        404: {"description": "账户不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="生成纸面交易回测场景",
    description="将纸面账户历史打包为类回测场景，用于与回测结果对比",
    dependencies=[Depends(verify_account_ownership)],
)
def get_backtest_scenario(
    account_id: int,
    strategy_name: str = Query("default", description="策略名称"),
    base_date: Optional[str] = Query(None, description="基准日期 ISO"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PaperTradingScenario:
    """Build a backtest-like scenario from paper-trading history."""
    account_mgr = PaperAccountManager(db_manager)
    try:
        account_mgr._get_account_by_id(account_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"账户 {account_id} 不存在"},
        )
    try:
        from paper_trading.backtest_adapter import PaperTradingToBacktestAdapter

        adapter = PaperTradingToBacktestAdapter(account_id, db_manager=db_manager)
        scenario = adapter.generate_backtest_scenario(
            strategy_name=strategy_name,
            base_date=_parse_iso_date(base_date),
        )
        return _paper_scenario_to_schema(scenario)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[paper_trading] backtest scenario failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"生成场景失败: {str(exc)}"},
        )


@router.post(
    "/accounts/{account_id}/backtest-comparison",
    response_model=BacktestPaperComparisonResponse,
    responses={
        200: {"description": "回测与纸面交易对比结果"},
        400: {"description": "请求参数错误", "model": ErrorResponse},
        404: {"description": "账户不存在或无回测汇总", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="回测与纸面交易对比",
    description="对比回测引擎输出与纸面账户实际表现，并可写入复盘笔记",
    dependencies=[Depends(verify_account_ownership)],
)
def compare_backtest_with_paper(
    account_id: int,
    request: BacktestPaperComparisonRequest,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> BacktestPaperComparisonResponse:
    """Compare a backtest summary with the actual paper-trading record."""
    account_mgr = PaperAccountManager(db_manager)
    try:
        account_mgr._get_account_by_id(account_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"账户 {account_id} 不存在"},
        )

    backtest_summary = request.backtest_summary
    if backtest_summary is None:
        from src.services.backtest_service import BacktestService

        service = BacktestService(db_manager)
        summary = service.get_summary(scope="overall")
        if summary is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": "未找到整体回测汇总"},
            )
        backtest_summary = summary

    try:
        from paper_trading.backtest_adapter import run_with_paper_validation

        result = run_with_paper_validation(
            backtest_summary=backtest_summary,
            strategy_name=request.strategy_name,
            account_id=account_id,
            db_manager=db_manager,
            persist_reflection=request.persist_reflection,
        )

        return BacktestPaperComparisonResponse(
            account_id=result["account_id"],
            strategy_name=result["strategy_name"],
            paper_scenario=_paper_scenario_to_schema(result["paper_scenario"]),
            backtest_summary=result["backtest_summary"],
            metrics=result["metrics"],
            interpretation=result["interpretation"],
            generated_at=result["generated_at"],
            reflection_persisted=bool(result.get("reflection_persisted", False)),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[paper_trading] backtest comparison failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"对比失败: {str(exc)}"},
        )

# ---------------------------------------------------------------------------
# Breaker status (integration ①)
# ---------------------------------------------------------------------------

@router.get(
    "/accounts/{account_id}/breaker/status",
    response_model=BreakerStatusResponse,
    summary="Get circuit breaker status for an account",
    dependencies=[Depends(verify_account_ownership)],
)
def get_breaker_status(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> BreakerStatusResponse:
    """Return the current circuit breaker state (SOFT/HARD/LIQUIDATION)."""
    try:
        engine = service.engine()
        breaker = getattr(engine, "circuit_breaker", None)
        if breaker is None:
            return BreakerStatusResponse(
                account_id=account_id, level="normal",
                can_trade=True, can_open_new=True, reason="breaker not configured",
            )
        return BreakerStatusResponse(
            account_id=account_id,
            level=breaker.state.level.value,
            can_trade=breaker.allow_any_trade(),
            can_open_new=breaker.allow_new_position(),
            reason=breaker.state.reason or "normal",
            triggered_at=breaker.state.triggered_at.isoformat() if breaker.state.triggered_at else None,
        )
    except Exception as exc:
        logger.error("[paper_trading] breaker status failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/extreme-market",
    response_model=ExtremeMarketStatusResponse,
    summary="Get extreme market alert status for an account",
    dependencies=[Depends(verify_account_ownership)],
)
def get_extreme_market_status(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> ExtremeMarketStatusResponse:
    """Return the current extreme market alert state.

    Reads the ``ExtremeMarketResponse`` attached to the account's
    ``MarketListener`` (if running). Returns an inactive default when no
    listener is active or the response module is not configured.
    """
    try:
        listener = service.get_listener()
        if listener is None:
            return ExtremeMarketStatusResponse(market="cn")

        em = getattr(listener, "_extreme_market_response", None)
        if em is None or not em.is_active():
            return ExtremeMarketStatusResponse(market="cn")

        alert = em.active_alert
        if alert is None:
            return ExtremeMarketStatusResponse(market="cn")

        return ExtremeMarketStatusResponse(
            market=alert.market,
            is_active=True,
            current_vol=float(alert.current_vol),
            historical_vol=float(alert.historical_vol),
            ratio=float(alert.ratio),
            actions=list(alert.actions),
            detected_at=alert.detected_at.isoformat() if alert.detected_at else None,
        )
    except Exception as exc:
        logger.error("[paper_trading] extreme market status failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ===================================================================
# A-01: Daily bars (frontend CandlestickChart)
# ===================================================================


@router.get(
    "/accounts/{account_id}/daily-bars/{code}",
    response_model=Dict[str, Any],
    summary="Daily OHLC bars for a stock code",
    dependencies=[Depends(verify_account_ownership)],
)
def get_daily_bars(
    account_id: int,
    code: str,
    days: int = Query(90, ge=1, le=500, description="Number of daily bars"),
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> Dict[str, Any]:
    """Return daily OHLC bars from the shared data fetcher.

    Matches the frontend contract ``getDailyBars`` (returns ``{items: [...]}``).
    Uses MarketListener.fetcher if available, else falls back to a fresh
    DataFetcherManager. On fetch failure returns an empty items list instead
    of raising (frontend shows empty state).
    """
    try:
        fetcher = None
        listener = service.get_listener()
        if listener is not None and getattr(listener, "fetcher", None) is not None:
            fetcher = listener.fetcher

        if fetcher is None:
            from data_provider.base import DataFetcherManager

            fetcher = DataFetcherManager()

        df, source = fetcher.get_daily_data(code, days=days)
        bars: List[Dict[str, Any]] = []
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                bars.append({
                    "date": str(getattr(row, "date", row.get("date", ""))),
                    "open": float(getattr(row, "open", row.get("open", 0))),
                    "high": float(getattr(row, "high", row.get("high", 0))),
                    "low": float(getattr(row, "low", row.get("low", 0))),
                    "close": float(getattr(row, "close", row.get("close", 0))),
                    "volume": float(getattr(row, "volume", row.get("volume", 0))),
                    "amount": float(getattr(row, "amount", row.get("amount", 0))),
                })
        return {
            "code": code,
            "source": str(source),
            "days": len(bars),
            "items": bars,
        }
    except Exception as exc:
        logger.warning("[paper_trading] daily-bars failed for %s: %s", code, exc)
        return {"code": code, "source": "error", "days": 0, "items": []}


# ===================================================================
# A-04: Strategy lifecycle
# ===================================================================


def _load_strategy_names() -> List[str]:
    """Load strategy names from the strategies configs directory."""
    from pathlib import Path

    from paper_trading.strategies import load_strategies_from_dir

    strategy_dir = Path("paper_trading/strategies/configs")
    try:
        strategies = load_strategies_from_dir(strategy_dir)
        return [s.name for s in strategies if s.name]
    except Exception as exc:
        logger.warning("[paper_trading] failed to load strategy names: %s", exc)
        return []


def _get_lifecycle() -> "Any":
    """Get the shared StrategyLifecycle (module-level singleton)."""
    from paper_trading.strategy_lifecycle import StrategyLifecycle

    return StrategyLifecycle()


@router.get(
    "/strategies/lifecycle",
    response_model=StrategyLifecycleListResponse,
    summary="List all strategies with lifecycle states",
)
def list_strategy_lifecycle(
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> StrategyLifecycleListResponse:
    """Return strategy lifecycle states for all configured strategies.

    Pre-seeds known strategy names from the YAML configs so the UI can
    render them even before any transition happened.
    """
    try:
        lc = _get_lifecycle()
        names = _load_strategy_names()
        items = []
        for name in names:
            state = lc.get_state(name)
            items.append(StrategyLifecycleItem(
                name=name,
                state=state.value if hasattr(state, "value") else str(state),
                is_live=lc.is_live(name),
            ))
        return StrategyLifecycleListResponse(items=items)
    except Exception as exc:
        logger.error("[paper_trading] strategy lifecycle list failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/strategies/lifecycle/{name}/transition",
    response_model=StrategyTransitionResponse,
    summary="Transition a strategy to a target lifecycle state",
)
def transition_strategy(
    name: str,
    request: StrategyTransitionRequest,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> StrategyTransitionResponse:
    """Transition a strategy lifecycle state. Illegal transitions return 400."""
    from paper_trading.strategy_lifecycle import LifecycleTransitionError

    try:
        lc = _get_lifecycle()
        # Validate target state enum
        lc.get_state(name)  # ensure strategy registered (DRAFT default)
        current = lc.get_state(name)
        new_state = lc.transition(name, request.new_state, operator=request.operator)
        return StrategyTransitionResponse(
            name=name,
            from_state=current.value if hasattr(current, "value") else str(current),
            to_state=new_state.value if hasattr(new_state, "value") else str(new_state),
            ok=True,
            message="",
        )
    except LifecycleTransitionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_transition", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("[paper_trading] strategy transition failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ===================================================================
# A-05: L2 depth quotes
# ===================================================================


@router.get(
    "/l2/{code}",
    response_model=L2DepthResponse,
    summary="Ten-level order-book snapshot for a stock code",
)
def get_l2_depth(
    code: str,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> L2DepthResponse:
    """Return the latest L2 ten-level order book.

    Uses the shared L2Fetcher if available; otherwise returns an empty
    order book with a marker so the frontend can show "L2 not available".
    """
    try:
        from data_provider.l2_fetcher import L2Fetcher

        fetcher = L2Fetcher()
        quote = fetcher.get_level2_quote(code)
        if quote is None:
            return L2DepthResponse(code=code, timestamp="", bids=[], asks=[], source="no-data")
        bids = [
            L2DepthLevel(price=float(quote.bid_prices[i]), volume=int(quote.bid_volumes[i]))
            for i in range(min(10, len(quote.bid_prices)))
            if quote.bid_prices[i] > 0
        ]
        asks = [
            L2DepthLevel(price=float(quote.ask_prices[i]), volume=int(quote.ask_volumes[i]))
            for i in range(min(10, len(quote.ask_prices)))
            if quote.ask_prices[i] > 0
        ]
        return L2DepthResponse(
            code=code,
            timestamp=quote.timestamp.isoformat(),
            bids=bids,
            asks=asks,
            bid_ask_imbalance=quote.bid_ask_imbalance,
            depth_weighted_spread=quote.depth_weighted_spread,
            source="tickflow",
        )
    except Exception as exc:
        logger.warning("[paper_trading] l2 depth failed for %s: %s", code, exc)
        return L2DepthResponse(code=code, timestamp="", bids=[], asks=[], source="error")


# ===================================================================
# Realtime WS: /ws/quotes + /ws/events (pending-api §2 / §3)
# ===================================================================


def _service_from_websocket(websocket: WebSocket) -> PaperTradingService:
    """Resolve the shared PaperTradingService from a WebSocket connection."""
    service = getattr(websocket.app.state, "paper_trading_service", None)
    if service is None:
        config = get_config_dep()
        db_manager = get_database_manager()
        service = PaperTradingService(config=config, db_manager=db_manager)
        websocket.app.state.paper_trading_service = service
    return service


def _collect_quotes(listener: Optional[MarketListener]) -> List[Dict[str, Any]]:
    """Build fresh quote messages from the listener's SharedQuoteCache.

    Matches the frontend ``QuoteItem`` shape consumed by ``QuoteTicker`` and
    ``useLivePositions`` (pending-api §2). Returns an empty list when the
    listener (or its quote cache) is unavailable.
    """
    quotes: List[Dict[str, Any]] = []
    cache = None
    if listener is not None:
        cache = getattr(listener, "_quote_cache", None)
    if cache is None:
        return quotes
    for code, cached in cache.get_all().items():
        quotes.append(
            {
                "code": code,
                "price": float(getattr(cached, "price", 0.0) or 0.0),
                "changePct": float(getattr(cached, "change_pct", 0.0) or 0.0),
                "volume": float(getattr(cached, "volume", 0.0) or 0.0),
                "timestamp": (
                    cached.timestamp.isoformat() if getattr(cached, "timestamp", None) else ""
                ),
            }
        )
    return quotes


@ws_router.websocket("/{account_id}/ws/quotes")
async def ws_quotes(websocket: WebSocket, account_id: int) -> None:
    """Push latest quotes for an account's watched codes (pending-api §2).

    Reads the MarketListener's SharedQuoteCache every push cycle (~3s).
    The handshake is rejected (HTTP 403) when the caller does not own the
    account. Listener not running → no messages are pushed; the frontend
    keeps the connection open and shows "等待行情推送".
    """
    try:
        verify_ws_account_ownership(websocket, account_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    service = _service_from_websocket(websocket)
    listener = service.get_listener()
    try:
        while True:
            import asyncio

            for quote in _collect_quotes(listener):
                await websocket.send_json(quote)
            await asyncio.sleep(3.0)
    except WebSocketDisconnect:
        logger.info("[paper_trading] ws/quotes client disconnected account=%s", account_id)
    except Exception as exc:
        logger.debug("[paper_trading] ws/quotes loop ended: %s", exc)


@ws_router.websocket("/{account_id}/ws/events")
async def ws_events(websocket: WebSocket, account_id: int) -> None:
    """Push paper-trading events + risk alerts (pending-api §3).

    Subscribes to the ``PaperTradingEventBus`` and forwards each payload
    as-is (already in the frontend contract shape). Recent events are replayed
    on connect. The subscription is removed on disconnect to avoid leaks.
    """
    try:
        verify_ws_account_ownership(websocket, account_id)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()

    from collections import deque

    from paper_trading.events import PaperTradingEventBus

    bus = PaperTradingEventBus.instance()
    queue: "deque[Dict[str, Any]]" = deque(maxlen=200)

    def _on_event(payload: Dict[str, Any]) -> None:
        queue.append(payload)

    bus.subscribe(_on_event)
    # Replay recent events so a freshly-connected feed shows recent activity.
    for payload in bus.replay():
        queue.append(payload)

    try:
        while True:
            import asyncio

            await asyncio.sleep(0.2)
            if queue:
                await websocket.send_json(queue.popleft())
    except WebSocketDisconnect:
        logger.info("[paper_trading] ws/events client disconnected account=%s", account_id)
    except Exception as exc:
        logger.debug("[paper_trading] ws/events loop ended: %s", exc)
    finally:
        bus.unsubscribe(_on_event)


# ===================================================================
# Drift / strategy performance / features (frontend-aligned additions)
# ===================================================================


def _drift_to_item(report: Any) -> DriftReportItem:
    """Serialize a DriftDetector.DriftReport to the frontend camelCase item."""
    return DriftReportItem(
        strategyName=report.strategy_name,
        isDrifting=bool(report.is_drifting),
        rollingSharpe=[round(float(v), 4) for v in report.rolling_sharpe],
        sharpeTrend=round(float(report.sharpe_trend), 4),
        consecutiveLosingDays=int(report.consecutive_losing_days),
        recommendedAction=report.recommended_action,
    )


def _strategy_status(name: str, recommended_action: str, weight: float) -> str:
    """Map lifecycle state + drift action to the frontend status enum."""
    try:
        from paper_trading.strategy_lifecycle import StrategyLifecycle

        state = StrategyLifecycle().get_state(name)
        state_str = state.value if hasattr(state, "value") else str(state)
        if state_str == "PAUSED":
            return "paused"
        if state_str == "RETIRED":
            return "retired"
    except Exception:
        pass
    if recommended_action == "retire":
        return "retired"
    if recommended_action == "pause" or weight <= 0.0:
        return "paused"
    if recommended_action == "reduce_weight":
        return "reduced"
    return "active"


def _compute_strategy_trade_metrics(
    account_id: int, db_manager: DatabaseManager
) -> Dict[str, Dict[str, Any]]:
    """Compute per-strategy trade metrics from the DB (running cost basis).

    Replays filled trades in chronological order, maintaining a running
    average cost per (strategy, code). Returns ``{strategy_name: {...}}`` with
    camelCase metric keys consumed by StrategyLeaderboard.
    """
    from src.storage import PaperOrder, PaperTrade

    with db_manager.session_scope() as session:
        orders = session.execute(
            select(PaperOrder).where(PaperOrder.account_id == account_id)
        ).scalars().all()
        trades = session.execute(
            select(PaperTrade)
            .where(PaperTrade.account_id == account_id)
            .order_by(PaperTrade.traded_at)
        ).scalars().all()
        # Snapshot into plain tuples while the session is open — the ORM
        # instances would be detached (and lazy-load would fail) afterwards.
        order_rows: List[Any] = [(o.id, o.strategy_name) for o in orders]
        trade_rows: List[Any] = [
            (t.order_id, t.code, t.side, t.price, t.quantity, t.fee, t.traded_at)
            for t in trades
        ]

    order_strategy: Dict[int, str] = {
        oid: (sname or "manual") for oid, sname in order_rows
    }
    by_strategy: Dict[str, List[Any]] = {}
    for (order_id, _code, _side, _price, _qty, _fee, _traded_at) in trade_rows:
        by_strategy.setdefault(order_strategy.get(order_id, "manual"), []).append(
            (order_id, _code, _side, _price, _qty, _fee, _traded_at)
        )

    try:
        from paper_trading.account import PaperAccountManager

        account = PaperAccountManager(db_manager).snapshot(account_id)
        initial_capital = float(getattr(account, "initial_capital", 0.0) or 0.0)
    except Exception:
        initial_capital = 0.0

    import numpy as np

    out: Dict[str, Dict[str, Any]] = {}
    for sname, trade_list in by_strategy.items():
        pos: Dict[str, List[float]] = {}  # code -> [qty, avg_cost]
        realized_by_day: Dict[str, float] = {}
        win_trades = 0
        realized_trades = 0
        total_realized = 0.0
        for (_order_id, code, side, price, qty, fee, traded_at) in trade_list:
            qty = float(qty or 0.0)
            price = float(price or 0.0)
            fee = float(fee or 0.0)
            day = traded_at.date().isoformat() if traded_at else ""
            p = pos.get(code, [0.0, 0.0])
            if side == "buy":
                new_qty = p[0] + qty
                new_cost = ((p[0] * p[1]) + (qty * price)) / new_qty if new_qty > 0 else 0.0
                pos[code] = [new_qty, new_cost]
            else:  # sell
                if p[0] > 0:
                    realized = (price - p[1]) * qty - fee
                    total_realized += realized
                    realized_by_day[day] = realized_by_day.get(day, 0.0) + realized
                    realized_trades += 1
                    if realized > 0:
                        win_trades += 1
                    pos[code] = [p[0] - qty, p[1]]

        trade_count = len(trade_list)
        win_rate = (win_trades / realized_trades) if realized_trades else 0.0

        days = sorted(realized_by_day)
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for d in days:
            cum += realized_by_day[d]
            peak = max(peak, cum)
            if peak > 0:
                dd = (peak - cum) / peak
                max_dd = max(max_dd, dd)
        max_dd_pct = max_dd * 100.0

        num_days = len(days) or 1
        avg_daily_return_pct = (
            ((total_realized / (initial_capital or 1.0)) / num_days) * 100.0 if days else 0.0
        )

        daily_values = [realized_by_day[d] for d in days]
        sharpe = 0.0
        if len(daily_values) > 1:
            arr = np.asarray(daily_values, dtype=float)
            std = float(arr.std())
            if std > 0:
                sharpe = (float(arr.mean()) / std) * np.sqrt(242)

        annualized_return_pct = avg_daily_return_pct * 242
        calmar = (annualized_return_pct / max_dd_pct) if max_dd_pct > 0 else None

        out[sname] = {
            "tradeCount": trade_count,
            "winRate": round(win_rate, 4),
            "maxDrawdownPct": round(max_dd_pct, 4),
            "avgDailyReturnPct": round(avg_daily_return_pct, 4),
            "sharpeRatio": round(sharpe, 4),
            "calmarRatio": round(calmar, 4) if calmar is not None else None,
        }
    return out


def _watched_codes(service: PaperTradingService) -> List[str]:
    """Return the account's watched codes (listener config first)."""
    listener = service.get_listener()
    if listener is not None:
        codes = list(getattr(listener.config, "watched_codes", []) or [])
        if codes:
            return codes
    return list(getattr(service.config, "stock_list", []) or [])[:50]


def _daily_data_for_codes(
    service: PaperTradingService, codes: List[str]
) -> Dict[str, Any]:
    """Collect daily DataFrames from the listener cache, falling back to fetch."""
    listener = service.get_listener()
    cache = getattr(listener, "_daily_df_cache", None) if listener is not None else None
    out: Dict[str, Any] = {}
    if cache:
        for code in codes:
            wrapper = cache.get(code)
            if wrapper is not None:
                _, df = wrapper
                if df is not None and not df.empty:
                    out[code] = df
    missing = [c for c in codes if c not in out]
    if missing:
        fetcher = service._get_data_fetcher()
        for code in missing[:20]:
            try:
                df, _source = fetcher.get_daily_data(code, days=90)
                if df is not None and not df.empty:
                    out[code] = df
            except Exception:
                continue
    return out


def _feature_rows(features: Any) -> List[Dict[str, Any]]:
    """Convert a (code, date) MultiIndex feature DataFrame to camelCase rows."""
    rows: List[Dict[str, Any]] = []

    def _f(v: Any) -> float:
        try:
            x = float(v)
            return x if x == x else 0.0  # NaN -> 0.0
        except (TypeError, ValueError):
            return 0.0

    if features is None or features.empty:
        return rows
    for (code, day), row in features.iterrows():
        rows.append(
            {
                "code": str(code),
                "date": str(day),
                "smaCrossover": _f(row.get("sma_crossover")),
                "rsi": _f(row.get("rsi")),
                "volumeSpike": _f(row.get("volume_spike")),
                "maAlignment": _f(row.get("ma_alignment")),
                "bidAskImbalance": _f(row.get("bid_ask_imbalance")),
            }
        )
    return rows


def _feature_snapshot(service: PaperTradingService, account_id: int) -> Dict[str, Any]:
    """Build a feature snapshot (asOf / features / skippedCodes)."""
    pipeline = service.feature_pipeline()
    codes = _watched_codes(service)
    if not codes:
        return {"asOf": "", "features": [], "skippedCodes": []}
    daily_data = _daily_data_for_codes(service, codes)
    if not daily_data:
        return {"asOf": "", "features": [], "skippedCodes": list(codes)}
    try:
        features = pipeline.run(list(daily_data.keys()), daily_data)
    except Exception as exc:
        logger.warning("[paper_trading] feature pipeline run failed: %s", exc)
        return {"asOf": "", "features": [], "skippedCodes": list(codes)}
    return {
        "asOf": date.today().isoformat(),
        "features": _feature_rows(features),
        "skippedCodes": list(getattr(pipeline, "skipped", []) or []),
    }


@router.get(
    "/accounts/{account_id}/drift",
    response_model=List[DriftReportItem],
    summary="List strategy drift reports",
    dependencies=[Depends(verify_account_ownership)],
)
def get_strategy_drift(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> List[DriftReportItem]:
    """Return drift-detection reports for all configured strategies."""
    try:
        names = _load_strategy_names()
        if not names:
            listener = service.get_listener()
            if listener is not None:
                names = [s.name for s in listener.strategies]
        detector = service.drift_detector()
        return [_drift_to_item(detector.check(n)) for n in names]
    except Exception as exc:
        logger.error("[paper_trading] get_strategy_drift failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/strategies/performance",
    response_model=List[StrategyPerformanceItem],
    summary="List strategy performance leaderboard",
    dependencies=[Depends(verify_account_ownership)],
)
def get_strategy_performance(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> List[StrategyPerformanceItem]:
    """Return per-strategy performance rows for the leaderboard."""
    try:
        names = _load_strategy_names()
        if not names:
            listener = service.get_listener()
            if listener is not None:
                names = [s.name for s in listener.strategies]
        drift = service.drift_detector()
        fusion = service.signal_fusion()
        trade_metrics = _compute_strategy_trade_metrics(account_id, db_manager)

        items: List[StrategyPerformanceItem] = []
        for name in names:
            report = drift.check(name)
            tm = trade_metrics.get(name, {})
            sharpe = (
                float(report.rolling_sharpe[-1])
                if report.rolling_sharpe
                else float(tm.get("sharpeRatio", 0.0))
            )
            weight = 1.0
            if hasattr(fusion, "_strategy_weights"):
                weight = float(fusion._strategy_weights.get(name, 1.0))
            items.append(
                StrategyPerformanceItem(
                    name=name,
                    sharpeRatio=round(sharpe, 4),
                    winRate=float(tm.get("winRate", 0.0)),
                    maxDrawdownPct=float(tm.get("maxDrawdownPct", 0.0)),
                    calmarRatio=tm.get("calmarRatio"),
                    avgDailyReturnPct=float(tm.get("avgDailyReturnPct", 0.0)),
                    currentWeight=round(weight, 4),
                    status=_strategy_status(name, report.recommended_action, weight),
                    tradeCount=int(tm.get("tradeCount", 0)),
                )
            )
        return items
    except Exception as exc:
        logger.error("[paper_trading] get_strategy_performance failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/accounts/{account_id}/features",
    response_model=FeatureSnapshotResponse,
    summary="Get feature-pipeline snapshot",
    dependencies=[Depends(verify_account_ownership)],
)
def get_features_snapshot(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> FeatureSnapshotResponse:
    """Return the latest computed feature snapshot (asOf/features/skippedCodes)."""
    try:
        return FeatureSnapshotResponse(**_feature_snapshot(service, account_id))
    except Exception as exc:
        logger.error("[paper_trading] get_features_snapshot failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/accounts/{account_id}/features/recompute",
    response_model=FeatureRecomputeResponse,
    summary="Trigger feature-pipeline recompute",
    dependencies=[Depends(verify_account_ownership)],
)
def recompute_features(
    account_id: int,
    service: PaperTradingService = Depends(get_paper_trading_service),
) -> FeatureRecomputeResponse:
    """Run the feature pipeline now and persist the result to parquet."""
    try:
        pipeline = service.feature_pipeline()
        codes = _watched_codes(service)
        daily_data = _daily_data_for_codes(service, codes)
        as_of = date.today()
        saved_path: Optional[str] = None
        if daily_data:
            features = pipeline.run(list(daily_data.keys()), daily_data)
            if features is not None and not features.empty:
                path = pipeline.save(features, as_of)
                saved_path = str(path)
        return FeatureRecomputeResponse(
            ok=True,
            message=f"features recomputed for {len(daily_data)} codes",
            asOf=as_of.isoformat(),
            savedPath=saved_path,
        )
    except Exception as exc:
        logger.error("[paper_trading] recompute_features failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
