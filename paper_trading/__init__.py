# -*- coding: utf-8 -*-
"""Paper trading subsystem.

Real-time paper trading with:
- Virtual account (default initial capital 1000 CNY)
- Programmatic rule strategies (strategies_v2/) as primary signals
- Agent risk-control layer for secondary confirmation
- Intraday market listener triggering order matching
- Order / position / trade / signal / net-value persistence (SQLAlchemy ORM)

Public API surface re-exported here for convenience.
"""

from paper_trading.account import PaperAccountManager, AccountSnapshot
from paper_trading.agent_risk import AgentReviewResult, AgentRiskReviewer
from paper_trading.battle_plan import (
    BattlePlan,
    BattlePlanGenerator,
    CandidatePlan,
    HoldingPlan,
    build_battle_plan_generator,
)
from paper_trading.content_generator import (
    ContentGenerator,
    DailyReportResult,
    build_content_generator,
)
from paper_trading.fees import FeeModel, DEFAULT_FEE_MODEL
from paper_trading.market_listener import (
    MarketListener,
    MarketListenerConfig,
    build_default_listener,
    is_market_open_now,
)
from paper_trading.notification_integration import (
    PaperTradingNotifier,
    PushResult,
    build_paper_trading_notifier,
)
from paper_trading.performance import (
    DrawdownRecord,
    PerformanceAnalyzer,
    PerformanceConfig,
    PerformanceMetrics,
)
from paper_trading.order import (
    OrderSide,
    OrderType,
    OrderStatus,
    OrderRequest,
    OrderManager,
)
from paper_trading.position import PositionManager, PositionSnapshot
from paper_trading.reflection import (
    ReflectionEngine,
    ReflectionNote,
    build_reflection_engine,
)
from paper_trading.risk import RiskChecker, RiskConfig, RiskDecision
from paper_trading.risk_order_adapter import OrderCommand, RiskOrderAdapter
from paper_trading.sltp_calculator import (
    SLTPCalculator,
    SLTPResult,
    build_sltp_calculator,
)
from paper_trading.trading_engine import TradeResult, TradingEngine

# Portfolio Manager agent (P0-B) - imported lazily-friendly from src.agent.
# Re-exported here so callers can do ``from paper_trading import PortfolioManagerAgent``.
from src.agent.portfolio_manager_agent import (  # noqa: E402
    PMDecision,
    PortfolioManagerAgent,
    build_portfolio_manager_agent,
    register_paper_trading_tools,
)

__all__ = [
    # Account
    "PaperAccountManager",
    "AccountSnapshot",
    # Agent risk review
    "AgentRiskReviewer",
    "AgentReviewResult",
    # Fees
    "FeeModel",
    "DEFAULT_FEE_MODEL",
    # Market listener (Phase 5)
    "MarketListener",
    "MarketListenerConfig",
    "build_default_listener",
    "is_market_open_now",
    # Orders
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "OrderRequest",
    "OrderManager",
    # Positions
    "PositionManager",
    "PositionSnapshot",
    # Risk
    "RiskChecker",
    "RiskConfig",
    "RiskDecision",
    # Risk order adapter (R2 fix)
    "RiskOrderAdapter",
    "OrderCommand",
    # Engine
    "TradingEngine",
    "TradeResult",
    # Portfolio Manager agent (P0-B)
    "PortfolioManagerAgent",
    "PMDecision",
    "build_portfolio_manager_agent",
    "register_paper_trading_tools",
    # Reflection engine (P0-D)
    "ReflectionEngine",
    "ReflectionNote",
    "build_reflection_engine",
    # SLTP calculator (P1-A)
    "SLTPCalculator",
    "SLTPResult",
    "build_sltp_calculator",
    # Battle plan generator (P1-B)
    "BattlePlan",
    "BattlePlanGenerator",
    "HoldingPlan",
    "CandidatePlan",
    "build_battle_plan_generator",
    # Content generator (P2-A)
    "ContentGenerator",
    "DailyReportResult",
    "build_content_generator",
    # Notification integration (P2-B)
    "PaperTradingNotifier",
    "PushResult",
    "build_paper_trading_notifier",
    # Performance analytics (Phase 2)
    "PerformanceAnalyzer",
    "PerformanceMetrics",
    "DrawdownRecord",
    "PerformanceConfig",
]
