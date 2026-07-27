# -*- coding: utf-8 -*-
"""Technical indicator calculators for the rule strategy engine.

All calculators accept a pandas DataFrame indexed by date (ascending) with
columns: open / high / low / close / volume. They return pandas Series
aligned to the input index; recent NaN values are normal for warm-up periods.

Supported indicators:
    ma{N}        Simple moving average of close over N periods.
    ema{N}       Exponential moving average of close over N periods.
    rsi{N}       Relative Strength Index (period N, default 14).
    macd         MACD line (ema12 - ema26).
    macd_signal  MACD signal line (ema9 of macd).
    macd_hist    MACD histogram (macd - macd_signal).
    boll_mid     Bollinger middle band (ma20).
    boll_upper   Bollinger upper band (mid + 2*std).
    boll_lower   Bollinger lower band (mid - 2*std).
    pct_chg{N}   Percentage change over N periods (default 1).
    atr{N}       Average True Range over N periods (default 14).
    fib{N}       Fibonacci retracement levels over N-bar lookback (default 60).
                 Returns 5 named series: fib_0.236, fib_0.382, fib_0.5,
                 fib_0.618, fib_0.786.
    support      Nearest support levels (fractal method, fixed window=20).
    resistance   Nearest resistance levels (fractal method, fixed window=20).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Standard Fibonacci retracement ratios.
FIB_RATIOS: Tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)

# Raw OHLCV column names that may be referenced directly in rules.
RAW_PRICE_COLUMNS: Tuple[str, ...] = ("close", "open", "high", "low", "volume")


@dataclass(frozen=True)
class IndicatorSpec:
    """Declarative spec for an indicator to compute.

    `kind` is one of:
        ma, ema, rsi, macd, boll, pct_chg, atr, fib, fib_level, support, resistance
        plus raw OHLCV column names: close, open, high, low, volume.
    `period` is the integer window (ignored for macd / boll which have fixed defaults;
    for support/resistance the window is fixed at 20).
    """

    kind: str
    period: Optional[int] = None

    @property
    def name(self) -> str:
        # Raw price columns and fixed-name indicators return their kind as-is.
        if self.kind in ("macd", "boll", "support", "resistance") or self.kind in RAW_PRICE_COLUMNS:
            return self.kind
        return f"{self.kind}{self.period or ''}"

    @property
    def is_raw_column(self) -> bool:
        """True if this spec refers to a raw OHLCV column rather than a computed indicator."""
        return self.kind in RAW_PRICE_COLUMNS

    @classmethod
    def parse(cls, text: str) -> "IndicatorSpec":
        """Parse an indicator reference string like 'ma5', 'rsi14', 'macd', 'fib_0.618'.

        For Fibonacci retracement levels, two forms are accepted:
        - ``fib{N}`` (e.g. ``fib60``): register all 5 ratio levels with N-bar lookback.
        - ``fib_0.618``: register a single ratio level (lookback defaults to 60).

        Raw OHLCV columns (close/open/high/low/volume) are also accepted and
        resolve to IndicatorSpec(kind=<column_name>, period=None).
        """
        text = text.strip().lower()

        # Raw OHLCV column reference: close / open / high / low / volume.
        if text in RAW_PRICE_COLUMNS:
            return cls(kind=text, period=None)

        # Single Fibonacci level: fib_0.236 / fib_0.382 / fib_0.5 / fib_0.618 / fib_0.786
        if text.startswith("fib_"):
            ratio_str = text[len("fib_"):]
            try:
                ratio = float(ratio_str)
            except ValueError as exc:
                raise ValueError(f"Invalid fib ratio: {text}") from exc
            if ratio not in FIB_RATIOS:
                raise ValueError(
                    f"Unsupported fib ratio {ratio}; must be one of {FIB_RATIOS}"
                )
            # Encode as kind="fib_level", period=int(ratio*1000) for hashable spec.
            return cls(kind="fib_level", period=int(round(ratio * 1000)))

        # All Fibonacci levels with lookback: fib / fib60 / fib30
        if text == "fib" or text.startswith("fib") and text[3:].isdigit():
            lookback = int(text[3:]) if len(text) > 3 else 60
            return cls(kind="fib", period=lookback)

        # Standard indicators.
        for kind in ("ma", "ema", "rsi", "pct_chg", "atr", "macd", "boll", "support", "resistance"):
            if text.startswith(kind):
                rest = text[len(kind):]
                period = int(rest) if rest else None
                return cls(kind=kind, period=period)
        raise ValueError(f"Unknown indicator: {text}")


def compute_indicators(df: pd.DataFrame, specs: List[IndicatorSpec]) -> Dict[str, pd.Series]:
    """Compute the requested indicators and return them as a dict keyed by name.

    The input DataFrame must contain at least a `close` column. For Bollinger
    bands a 20-period window is used; for MACD the standard 12/26/9 params.

    New indicators (P0-A):
    - ``atr{N}``: Average True Range (default period 14). Requires high/low/close.
    - ``fib{N}``: Fibonacci retracement levels over N-bar lookback (default 60).
      Produces 5 series named ``fib_0.236`` / ``fib_0.382`` / ``fib_0.5`` /
      ``fib_0.618`` / ``fib_0.786``. Requires high/low/close.
    - ``fib_0.618`` (single level): a single Fibonacci level series.
    - ``support`` / ``resistance``: nearest support / resistance price levels
      (fractal method, fixed window=20). Requires high/low.
    """
    if "close" not in df.columns:
        raise ValueError("DataFrame must contain a 'close' column")
    if df.empty:
        return {}

    close = df["close"]
    out: Dict[str, pd.Series] = {}

    for spec in specs:
        name = spec.name
        if name in out:
            continue
        if spec.is_raw_column:
            # Raw OHLCV column reference — just expose the column itself.
            if spec.kind in df.columns:
                out[name] = df[spec.kind]
            else:
                logger.warning("Raw column %s not found in DataFrame", spec.kind)
        elif spec.kind == "ma":
            n = spec.period or 5
            out[name] = close.rolling(window=n, min_periods=n).mean()
        elif spec.kind == "ema":
            n = spec.period or 5
            out[name] = close.ewm(span=n, adjust=False).mean()
        elif spec.kind == "rsi":
            n = spec.period or 14
            out[name] = _rsi(close, n)
        elif spec.kind == "macd":
            macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
            signal = macd_line.ewm(span=9, adjust=False).mean()
            out["macd"] = macd_line
            out["macd_signal"] = signal
            out["macd_hist"] = macd_line - signal
        elif spec.kind == "boll":
            mid = close.rolling(window=20, min_periods=20).mean()
            std = close.rolling(window=20, min_periods=20).std(ddof=0)
            out["boll_mid"] = mid
            out["boll_upper"] = mid + 2 * std
            out["boll_lower"] = mid - 2 * std
        elif spec.kind == "pct_chg":
            n = spec.period or 1
            out[name] = close.pct_change(periods=n) * 100.0
        elif spec.kind == "atr":
            n = spec.period or 14
            out[name] = compute_atr(df, period=n)
        elif spec.kind == "fib":
            lookback = spec.period or 60
            fib_levels = compute_fibonacci_retracement(df, lookback=lookback)
            for ratio, series in fib_levels.items():
                key = f"fib_{ratio}"
                if key not in out:
                    out[key] = series
        elif spec.kind == "fib_level":
            # Single ratio encoded as period=int(ratio*1000).
            ratio = (spec.period or 618) / 1000.0
            # Use default 60-bar lookback for single-level spec.
            fib_levels = compute_fibonacci_retracement(df, lookback=60)
            series = fib_levels.get(ratio)
            if series is not None:
                out[f"fib_{ratio}"] = series
        elif spec.kind == "support":
            out["support"] = compute_support_resistance(df)["support_series"]
        elif spec.kind == "resistance":
            out["resistance"] = compute_support_resistance(df)["resistance_series"]
        else:
            logger.warning("Unsupported indicator kind: %s", spec.kind)

    return out


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's smoothing = EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss is 0 (all gains), RSI = 100; when avg_gain is 0 (all losses), RSI = 0.
    rsi = rsi.fillna(100.0 if avg_loss.iloc[-1] == 0 else 0.0)
    return rsi


# ============================================================
# P0-A: New indicators — ATR / Fibonacci / Support-Resistance
# ============================================================

def compute_atr(
    df: pd.DataFrame,
    period: int = 14,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.Series:
    """Compute Average True Range (ATR) as Wilder's EMA of True Range.

    True Range = max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )

    The first row has no prev_close; its TR equals high - low.

    Args:
        df: Daily-bar DataFrame indexed by date ascending.
        period: EMA window (Wilder's smoothing). Default 14.
        high_col / low_col / close_col: Column names.

    Returns:
        pd.Series indexed like df, ATR values. First `period-1` rows may
        be NaN due to EMA warm-up. Use ``.iloc[-1]`` to get the latest ATR.
    """
    if high_col not in df.columns or low_col not in df.columns or close_col not in df.columns:
        raise ValueError(f"DataFrame must contain {high_col}/{low_col}/{close_col} columns for ATR")

    high = df[high_col]
    low = df[low_col]
    prev_close = df[close_col].shift(1)

    # True Range: max of three candidates. For the first row, prev_close is NaN,
    # so the abs(...) terms become NaN, and max(NaN, NaN, high-low) = high-low
    # only if we use np.nanmax — but pandas .max(skipna=True) on a Series
    # constructed from a DataFrame row returns NaN. We handle it explicitly.
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    # Element-wise max ignoring NaN (first row: tr2/tr3 are NaN, use tr1).
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1, skipna=True)
    # For the very first row, .max(skipna=True) returns tr1 since tr2/tr3 are NaN
    # and pandas max skips NaN. But if all three are NaN (shouldn't happen),
    # the result is NaN; that's acceptable.

    # Wilder's smoothing = EMA with alpha = 1/period.
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr


def compute_fibonacci_retracement(
    df: pd.DataFrame,
    lookback: int = 60,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> Dict[float, pd.Series]:
    """Compute Fibonacci retracement levels as time series over a rolling window.

    For each bar at index i, the lookback window is [i-lookback+1, i]. Within
    that window:
        swing_high = max(high)
        swing_low  = min(low)
        diff = swing_high - swing_low

    Trend direction is auto-detected per bar by comparing the latest close
    to the close `lookback` bars ago:
        - If latest_close >= window_first_close: up-trend
            level = swing_high - ratio * diff
        - Else: down-trend
            level = swing_low + ratio * diff

    Args:
        df: Daily-bar DataFrame indexed by date ascending.
        lookback: Number of bars to consider for swing high/low. Default 60.
        high_col / low_col / close_col: Column names.

    Returns:
        Dict mapping each ratio in FIB_RATIOS (0.236, 0.382, 0.5, 0.618, 0.786)
        to a pd.Series of retracement price levels, indexed like df.
        First `lookback-1` rows may be NaN.
    """
    if high_col not in df.columns or low_col not in df.columns or close_col not in df.columns:
        raise ValueError(
            f"DataFrame must contain {high_col}/{low_col}/{close_col} columns for Fibonacci"
        )
    if lookback < 2:
        raise ValueError(f"lookback must be >= 2, got {lookback}")

    high = df[high_col]
    low = df[low_col]
    close = df[close_col]

    # Rolling swing high/low.
    swing_high = high.rolling(window=lookback, min_periods=lookback).max()
    swing_low = low.rolling(window=lookback, min_periods=lookback).min()
    diff = swing_high - swing_low

    # Trend direction: compare latest close to close `lookback` bars ago.
    window_first_close = close.shift(lookback - 1)
    is_up_trend = close >= window_first_close

    # For each ratio, compute the level series.
    out: Dict[float, pd.Series] = {}
    for ratio in FIB_RATIOS:
        up_level = swing_high - ratio * diff
        down_level = swing_low + ratio * diff
        # Use numpy where to pick per-bar based on trend direction.
        level = pd.Series(
            np.where(is_up_trend, up_level, down_level),
            index=df.index,
        )
        # Mask NaN where rolling window hasn't filled yet.
        level = level.where(swing_high.notna())
        out[ratio] = level
    return out


def compute_support_resistance(
    df: pd.DataFrame,
    window: int = 20,
    method: str = "fractal",
    high_col: str = "high",
    low_col: str = "low",
) -> Dict[str, Any]:
    """Identify support and resistance levels using fractal or cluster method.

    Fractal method (default):
        A bar's high is a resistance if it's the maximum within the window
        [i-window, i+window]. Similarly for lows as supports.
        Returns two Series (support_series / resistance_series) where each
        bar's value is the nearest identified support/resistance level
        looking backward. This makes the output usable in rule comparisons
        (e.g. ``close <= support``).

    Cluster method:
        Round prices to bins (0.5% of price range), find density peaks.
        Returns the same Series shape but with cluster centroids.

    Args:
        df: Daily-bar DataFrame.
        window: Lookback window on each side for fractal detection.
        method: "fractal" (default) or "cluster".
        high_col / low_col: Column names.

    Returns:
        Dict with keys:
        - "supports": List[float] — all identified support levels (ascending).
        - "resistances": List[float] — all identified resistance levels (ascending).
        - "support_series": pd.Series — nearest support per bar (for rule engine).
        - "resistance_series": pd.Series — nearest resistance per bar.
    """
    if high_col not in df.columns or low_col not in df.columns:
        raise ValueError(
            f"DataFrame must contain {high_col}/{low_col} columns for support/resistance"
        )
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")

    high = df[high_col]
    low = df[low_col]

    if method == "fractal":
        supports_list, resistances_list = _detect_fractals(high, low, window)
    elif method == "cluster":
        supports_list, resistances_list = _detect_clusters(high, low)
    else:
        raise ValueError(f"Unknown method: {method!r}, must be 'fractal' or 'cluster'")

    # Build per-bar nearest-level series for rule engine use.
    support_series = _nearest_level_series(low, supports_list)
    resistance_series = _nearest_level_series(high, resistances_list)

    return {
        "supports": sorted(supports_list),
        "resistances": sorted(resistances_list),
        "support_series": support_series,
        "resistance_series": resistance_series,
    }


def _detect_fractals(
    high: pd.Series,
    low: pd.Series,
    window: int,
) -> Tuple[List[float], List[float]]:
    """Detect support (low fractal) and resistance (high fractal) levels.

    A high at index i is a resistance fractal if it's the strict maximum
    within [i-window, i+window]. A low at index i is a support fractal if
    it's the strict minimum within the same window.

    Edge bars (first/last `window` bars) are excluded.
    """
    n = len(high)
    supports: List[float] = []
    resistances: List[float] = []

    if n < 2 * window + 1:
        return supports, resistances

    high_vals = high.values
    low_vals = low.values

    for i in range(window, n - window):
        window_high = high_vals[i - window : i + window + 1]
        window_low = low_vals[i - window : i + window + 1]
        # Strict max: this bar's high is strictly greater than all others.
        if high_vals[i] == window_high.max() and (window_high == high_vals[i]).sum() == 1:
            resistances.append(float(high_vals[i]))
        if low_vals[i] == window_low.min() and (window_low == low_vals[i]).sum() == 1:
            supports.append(float(low_vals[i]))

    return supports, resistances


def _detect_clusters(
    high: pd.Series,
    low: pd.Series,
    n_bins: int = 50,
) -> Tuple[List[float], List[float]]:
    """Detect support/resistance via price-bin density peaks.

    Simplified approach: divide the price range into n_bins equal-width bins,
    count how many highs/lows fall in each bin, and select bins whose count
    exceeds 1.5x the mean as levels.
    """
    if high.empty or low.empty:
        return [], []

    all_prices = pd.concat([high, low])
    pmin = float(all_prices.min())
    pmax = float(all_prices.max())
    if pmax <= pmin:
        return [], []

    bin_width = (pmax - pmin) / n_bins
    # Count highs per bin.
    high_bins = ((high - pmin) / bin_width).astype(int).clip(0, n_bins - 1)
    low_bins = ((low - pmin) / bin_width).astype(int).clip(0, n_bins - 1)

    high_counts = high_bins.value_counts()
    low_counts = low_bins.value_counts()

    threshold_high = max(2, high_counts.mean() * 1.5)
    threshold_low = max(2, low_counts.mean() * 1.5)

    resistances = [
        float(pmin + (b + 0.5) * bin_width)
        for b in high_counts[high_counts > threshold_high].index
    ]
    supports = [
        float(pmin + (b + 0.5) * bin_width)
        for b in low_counts[low_counts > threshold_low].index
    ]

    return supports, resistances


def _nearest_level_series(
    prices: pd.Series,
    levels: List[float],
) -> pd.Series:
    """Build a Series where each bar's value is the nearest level at or below
    the bar's price (for supports) or at or above (for resistances).

    For supports: pick the largest level <= price (so ``close <= support``
    triggers when price has fallen to the nearest support below).
    For resistances: pick the smallest level >= price.

    The caller passes `low` for supports and `high` for resistances, which
    determines the direction. If no level satisfies the constraint, NaN.

    To keep the API simple and the rule engine able to do both > and <
    comparisons, we return the *nearest* level (absolute distance) per bar,
    regardless of direction. The rule author chooses the comparison operator.
    """
    if not levels:
        return pd.Series([float("nan")] * len(prices), index=prices.index)

    levels_arr = np.array(sorted(levels))
    out = []
    for p in prices.values:
        if pd.isna(p):
            out.append(float("nan"))
            continue
        # Find the nearest level by absolute distance.
        idx = np.abs(levels_arr - p).argmin()
        out.append(float(levels_arr[idx]))
    return pd.Series(out, index=prices.index)
