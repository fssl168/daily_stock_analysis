# -*- coding: utf-8 -*-
"""Smart Stop-Loss / Take-Profit calculator (P1-A).

Computes a three-line exit plan (stop_loss / take_profit_1 / take_profit_2)
by combining three independent signal sources:

1. **ATR (volatility)** — sets a volatility-adjusted distance from entry.
2. **Fibonacci retracement** — uses the 0.618 retracement as a protective
   stop and the 1.618 extension as a profit target.
3. **Support / Resistance + chip distribution** — snaps the stops to the
   nearest structural level so they sit below support (for stops) or just
   below resistance (for take-profit).

Final values are the most conservative (i.e., tightest risk-aligned) blend
across the three sources. The result is a :class:`SLTPResult` that can be
persisted onto :class:`PaperPosition` (stop_loss / take_profit columns)
and used by :class:`TradingEngine.check_stop_loss_take_profit`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from paper_trading.strategies import (
    FIB_RATIOS,
    compute_atr,
    compute_fibonacci_retracement,
    compute_support_resistance,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class SLTPResult:
    """Three-line exit plan produced by :class:`SLTPCalculator`."""

    entry_price: float
    stop_loss: float
    take_profit_1: float  # short-term target (e.g., 1.5x ATR or nearest resistance)
    take_profit_2: float  # mid-term target (e.g., 3x ATR or Fib 1.618 extension)
    # Diagnostic / audit fields.
    atr: Optional[float] = None
    atr_period: int = 14
    fib_0618: Optional[float] = None
    fib_1618: Optional[float] = None  # custom extension (not in FIB_RATIOS by default)
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    chip_peak_price: Optional[float] = None  # price with highest volume concentration
    method: str = "atr_fib_support_blend"
    reasoning: str = ""
    # Per-component raw values for debugging.
    components: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "atr": self.atr,
            "atr_period": self.atr_period,
            "fib_0618": self.fib_0618,
            "fib_1618": self.fib_1618,
            "nearest_support": self.nearest_support,
            "nearest_resistance": self.nearest_resistance,
            "chip_peak_price": self.chip_peak_price,
            "method": self.method,
            "reasoning": self.reasoning,
            "components": self.components,
        }


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

class SLTPCalculator:
    """Three-source stop-loss / take-profit calculator.

    Usage:
        calc = SLTPCalculator(data_provider=...)
        result = calc.compute(code="600519", entry_price=1800.0)
        # result.stop_loss / result.take_profit_1 / result.take_profit_2
    """

    def __init__(
        self,
        data_provider: Optional[Any] = None,
        lookback: int = 60,
        atr_period: int = 14,
        support_window: int = 10,
        chip_bins: int = 30,
        chip_lookback: int = 90,
        sl_atr_mult: float = 1.5,
        tp1_atr_mult: float = 1.5,
        tp2_atr_mult: float = 3.0,
        sl_buffer_pct: float = 0.003,  # 0.3% buffer below support for stop
    ):
        """Initialize calculator.

        Args:
            data_provider: Data provider with get_daily_data(code, days=...).
                If None, the caller must supply a DataFrame to compute().
            lookback: Fibonacci lookback window (bars).
            atr_period: ATR EMA period.
            support_window: Fractal window for support/resistance detection.
            chip_bins: Number of price bins for chip-distribution estimation.
            chip_lookback: Bars of history to aggregate for chip distribution.
            sl_atr_mult: Stop-loss = entry - sl_atr_mult * ATR.
            tp1_atr_mult: TP1 = entry + tp1_atr_mult * ATR.
            tp2_atr_mult: TP2 = entry + tp2_atr_mult * ATR.
            sl_buffer_pct: Stop is placed this far below the nearest support
                to avoid being triggered by ordinary noise.
        """
        self.data_provider = data_provider
        self.lookback = int(lookback)
        self.atr_period = int(atr_period)
        self.support_window = int(support_window)
        self.chip_bins = int(chip_bins)
        self.chip_lookback = int(chip_lookback)
        self.sl_atr_mult = float(sl_atr_mult)
        self.tp1_atr_mult = float(tp1_atr_mult)
        self.tp2_atr_mult = float(tp2_atr_mult)
        self.sl_buffer_pct = float(sl_buffer_pct)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compute(
        self,
        code: str,
        entry_price: float,
        df: Optional[pd.DataFrame] = None,
        market: str = "cn",
    ) -> SLTPResult:
        """Compute the three-line exit plan for a long position.

        Args:
            code: Stock code (used for data fetching if df is None).
            entry_price: Intended entry price (e.g., current market price).
            df: Pre-fetched daily-bar DataFrame. If None, fetched via
                data_provider.
            market: Market hint for data provider ("cn" / "hk" / "us").

        Returns:
            SLTPResult with stop_loss, take_profit_1, take_profit_2.
        """
        if entry_price <= 0:
            raise ValueError(f"entry_price must be positive, got {entry_price}")

        # Fetch data if not supplied.
        if df is None:
            df = self._fetch_data(code, market=market)

        if df is None or len(df) < max(self.lookback, self.atr_period + 5):
            logger.warning(
                "[SLTPCalculator] Insufficient data for code=%s (rows=%s); "
                "falling back to ATR-only defaults",
                code, len(df) if df is not None else 0,
            )
            return self._fallback_result(entry_price, reason="insufficient data")

        # Compute the three signal sources.
        atr_val = self._compute_atr(df)
        fib_levels = self._compute_fib(df)
        sr = self._compute_support_resistance(df)
        chip = self._fetch_chip_distribution(df)

        # Extract scalar values.
        fib_0618 = self._last_scalar(fib_levels.get(0.618))
        # Fib 1.618 extension (not in FIB_RATIOS by default) — compute manually.
        fib_1618 = self._compute_fib_extension(df, ratio=1.618)

        nearest_support = self._nearest_below(sr.get("supports", []), entry_price)
        nearest_resistance = self._nearest_above(sr.get("resistances", []), entry_price)
        chip_peak = chip.get("peak_price")

        # --- Stop loss: take the HIGHEST (most conservative) of:
        #   1. entry - sl_atr_mult * ATR
        #   2. fib 0.618 retracement (if in up-trend, this is below entry)
        #   3. nearest_support * (1 - sl_buffer_pct)
        # The highest stop is the tightest — it gives back the least if hit.
        candidates_sl: List[float] = [entry_price - self.sl_atr_mult * atr_val]
        if fib_0618 is not None and fib_0618 < entry_price:
            candidates_sl.append(fib_0618)
        if nearest_support is not None and nearest_support < entry_price:
            candidates_sl.append(nearest_support * (1.0 - self.sl_buffer_pct))
        # Chip peak below entry acts as strong support.
        if chip_peak is not None and chip_peak < entry_price:
            candidates_sl.append(chip_peak * (1.0 - self.sl_buffer_pct))

        stop_loss = max(candidates_sl)
        # Safety: stop must be below entry.
        if stop_loss >= entry_price:
            stop_loss = entry_price - self.sl_atr_mult * atr_val

        # --- Take profit 1 (short-term): take the LOWEST (most conservative):
        #   1. entry + tp1_atr_mult * ATR
        #   2. nearest_resistance * (1 - sl_buffer_pct)  [just below resistance]
        #   3. chip peak above entry
        candidates_tp1: List[float] = [entry_price + self.tp1_atr_mult * atr_val]
        if nearest_resistance is not None and nearest_resistance > entry_price:
            candidates_tp1.append(nearest_resistance * (1.0 - self.sl_buffer_pct))
        if chip_peak is not None and chip_peak > entry_price:
            candidates_tp1.append(chip_peak)
        take_profit_1 = min(candidates_tp1)
        if take_profit_1 <= entry_price:
            take_profit_1 = entry_price + self.tp1_atr_mult * atr_val

        # --- Take profit 2 (mid-term): take the LOWEST:
        #   1. entry + tp2_atr_mult * ATR
        #   2. fib 1.618 extension (if above entry)
        candidates_tp2: List[float] = [entry_price + self.tp2_atr_mult * atr_val]
        if fib_1618 is not None and fib_1618 > entry_price:
            candidates_tp2.append(fib_1618)
        take_profit_2 = min(candidates_tp2)
        if take_profit_2 <= take_profit_1:
            take_profit_2 = take_profit_1 + self.tp1_atr_mult * atr_val

        # Round to 2 decimals for cleanliness.
        stop_loss = round(stop_loss, 2)
        take_profit_1 = round(take_profit_1, 2)
        take_profit_2 = round(take_profit_2, 2)

        # Enforce a minimum risk/reward ratio of 1:1 for TP1 (after rounding).
        risk = entry_price - stop_loss
        reward_1 = take_profit_1 - entry_price
        if risk > 0 and reward_1 < risk:
            # Add a 1-tick buffer so that rounding/float noise keeps RR >= 1.0.
            take_profit_1 = round(entry_price + risk + 0.01, 2)
            reward_1 = take_profit_1 - entry_price

        # Risk/reward sanity: RR should be >= 1.
        risk = entry_price - stop_loss
        reward_1 = take_profit_1 - entry_price
        rr1 = reward_1 / risk if risk > 0 else 0.0
        reasoning = (
            f"SLTP blended (ATR={atr_val:.4f}, Fib0618={fib_0618}, "
            f"Support={nearest_support}, Resistance={nearest_resistance}, "
            f"ChipPeak={chip_peak}); RR1={rr1:.2f}"
        )

        return SLTPResult(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            atr=atr_val,
            atr_period=self.atr_period,
            fib_0618=fib_0618,
            fib_1618=fib_1618,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            chip_peak_price=chip_peak,
            method="atr_fib_support_blend",
            reasoning=reasoning,
            components={
                "atr_candidates_sl": [entry_price - self.sl_atr_mult * atr_val],
                "fib_0618": fib_0618,
                "nearest_support": nearest_support,
                "chip_peak": chip_peak,
                "atr_candidates_tp1": [entry_price + self.tp1_atr_mult * atr_val],
                "nearest_resistance": nearest_resistance,
                "fib_1618": fib_1618,
                "risk": risk,
                "reward_1": reward_1,
                "rr1": rr1,
            },
        )

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_data(
        self, code: str, market: str = "cn", days: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """Fetch daily-bar DataFrame via data_provider."""
        if self.data_provider is None:
            return None
        days = days or max(self.lookback, self.chip_lookback, self.atr_period + 30)
        try:
            df = self.data_provider.get_daily_data(code, days=days)
            if df is None or df.empty:
                return None
            # Standardize: ensure datetime index ascending.
            if not isinstance(df.index, pd.DatetimeIndex):
                if "date" in df.columns:
                    df = df.set_index("date")
                else:
                    df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            # Required columns.
            required = ["high", "low", "close"]
            missing = [c for c in required if c not in df.columns]
            if missing:
                logger.warning(
                    "[SLTPCalculator] DataFrame missing columns %s for code=%s",
                    missing, code,
                )
                return None
            return df
        except Exception as exc:
            logger.warning(
                "[SLTPCalculator] _fetch_data failed for code=%s: %s",
                code, exc,
            )
            return None

    def _fetch_chip_distribution(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Estimate chip (volume) distribution from historical bars.

        Returns a dict with:
        - peak_price: price bin with the highest cumulative volume (strongest
          support/resistance by volume concentration).
        - bins: list of (low, high, volume) tuples.
        - total_volume: sum of all volumes in the lookback window.
        """
        if df is None or df.empty or "volume" not in df.columns:
            return {"peak_price": None, "bins": [], "total_volume": 0.0}

        # Use the last chip_lookback bars.
        window = df.tail(self.chip_lookback)
        if window.empty:
            return {"peak_price": None, "bins": [], "total_volume": 0.0}

        # Use typical price = (high + low + close) / 3 as the chip price.
        typical = ((window["high"] + window["low"] + window["close"]) / 3.0).values
        vols = window["volume"].astype(float).values

        if len(typical) == 0 or np.nansum(vols) == 0:
            return {"peak_price": None, "bins": [], "total_volume": 0.0}

        price_min = float(np.nanmin(typical))
        price_max = float(np.nanmax(typical))
        if price_max <= price_min:
            return {"peak_price": float(typical[-1]), "bins": [], "total_volume": float(np.nansum(vols))}

        bins = np.linspace(price_min, price_max, self.chip_bins + 1)
        bin_vols = np.zeros(self.chip_bins)
        for price, vol in zip(typical, vols):
            if np.isnan(price) or np.isnan(vol) or vol <= 0:
                continue
            idx = int((price - price_min) / (price_max - price_min) * self.chip_bins)
            idx = min(max(idx, 0), self.chip_bins - 1)
            bin_vols[idx] += vol

        peak_idx = int(np.argmax(bin_vols))
        peak_price = float((bins[peak_idx] + bins[peak_idx + 1]) / 2.0)
        bin_records = [
            (float(bins[i]), float(bins[i + 1]), float(bin_vols[i]))
            for i in range(self.chip_bins)
        ]
        return {
            "peak_price": peak_price,
            "bins": bin_records,
            "total_volume": float(np.nansum(vols)),
        }

    # ------------------------------------------------------------------
    # Component computations
    # ------------------------------------------------------------------

    def _compute_atr(self, df: pd.DataFrame) -> float:
        """Compute the latest ATR value."""
        try:
            atr_series = compute_atr(
                df, period=self.atr_period,
                high_col="high", low_col="low", close_col="close",
            )
            val = float(atr_series.iloc[-1])
            if np.isnan(val):
                # Fallback: simple average of recent true range.
                recent = df.tail(self.atr_period)
                tr = (recent["high"] - recent["low"]).mean()
                val = float(tr) if not np.isnan(tr) else 0.01
            return max(val, 0.001)  # floor to avoid zero ATR
        except Exception as exc:
            logger.warning("[SLTPCalculator] _compute_atr failed: %s", exc)
            return 0.01

    def _compute_fib(self, df: pd.DataFrame) -> Dict[float, pd.Series]:
        """Compute Fibonacci retracement levels."""
        try:
            return compute_fibonacci_retracement(
                df, lookback=self.lookback,
                high_col="high", low_col="low", close_col="close",
            )
        except Exception as exc:
            logger.warning("[SLTPCalculator] _compute_fib failed: %s", exc)
            return {}

    def _compute_fib_extension(self, df: pd.DataFrame, ratio: float = 1.618) -> Optional[float]:
        """Compute the latest Fib extension level (e.g., 1.618).

        For an up-trend: extension = swing_low + ratio * (swing_high - swing_low)
        For a down-trend: extension = swing_high - ratio * (swing_high - swing_low)
        """
        try:
            window = df.tail(self.lookback)
            if len(window) < 5:
                return None
            swing_high = float(window["high"].max())
            swing_low = float(window["low"].min())
            diff = swing_high - swing_low
            if diff <= 0:
                return None
            # Trend direction: compare first vs last close in window.
            first_close = float(window["close"].iloc[0])
            last_close = float(window["close"].iloc[-1])
            if last_close >= first_close:
                # up-trend: extension above
                return swing_low + ratio * diff
            else:
                # down-trend: extension below — not a useful TP for longs.
                return None
        except Exception as exc:
            logger.warning("[SLTPCalculator] _compute_fib_extension failed: %s", exc)
            return None

    def _compute_support_resistance(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Compute support/resistance levels."""
        try:
            return compute_support_resistance(
                df, window=self.support_window, method="fractal",
                high_col="high", low_col="low",
            )
        except Exception as exc:
            logger.warning("[SLTPCalculator] _compute_support_resistance failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _last_scalar(series: Optional[pd.Series]) -> Optional[float]:
        if series is None or series.empty:
            return None
        val = series.iloc[-1]
        if np.isnan(val):
            return None
        return float(val)

    @staticmethod
    def _nearest_below(levels: List[float], price: float) -> Optional[float]:
        """Find the largest level that is strictly below `price`."""
        below = [lv for lv in levels if lv < price]
        if not below:
            return None
        return max(below)

    @staticmethod
    def _nearest_above(levels: List[float], price: float) -> Optional[float]:
        """Find the smallest level that is strictly above `price`."""
        above = [lv for lv in levels if lv > price]
        if not above:
            return None
        return min(above)

    def _fallback_result(self, entry_price: float, reason: str) -> SLTPResult:
        """Fallback when data is insufficient — ATR-only defaults using a
        small synthetic ATR (1% of entry price).
        """
        synthetic_atr = entry_price * 0.01
        return SLTPResult(
            entry_price=entry_price,
            stop_loss=round(entry_price - self.sl_atr_mult * synthetic_atr, 2),
            take_profit_1=round(entry_price + self.tp1_atr_mult * synthetic_atr, 2),
            take_profit_2=round(entry_price + self.tp2_atr_mult * synthetic_atr, 2),
            atr=synthetic_atr,
            atr_period=self.atr_period,
            method="atr_fallback",
            reasoning=f"fallback ({reason}); synthetic ATR=1% of entry",
            components={"synthetic_atr": synthetic_atr, "reason": reason},
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_sltp_calculator(
    data_provider: Optional[Any] = None,
    **kwargs,
) -> SLTPCalculator:
    """Convenience factory (mirrors the pattern used by other engines)."""
    return SLTPCalculator(data_provider=data_provider, **kwargs)
