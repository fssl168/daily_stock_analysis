# -*- coding: utf-8 -*-
"""Unit tests for T7 RiskDaemon (paper_trading/risk_daemon.py)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.risk_daemon import (  # noqa: E402
    ANOMALY_ACTIONS,
    ANOMALY_VOLATILITY_MULTIPLIER,
    MAX_DAYS_TO_LIQUIDATE,
    MIN_TURNOVER_RATE,
    VAR_CAPITAL_THRESHOLD_PCT,
    VOL_LOOKBACK_DAYS,
    LiquidityMonitor,
    LiquidityRisk,
    MarketAnomaly,
    MarketAnomalyDetector,
    RiskAlert,
    RiskAlertType,
    RiskDaemon,
    VaRMonitor,
    VaRResult,
)

# 触发 VaR breach 的 PnL 序列：var_95 = -5000，占 100000 资金的 5% ≥ 2%。
BREACH_PNL = [-5000.0] * 50 + [100.0] * 50


def make_snapshot(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def make_position(code: str, turnover_rate=0.05, bid_ask_spread=0.01, days_to_liquidate=1.0) -> SimpleNamespace:
    return SimpleNamespace(
        code=code,
        turnover_rate=turnover_rate,
        bid_ask_spread=bid_ask_spread,
        days_to_liquidate=days_to_liquidate,
    )


def spike_prices(seed=42, calm_days=120, spike_days=20, calm_vol=0.005, spike_vol=0.05) -> list:
    """先低波动后高波动的确定性价格序列（波动率尖峰）。"""
    rng = np.random.default_rng(seed)
    calm = rng.normal(0.0, calm_vol, calm_days)
    spike = rng.normal(0.0, spike_vol, spike_days)
    returns = np.concatenate([calm, spike])
    return list(100.0 * np.cumprod(1.0 + returns))


def calm_prices(seed=7, days=100, vol=0.01) -> list:
    """低波动平稳价格序列。"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, vol, days)
    return list(100.0 * np.cumprod(1.0 + returns))


class FakeCircuitBreaker:
    """记录 evaluate 调用的假熔断器（可注入异常）。"""

    def __init__(self, raise_on_evaluate: bool = False):
        self.calls = []
        self.raise_on_evaluate = raise_on_evaluate

    def evaluate(self, **kwargs):
        if self.raise_on_evaluate:
            raise RuntimeError("breaker boom")
        self.calls.append(kwargs)


# ---------------------------------------------------------------------------
# 数据结构与常量
# ---------------------------------------------------------------------------


class TestDataStructures:
    def test_alert_type_enum_values(self):
        assert RiskAlertType.VAR_BREACH == "var_breach"
        assert RiskAlertType.LIQUIDITY_WARNING == "liquidity_warning"
        assert RiskAlertType.MARKET_ANOMALY == "market_anomaly"

    def test_risk_alert_defaults(self):
        alert = RiskAlert(RiskAlertType.VAR_BREACH)
        assert alert.detail is None
        assert isinstance(alert.timestamp, datetime)

    def test_var_result_defaults(self):
        r = VaRResult()
        assert r.var_95_pct == 0.0
        assert r.var_99_pct == 0.0
        assert r.cvar_95_pct == 0.0
        assert r.var_pct_of_capital == 0.0
        assert r.is_breach is False

    def test_liquidity_risk_defaults(self):
        r = LiquidityRisk()
        assert r.code == ""
        assert r.daily_turnover_rate == 0.0
        assert r.bid_ask_spread_pct == 0.0
        assert r.is_illiquid is False
        assert r.days_to_liquidate == 0.0

    def test_market_anomaly_defaults(self):
        r = MarketAnomaly()
        assert r.detected is False
        assert r.current_vol == 0.0
        assert r.historical_vol == 0.0
        assert r.ratio == 0.0
        assert r.actions == []

    def test_constants(self):
        assert MIN_TURNOVER_RATE == 0.005
        assert MAX_DAYS_TO_LIQUIDATE == 5.0
        assert VOL_LOOKBACK_DAYS == 20
        assert ANOMALY_VOLATILITY_MULTIPLIER == 3.0
        assert VAR_CAPITAL_THRESHOLD_PCT == 0.02
        assert ANOMALY_ACTIONS == ["暂停规则策略", "只执行止损", "禁止市价单开仓"]


