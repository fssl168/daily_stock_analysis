# -*- coding: utf-8 -*-
"""Paper trading API schemas (P3-A).

Pydantic models for the paper trading subsystem endpoints. Mirrors the
dataclasses/ORM rows exposed by ``paper_trading/``:

- Account snapshots (cash, net value, return %)
- Orders (create / cancel / modify / list)
- Positions (list with PnL)
- Trades (history)
- Signals (audit trail with agent verdict)
- Reflection notes (post-trade / daily reviews)
- Battle plans (next-day operations card)
- PM decisions (AI portfolio manager timeline)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


class AccountSnapshotResponse(BaseModel):
    """Paper trading account snapshot."""

    account_id: int
    name: str
    initial_capital: float
    cash: float
    frozen_cash: float
    total_market_value: float = Field(0.0, description="Sum of position market value")
    net_value: float = Field(..., description="Cash + market value")
    return_pct: float = Field(0.0, description="(net_value/initial_capital - 1) * 100")
    position_count: int = 0
    status: str = "active"


class AccountCreateRequest(BaseModel):
    """Create-or-reset paper trading account."""

    name: str = Field("default", description="Account name (unique)")
    initial_capital: float = Field(1000.0, gt=0, description="Initial cash (CNY)")
    reset_if_exists: bool = Field(
        False, description="If true and account exists, reset its cash/positions"
    )


class AccountUpdateRequest(BaseModel):
    """Update paper trading account metadata."""

    name: Optional[str] = Field(None, description="New account name (unique)")
    initial_capital: Optional[float] = Field(
        None, gt=0, description="Initial capital for return calculation"
    )


class AccountListItem(BaseModel):
    """Minimal paper trading account item for list views."""

    account_id: int
    name: str
    initial_capital: float
    cash: float
    frozen_cash: float
    total_market_value: float = Field(0.0, description="Sum of position market value")
    net_value: float = Field(0.0, description="Cash + market value")
    return_pct: float = Field(0.0, description="(net_value/initial_capital - 1) * 100")
    position_count: int = 0
    status: str = "active"


class AccountListResponse(BaseModel):
    """List of paper trading accounts."""

    accounts: List[AccountListItem]
    total: int


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderCreateRequest(BaseModel):
    """Submit a manual order (bypasses signal pipeline).

    For strategy-driven orders, use the signal endpoint instead.
    """

    account_id: int
    code: str
    side: str = Field(..., description="buy | sell")
    quantity: float = Field(..., gt=0)
    order_type: str = Field("market", description="market | limit")
    limit_price: Optional[float] = Field(None, gt=0)
    name: Optional[str] = None
    strategy_name: Optional[str] = None
    reason: Optional[str] = None


class OrderCancelRequest(BaseModel):
    """Cancel a pending signal and its associated order."""

    signal_id: int
    reason: Optional[str] = None


class OrderModifyRequest(BaseModel):
    """Modify a pending limit order's price/quantity."""

    signal_id: int
    new_limit_price: Optional[float] = Field(None, gt=0)
    new_quantity: Optional[float] = Field(None, gt=0)
    reason: Optional[str] = None


class TradeResultResponse(BaseModel):
    """Outcome of a submitted signal/order."""

    signal_id: int
    order_id: Optional[int] = None
    side: str
    code: str
    status: str = Field(..., description="executed | rejected | pending")
    fill_price: Optional[float] = None
    fill_quantity: Optional[float] = None
    fee: Optional[float] = None
    reason: str = ""
    risk_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    agent_review: Optional[Dict[str, Any]] = None


class OrderItem(BaseModel):
    """Paper order row."""

    id: int
    account_id: int
    code: str
    name: Optional[str] = None
    side: str
    order_type: str
    price: Optional[float] = None
    quantity: float
    filled_quantity: float = 0.0
    filled_price_avg: float = 0.0
    status: str
    strategy_name: Optional[str] = None
    signal_id: Optional[int] = None
    reason: Optional[str] = None
    reject_reason: Optional[str] = None
    created_at: Optional[str] = None
    filled_at: Optional[str] = None


class OrderListResponse(BaseModel):
    account_id: int
    total: int
    items: List[OrderItem]


