# -*- coding: utf-8 -*-
"""CircuitBreaker — 三级熔断机制（P0 / T2）.

独立于 TradingEngine 的熔断守护，按当日累计盈亏相对初始资金的比例分层触发：

- SOFT（软熔断，3%）：禁止新开仓，次日自动解除
- HARD（硬熔断，5%）：禁止所有交易
- LIQUIDATION（强制平仓，8% 或 VaR > 10%）：锁仓 + 冷却期

实现依据: docs/architecture/realtime_quant_system_design.md §2.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class BreakerLevel(str, Enum):
    """熔断级别."""

    NORMAL = "normal"  # 正常
    SOFT = "soft"  # 软熔断（禁止新开仓）
    HARD = "hard"  # 硬熔断（禁止所有交易）
    LIQUIDATION = "liquidate"  # 强制平仓（锁仓）


@dataclass
class BreakerConfig:
    """熔断配置项."""

    soft_threshold_pct: float = 0.03  # 3% → 软熔断
    hard_threshold_pct: float = 0.05  # 5% → 硬熔断
    liquidation_threshold_pct: float = 0.08  # 8% → 强制平仓
    var_threshold_pct: float = 0.10  # VaR > 10% → 强制平仓
    cooling_period_hours: int = 24  # 强制平仓后冷却期（小时）
    enable_auto_reset_soft: bool = True  # 软熔断次日自动解除
    check_interval_seconds: float = 1.0  # 熔断检查间隔


@dataclass
class BreakerState:
    """熔断状态快照."""

    level: BreakerLevel = BreakerLevel.NORMAL
    triggered_at: Optional[datetime] = None
    daily_pnl: float = 0.0
    initial_capital: float = 0.0
    reason: str = ""


def _now() -> datetime:
    """返回当前时间（独立函数，便于测试注入时间）. """
    return datetime.now()


class CircuitBreaker:
    """三级熔断守护（独立于 TradingEngine）.

    与 MarketListener 并行运行，持续监控 PnL。每次 tick 调用 :meth:`evaluate`，
    若返回非 NORMAL 级别，交易引擎必须 reject 对应的交易请求。
    """

    def __init__(
        self,
        config: BreakerConfig,
        account_id: int,
        on_soft_trigger: Optional[Callable] = None,  # 通知回调
        on_hard_trigger: Optional[Callable] = None,
        on_liquidation: Optional[Callable] = None,  # 平仓执行回调
    ):
        self.config = config
        self.account_id = account_id
        self.state = BreakerState()
        self._callbacks = {
            BreakerLevel.SOFT: on_soft_trigger,
            BreakerLevel.HARD: on_hard_trigger,
            BreakerLevel.LIQUIDATION: on_liquidation,
        }

    def evaluate(
        self,
        current_pnl: float,
        initial_capital: float,
        current_var: Optional[float] = None,
    ) -> BreakerState:
        """每次 tick 调用：评估是否需要熔断.

        按层级阈值判断触发（3%→SOFT, 5%→HARD, 8%→LIQUIDATION, VaR>10%→LIQUIDATION）；
        LIQUIDATION 后冷却期内保持锁定；触发时调用对应回调，回调异常被捕获不影响主逻辑。

        Args:
            current_pnl: 当日累计盈亏。
            initial_capital: 初始资金。
            current_var: 组合 VaR（可选）。

        Returns:
            当前熔断状态（调用方根据 level 决定是否拒绝交易）。
        """
        self.state.daily_pnl = current_pnl
        self.state.initial_capital = initial_capital
        pnl_pct = abs(current_pnl) / initial_capital if initial_capital > 0 else 0.0

        # 检查是否在冷却期：LIQUIDATION 后 cooling_period_hours 内保持锁定
        if self.state.level == BreakerLevel.LIQUIDATION and self.state.triggered_at is not None:
            elapsed_hours = (_now() - self.state.triggered_at).total_seconds() / 3600.0
            if elapsed_hours < self.config.cooling_period_hours:
                return self.state  # 仍在冷却期

        # Level 3: 强制平仓
        if pnl_pct >= self.config.liquidation_threshold_pct:
            return self._trigger(
                BreakerLevel.LIQUIDATION,
                f"PnL={pnl_pct:.2%} exceeded liquidation limit",
            )
        if (
            current_var is not None
            and initial_capital > 0
            and abs(current_var) / initial_capital >= self.config.var_threshold_pct
        ):
            return self._trigger(BreakerLevel.LIQUIDATION, "VaR exceeded limit")

        # Level 2: 硬熔断（同一级别已在生效时不重复触发）
        if (
            pnl_pct >= self.config.hard_threshold_pct
            and self.state.level != BreakerLevel.HARD
        ):
            return self._trigger(
                BreakerLevel.HARD,
                f"PnL={pnl_pct:.2%} exceeded hard limit",
            )

        # Level 1: 软熔断（仅从 NORMAL 触发，避免重复触发）
        if (
            pnl_pct >= self.config.soft_threshold_pct
            and self.state.level == BreakerLevel.NORMAL
        ):
            return self._trigger(
                BreakerLevel.SOFT,
                f"PnL={pnl_pct:.2%} exceeded soft limit",
            )

        return self.state

    def _trigger(self, level: BreakerLevel, reason: str) -> BreakerState:
        """切换到指定熔断级别并调用对应回调.

        回调异常必须被捕获，不影响熔断主逻辑。
        """
        self.state.level = level
        self.state.triggered_at = _now()
        self.state.reason = reason
        cb = self._callbacks.get(level)
        if cb is not None:
            try:
                cb(level, reason)
            except Exception:  # noqa: BLE001 - 回调失败不能影响熔断主逻辑
                logger.exception("CircuitBreaker callback for %s failed", level)
        return self.state

    def reset_daily(self):
        """每日开盘时重置（LIQUIDATION 级别不重置）."""
        if self.state.level != BreakerLevel.LIQUIDATION:
            self.state = BreakerState()

    def allow_new_position(self) -> bool:
        """是否允许新开仓：仅 NORMAL 允许."""
        return self.state.level == BreakerLevel.NORMAL

    def allow_any_trade(self) -> bool:
        """是否允许任意交易：NORMAL / SOFT 允许."""
        return self.state.level in (BreakerLevel.NORMAL, BreakerLevel.SOFT)