# ---------------------------------------------------------------------------
# VaRMonitor
# ---------------------------------------------------------------------------


class TestVaRMonitor:
    def test_exact_quantiles(self):
        """range(-100, 101) 的 5%/1% 分位数与 CVaR 可精确手算。"""
        res = VaRMonitor().compute(list(range(-100, 101)))
        assert res.var_95_pct == -90.0
        assert res.var_99_pct == -98.0
        assert res.cvar_95_pct == -95.0

    def test_single_value(self):
        res = VaRMonitor().compute([-50.0])
        assert res.var_95_pct == -50.0
        assert res.var_99_pct == -50.0
        assert res.cvar_95_pct == -50.0

    def test_empty_input(self):
        res = VaRMonitor().compute([])
        assert res.var_95_pct == 0.0
        assert res.var_99_pct == 0.0
        assert res.cvar_95_pct == 0.0
        assert res.var_pct_of_capital == 0.0
        assert res.is_breach is False

    def test_cvar_is_mean_of_tail_and_no_more_extreme_than_var(self):
        pnl = [-99.0, -80.0, -60.0, -50.0, -40.0, -20.0, -5.0, 1.0, 2.0, 3.0, 4.0, 7.0, 10.0]
        res = VaRMonitor().compute(pnl)
        arr = np.asarray(pnl)
        tail = arr[arr <= res.var_95_pct]
        assert res.cvar_95_pct == pytest.approx(float(np.mean(tail)))
        assert res.cvar_95_pct <= res.var_95_pct

    def test_var_99_more_extreme_than_var_95(self):
        res = VaRMonitor().compute(list(range(-500, 501)))
        assert res.var_99_pct < res.var_95_pct
        assert res.var_95_pct < 0.0

    def test_breach_when_var_exceeds_capital_threshold(self):
        res = VaRMonitor(capital=100_000.0).compute(BREACH_PNL)
        assert res.var_95_pct == pytest.approx(-5000.0)
        assert res.var_pct_of_capital == pytest.approx(0.05)
        assert res.is_breach is True

    def test_no_breach_when_var_below_capital_threshold(self):
        res = VaRMonitor(capital=10_000_000.0).compute(BREACH_PNL)
        assert res.var_pct_of_capital == pytest.approx(0.0005)
        assert res.is_breach is False

    def test_zero_capital_never_breach(self):
        res = VaRMonitor().compute([-9000.0, -8000.0])
        assert res.var_pct_of_capital == 0.0
        assert res.is_breach is False

    def test_custom_capital_threshold(self):
        monitor = VaRMonitor(capital=100_000.0, capital_threshold_pct=0.001)
        assert monitor.compute(list(range(-100, 101))).is_breach is False  # 0.09% < 0.1%
        monitor2 = VaRMonitor(capital=100_000.0, capital_threshold_pct=0.0005)
        assert monitor2.compute(list(range(-100, 101))).is_breach is True  # 0.09% >= 0.05%


# ---------------------------------------------------------------------------
# LiquidityMonitor
# ---------------------------------------------------------------------------


class TestLiquidityMonitor:
    def test_low_turnover_illiquid(self):
        res = LiquidityMonitor().check("600519", 0.001, 0.02, 1.0)
        assert res.code == "600519"
        assert res.daily_turnover_rate == 0.001
        assert res.bid_ask_spread_pct == 0.02
        assert res.days_to_liquidate == 1.0
        assert res.is_illiquid is True

    def test_high_days_to_liquidate_illiquid(self):
        res = LiquidityMonitor().check("000001", 0.05, 0.01, 6.0)
        assert res.is_illiquid is True

    def test_turnover_boundary_not_illiquid(self):
        assert LiquidityMonitor().check("A", MIN_TURNOVER_RATE, 0.01, 1.0).is_illiquid is False

    def test_days_boundary_not_illiquid(self):
        assert LiquidityMonitor().check("A", 0.05, 0.01, MAX_DAYS_TO_LIQUIDATE).is_illiquid is False

    def test_liquid_position(self):
        res = LiquidityMonitor().check("AAPL", 0.08, 0.005, 0.5)
        assert res.is_illiquid is False
        assert res.daily_turnover_rate == 0.08
        assert res.bid_ask_spread_pct == 0.005

    def test_both_conditions_illiquid(self):
        res = LiquidityMonitor().check("B", 0.0001, 0.05, 20.0)
        assert res.is_illiquid is True

    def test_custom_thresholds(self):
        mon = LiquidityMonitor(min_turnover_rate=0.01, max_days_to_liquidate=3.0)
        assert mon.check("A", 0.008, 0.01, 1.0).is_illiquid is True
        assert mon.check("A", 0.02, 0.01, 4.0).is_illiquid is True
        assert mon.check("A", 0.02, 0.01, 2.0).is_illiquid is False