class BatchOrderItem(BaseModel):
    """One order inside a batch create request."""

    code: str
    side: str = Field(..., description="buy | sell")
    quantity: float = Field(..., gt=0)
    order_type: str = Field("market", description="market | limit")
    limit_price: Optional[float] = Field(None, gt=0)
    name: Optional[str] = None
    strategy_name: Optional[str] = None
    reason: Optional[str] = None


class BatchOrderCreateRequest(BaseModel):
    """Create multiple orders atomically."""

    account_id: int
    orders: List[BatchOrderItem] = Field(..., min_length=1)


class BatchOrderResponse(BaseModel):
    """Result of a batch order submission."""

    account_id: int
    total: int
    results: List[TradeResultResponse]


class ConditionalOrderCreateRequest(BaseModel):
    """Create a conditional order (stop-loss / take-profit / OCO)."""

    account_id: int
    code: str
    side: str = Field(..., description="buy | sell")
    quantity: float = Field(..., gt=0)
    order_type: str = Field(
        ...,
        description="stop_loss | take_profit | oco_primary | oco_secondary",
    )
    trigger_price: float = Field(..., gt=0)
    limit_price: Optional[float] = Field(None, gt=0)
    linked_order_id: Optional[int] = Field(
        None, description="Sibling order id for OCO linkage"
    )
    name: Optional[str] = None
    strategy_name: Optional[str] = None
    reason: Optional[str] = None


class ConditionalOrderItem(OrderItem):
    """Conditional order response item (includes trigger metadata)."""

    trigger_price: Optional[float] = None
    linked_order_id: Optional[int] = None
    triggered_at: Optional[str] = None


class OrderListFilterParams(BaseModel):
    """Query parameters for order listing."""

    status: Optional[str] = Field(None, description="Filter by order status")
    side: Optional[str] = Field(None, description="buy | sell")
    code: Optional[str] = Field(None, description="Filter by stock code")
    from_date: Optional[str] = Field(None, description="ISO date / datetime")
    to_date: Optional[str] = Field(None, description="ISO date / datetime")
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


# ---------------------------------------------------------------------------
# Positions / Trades / Signals
# ---------------------------------------------------------------------------


class PositionItem(BaseModel):
    account_id: int
    code: str
    name: Optional[str] = None
    quantity: float
    available_quantity: float
    avg_cost: float
    last_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    take_profit_2: Optional[float] = None
    sltp_reasoning: Optional[str] = None
    floating_pnl: float = 0.0
    floating_pnl_pct: float = 0.0


class PositionListResponse(BaseModel):
    account_id: int
    positions: List[PositionItem]
    total_market_value: float


class TradeItem(BaseModel):
    id: int
    order_id: int
    account_id: int
    code: str
    name: Optional[str] = None
    side: str
    fill_price: float
    fill_quantity: float
    fee: float
    realized_pnl: Optional[float] = None
    traded_at: str


class TradeListResponse(BaseModel):
    account_id: int
    total: int
    items: List[TradeItem]


class SignalItem(BaseModel):
    id: int
    account_id: int
    code: str
    name: Optional[str] = None
    side: str
    trigger_price: float
    suggested_quantity: Optional[float] = None
    strategy_name: Optional[str] = None
    rule_name: Optional[str] = None
    reason: Optional[str] = None
    status: str
    agent_confirmed: Optional[bool] = None
    agent_reason: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: str


class SignalListResponse(BaseModel):
    account_id: int
    total: int
    items: List[SignalItem]


# ---------------------------------------------------------------------------
# Reflection notes
# ---------------------------------------------------------------------------


class ReflectionNoteItem(BaseModel):
    id: int
    account_id: int
    scope: str = Field(..., description="trade | daily | weekly | adhoc")
    subject: str
    summary: str
    takeaway: str
    lessons: List[str] = Field(default_factory=list)
    tags: str = ""
    mood: str = "neutral"
    trade_id: Optional[int] = None
    order_id: Optional[int] = None
    code: Optional[str] = None
    created_at: str


class ReflectionListResponse(BaseModel):
    account_id: int
    total: int
    items: List[ReflectionNoteItem]


class DailyReflectionRequest(BaseModel):
    """Trigger a daily reflection manually."""

    account_id: int
    review_date: Optional[str] = Field(
        None, description="ISO date; defaults to today"
    )


# ---------------------------------------------------------------------------
# Battle plan
# ---------------------------------------------------------------------------


