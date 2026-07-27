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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select

from api.deps import get_config_dep, get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.paper_trading import (
    AccountCreateRequest,
    AccountSnapshotResponse,
    BatchOrderCreateRequest,
    BatchOrderResponse,
    BattlePlanGenerateRequest,
    BattlePlanItem,
    BattlePlanMarkdownResponse,
    ConditionalOrderCreateRequest,
    ConditionalOrderItem,
    DailyReflectionRequest,
    DailyReportResponse,
    DrawdownItem,
    HoldingPlanItem,
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

router = APIRouter()


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

        from paper_trading.market_listener import build_default_listener

        watched_codes = request.watched_codes or list(
            getattr(self.config, "stock_list", []) or []
        )
        markets = set(request.markets) if request.markets else {"cn"}

        # PM agent / reflection / battle plan hooks are wired only when
        # their respective flags are enabled. This keeps the listener
        # lightweight when LLM credentials are missing.
        pm_agent = None
        if getattr(self.config, "paper_trading_enable_pm_agent", False):
            try:
                pm_agent = self.pm_agent()
            except Exception as exc:
                logger.warning(
                    "[PaperTradingService] PM agent unavailable, listener will run without it: %s",
                    exc,
                )

        reflection_engine = self.reflection_engine() if request.enable_daily_reflection else None
        battle_plan_generator = (
            self.battle_plan_generator() if request.enable_battle_plan else None
        )

        listener = build_default_listener(
            config=self.config,
            account_id=request.account_id,
            watched_codes=watched_codes,
            markets=markets,
            tick_interval_seconds=request.tick_interval_seconds or 10.0,
            enable_strategies=request.enable_strategies,
            pm_agent=pm_agent,
            reflection_engine=reflection_engine,
            battle_plan_generator=battle_plan_generator,
            pm_decision_interval_seconds=request.pm_decision_interval_seconds,
            enable_daily_reflection=request.enable_daily_reflection,
            enable_battle_plan=request.enable_battle_plan,
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


@router.get(
    "/accounts/{account_id}",
    response_model=AccountSnapshotResponse,
    responses={
        404: {"description": "Account not found", "model": ErrorResponse},
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="Get account snapshot",
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
                fill_price = float(order.price or 0.0)
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


@router.get(
    "/accounts/{account_id}/positions",
    response_model=PositionListResponse,
    responses={
        500: {"description": "Server error", "model": ErrorResponse},
    },
    summary="List open positions",
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
)
async def generate_daily_report(
    account_id: int,
    save: bool = True,
) -> DailyReportResponse:
    """Generate a daily trading report (P2-A)."""
    from datetime import date as date_cls
    from paper_trading.content_generator import ContentGenerator
    try:
        generator = ContentGenerator()
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
