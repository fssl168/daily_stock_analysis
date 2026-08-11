# -*- coding: utf-8 -*-
"""RiskDaemon — 实时风控守护（VaR + 流动性 + 市场异常检测）（T7）.

来源: docs/architecture/realtime_quant_system_design.md §2.3
规格: .claude/specs/quant-p1/dev-plan.md T7

独立于 TradingEngine 的风控守护，持续监控组合风险但不直接干预交易：

- VaR：历史模拟法，基于 lookback 日 PnL 分布计算 95%/99% 分位数与 CVaR，
  超过资金阈值触发 VAR_BREACH 告警，并联动注入的 circuit_breaker。
- 流动性：换手率过低（< 0.5%）或清仓天数过长（> 5）触发 LIQUIDITY_WARNING。
- 市场异常：当前 20 日波动率超过历史均值 3 倍触发 MARKET_ANOMALY。

纯 numpy 计算；tick 内任何子检查异常都会被记录并跳过，不影响其余检查。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Deque, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "RiskAlertType",
    "RiskAlert",
    "VaRResult",
    "LiquidityRisk",
    "MarketAnomaly",
    "VaRMonitor",
    "LiquidityMonitor",
    "MarketAnomalyDetector",
    "RiskDaemon",
    "MIN_TURNOVER_RATE",
    "MAX_DAYS_TO_LIQUIDATE",
    "VOL_LOOKBACK_DAYS",
    "ANOMALY_VOLATILITY_MULTIPLIER",
    "VAR_CAPITAL_THRESHOLD_PCT",
]

# 换手率 < 0.5% 视为不流动（对齐 RISK_LIQUIDITY_MIN_TURNOVER_RATE）。
MIN_TURNOVER_RATE = 0.005
# 清仓天数 > 5 视为流动性风险（对齐 RISK_LIQUIDITY_MAX_DAYS_TO_LIQUIDATE）。
MAX_DAYS_TO_LIQUIDATE = 5.0
# 当前波动率窗口（20 日）。
VOL_LOOKBACK_DAYS = 20
# 当前波动率 > 历史均值 × 3 → 市场异常（对齐 RISK_ANOMALY_VOLATILITY_MULTIPLIER）。
ANOMALY_VOLATILITY_MULTIPLIER = 3.0
# 市场异常告警的默认动作。
ANOMALY_ACTIONS = ["暂停规则策略", "只执行止损", "禁止市价单开仓"]
# VaR 占资金比例达到该阈值即视为 breach。
VAR_CAPITAL_THRESHOLD_PCT = 0.02
# VaR 历史模拟 lookback 天数（内部滚动 PnL 缓冲上限）。
VAR_LOOKBACK_DAYS = 252


class RiskAlertType(str, Enum):
    """风控告警类型."""

    VAR_BREACH = "var_breach"  # VaR 超限
    LIQUIDITY_WARNING = "liquidity_warning"  # 流动性风险
    MARKET_ANOMALY = "market_anomaly"  # 市场异常


@dataclass
class VaRResult:
    """历史模拟法 VaR 计算结果."""

    var_95_pct: float = 0.0  # 95% 置信 VaR（PnL 分布 5% 分位数，通常为负）
    var_99_pct: float = 0.0  # 99% 置信 VaR（PnL 分布 1% 分位数）
    cvar_95_pct: float = 0.0  # 95% 置信 CVaR（Expected Shortfall）
    var_pct_of_capital: float = 0.0  # VaR 占资金比例
    is_breach: bool = False  # 是否超过资金阈值


@dataclass
class LiquidityRisk:
    """单只持仓的流动性风险."""

    code: str = ""
    daily_turnover_rate: float = 0.0  # 换手率
    bid_ask_spread_pct: float = 0.0  # 买卖价差百分比（信息项，不参与判定）
    is_illiquid: bool = False
    days_to_liquidate: float = 0.0  # 按当前成交量清仓所需天数


@dataclass
class MarketAnomaly:
    """市场异常检测结果."""

    detected: bool = False
    current_vol: float = 0.0  # 当前 20 日波动率
    historical_vol: float = 0.0  # 历史波动率均值
    ratio: float = 0.0  # current_vol / historical_vol
    actions: List[str] = field(default_factory=list)


@dataclass
class RiskAlert:
    """一条风控告警."""

    alert_type: RiskAlertType
    detail: Any = None
    timestamp: datetime = field(default_factory=datetime.now)


class VaRMonitor:
    """组合 VaR 计算 — 历史模拟法.

    基于 lookback 日 PnL 序列的分布：95%/99% VaR 取 5%/1% 分位数，
    CVaR 取尾部（≤ 5% 分位数）PnL 的均值。
    """

    def __init__(
        self,
        capital: float = 0.0,
        capital_threshold_pct: float = VAR_CAPITAL_THRESHOLD_PCT,
    ):
        self.capital = capital
        self.capital_threshold_pct = capital_threshold_pct

    def compute(self, positions_pnl_history: List[float]) -> VaRResult:
        """对给定日 PnL 序列计算历史模拟法 VaR.

        Args:
            positions_pnl_history: 历史每日 PnL 序列（单位与 capital 一致）。

        Returns:
            VaRResult；空输入返回全零默认值（不触发 breach）。
        """
        arr = np.asarray(positions_pnl_history, dtype=float)
        if arr.size == 0:
            return VaRResult()

        var_95 = float(np.percentile(arr, 5))
        var_99 = float(np.percentile(arr, 1))
        tail = arr[arr <= var_95]
        cvar_95 = float(np.mean(tail))

        var_pct = abs(var_95) / self.capital if self.capital > 0 else 0.0
        return VaRResult(
            var_95_pct=var_95,
            var_99_pct=var_99,
            cvar_95_pct=cvar_95,
            var_pct_of_capital=var_pct,
            is_breach=var_pct >= self.capital_threshold_pct,
        )


class LiquidityMonitor:
    """流动性检查：换手率过低或清仓天数过长 → 不流动."""

    def __init__(
        self,
        min_turnover_rate: float = MIN_TURNOVER_RATE,
        max_days_to_liquidate: float = MAX_DAYS_TO_LIQUIDATE,
    ):
        self.min_turnover_rate = min_turnover_rate
        self.max_days_to_liquidate = max_days_to_liquidate

    def check(
        self,
        code: str,
        turnover_rate: float,
        bid_ask_spread: float,
        days_to_liquidate: float,
    ) -> LiquidityRisk:
        """检查单只股票/持仓的流动性风险."""
        turnover = float(turnover_rate)
        spread = float(bid_ask_spread)
        days = float(days_to_liquidate)
        is_illiquid = turnover < self.min_turnover_rate or days > self.max_days_to_liquidate
        return LiquidityRisk(
            code=code,
            daily_turnover_rate=turnover,
            bid_ask_spread_pct=spread,
            is_illiquid=is_illiquid,
            days_to_liquidate=days,
        )


class MarketAnomalyDetector:
    """市场异常检测：当前 20 日波动率 vs 历史波动率均值.

    历史均值取「当前窗口之前」所有滚动 20 日波动率的均值（避免当前尖峰自我抬高基线），
    最多回看 historical_window 个窗口。
    """

    def __init__(
        self,
        vol_lookback_days: int = VOL_LOOKBACK_DAYS,
        multiplier: float = ANOMALY_VOLATILITY_MULTIPLIER,
        historical_window: int = 240,
    ):
        self.vol_lookback_days = vol_lookback_days
        self.multiplier = multiplier
        self.historical_window = historical_window

    def detect(self, latest_prices) -> MarketAnomaly:
        """基于收盘价序列检测波动率尖峰.

        Args:
            latest_prices: 连续价格序列（list/array 均可）。

        Returns:
            MarketAnomaly；数据不足或历史波动率为 0 时 not detected。
        """
        prices = np.asarray(latest_prices, dtype=float)
        # 至少需要 vol_lookback_days + 1 个价格才能形成 20 日收益窗口。
        if prices.size < self.vol_lookback_days + 1:
            return MarketAnomaly()

        returns = np.diff(prices) / prices[:-1]
        current_vol = float(np.std(returns[-self.vol_lookback_days :]))

        window = self.vol_lookback_days
        rolling = np.array(
            [np.std(returns[i - window : i]) for i in range(window, returns.size + 1)]
        )
        baseline = rolling[:-1] if rolling.size > 1 else np.array([])
        if baseline.size:
            historical_vol = float(np.mean(baseline[-self.historical_window :]))
        else:
            historical_vol = current_vol

        ratio = current_vol / historical_vol if historical_vol > 0 else 0.0
        detected = ratio > self.multiplier
        return MarketAnomaly(
            detected=detected,
            current_vol=round(current_vol, 6),
            historical_vol=round(historical_vol, 6),
            ratio=round(ratio, 4),
            actions=list(ANOMALY_ACTIONS) if detected else [],
        )


class RiskDaemon:
    """实时风控守护：每 tick 执行 VaR / 流动性 / 市场异常三类检查.

    返回 RiskAlert 列表，调用方决定如何响应；tick 内任何子检查异常都会被
    记录并跳过，不中断整个 tick。
    """

    def __init__(self, circuit_breaker=None, check_interval: float = 1.0):
        self.circuit_breaker = circuit_breaker
        self.check_interval = check_interval
        self._var_monitor = VaRMonitor()
        self._liquidity_monitor = LiquidityMonitor()
        self._anomaly_detector = MarketAnomalyDetector()
        # 内部滚动日 PnL 缓冲（snapshot 未显式携带 pnl_history 时使用）。
        self._pnl_history: Deque[float] = deque(maxlen=VAR_LOOKBACK_DAYS)
        self._prev_equity: Optional[float] = None

    def tick(self, account_snapshot, positions, latest_prices) -> List[RiskAlert]:
        """执行全部风控检查，返回本 tick 产生的告警列表.

        Args:
            account_snapshot: dict 或对象，支持字段
                initial_capital（资金）/ total_equity 或 equity（当前权益）/
                pnl_history（可选，直接提供历史日 PnL 序列）。
            positions: 持仓迭代（dict 或对象），支持字段
                code / turnover_rate / bid_ask_spread（或 bid_ask_spread_pct）/
                days_to_liquidate。
            latest_prices: 市场最新连续价格序列（用于波动率异常检测）。
        """
        alerts: List[RiskAlert] = []
        timestamp = datetime.now()
        initial_capital = float(self._get(account_snapshot, "initial_capital", 0.0) or 0.0)

        # 1. VaR 检查（历史模拟法）。
        try:
            pnl_history = self._get(account_snapshot, "pnl_history", None)
            if pnl_history is None:
                pnl_history = self._append_equity_pnl(account_snapshot)
            self._var_monitor.capital = initial_capital
            var_result = self._var_monitor.compute(pnl_history)
            if var_result.is_breach:
                alerts.append(RiskAlert(RiskAlertType.VAR_BREACH, var_result, timestamp))
                if self.circuit_breaker is not None:
                    self.circuit_breaker.evaluate(
                        current_pnl=self._current_pnl(account_snapshot, initial_capital),
                        initial_capital=initial_capital,
                        current_var=var_result.var_95_pct,
                    )
        except Exception:  # noqa: BLE001 - 子检查异常不能中断整个 tick
            logger.exception("RiskDaemon VaR check failed; skipped")

        # 2. 流动性检查（逐持仓）。
        for pos in positions or []:
            try:
                liq = self._liquidity_monitor.check(
                    code=str(self._get(pos, "code", "") or ""),
                    turnover_rate=self._get(pos, "turnover_rate", 0.0),
                    bid_ask_spread=self._get(
                        pos, "bid_ask_spread", self._get(pos, "bid_ask_spread_pct", 0.0)
                    ),
                    days_to_liquidate=self._get(pos, "days_to_liquidate", 0.0),
                )
                if liq.is_illiquid:
                    alerts.append(RiskAlert(RiskAlertType.LIQUIDITY_WARNING, liq, timestamp))
            except Exception:  # noqa: BLE001
                logger.exception("RiskDaemon liquidity check failed for %r; skipped", pos)

        # 3. 市场异常检测。
        try:
            anomaly = self._anomaly_detector.detect(latest_prices)
            if anomaly.detected:
                alerts.append(RiskAlert(RiskAlertType.MARKET_ANOMALY, anomaly, timestamp))
        except Exception:  # noqa: BLE001
            logger.exception("RiskDaemon market anomaly check failed; skipped")

        return alerts

    def _append_equity_pnl(self, account_snapshot) -> List[float]:
        """基于 snapshot 权益变化累积内部日 PnL 缓冲，并返回缓冲快照."""
        equity = self._get(account_snapshot, "total_equity", self._get(account_snapshot, "equity", None))
        if equity is not None:
            equity = float(equity)
            if self._prev_equity is not None:
                self._pnl_history.append(equity - self._prev_equity)
            else:
                self._pnl_history.append(0.0)
            self._prev_equity = equity
        return list(self._pnl_history)

    def _current_pnl(self, account_snapshot, initial_capital: float) -> float:
        """计算当前累计 PnL（权益 - 初始资金）；无法取到权益时返回 0.0."""
        equity = self._get(account_snapshot, "total_equity", self._get(account_snapshot, "equity", None))
        if equity is None:
            return 0.0
        return float(equity) - initial_capital

    @staticmethod
    def _get(item, key: str, default: Any = None) -> Any:
        """同时支持 dict 与对象属性访问的取值辅助."""
        if item is None:
            return default
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