class HoldingPlanItem(BaseModel):
    code: str
    name: str = ""
    current_price: float
    strong_scenario: str = ""
    neutral_scenario: str = ""
    weak_scenario: str = ""
    action_conditions: List[str] = Field(default_factory=list)
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None


class CandidatePlanItem(BaseModel):
    code: str
    name: str = ""
    auction_condition: str = ""
    intraday_trigger: str = ""
    position_ratio: float
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    technical_score: float


class BattlePlanItem(BaseModel):
    plan_id: int
    account_id: int
    date: str
    holdings_plans: List[HoldingPlanItem] = Field(default_factory=list)
    candidates: List[CandidatePlanItem] = Field(default_factory=list)
    market_review: str = ""
    sentiment_score: int = 50
    main_theme: str = ""
    used_fallback: bool = False
    created_at: Optional[str] = None


class BattlePlanGenerateRequest(BaseModel):
    """Trigger battle plan generation manually."""

    account_id: int
    target_date: Optional[str] = Field(
        None, description="ISO date; defaults to next trading day"
    )
    watched_codes: Optional[List[str]] = None


class BattlePlanMarkdownResponse(BaseModel):
    """Markdown rendering of a battle plan (for Lark/DingTalk push)."""

    plan_id: int
    date: str
    markdown: str


# ---------------------------------------------------------------------------
# PM decisions
# ---------------------------------------------------------------------------


class PMDecisionItem(BaseModel):
    id: int
    account_id: int
    action: str
    code: Optional[str] = None
    name: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0
    elapsed_seconds: float = 0.0
    used_fallback: bool = False
    error: Optional[str] = None
    status: str = Field("pending", description="pending / executed / rejected / skipped")
    signal_id: Optional[int] = None
    order_id: Optional[int] = None
    created_at: str


class PMDecisionListResponse(BaseModel):
    account_id: int
    total: int
    items: List[PMDecisionItem]


class PMDecisionTriggerRequest(BaseModel):
    """Manually trigger one PM decision cycle."""

    account_id: int
    extra_context: Optional[Dict[str, Any]] = None


class PMDecisionExecuteResponse(BaseModel):
    """Result of executing a pending PM decision."""

    decision_id: int
    account_id: int
    signal_id: int
    order_id: Optional[int] = None
    side: str
    code: str
    status: str = Field(..., description="executed | rejected | pending")
    fill_price: Optional[float] = None
    fill_quantity: Optional[float] = None
    fee: Optional[float] = None
    reason: str = ""


class PMDecisionIgnoreRequest(BaseModel):
    """Ignore / skip a pending PM decision."""

    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Net value curve
# ---------------------------------------------------------------------------


class NetValuePoint(BaseModel):
    date: str
    net_value: float
    cash: float
    market_value: float
    return_pct: float = 0.0


class NetValueCurveResponse(BaseModel):
    account_id: int
    points: List[NetValuePoint]


# ---------------------------------------------------------------------------
# Listener status
# ---------------------------------------------------------------------------


class ListenerStatusResponse(BaseModel):
    """Runtime status of the MarketListener (if started)."""

    running: bool
    account_id: Optional[int] = None
    watched_codes_count: int = 0
    strategies_count: int = 0
    markets: List[str] = Field(default_factory=list)
    last_settle_date: Optional[str] = None
    last_battle_plan_date: Optional[str] = None
    last_daily_reflection_date: Optional[str] = None
    last_pm_decision_at: Optional[Dict[str, str]] = None


class ListenerStartRequest(BaseModel):
    """Start the MarketListener with optional overrides."""

    account_id: int
    watched_codes: Optional[List[str]] = None
    markets: Optional[List[str]] = None
    tick_interval_seconds: Optional[float] = Field(None, gt=0)
    enable_strategies: bool = True
    enable_agent_review: bool = False
    enable_daily_reflection: bool = True
    enable_battle_plan: bool = True
    pm_decision_interval_seconds: Optional[float] = Field(None, ge=0)


class ListenerControlResponse(BaseModel):
    """Result of a listener start/stop command."""

    running: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Performance / Risk metrics (Phase 2)
# ---------------------------------------------------------------------------


class PerformanceMetricsResponse(BaseModel):
    """Account performance summary."""

    account_id: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    max_drawdown_start_date: Optional[str] = None
    max_drawdown_end_date: Optional[str] = None
    volatility_annualized: Optional[float] = None
    win_rate: float = 0.0
    profit_factor: Optional[float] = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
    calmar_ratio: Optional[float] = None
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0