# ---------------------------------------------------------------------------
# MarketAnomalyDetector
# ---------------------------------------------------------------------------


class TestMarketAnomalyDetector:
    def test_empty_input_not_detected(self):
        res = MarketAnomalyDetector().detect([])
        assert res.detected is False
        assert res.ratio == 0.0

    def test_single_price_not_detected(self):
        assert MarketAnomalyDetector().detect([100.0]).detected is False

    def test_insufficient_prices_not_detected(self):
        prices = [100.0 + i for i in range(VOL_LOOKBACK_DAYS)]  # 只有 20 个价格
        assert MarketAnomalyDetector().detect(prices).detected is False

    def test_single_window_ratio_falls_back_to_one(self):
        # 21 个价格 → 20 个收益 → 只有一个滚动窗口 → ratio 退化为 1.0，不触发。
        returns = np.random.default_rng(1).normal(0.0, 0.01, VOL_LOOKBACK_DAYS)
        prices = [100.0] + list(100.0 * np.cumprod(1.0 + returns))
        res = MarketAnomalyDetector().detect(prices)
        assert res.detected is False
        assert res.ratio == pytest.approx(1.0)

    def test_constant_prices_not_detected(self):
        res = MarketAnomalyDetector().detect([100.0] * 60)
        assert res.detected is False
        assert res.historical_vol == 0.0
        assert res.ratio == 0.0

    def test_vol_spike_detected(self):
        res = MarketAnomalyDetector().detect(spike_prices())
        assert res.detected is True
        assert res.ratio > ANOMALY_VOLATILITY_MULTIPLIER
        assert res.current_vol > res.historical_vol
        assert res.actions == ANOMALY_ACTIONS

    def test_normal_vol_not_detected(self):
        res = MarketAnomalyDetector().detect(calm_prices())
        assert res.detected is False
        assert res.ratio < ANOMALY_VOLATILITY_MULTIPLIER
        assert res.actions == []

    def test_custom_multiplier(self):
        prices = spike_prices(seed=3)
        assert MarketAnomalyDetector(multiplier=1.2).detect(prices).detected is True
        assert MarketAnomalyDetector(multiplier=100.0).detect(prices).detected is False

    def test_custom_lookback_and_historical_window(self):
        det = MarketAnomalyDetector(vol_lookback_days=10, historical_window=50)
        res = det.detect(spike_prices(seed=42, calm_days=60, spike_days=10))
        assert res.detected is True
        assert res.ratio > ANOMALY_VOLATILITY_MULTIPLIER


# ---------------------------------------------------------------------------
# RiskDaemon.tick
# ---------------------------------------------------------------------------


