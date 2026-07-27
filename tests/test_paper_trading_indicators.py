# -*- coding: utf-8 -*-
"""Unit tests for P0-A technical indicator enhancements.

Validates Fibonacci retracement, ATR and support/resistance calculations
using deterministic synthetic data so the tests run without network access.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategies_v2.indicators import (
    IndicatorSpec,
    compute_atr,
    compute_fibonacci_retracement,
    compute_indicators,
    compute_support_resistance,
)


def _make_trend_df(
    start_price: float = 100.0,
    days: int = 80,
    uptrend: bool = True,
) -> pd.DataFrame:
    """Build a deterministic daily-bar DataFrame with a clear trend.

    For an up-trend: close rises from start_price to start_price + 40.
    For a down-trend: close falls from start_price + 40 to start_price.
    """
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    n = len(idx)
    if uptrend:
        close_arr = np.linspace(start_price, start_price + 40.0, n)
    else:
        close_arr = np.linspace(start_price + 40.0, start_price, n)
    high_arr = close_arr + 0.5
    low_arr = close_arr - 0.5
    open_arr = np.concatenate([[start_price], close_arr[:-1]])
    df = pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": np.full(n, 10000.0),
        },
        index=idx,
    )
    df.index.name = "date"
    return df


class TestIndicatorSpec:
    """IndicatorSpec parsing rules."""

    def test_parse_raw_columns(self):
        for col in ("close", "open", "high", "low", "volume"):
            spec = IndicatorSpec.parse(col)
            assert spec.kind == col
            assert spec.is_raw_column is True

    def test_parse_fib_lookback(self):
        spec = IndicatorSpec.parse("fib60")
        assert spec.kind == "fib"
        assert spec.period == 60
        assert spec.name == "fib60"

    def test_parse_single_fib_level(self):
        spec = IndicatorSpec.parse("fib_0.618")
        assert spec.kind == "fib_level"
        assert spec.period == 618

    def test_parse_unknown_indicator_raises(self):
        with pytest.raises(ValueError):
            IndicatorSpec.parse("unknown999")


class TestComputeATR:
    """Average True Range calculation."""

    def test_atr_requires_ohlc(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        with pytest.raises(ValueError):
            compute_atr(df)

    def test_atr_flat_range(self):
        """When high-low is constant at 2.0, ATR converges toward 2.0."""
        n = 30
        df = pd.DataFrame(
            {
                "open": np.full(n, 100.0),
                "high": np.full(n, 101.0),
                "low": np.full(n, 99.0),
                "close": np.full(n, 100.0),
            },
            index=pd.bdate_range(end=pd.Timestamp.today(), periods=n),
        )
        atr = compute_atr(df, period=14)
        assert not np.isnan(atr.iloc[-1])
        assert atr.iloc[-1] == pytest.approx(2.0, abs=0.01)

    def test_atr_gaps_are_captured(self):
        """A gap-up day should increase true range.

        We verify two things:
        - the last raw true range captures the gap (equals 6.0);
        - the smoothed ATR with a gap is strictly higher than without one.
        Wilder's EMA damps a single outlier, so asserting the ATR itself is
        above 4.0 would be numerically incorrect.
        """
        n = 20
        closes = np.full(n, 100.0)
        closes[-1] = 105.0  # large gap up
        highs = closes + 1.0
        lows = closes - 1.0
        opens = np.concatenate([[100.0], closes[:-1]])
        df = pd.DataFrame(
            {
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
            },
            index=pd.bdate_range(end=pd.Timestamp.today(), periods=n),
        )
        atr = compute_atr(df, period=14)
        # Last raw TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
        # = max(2, abs(106-100), abs(104-100)) = 6
        prev_close = df["close"].shift(1).iloc[-1]
        last_tr = max(
            highs[-1] - lows[-1],
            abs(highs[-1] - prev_close),
            abs(lows[-1] - prev_close),
        )
        assert last_tr == pytest.approx(6.0, abs=0.01)
        # Compare against a flat baseline to prove the gap was incorporated.
        flat_df = df.copy()
        flat_df.iloc[-1, flat_df.columns.get_loc("close")] = 100.0
        flat_df.iloc[-1, flat_df.columns.get_loc("high")] = 101.0
        flat_df.iloc[-1, flat_df.columns.get_loc("low")] = 99.0
        flat_atr = compute_atr(flat_df, period=14)
        assert atr.iloc[-1] > flat_atr.iloc[-1]


class TestComputeFibonacci:
    """Fibonacci retracement levels."""

    def test_fib_values_for_uptrend(self):
        """In a clean up-trend, 0.618 level should sit near 100 + 0.382*40 = 115.28.

        The scheme says 87.6 in a synthetic example; here we use a 100->140 move
        and verify the ratio math instead of hard-coding a magic number.
        """
        df = _make_trend_df(start_price=100.0, days=80, uptrend=True)
        fib = compute_fibonacci_retracement(df, lookback=60)
        last = {r: series.iloc[-1] for r, series in fib.items()}
        swing_high = df["high"].iloc[-60:].max()
        swing_low = df["low"].iloc[-60:].min()
        diff = swing_high - swing_low

        assert last[0.618] == pytest.approx(swing_high - 0.618 * diff, abs=0.01)
        assert last[0.5] == pytest.approx(swing_high - 0.5 * diff, abs=0.01)
        assert last[0.382] == pytest.approx(swing_high - 0.382 * diff, abs=0.01)

    def test_fib_values_for_downtrend(self):
        """In a down-trend, levels are computed from the swing low upward."""
        df = _make_trend_df(start_price=100.0, days=80, uptrend=False)
        fib = compute_fibonacci_retracement(df, lookback=60)
        last = {r: series.iloc[-1] for r, series in fib.items()}
        swing_high = df["high"].iloc[-60:].max()
        swing_low = df["low"].iloc[-60:].min()
        diff = swing_high - swing_low

        assert last[0.618] == pytest.approx(swing_low + 0.618 * diff, abs=0.01)

    def test_fib_warmup_nan(self):
        df = _make_trend_df(days=80, uptrend=True)
        fib = compute_fibonacci_retracement(df, lookback=60)
        assert np.isnan(fib[0.618].iloc[58])
        assert not np.isnan(fib[0.618].iloc[59])


class TestComputeSupportResistance:
    """Support / resistance detection."""

    def test_fractal_detects_clear_levels(self):
        """A repeated V-shape should produce support/resistance levels."""
        idx = pd.bdate_range(end=pd.Timestamp.today(), periods=60)
        base = np.linspace(100.0, 100.0, 60)
        # Create clear fractal highs and lows by spiking every 10 bars.
        highs = base + np.where(np.arange(60) % 10 == 5, 5.0, 0.5)
        lows = base - np.where(np.arange(60) % 10 == 0, 5.0, 0.5)
        df = pd.DataFrame(
            {
                "open": base,
                "high": highs,
                "low": lows,
                "close": base,
            },
            index=idx,
        )
        sr = compute_support_resistance(df, window=3, method="fractal")
        assert len(sr["supports"]) > 0
        assert len(sr["resistances"]) > 0
        assert all(isinstance(lv, float) for lv in sr["supports"])
        assert all(isinstance(lv, float) for lv in sr["resistances"])

    def test_cluster_method_returns_levels(self):
        df = _make_trend_df(days=60, uptrend=True)
        sr = compute_support_resistance(df, method="cluster")
        # Cluster method may return empty on monotonic data; just verify shape.
        assert "supports" in sr
        assert "resistances" in sr

    def test_unknown_method_raises(self):
        df = _make_trend_df(days=60, uptrend=True)
        with pytest.raises(ValueError):
            compute_support_resistance(df, method="invalid")


class TestComputeIndicatorsDispatcher:
    """compute_indicators orchestration."""

    def test_compute_atr_via_specs(self):
        df = _make_trend_df(days=40, uptrend=True)
        out = compute_indicators(df, [IndicatorSpec.parse("atr14")])
        assert "atr14" in out
        assert not np.isnan(out["atr14"].iloc[-1])

    def test_compute_fib_via_specs(self):
        df = _make_trend_df(days=80, uptrend=True)
        out = compute_indicators(df, [IndicatorSpec.parse("fib60")])
        for ratio in (0.236, 0.382, 0.5, 0.618, 0.786):
            key = f"fib_{ratio}"
            assert key in out
            assert not np.isnan(out[key].iloc[-1])

    def test_compute_support_resistance_via_specs(self):
        df = _make_trend_df(days=80, uptrend=True)
        out = compute_indicators(
            df,
            [IndicatorSpec.parse("support"), IndicatorSpec.parse("resistance")],
        )
        assert "support" in out
        assert "resistance" in out
