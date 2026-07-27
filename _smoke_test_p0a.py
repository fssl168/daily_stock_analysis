# -*- coding: utf-8 -*-
"""P0-A smoke test — verify Fibonacci / ATR / Support-Resistance indicators."""

import pandas as pd
from strategies_v2 import (
    compute_atr,
    compute_fibonacci_retracement,
    compute_indicators,
    compute_support_resistance,
    FIB_RATIOS,
    IndicatorSpec,
)


def build_synthetic_df(n: int = 80) -> pd.DataFrame:
    """Build a synthetic daily-bar DataFrame for testing.

    Uses a known pattern: prices rise linearly from 80 to 120, with high/low
    bands of +/-1 around close. With n=80 and lookback=60, the rolling window
    for the last bar covers bars 20..79, giving swing_high=121, swing_low=89.127.
    """
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    # Up-trend: close rises linearly from 80 to 120, indexed by dates so the
    # DataFrame constructor does not misalign RangeIndex vs DatetimeIndex.
    close = pd.Series(
        [80 + i * (40 / (n - 1)) for i in range(n)], index=dates, name="close"
    )
    # High = close + small noise, Low = close - small noise.
    high = close + 1.0
    low = close - 1.0
    df = pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000000,
    }, index=dates)
    df.index.name = "date"
    return df


def test_fibonacci_levels():
    df = build_synthetic_df(80)
    levels = compute_fibonacci_retracement(df, lookback=60)
    assert set(levels.keys()) == set(FIB_RATIOS), f"Got {set(levels.keys())}"
    # For the up-trend case at the last bar (i=79), the lookback window is
    # bars 20..79 (60 bars). Within that window:
    #   swing_high = max(high[20:80]) = high[-1] = close[-1] + 1 = 121
    #   swing_low  = min(low[20:80])  = low[20]  = close[20] - 1 = 89.1266
    #   diff = 121 - 89.1266 = 31.8734
    # Trend is up because close[-1]=120 >= close[20]=90.1266.
    # For ratio 0.618: level = 121 - 0.618 * 31.8734 = 101.3022
    latest_618 = float(levels[0.618].iloc[-1])
    swing_high = 121.0
    swing_low = 80 + 20 * (40 / 79) - 1.0  # close[20] - 1
    expected_618 = swing_high - 0.618 * (swing_high - swing_low)
    assert abs(latest_618 - expected_618) < 0.01, (
        f"fib 0.618 = {latest_618}, expected {expected_618}"
    )
    # Check that 0.236 < 0.382 < 0.5 < 0.618 < 0.786 in up-trend
    # (in up-trend, larger ratio => deeper retracement => lower price).
    latest = {r: float(levels[r].iloc[-1]) for r in FIB_RATIOS}
    assert latest[0.236] > latest[0.382] > latest[0.5] > latest[0.618] > latest[0.786], (
        f"Fib levels not monotonic in up-trend: {latest}"
    )
    print(f"[1] Fibonacci levels OK: {latest}")


def test_atr():
    df = build_synthetic_df(80)
    atr = compute_atr(df, period=14)
    # ATR should be positive and finite (no NaN at the end).
    latest = float(atr.iloc[-1])
    assert latest > 0, f"ATR should be > 0, got {latest}"
    assert not pd.isna(latest), "ATR should not be NaN at the end"
    # For synthetic data with high-low = 2.0 every day, ATR should be ~2.0.
    assert 1.5 < latest < 3.0, f"ATR expected ~2.0, got {latest}"
    print(f"[2] ATR OK: latest={latest:.4f}")


def test_support_resistance():
    df = build_synthetic_df(80)
    sr = compute_support_resistance(df, window=10, method="fractal")
    assert "supports" in sr and "resistances" in sr
    assert "support_series" in sr and "resistance_series" in sr
    # Fractal detection with strict max should find some levels.
    print(f"[3] Supports found: {len(sr['supports'])}, Resistances: {len(sr['resistances'])}")
    # Series should have same length as df.
    assert len(sr["support_series"]) == len(df)
    assert len(sr["resistance_series"]) == len(df)
    # Test cluster method too.
    sr_cluster = compute_support_resistance(df, method="cluster")
    assert "supports" in sr_cluster and "resistances" in sr_cluster
    print(f"[4] Cluster method OK: supports={len(sr_cluster['supports'])}, resistances={len(sr_cluster['resistances'])}")


def test_indicator_spec_parse():
    # Standard
    s1 = IndicatorSpec.parse("ma5")
    assert s1.kind == "ma" and s1.period == 5
    # Fib all levels
    s2 = IndicatorSpec.parse("fib")
    assert s2.kind == "fib" and s2.period == 60
    # Fib with lookback
    s3 = IndicatorSpec.parse("fib30")
    assert s3.kind == "fib" and s3.period == 30
    # Single fib level
    s4 = IndicatorSpec.parse("fib_0.618")
    assert s4.kind == "fib_level"
    assert s4.period == 618
    # Support/resistance
    s5 = IndicatorSpec.parse("support")
    assert s5.kind == "support"
    s6 = IndicatorSpec.parse("resistance")
    assert s6.kind == "resistance"
    print("[5] IndicatorSpec.parse OK for all new indicator types")


def test_compute_indicators_dispatch():
    df = build_synthetic_df(80)
    specs = [
        IndicatorSpec.parse("ma5"),
        IndicatorSpec.parse("atr"),
        IndicatorSpec.parse("fib60"),
        IndicatorSpec.parse("fib_0.618"),
        IndicatorSpec.parse("support"),
        IndicatorSpec.parse("resistance"),
    ]
    out = compute_indicators(df, specs)
    # All expected keys present
    assert "ma5" in out
    assert "atr" in out
    assert "fib_0.236" in out and "fib_0.382" in out and "fib_0.5" in out
    assert "fib_0.618" in out and "fib_0.786" in out
    assert "support" in out
    assert "resistance" in out
    # fib_0.618 single-level spec should NOT duplicate (already set by fib60)
    # Check that values are valid floats at the latest bar.
    assert not pd.isna(out["atr"].iloc[-1])
    assert not pd.isna(out["fib_0.618"].iloc[-1])
    print(f"[6] compute_indicators dispatch OK: keys={sorted(out.keys())}")


def test_fib_in_rules():
    """Test that Fib levels can be used in rule comparisons via schema."""
    from strategies_v2.schema import Rule

    # close <= fib_0.618 should parse successfully.
    r = Rule(left="close", op="<=", right="fib_0.618")
    assert r.left == "close"
    assert r.right == "fib_0.618"
    # The right side should parse as a fib_level indicator spec.
    assert r.right_ref is not None, "fib_0.618 should parse as indicator"
    assert r.right_ref.kind == "fib_level"
    print(f"[7] Rule with fib_0.618 parses OK: right_ref.kind={r.right_ref.kind}")


def main():
    test_fibonacci_levels()
    test_atr()
    test_support_resistance()
    test_indicator_spec_parse()
    test_compute_indicators_dispatch()
    test_fib_in_rules()
    print("\nAll P0-A smoke tests passed.")


if __name__ == "__main__":
    main()
