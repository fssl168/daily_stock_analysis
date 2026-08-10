# -*- coding: utf-8 -*-
"""ExtremeMarketDetector — 极端行情应对（P2 / T15）.

基于 VIX-like 波动率突增检测市场极端状态：当前 ``window_days`` 日年化波动率相对
历史波动率（近 ``lookback_days`` 日滚动 ``window_days`` 日波动率的均值，不含当前
窗口，避免尖峰自我抬高）超过 ``volatility_multiplier`` 倍时触发警报。随后由
:class:`ExtremeMarketResponse` 暂停规则策略 buy 信号并禁用市价单开仓。

实现依据: docs/architecture/realtime_quant_system_design.md §5.4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# A 股年化交易日
TRADING_DAYS_PER_YEAR = 242.0

# 极端行情触发后的默认应对动作
DEFAULT_ACTIONS = ["暂停规则策略", "只执行止损", "禁止市价单开仓"]


@dataclass
class ExtremeMarketAlert:
    """极端行情警报."""

    market: str
    current_vol: float  # 当前 window_days 日年化波动率
    historical_vol: float  # 历史年化波动率（不含当前窗口）
    ratio: float  # current_vol / historical_vol
    actions: List[str] = field(default_factory=lambda: list(DEFAULT_ACTIONS))
    detected_at: datetime = field(default_factory=datetime.now)


class ExtremeMarketDetector:
    """检测市场极端状态（VIX-like 波动率突增）.

    Args:
        volatility_multiplier: 触发阈值，当前波动率 > 历史波动率 × multiplier 时触发。
        lookback_days: 历史波动率统计窗口（交易日）。
        window_days: 当前波动率窗口（默认 20 日）。
    """

    def __init__(
        self,
        volatility_multiplier: float = 3.0,
        lookback_days: int = 252,
        window_days: int = 20,
    ):
        self.multiplier = volatility_multiplier
        self.lookback_days = lookback_days
        self.window_days = window_days

    def detect(self, market: str, index_df: pd.DataFrame) -> Optional[ExtremeMarketAlert]:
        """检测指定市场是否处于极端行情.

        当前波动率 = 近 window_days 日收益 std × √242（年化）；
        历史波动率 = 近 lookback_days 日滚动 window_days 日波动率的均值（不含当前窗口）；
        ratio > multiplier → 返回警报，否则返回 None。

        数据不足（< window_days+1 行）或历史波动率为 0 / 非有限值 → None（避免除零）。
        """
        if (
            index_df is None
            or "close" not in index_df.columns
            or len(index_df) < self.window_days + 1
        ):
            return None

        returns = index_df["close"].pct_change(fill_method=None).dropna()
        if len(returns) < self.window_days:
            return None

        rolling_vol = returns.rolling(self.window_days).std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        current_vol = rolling_vol.iloc[-1]
        # 不含当前窗口：历史均值只取当前窗口之前的滚动波动率，避免尖峰自我抬高
        historical_vol = rolling_vol.iloc[-self.lookback_days:-1].mean()

        if historical_vol == 0 or not np.isfinite(historical_vol):
            return None

        ratio = current_vol / historical_vol
        if ratio > self.multiplier:
            return ExtremeMarketAlert(
                market=market,
                current_vol=current_vol,
                historical_vol=historical_vol,
                ratio=ratio,
            )
        return None


class ExtremeMarketResponse:
    """极端行情响应策略：激活时暂停 buy 信号、禁用市价单开仓.

    Args:
        on_activate: 激活时的回调（异常被捕获，不影响状态记录）。
        hold_buy_on_activation: 激活时是否停摆 buy 信号（可配开关，默认开）。
        disable_market_orders_on_activation: 激活时是否禁用市价单（可配开关，默认开）。
    """

    def __init__(
        self,
        on_activate: Optional[Callable] = None,
        hold_buy_on_activation: bool = True,
        disable_market_orders_on_activation: bool = True,
    ):
        self._on_activate = on_activate
        self.hold_buy_on_activation = hold_buy_on_activation
        self.disable_market_orders_on_activation = disable_market_orders_on_activation
        self.active_alert: Optional[ExtremeMarketAlert] = None
        self.activated_at: Optional[datetime] = None

    def activate(self, alert: ExtremeMarketAlert) -> None:
        """记录当前状态并调用 on_activate 回调（回调异常被捕获）."""
        self.active_alert = alert
        self.activated_at = datetime.now()
        if self._on_activate is not None:
            try:
                self._on_activate(alert)
            except Exception:  # noqa: BLE001 - 回调失败不能影响状态记录
                logger.exception("ExtremeMarketResponse on_activate callback failed")

    def deactivate(self) -> None:
        """清除极端行情状态."""
        self.active_alert = None
        self.activated_at = None

    def is_active(self) -> bool:
        return self.active_alert is not None

    def force_hold_buy(self) -> bool:
        """激活时 buy 信号停摆（可配开关）."""
        return self.is_active() and self.hold_buy_on_activation

    def allow_market_orders(self) -> bool:
        """激活时市价单禁用（可配开关）."""
        if not self.is_active():
            return True
        return not self.disable_market_orders_on_activation

    # ---- T-011: auto-resume + circuit breaker threshold widening ----

    def auto_resume(self, auto_resume_minutes: int = 30) -> bool:
        """Auto-deactivate after *auto_resume_minutes* have passed since activation.

        Returns True if the deactivation happened, False otherwise.
        """
        if not self.is_active() or self.activated_at is None:
            return False
        elapsed = (datetime.now() - self.activated_at).total_seconds()
        if elapsed >= auto_resume_minutes * 60:
            self.deactivate()
            logger.info("ExtremeMarketResponse auto-resumed after %.0f min", elapsed / 60)
            return True
        return False

    def widen_circuit_breaker(self, cb: Any, factor: float = 2.0) -> None:
        """Temporarily widen circuit breaker thresholds during extreme markets.

        Multiplies soft/hard/liquidation thresholds by *factor* so the breaker
        doesn't trip prematurely amid elevated volatility.
        """
        if cb is None:
            return
        cfg = getattr(cb, "config", None)
        if cfg is None:
            return
        cfg.soft_threshold_pct = getattr(cfg, "soft_threshold_pct", 3.0) * factor
        cfg.hard_threshold_pct = getattr(cfg, "hard_threshold_pct", 5.0) * factor
        cfg.liquidation_threshold_pct = getattr(cfg, "liquidation_threshold_pct", 8.0) * factor
        logger.warning(
            "CircuitBreaker thresholds widened: soft=%.1f%% hard=%.1f%% liq=%.1f%% (factor=%.1fx)",
            cfg.soft_threshold_pct, cfg.hard_threshold_pct,
            cfg.liquidation_threshold_pct, factor,
        )

