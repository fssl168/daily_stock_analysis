# -*- coding: utf-8 -*-
"""Unit tests for P1-A smart stop-loss / take-profit calculator."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.sltp_calculator import SLTPCalculator, build_sltp_calculator


def _make_uptrend_df(days: int = 90, base_price: float = 10.0) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    n = len(idx)
    drift = [i * 0.02 for i in range(n)]
    wave = [((i % 10) - 5) * 0.05 for i in range(n)]
    close = [base_price + drift[i] + wave[i] for i in range(n)]
    high = [c + 0.15 for c in close]
    low = [c - 0.15 for c in close]
    opn = [base_price] + close[:-1]
    df = pd.DataFrame(
        {
            "open": opn,
            "high": high,
            "low": low,
            "close": close,
            "volume": [10000 + (i % 5) * 500 for i in range(n)],
        },
        index=idx,
    )
    df.index.name = "date"
    return df


class TestSLTPCalculatorBasics:
    """Core SLTP computation invariants."""

    def test_entry_price_must_be_positive(self):
        calc = SLTPCalculator(data_provider=None)
        with pytest.raises(ValueError):
            calc.compute(code="000001", entry_price=0.0)

    def test_fallback_when_data_is_missing(self):
        calc = SLTPCalculator(data_provider=None)
        result = calc.compute(code="000001", entry_price=100.0)
        assert result.method == "atr_fallback"
        assert result.stop_loss < result.entry_price
        assert result.take_profit_1 > result.entry_price
        assert result.take_profit_2 > result.take_profit_1

    def test_compute_with_supplied_df(self):
        df = _make_uptrend_df(days=90, base_price=10.0)
        calc = SLTPCalculator(data_provider=None, lookback=60, atr_period=14)
        entry = float(df["close"].iloc[-1])
        result = calc.compute(code="000001", entry_price=entry, df=df)
        assert result.entry_price == pytest.approx(entry, abs=0.01)
        assert result.stop_loss < entry
        assert result.take_profit_1 > entry
        assert result.take_profit_2 > result.take_profit_1
        assert result.atr is not None and result.atr > 0

    def test_stop_loss_is_most_conservative(self):
        """SL should be the highest (tightest) of ATR/Fib/support candidates."""
        df = _make_uptrend_df(days=90, base_price=100.0)
        calc = SLTPCalculator(data_provider=None, lookback=60, sl_atr_mult=1.5)
        entry = float(df["close"].iloc[-1])
        result = calc.compute(code="000001", entry_price=entry, df=df)
        atr_sl = entry - 1.5 * result.atr
        # The chosen SL must be >= the pure ATR SL.
        assert result.stop_loss >= round(atr_sl, 2) - 0.01
        assert result.stop_loss < entry

    def test_take_profit_1_is_most_conservative(self):
        """TP1 should be the lowest of ATR/resistance candidates."""
        df = _make_uptrend_df(days=90, base_price=100.0)
        calc = SLTPCalculator(data_provider=None, lookback=60, tp1_atr_mult=1.5)
        entry = float(df["close"].iloc[-1])
        result = calc.compute(code="000001", entry_price=entry, df=df)
        atr_tp1 = entry + 1.5 * result.atr
        assert result.take_profit_1 <= round(atr_tp1, 2) + 0.01
        assert result.take_profit_1 > entry

    def test_risk_reward_sanity(self):
        df = _make_uptrend_df(days=90, base_price=100.0)
        calc = SLTPCalculator(data_provider=None)
        entry = float(df["close"].iloc[-1])
        result = calc.compute(code="000001", entry_price=entry, df=df)
        risk = entry - result.stop_loss
        reward = result.take_profit_1 - entry
        assert risk > 0
        assert reward > 0
        # RR1 should be >= 1.0 by design.
        assert reward / risk >= 1.0


class TestSLTPWithStubProvider:
    """SLTP calculator fetching via a stub data provider."""

    def test_factory_build_and_fetch(self, stub_data_provider):
        calc = build_sltp_calculator(data_provider=stub_data_provider)
        result = calc.compute(code="000001", entry_price=12.0)
        assert result.stop_loss < 12.0
        assert result.take_profit_1 > 12.0
        assert "000001" in stub_data_provider.calls


class TestSLTPDiagnosticFields:
    """Diagnostic fields are populated for observability."""

    def test_components_and_reasoning_present(self):
        df = _make_uptrend_df(days=90, base_price=100.0)
        calc = SLTPCalculator(data_provider=None)
        entry = float(df["close"].iloc[-1])
        result = calc.compute(code="000001", entry_price=entry, df=df)
        assert result.reasoning
        assert "components" in result.to_dict()
        assert "atr_candidates_sl" in result.components


class TestSLTPFallback:
    """Fallback behaviour on insufficient data."""

    def test_insufficient_data_returns_fallback(self):
        df = pd.DataFrame(
            {
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.0],
                "volume": [1000],
            }
        )
        calc = SLTPCalculator(data_provider=None, lookback=60, atr_period=14)
        result = calc.compute(code="000001", entry_price=10.0, df=df)
        assert result.method == "atr_fallback"
        assert result.stop_loss == pytest.approx(10.0 * 0.985, abs=0.001)