class TestRiskDaemon:
    def test_defaults(self):
        daemon = RiskDaemon()
        assert daemon.circuit_breaker is None
        assert daemon.check_interval == 1.0
        daemon2 = RiskDaemon(circuit_breaker="cb", check_interval=2.5)
        assert daemon2.circuit_breaker == "cb"
        assert daemon2.check_interval == 2.5

    def test_empty_inputs_return_no_alerts(self):
        daemon = RiskDaemon()
        assert daemon.tick({}, [], []) == []

    def test_none_inputs_do_not_crash(self):
        daemon = RiskDaemon()
        assert daemon.tick(None, None, None) == []

    def test_healthy_tick_no_alerts(self):
        daemon = RiskDaemon()
        snapshot = make_snapshot(initial_capital=100_000.0, total_equity=100_500.0)
        positions = [make_position("600519"), make_position("AAPL")]
        assert daemon.tick(snapshot, positions, calm_prices()) == []

    def test_var_breach_alert_and_breaker_called(self):
        breaker = FakeCircuitBreaker()
        daemon = RiskDaemon(circuit_breaker=breaker)
        snapshot = make_snapshot(
            initial_capital=100_000.0,
            total_equity=95_000.0,
            pnl_history=BREACH_PNL,
        )
        alerts = daemon.tick(snapshot, [], calm_prices())
        var_alerts = [a for a in alerts if a.alert_type == RiskAlertType.VAR_BREACH]
        assert len(var_alerts) == 1
        assert isinstance(var_alerts[0].timestamp, datetime)
        assert var_alerts[0].detail.is_breach is True
        assert len(breaker.calls) == 1
        call = breaker.calls[0]
        assert call["initial_capital"] == 100_000.0
        assert call["current_var"] == pytest.approx(var_alerts[0].detail.var_95_pct)
        assert call["current_pnl"] == pytest.approx(-5000.0)  # 权益 - 初始资金

    def test_var_breach_without_breaker(self):
        daemon = RiskDaemon()
        snapshot = make_snapshot(initial_capital=100_000.0, pnl_history=BREACH_PNL)
        alerts = daemon.tick(snapshot, [], [])
        assert any(a.alert_type == RiskAlertType.VAR_BREACH for a in alerts)

    def test_breaker_exception_isolated(self):
        breaker = FakeCircuitBreaker(raise_on_evaluate=True)
        daemon = RiskDaemon(circuit_breaker=breaker)
        snapshot = make_snapshot(initial_capital=100_000.0, pnl_history=BREACH_PNL)
        alerts = daemon.tick(snapshot, [], [])
        assert any(a.alert_type == RiskAlertType.VAR_BREACH for a in alerts)

    def test_current_pnl_defaults_zero_without_equity(self):
        breaker = FakeCircuitBreaker()
        daemon = RiskDaemon(circuit_breaker=breaker)
        snapshot = make_snapshot(initial_capital=100_000.0, pnl_history=BREACH_PNL)
        daemon.tick(snapshot, [], [])
        assert breaker.calls[0]["current_pnl"] == 0.0

    def test_dict_snapshot_supported(self):
        breaker = FakeCircuitBreaker()
        daemon = RiskDaemon(circuit_breaker=breaker)
        snapshot = {"initial_capital": 100_000.0, "pnl_history": BREACH_PNL}
        alerts = daemon.tick(snapshot, [], [])
        assert any(a.alert_type == RiskAlertType.VAR_BREACH for a in alerts)
        assert breaker.calls[0]["current_var"] is not None

    def test_liquidity_warning_low_turnover(self):
        daemon = RiskDaemon()
        pos = {"code": "600519", "turnover_rate": 0.001, "bid_ask_spread": 0.02, "days_to_liquidate": 1.0}
        alerts = daemon.tick(make_snapshot(initial_capital=100_000.0), [pos], [])
        liq_alerts = [a for a in alerts if a.alert_type == RiskAlertType.LIQUIDITY_WARNING]
        assert len(liq_alerts) == 1
        assert liq_alerts[0].detail.code == "600519"
        assert liq_alerts[0].detail.is_illiquid is True

    def test_liquidity_warning_high_days_to_liquidate(self):
        daemon = RiskDaemon()
        pos = make_position("000001", turnover_rate=0.05, days_to_liquidate=10.0)
        alerts = daemon.tick(make_snapshot(initial_capital=100_000.0), [pos], [])
        assert any(a.alert_type == RiskAlertType.LIQUIDITY_WARNING for a in alerts)

    def test_multiple_illiquid_positions(self):
        daemon = RiskDaemon()
        positions = [
            {"code": "A", "turnover_rate": 0.0001, "bid_ask_spread": 0.01, "days_to_liquidate": 1.0},
            {"code": "B", "turnover_rate": 0.05, "bid_ask_spread_pct": 0.02, "days_to_liquidate": 9.0},
        ]
        alerts = daemon.tick(make_snapshot(initial_capital=100_000.0), positions, [])
        liq = [a for a in alerts if a.alert_type == RiskAlertType.LIQUIDITY_WARNING]
        assert {a.detail.code for a in liq} == {"A", "B"}

    def test_market_anomaly_alert(self):
        daemon = RiskDaemon()
        alerts = daemon.tick(make_snapshot(initial_capital=100_000.0), [], spike_prices())
        anomaly = [a for a in alerts if a.alert_type == RiskAlertType.MARKET_ANOMALY]
        assert len(anomaly) == 1
        assert anomaly[0].detail.detected is True

    def test_all_three_alert_types_in_one_tick(self):
        daemon = RiskDaemon()
        snapshot = make_snapshot(initial_capital=100_000.0, pnl_history=BREACH_PNL)
        positions = [{"code": "C", "turnover_rate": 0.0005, "bid_ask_spread": 0.02, "days_to_liquidate": 1.0}]
        alerts = daemon.tick(snapshot, positions, spike_prices(seed=5))
        types = {a.alert_type for a in alerts}
        assert types == {
            RiskAlertType.VAR_BREACH,
            RiskAlertType.LIQUIDITY_WARNING,
            RiskAlertType.MARKET_ANOMALY,
        }

    def test_internal_pnl_history_accumulates_equity_change(self):
        daemon = RiskDaemon()
        alerts = []
        for equity in (100_000.0, 90_000.0, 80_000.0, 70_000.0, 60_000.0):
            snapshot = make_snapshot(initial_capital=100_000.0, total_equity=equity)
            alerts = daemon.tick(snapshot, [], [])
        assert list(daemon._pnl_history) == [0.0, -10000.0, -10000.0, -10000.0, -10000.0]
        var_alerts = [a for a in alerts if a.alert_type == RiskAlertType.VAR_BREACH]
        assert len(var_alerts) == 1
        assert var_alerts[0].detail.var_95_pct == pytest.approx(-10000.0)

    def test_snapshot_with_equity_key_fallback(self):
        daemon = RiskDaemon()
        alerts = []
        for equity in (100_000.0, 90_000.0):
            alerts = daemon.tick(make_snapshot(initial_capital=100_000.0, equity=equity), [], [])
        assert list(daemon._pnl_history) == [0.0, -10000.0]
        assert any(a.alert_type == RiskAlertType.VAR_BREACH for a in alerts)

    def test_var_exception_isolated(self):
        daemon = RiskDaemon()
        snapshot = make_snapshot(initial_capital=100_000.0, pnl_history=["bad", 1.0])
        positions = [{"code": "A", "turnover_rate": 0.0001, "bid_ask_spread": 0.01, "days_to_liquidate": 1.0}]
        alerts = daemon.tick(snapshot, positions, [])
        assert any(a.alert_type == RiskAlertType.LIQUIDITY_WARNING for a in alerts)
        assert not any(a.alert_type == RiskAlertType.VAR_BREACH for a in alerts)

    def test_liquidity_exception_isolated(self):
        daemon = RiskDaemon()
        positions = [
            make_position("GOOD", turnover_rate=0.0001),
            {"code": "BAD", "turnover_rate": "oops", "bid_ask_spread": 0.01, "days_to_liquidate": 1.0},
            make_position("GOOD2", turnover_rate=0.0002),
        ]
        alerts = daemon.tick(make_snapshot(initial_capital=100_000.0), positions, [])
        liq = [a for a in alerts if a.alert_type == RiskAlertType.LIQUIDITY_WARNING]
        assert {a.detail.code for a in liq} == {"GOOD", "GOOD2"}

    def test_anomaly_exception_isolated(self):
        daemon = RiskDaemon()
        snapshot = make_snapshot(initial_capital=100_000.0, pnl_history=BREACH_PNL)
        alerts = daemon.tick(snapshot, [], ["not-a-number", 1.0])
        assert any(a.alert_type == RiskAlertType.VAR_BREACH for a in alerts)
        assert not any(a.alert_type == RiskAlertType.MARKET_ANOMALY for a in alerts)
