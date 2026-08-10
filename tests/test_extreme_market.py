# -*- coding: utf-8 -*-
"""Unit tests for T15 ExtremeMarketDetector (paper_trading/extreme_market.py)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.extreme_market import (
    DEFAULT_ACTIONS,
    ExtremeMarketAlert,
    ExtremeMarketDetector,
    ExtremeMarketResponse,
)


def make_index_df(returns, start_price: float = 100.0) -> pd.DataFrame:
    """由收益序列构造含 close 列的指数日线 DataFrame."""
    close = start_price * np.cumprod(1.0 + returns)
    return pd.DataFrame({"close": close})


def build_baseline_df(n: int = 300) -> pd.DataFrame:
    """低波动基准行情：收益在 ±1% 间交替，无极端行情."""
    returns = np.where(np.arange(n) % 2 == 0, 0.01, -0.01)
    return make_index_df(returns)


def build_spike_df(hist: int = 300, spike_days: int = 20, amp: float = 0.04) -> pd.DataFrame:
    """构造极端行情：前 hist 天低波动，最后 spike_days 天波动放大到 amp."""
    hist_returns = np.where(np.arange(hist) % 2 == 0, 0.01, -0.01)
    spike_returns = np.where(np.arange(spike_days) % 2 == 0, amp, -amp)
    return make_index_df(np.concatenate([hist_returns, spike_returns]))


def make_alert() -> ExtremeMarketAlert:
    return ExtremeMarketAlert(
        market="HSI",
        current_vol=0.5,
        historical_vol=0.1,
        ratio=5.0,
    )


class TestExtremeMarketAlert:
    def test_default_actions_list(self):
        alert = make_alert()
        assert alert.actions == ["暂停规则策略", "只执行止损", "禁止市价单开仓"]
        assert alert.actions == DEFAULT_ACTIONS

    def test_fields_and_detected_at_default(self):
        before = datetime.now()
        alert = make_alert()
        after = datetime.now()
        assert alert.market == "HSI"
        assert alert.current_vol == 0.5
        assert alert.historical_vol == 0.1
        assert alert.ratio == 5.0
        assert before <= alert.detected_at <= after

    def test_actions_are_independent_copies(self):
        a1 = make_alert()
        a2 = make_alert()
        a1.actions.append("extra")
        assert a2.actions == DEFAULT_ACTIONS


class TestExtremeMarketDetector:
    def test_detect_returns_alert_on_vol_spike(self):
        detector = ExtremeMarketDetector()
        alert = detector.detect("HSI", build_spike_df())
        assert alert is not None
        assert alert.market == "HSI"
        assert alert.ratio > 3.0
        assert alert.current_vol > alert.historical_vol
        assert alert.current_vol > 0 and alert.historical_vol > 0
        assert np.isclose(alert.ratio, alert.current_vol / alert.historical_vol)

    def test_detect_no_alert_without_spike(self):
        detector = ExtremeMarketDetector()
        assert detector.detect("HSI", build_baseline_df()) is None

    def test_ratio_boundary_exactly_3_0_no_alert(self):
        # 构造 ratio 计算值恰为 3.0（严格 > 语义 → 不触发）
        detector = ExtremeMarketDetector(volatility_multiplier=3.0, lookback_days=252, window_days=2)
        alt = np.where(np.arange(400) % 2 == 0, 0.5, -0.5)
        tail = np.array([0.5, -0.5, 2.5])
        df = make_index_df(np.concatenate([alt, tail]))
        assert detector.detect("boundary", df) is None

    def test_ratio_boundary_strict_greater_than(self):
        # multiplier 恰好等于计算出的 ratio → 不触发；略小于 ratio → 触发
        detector = ExtremeMarketDetector(volatility_multiplier=3.0)
        df = build_spike_df()
        alert = detector.detect("HSI", df)
        assert alert is not None
        r = alert.ratio
        assert ExtremeMarketDetector(volatility_multiplier=r).detect("HSI", df) is None
        below = np.nextafter(r, 0.0)
        assert ExtremeMarketDetector(volatility_multiplier=below).detect("HSI", df) is not None

    def test_insufficient_data_returns_none(self):
        detector = ExtremeMarketDetector()
        # 行数 < window_days + 1
        small = build_baseline_df(n=20)
        assert detector.detect("HSI", small) is None

    def test_minimum_rows_but_no_history_returns_none(self):
        # 恰为 window_days+1 行：当前波动率可算，但历史波动率无数据 → None
        detector = ExtremeMarketDetector()
        df = build_baseline_df(n=21)
        assert detector.detect("HSI", df) is None

    def test_empty_df_returns_none(self):
        detector = ExtremeMarketDetector()
        assert detector.detect("HSI", pd.DataFrame({"close": []})) is None

    def test_none_input_returns_none(self):
        detector = ExtremeMarketDetector()
        assert detector.detect("HSI", None) is None

    def test_missing_close_column_returns_none(self):
        detector = ExtremeMarketDetector()
        df = pd.DataFrame({"open": np.arange(30.0)})
        assert detector.detect("HSI", df) is None

    def test_nan_close_insufficient_returns_none(self):
        # close 尾部大量 NaN → 有效收益不足 window_days → None
        detector = ExtremeMarketDetector()
        close = np.concatenate([np.arange(1.0, 6.0), np.full(25, np.nan)])
        df = pd.DataFrame({"close": close})
        assert detector.detect("HSI", df) is None

    def test_zero_historical_vol_returns_none(self):
        # 价格恒定 → 收益全为 0 → 历史波动率为 0，避免除零 → None
        detector = ExtremeMarketDetector()
        df = pd.DataFrame({"close": np.full(60, 100.0)})
        assert detector.detect("HSI", df) is None

    def test_custom_multiplier(self):
        df = build_spike_df()  # ratio ≈ 3.5
        assert ExtremeMarketDetector(volatility_multiplier=2.0).detect("HSI", df) is not None
        assert ExtremeMarketDetector(volatility_multiplier=5.0).detect("HSI", df) is None

    def test_custom_lookback_days(self):
        # lookback 过短 → 历史均值被过渡窗口抬高 → 不触发
        df = build_spike_df()
        default = ExtremeMarketDetector(volatility_multiplier=3.0, lookback_days=252)
        assert default.detect("HSI", df) is not None
        short = ExtremeMarketDetector(volatility_multiplier=3.0, lookback_days=20)
        assert short.detect("HSI", df) is None

    def test_custom_window_days(self):
        # 10 日窗口：最后 10 天为 ±4% 尖峰 → 触发
        detector = ExtremeMarketDetector(volatility_multiplier=3.0, lookback_days=252, window_days=10)
        assert detector.window_days == 10
        df = build_spike_df(spike_days=10, amp=0.04)
        alert = detector.detect("HSI", df)
        assert alert is not None and alert.ratio > 3.0

    def test_detector_attributes(self):
        d = ExtremeMarketDetector(volatility_multiplier=2.5, lookback_days=100, window_days=15)
        assert d.multiplier == 2.5
        assert d.lookback_days == 100
        assert d.window_days == 15


class TestExtremeMarketResponse:
    def test_activate_deactivate_is_active(self):
        resp = ExtremeMarketResponse()
        alert = make_alert()
        assert resp.is_active() is False
        resp.activate(alert)
        assert resp.is_active() is True
        assert resp.active_alert is alert
        assert resp.activated_at is not None
        resp.deactivate()
        assert resp.is_active() is False
        assert resp.active_alert is None
        assert resp.activated_at is None

    def test_on_activate_callback_called(self):
        calls = []

        def cb(alert):
            calls.append(alert)

        resp = ExtremeMarketResponse(on_activate=cb)
        alert = make_alert()
        resp.activate(alert)
        assert calls == [alert]

    def test_callback_exception_isolation(self):
        def boom(alert):
            raise RuntimeError("callback failed")

        resp = ExtremeMarketResponse(on_activate=boom)
        alert = make_alert()
        resp.activate(alert)  # 不应抛异常
        assert resp.is_active() is True
        assert resp.active_alert is alert

    def test_force_hold_buy_gating(self):
        resp = ExtremeMarketResponse()
        assert resp.force_hold_buy() is False
        resp.activate(make_alert())
        assert resp.force_hold_buy() is True
        resp.deactivate()
        assert resp.force_hold_buy() is False

    def test_allow_market_orders_gating(self):
        resp = ExtremeMarketResponse()
        assert resp.allow_market_orders() is True
        resp.activate(make_alert())
        assert resp.allow_market_orders() is False
        resp.deactivate()
        assert resp.allow_market_orders() is True

    def test_gating_switches_disabled(self):
        resp = ExtremeMarketResponse(
            on_activate=None,
            hold_buy_on_activation=False,
            disable_market_orders_on_activation=False,
        )
        resp.activate(make_alert())
        assert resp.is_active() is True
        assert resp.force_hold_buy() is False
        assert resp.allow_market_orders() is True

    def test_reactivate_updates_state(self):
        resp = ExtremeMarketResponse()
        a1 = make_alert()
        a2 = make_alert()
        a2.ratio = 9.0
        resp.activate(a1)
        first_activated_at = resp.activated_at
        resp.activate(a2)
        assert resp.active_alert is a2
        assert resp.activated_at is not None
        # deactivate 后再激活
        resp.deactivate()
        resp.activate(a2)
        assert resp.active_alert is a2
        assert resp.activated_at is not None
