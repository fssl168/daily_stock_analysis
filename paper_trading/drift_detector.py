# -*- coding: utf-8 -*-
"""Strategy drift detector (T4).

来源: docs/architecture/realtime_quant_system_design.md §4.5
规格: .claude/specs/quant-p0/dev-plan.md T4

基于每日 PnL 的滚动序列，用纯 numpy 统计方法检测策略是否漂移：
- 滑动 Sharpe（window=20，年化 ×√242）
- Sharpe 趋势（线性回归斜率，polyfit）
- 连续亏损天数
- 推荐动作: keep / reduce_weight / pause / retire

每个策略维护独立的滚动 PnL 缓冲，互不影响。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List

import numpy as np

__all__ = ["DriftReport", "DriftDetector"]

# 年化交易日数（与架构文档一致）。
ANNUALIZATION_FACTOR = 242
# 滑动 Sharpe 窗口。
SHARPE_WINDOW = 20


@dataclass
class DriftReport:
    """一次策略漂移检测的结果。

    Attributes:
        strategy_name: 策略名称。
        is_drifting: 是否判定为漂移。
        rolling_sharpe: 最近 N 日滑动年化 Sharpe 序列。
        sharpe_trend: Sharpe 趋势（正 = 改善，负 = 恶化）。
        consecutive_losing_days: 连续亏损天数。
        recommended_action: 推荐动作 "keep"/"reduce_weight"/"pause"/"retire"。
    """

    strategy_name: str
    is_drifting: bool = False
    rolling_sharpe: List[float] = field(default_factory=list)
    sharpe_trend: float = 0.0
    consecutive_losing_days: int = 0
    recommended_action: str = "keep"


class DriftDetector:
    """策略漂移检测器。

    纯统计计算，不依赖 LLM 与外部服务。
    """

    def __init__(self, window_days: int = 60, min_trades: int = 20):
        self.window_days = window_days
        self.min_trades = min_trades
        # strategy -> 滚动每日 PnL（只保留最近 window_days 条）。
        self._daily_pnl: Dict[str, Deque[float]] = {}

    def record_daily_pnl(self, strategy_name: str, daily_pnl: float) -> None:
        """记录某个策略一天的 PnL（滚动记录，超窗自动丢弃旧数据）。"""
        if strategy_name not in self._daily_pnl:
            self._daily_pnl[strategy_name] = deque(maxlen=self.window_days)
        self._daily_pnl[strategy_name].append(daily_pnl)

    def check(self, strategy_name: str) -> DriftReport:
        """检测策略是否漂移，返回漂移报告与推荐动作。"""
        pnl = list(self._daily_pnl.get(strategy_name, []))

        # 样本不足 → 不漂移，保持观察。
        if len(pnl) < self.min_trades:
            return DriftReport(strategy_name=strategy_name)

        # 滑动 Sharpe。
        rolling_sharpe = self._compute_rolling_sharpe(pnl, window=SHARPE_WINDOW)
        # 样本够 min_trades 但还凑不满一个完整滑动窗口 → 同样不漂移。
        if not rolling_sharpe:
            return DriftReport(strategy_name=strategy_name)

        # Sharpe 趋势（线性回归斜率）。
        x = np.arange(len(rolling_sharpe))
        sharpe_trend = float(np.polyfit(x, rolling_sharpe, 1)[0]) if len(rolling_sharpe) > 1 else 0.0

        # 连续亏损天数（从尾部向前统计）。
        consecutive_losing = 0
        for p in reversed(pnl):
            if p < 0:
                consecutive_losing += 1
            else:
                break

        # 判断漂移：后续规则命中时覆盖前面的动作（retire > pause > reduce_weight）。
        is_drifting = False
        action = "keep"

        if sharpe_trend < -0.01 and len(rolling_sharpe) >= 30:
            is_drifting = True
            action = "reduce_weight"
        if rolling_sharpe[-1] <= 0.0 and sharpe_trend < -0.02:
            is_drifting = True
            action = "pause"
        if consecutive_losing >= 15:
            is_drifting = True
            action = "retire"

        return DriftReport(
            strategy_name=strategy_name,
            is_drifting=is_drifting,
            rolling_sharpe=rolling_sharpe,
            sharpe_trend=round(float(sharpe_trend), 4),
            consecutive_losing_days=consecutive_losing,
            recommended_action=action,
        )

    @staticmethod
    def _compute_rolling_sharpe(pnl: List[float], window: int) -> List[float]:
        """滑动窗口年化 Sharpe（×√242），窗口内 std 为 0 时返回 0.0。"""
        pnl_arr = np.asarray(pnl, dtype=float)
        result = []
        for i in range(window, len(pnl_arr) + 1):
            window_pnl = pnl_arr[i - window : i]
            mean = float(np.mean(window_pnl))
            std = float(np.std(window_pnl))
            if std > 0:
                result.append((mean / std) * np.sqrt(ANNUALIZATION_FACTOR))
            else:
                result.append(0.0)
        return result
