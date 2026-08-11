# -*- coding: utf-8 -*-
"""Paper trading subsystem.

Real-time paper trading with:
- Virtual account (default initial capital 1000 CNY)
- Programmatic rule strategies (strategies/) as primary signals
- Agent risk-control layer for secondary confirmation
- Intraday market listener triggering order matching
- Order / position / trade / signal / net-value persistence (SQLAlchemy ORM)

Public API surface re-exported here for convenience.
"""


from typing import List, Optional, Dict, Any

from paper_trading.account import PaperAccountManager, AccountSnapshot
from paper_trading.agent_risk import AgentReviewResult, AgentRiskReviewer
from paper_trading.backtest_adapter import (
    PaperTradingScenario, PaperTradingToBacktestAdapter,
    run_with_paper_validation, update_paper_trading_from_backtest, )
from paper_trading.battle_plan import (
    BattlePlan, BattlePlanGenerator, CandidatePlan, HoldingPlan, build_battle_plan_generator, )
from paper_trading.content_generator import (
    ContentGenerator, DailyReportResult, build_content_generator, )
from paper_trading.fees import FeeModel, DEFAULT_FEE_MODEL
from paper_trading.market_listener import (
    MarketListener, MarketListenerConfig, build_default_listener, is_market_open_now, )
from paper_trading.notification_integration import (
    PaperTradingNotifier, PushResult, build_paper_trading_notifier, )
from paper_trading.performance import (
    DrawdownRecord, PerformanceAnalyzer, PerformanceConfig, PerformanceMetrics, )
from paper_trading.order import (
    OrderSide, OrderType, OrderStatus, OrderRequest, OrderManager, )
from paper_trading.position import PositionManager, PositionSnapshot
from paper_trading.reflection import (
    ReflectionEngine, ReflectionNote, build_reflection_engine, )
from paper_trading.risk import RiskChecker, RiskConfig, RiskDecision
from paper_trading.risk_order_adapter import OrderCommand, RiskOrderAdapter
from paper_trading.sltp_calculator import (
    SLTPCalculator, SLTPResult, build_sltp_calculator, )
from paper_trading.trading_engine import TradeResult, TradingEngine

# Portfolio Manager agent (P0-B) - imported lazily-friendly from src.agent.
# Re-exported here so callers can do ``from paper_trading import PortfolioManagerAgent``.
from src.agent.portfolio_manager_agent import (  # noqa: E402
    PMDecision, PortfolioManagerAgent, build_portfolio_manager_agent, register_paper_trading_tools, )

__all__ = [
    # Account
    "PaperAccountManager", "AccountSnapshot",
    # Backtest-paper integration (P3-F)
    "PaperTradingScenario", "PaperTradingToBacktestAdapter",
    "run_with_paper_validation", "update_paper_trading_from_backtest",
    # Agent risk review
    "AgentRiskReviewer", "AgentReviewResult", # Fees
    "FeeModel", "DEFAULT_FEE_MODEL", # Market listener (Phase 5)
    "MarketListener", "MarketListenerConfig", "build_default_listener", "is_market_open_now", # Orders
    "OrderSide", "OrderType", "OrderStatus", "OrderRequest", "OrderManager", # Positions
    "PositionManager", "PositionSnapshot", # Risk
    "RiskChecker", "RiskConfig", "RiskDecision", # Risk order adapter (R2 fix)
    "RiskOrderAdapter", "OrderCommand", # Engine
    "TradingEngine", "TradeResult", # Portfolio Manager agent (P0-B)
    "PortfolioManagerAgent", "PMDecision", "build_portfolio_manager_agent", "register_paper_trading_tools", # Reflection engine (P0-D)
    "ReflectionEngine", "ReflectionNote", # SLTP calculator (P1-A)
    "SLTPCalculator", "SLTPResult", "build_sltp_calculator", # Battle plan generator (P1-B)
    "BattlePlan", "BattlePlanGenerator", "HoldingPlan", "CandidatePlan", "build_battle_plan_generator", # Content generator (P2-A)
    "ContentGenerator", "DailyReportResult", "build_content_generator", # Notification integration (P2-B)
    "PaperTradingNotifier", "PushResult", "build_paper_trading_notifier", # Performance analytics (Phase 2)
    "PerformanceAnalyzer", "PerformanceMetrics", "DrawdownRecord", "PerformanceConfig",
    # Stock list sync utility (P0)
    "get_watched_codes",
    # Hook functions for external integration (P1)
    "hooks",
]


def get_watched_codes(account_id: int = 0) -> List[str]:
    """获取纸面交易关注的股票代码，优先从 config 联动，其次从 env，最后默认空列表.

    如果 paper_trading_sync_stock_list=True 且配置了 STOCK_LIST，则直接使用自选股列表；
    否则使用显式配置的 paper_trading_watched_codes；如果两者都未设置，返回空列表。
    """
    from src.config import get_config
    from src.services.stock_list_parser import split_stock_list

    cfg = get_config()

    # 1. 如果启用同步且自有自选股，直接使用
    if cfg.paper_trading_sync_stock_list and cfg.stock_list:
        return [c.upper().strip() for c in cfg.stock_list if c.strip()]

    # 2. 否则使用显式配置的 watched_codes
    if cfg.paper_trading_watched_codes:
        return [c.upper().strip() for c in cfg.paper_trading_watched_codes]

    # 3. 空兜底
    return []