class DrawdownItem(BaseModel):
    """A single point on the account drawdown curve."""

    date: str
    net_value: float
    peak_net_value: float
    drawdown_pct: float


class RiskMetricsResponse(BaseModel):
    """Current risk snapshot for an account."""

    account_id: int
    max_single_stock_concentration_pct: float = 0.0
    max_open_positions_limit: int = 8
    current_open_positions: int = 0
    max_pct_per_stock_limit: float = 30.0
    max_cash_per_buy_limit: float = 50.0
    max_daily_loss_limit: float = 5.0
    current_drawdown_pct: float = 0.0


# ---------------------------------------------------------------------------
# Backtest comparison (P3-F)
# ---------------------------------------------------------------------------


class PaperTradingScenario(BaseModel):
    """Paper-trading history packaged like a backtest scenario."""

    account_id: int
    strategy_name: str
    base_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 1000.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    net_value_curve: List[NetValuePoint] = Field(default_factory=list)
    trades: List[Dict[str, Any]] = Field(default_factory=list)


class BacktestPaperComparisonMetric(BaseModel):
    """Single metric compared between backtest and paper trading."""

    backtest: Optional[float] = None
    paper: Optional[float] = None
    delta: Optional[float] = None


class BacktestPaperComparisonSampleSize(BaseModel):
    """Sample size metadata for the comparison."""

    backtest_completed: int = 0
    backtest_long_signals: int = 0
    paper_trades: int = 0


class BacktestPaperComparisonMetrics(BaseModel):
    """Numeric comparison between backtest summary and paper trading record."""

    win_rate_pct: BacktestPaperComparisonMetric = Field(
        default_factory=BacktestPaperComparisonMetric
    )
    total_return_pct: BacktestPaperComparisonMetric = Field(
        default_factory=BacktestPaperComparisonMetric
    )
    max_drawdown_pct: Dict[str, Optional[float]] = Field(default_factory=dict)
    sample_size: BacktestPaperComparisonSampleSize = Field(
        default_factory=BacktestPaperComparisonSampleSize
    )


class BacktestPaperComparisonRequest(BaseModel):
    """Compare a backtest summary with the paper-trading account record."""

    strategy_name: str = Field(..., description="Strategy name to evaluate")
    backtest_summary: Optional[Dict[str, Any]] = Field(
        None, description="Backtest summary; if omitted the endpoint uses the latest overall summary"
    )
    persist_reflection: bool = Field(
        True, description="Persist the comparison as a reflection note"
    )


class BacktestPaperComparisonResponse(BaseModel):
    """Result of comparing backtest output with paper-trading performance."""

    account_id: int
    strategy_name: str
    paper_scenario: PaperTradingScenario
    backtest_summary: Dict[str, Any] = Field(default_factory=dict)
    metrics: BacktestPaperComparisonMetrics
    interpretation: str = ""
    generated_at: str = ""
    reflection_persisted: bool = False


# ---------------------------------------------------------------------------
# Daily report (P2-A)
# ---------------------------------------------------------------------------


class DailyReportResponse(BaseModel):
    """Response model for daily report generation."""

    date: str = Field(..., description="Report date (YYYY-MM-DD)")
    markdown: Optional[str] = Field(None, description="Full markdown content")
    report_path: Optional[str] = Field(None, description="Saved file path")
    voice_path: Optional[str] = Field(None, description="Saved voice script path")
    used_fallback: bool = Field(False, description="Whether fallback narrative was used")
    error: Optional[str] = Field(None, description="Error message if generation failed")

# ---------------------------------------------------------------------------
# Breaker status (integration ①)
# ---------------------------------------------------------------------------

class BreakerStatusResponse(BaseModel):
    """Circuit breaker state for frontend consumption."""
    account_id: int = Field(..., description="Account ID")
    level: str = Field("normal", description="Breaker level: normal/soft/hard/liquidate")
    can_trade: bool = Field(True, description="Whether any trade is allowed")
    can_open_new: bool = Field(True, description="Whether new positions can be opened")
    reason: str = Field("", description="Breaker reason if engaged")
    triggered_at: Optional[str] = Field(None, description="ISO timestamp of trigger")
