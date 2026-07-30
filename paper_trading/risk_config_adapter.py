# -*- coding: utf-8 -*-
"""Risk and performance config adapter for paper trading.

This module provides functions to create Paper Trading RiskConfig and
PerformanceConfig from the main system Config, enabling parameter alignment
between the paper trading subsystem and the overall portfolio risk management.

The mapping relationships:
- portfolio_risk_concentration_alert_pct -> max_pct_per_stock (conservatively capped at 30%)
- portfolio_max_open_positions -> max_open_positions (if exists, otherwise default to 8)
- portfolio_risk_max_cash_per_buy_pct -> max_pct_cash_per_buy (default 50%)
- paper_trading_max_daily_loss_pct -> max_daily_loss_pct (directly used)
- paper_trading_risk_free_rate -> risk_free_rate_annual (default 3% if not set)
"""

from typing import Optional
from paper_trading.risk import RiskConfig
from paper_trading.performance import PerformanceConfig
import logging

logger = logging.getLogger(__name__)


def create_risk_config_from_main() -> RiskConfig:
    """根据主系统 config 创建纸面交易 RiskConfig，实现参数对齐.

    映射关系：
    - portfolio_risk_concentration_alert_pct → max_pct_per_stock (实际使用时折半留余地，保守取 30%)
    - portfolio_max_open_positions → max_open_positions (如存在，否则用默认值 8)
    - portfolio_risk_max_cash_per_buy_pct → max_pct_cash_per_buy (默认 50%，上限也是 50%)
    - paper_trading_max_daily_loss_pct → max_daily_loss_pct (直接保留)

    Returns:
        RiskConfig with values mapped from main system config.
    """
    from src.config import get_config
    cfg = get_config()

    # concentration: 主系统 alert 用百分比，纸面交易限制更保守取 30% 作为上限
    concentration_pct = getattr(cfg, 'portfolio_risk_concentration_alert_pct', None)
    if concentration_pct is not None and concentration_pct > 0:
        concentration_limit = min(concentration_pct / 100.0, 0.30)  # 最多不超过 30%
    else:
        concentration_limit = 0.30  # 默认 30%

    # max positions: 优先用 portfolio_max_open_positions，不存在则用默认值 8
    max_pos = getattr(cfg, 'portfolio_max_open_positions', None)
    if max_pos is None or max_pos <= 0:
        max_pos = 8
    else:
        max_pos = int(max_pos)

    # cash per buy: 使用 portfolio_risk_max_cash_per_buy_pct，默认 50%
    cash_pct = getattr(cfg, 'portfolio_risk_max_cash_per_buy_pct', None)
    if cash_pct is None or cash_pct <= 0:
        cash_pct = 0.50
    else:
        # 转换为比例形式并限制上限为 50%
        cash_pct = min(cash_pct / 100.0, 0.50)

    # daily loss limit: 直接使用 paper_trading_max_daily_loss_pct
    daily_loss = getattr(cfg, 'paper_trading_max_daily_loss_pct', 0.05)

    return RiskConfig(
        max_pct_per_stock=concentration_limit,
        max_open_positions=int(max_pos),
        max_pct_cash_per_buy=cash_pct,
        max_daily_loss_pct=float(daily_loss),
    )


def create_performance_config_from_main() -> PerformanceConfig:
    """创建 PerformanceConfig，可选从主系统获取 risk_free_rate.

    Returns:
        PerformanceConfig with risk_free_rate mapped from config.
    """
    from src.config import get_config
    cfg = get_config()
    rfr = getattr(cfg, 'paper_trading_risk_free_rate', None)
    if rfr is None or rfr < 0:
        rfr = 0.03  # 默认 3% 无风险利率
    return PerformanceConfig(risk_free_rate_annual=float(rfr))
