# -*- coding: utf-8 -*-
"""Unit tests for Phase 3: strategy rule-engine indicators and templates.

Covers new technical indicators, strategy templates, multi-timeframe
rule evaluation, and MarketListener multi-timeframe data wiring.
Tests are deterministic and network-free.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.strategies import (
    RuleEngine,
    RuleStrategy,
    TEMPLATES,
    get_template,
)
from paper_trading.strategies.engine.indicators import (
    IndicatorSpec,
    compute_cci,
    compute_indicators,
    compute_obv,
    compute_stochastic,
    compute_vwap,
    compute_volume_ma,
    compute_williams_r,
)
from paper_trading.strategies.engine.schema import Rule
from paper_trading.market_listener import MarketListener, MarketListenerConfig


def _make_ohlcv_df(
    days: int = 60,
    start_price: float = 100.0,
    trend: str = "up",
    seed: int = 42,
) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame with a monotonic trend."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    n = len(idx)
    if trend == "up":
        close_arr = np.linspace(start_price, start_price + 20.0, n)
    elif trend == "down":
        close_arr = np.linspace(start_price + 20.0, start_price, n)
    else:
        close_arr = np.full(n, start_price)
    noise = rng.normal(0, 0.5, n)
    close_arr = close_arr + noise
    high_arr = close_arr + 0.6
    low_arr = close_arr - 0.6
    open_arr = np.concatenate([[start_price], close_arr[:-1]])
    return pd.DataFrame(
        {
            "open": open_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": np.full(n, 10000.0),
        },
        index=idx,
    )


def _make_crossover_df(days: int = 30) -> pd.DataFrame:
    """Build a DataFrame where MA5 crosses above MA10 on the last bar."""
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    n = len(idx)
    # Keep most bars flat so MA5 and MA10 are equal going into the final bar,
    # then jump the close on the last bar to force a cross_up.
    close = np.full(n, 100.0)
    close[-1] = 104.0
    high = close + 0.5
    low = close - 0.5
    open_arr = np.concatenate([[100.0], close[:-1]])
    return pd.DataFrame(
        {
            "open": open_arr,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 10000.0),
        },
        index=idx,
    )


def _make_crossdown_df(days: int = 30) -> pd.DataFrame:
    """Build a DataFrame where MA5 crosses below MA10 on the last bar."""
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    n = len(idx)
    close = np.full(n, 100.0)
    close[-1] = 96.0
    high = close + 0.5
    low = close - 0.5
    open_arr = np.concatenate([[100.0], close[:-1]])
    return pd.DataFrame(
        {
            "open": open_arr,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 10000.0),
        },
        index=idx,
    )


class TestPhase3IndicatorSpec:
    """IndicatorSpec parsing for Phase 3 indicators."""

    @pytest.mark.parametrize(
        "text, kind, period, name",
        [
            ("obv", "obv", None, "obv"),
            ("vwap", "vwap", None, "vwap"),
            ("sto", "sto", 14, "sto"),
            ("sto20", "sto", 20, "sto"),
            ("sto_k", "sto_k", 14, "sto_k"),
            ("sto_k10", "sto_k", 10, "sto_k"),
            ("sto_d", "sto_d", 3, "sto_d"),
            ("sto_d5", "sto_d", 5, "sto_d"),
            ("cci", "cci", 20, "cci20"),
            ("cci10", "cci", 10, "cci10"),
            ("wr", "wr", 14, "wr14"),
            ("wr21", "wr", 21, "wr21"),
            ("vma", "vma", 20, "vma20"),
            ("vma30", "vma", 30, "vma30"),
        ],
    )
    def test_parse_phase3_indicators(self, text, kind, period, name):
        spec = IndicatorSpec.parse(text)
        assert spec.kind == kind
        assert spec.period == period
        assert spec.name == name


class TestPhase3IndicatorCalculations:
    """Direct calculation of Phase 3 indicators."""

    def test_obv_cumulative_signed_volume(self):
        df = pd.DataFrame(
            {
                "close": [100.0, 101.0, 101.0, 99.0, 100.0],
                "volume": [100.0, 200.0, 50.0, 300.0, 150.0],
            }
        )
        obv = compute_obv(df)
        # First bar -> 0; up +200; flat +0; down -300; up +150
        expected = [0.0, 200.0, 200.0, -100.0, 50.0]
        assert obv.tolist() == pytest.approx(expected, abs=0.01)

    def test_stochastic_basic_shape(self):
        df = _make_ohlcv_df(days=40)
        stoch = compute_stochastic(df)
        assert "k" in stoch and "d" in stoch
        assert stoch["k"].iloc[:13].isna().all()
        assert not np.isnan(stoch["k"].iloc[-1])
        assert not np.isnan(stoch["d"].iloc[-1])
        # %K is bounded 0-100 by construction.
        assert stoch["k"].dropna().between(0, 100).all()

    def test_cci_requires_ohlc(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        with pytest.raises(ValueError):
            compute_cci(df)

    def test_williams_r_bounded(self):
        df = _make_ohlcv_df(days=40)
        wr = compute_williams_r(df)
        assert wr.dropna().between(-100, 0).all()

    def test_volume_ma_matches_rolling_mean(self):
        df = _make_ohlcv_df(days=40)
        vma = compute_volume_ma(df, period=20)
        expected = df["volume"].rolling(window=20, min_periods=20).mean()
        pd.testing.assert_series_equal(vma, expected)

    def test_vwap_anchored_cumulative(self):
        df = _make_ohlcv_df(days=20)
        vwap = compute_vwap(df)
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        expected = (typical * df["volume"]).cumsum() / df["volume"].cumsum()
        pd.testing.assert_series_equal(vwap, expected)

    def test_compute_indicators_dispatcher_phase3(self):
        df = _make_ohlcv_df(days=40)
        specs = [
            IndicatorSpec.parse("obv"),
            IndicatorSpec.parse("sto"),
            IndicatorSpec.parse("cci20"),
            IndicatorSpec.parse("wr14"),
            IndicatorSpec.parse("vma20"),
            IndicatorSpec.parse("vwap"),
        ]
        out = compute_indicators(df, specs)
        assert "obv" in out
        assert "sto_k" in out
        assert "sto_d" in out
        assert "cci20" in out
        assert "wr14" in out
        assert "vma20" in out
        assert "vwap" in out


class TestStrategyTemplates:
    """Pre-built strategy templates."""

    @pytest.mark.parametrize(
        "name",
        ["golden_cross", "rsi_reversal", "boll_breakout", "macd_momentum"],
    )
    def test_template_roundtrip(self, name):
        strategy = get_template(name)
        assert strategy.name == name
        assert strategy.template == name
        assert strategy.entry_rules
        assert strategy.exit_rules
        # Every template must serialize cleanly.
        data = strategy.to_dict()
        assert data["template"] == name
        assert data["timeframes"] == ["1d"]

    def test_template_registry_completeness(self):
        assert set(TEMPLATES.keys()) == {
            "golden_cross",
            "rsi_reversal",
            "boll_breakout",
            "macd_momentum",
        }


class TestMultiTimeframeEvaluation:
    """RuleEngine.evaluate_multi_timeframe semantics."""

    def test_consensus_buy_across_timeframes(self):
        strategy = RuleStrategy(
            name="ma_cross",
            display_name="MA Cross",
            indicators=[],
            entry_rules=[Rule(left="ma5", op="cross_up", right="ma10")],
            exit_rules=[],
            params={"lot_size": 100},
            timeframes=["1d", "1w"],
        )
        engine = RuleEngine()
        df = _make_crossover_df(days=30)
        signal = engine.evaluate_multi_timeframe(
            strategy, {"1d": df, "1w": df}, code="600519"
        )
        assert signal.side == "buy"
        assert "Multi-timeframe consensus" in signal.reason
        assert "1d,1w" in signal.reason

    def test_mixed_signals_return_none(self):
        strategy = RuleStrategy(
            name="ma_cross",
            display_name="MA Cross",
            indicators=[],
            entry_rules=[Rule(left="ma5", op="cross_up", right="ma10")],
            exit_rules=[Rule(left="ma5", op="cross_down", right="ma10")],
            params={"lot_size": 100},
            timeframes=["1d", "1w"],
        )
        engine = RuleEngine()
        buy_df = _make_crossover_df(days=30)
        sell_df = _make_crossdown_df(days=30)
        signal = engine.evaluate_multi_timeframe(
            strategy, {"1d": buy_df, "1w": sell_df}, code="600519"
        )
        assert signal.side == "none"
        assert "mixed signals" in signal.reason

    def test_missing_timeframe_returns_none(self):
        strategy = RuleStrategy(
            name="ma_cross",
            display_name="MA Cross",
            indicators=[],
            entry_rules=[Rule(left="ma5", op="cross_up", right="ma10")],
            exit_rules=[],
            timeframes=["1d", "1w"],
        )
        engine = RuleEngine()
        signal = engine.evaluate_multi_timeframe(
            strategy, {"1d": _make_crossover_df(days=30)}, code="600519"
        )
        assert signal.side == "none"
        assert "timeframe 1w unavailable" in signal.reason


class FakeFetcher:
    """Deterministic data fetcher for MarketListener tests."""

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def get_daily_data(self, code: str, days: int = 60) -> pd.DataFrame:
        return self.df.copy()

    def get_realtime_quote(self, code: str) -> Any:
        return types.SimpleNamespace(price=100.0)


class FakeEngine:
    """Records submitted signals without touching the database."""

    def __init__(self):
        self.signals: List[Dict[str, Any]] = []

    def submit_signal(self, account_id: int, signal: Any) -> Any:
        self.signals.append({"account_id": account_id, "signal": signal})
        return types.SimpleNamespace(status="submitted", reason="ok")

    def match_pending_orders(self, prices: Dict[str, float]) -> List[Any]:
        return []

    def check_stop_loss_take_profit(
        self, prices: Dict[str, float], account_id: int
    ) -> List[Any]:
        return []


class TestMarketListenerMultiTimeframe:
    """MarketListener fetches/resamples data and feeds the rule engine."""

    def test_get_strategy_data_resamples_weekly(self):
        df = _make_crossover_df(days=60)
        listener = MarketListener(
            engine=FakeEngine(),
            data_fetcher=FakeFetcher(df),
            strategies=[],
            config=MarketListenerConfig(account_id=1),
        )
        data = listener._get_strategy_data("600519", ["1d", "1w"])
        assert data is not None
        assert "1d" in data and "1w" in data
        assert len(data["1d"]) == len(df)
        # Weekly resampling of 60 daily bars yields >= 10 weekly bars.
        assert len(data["1w"]) >= 10
        assert "close" in data["1w"].columns

    def test_get_strategy_data_unsupported_timeframe_returns_none(self):
        df = _make_crossover_df(days=30)
        listener = MarketListener(
            engine=FakeEngine(),
            data_fetcher=FakeFetcher(df),
            strategies=[],
            config=MarketListenerConfig(account_id=1),
        )
        data = listener._get_strategy_data("600519", ["1d", "1x"])
        assert data is None

    def test_evaluate_strategies_uses_multi_timeframe(self):
        df = _make_crossover_df(days=60)
        strategy = RuleStrategy(
            name="ma_cross",
            display_name="MA Cross",
            indicators=[],
            entry_rules=[Rule(left="ma5", op="cross_up", right="ma10")],
            exit_rules=[],
            timeframes=["1d", "1w"],
        )
        engine = FakeEngine()
        listener = MarketListener(
            engine=engine,
            data_fetcher=FakeFetcher(df),
            strategies=[strategy],
            config=MarketListenerConfig(
                account_id=1,
                signal_cooldown_seconds=0,
            ),
        )
        listener._evaluate_strategies(
            ["600519"], {"600519": 100.0}, market="cn"
        )
        assert len(engine.signals) == 1
        sig = engine.signals[0]["signal"]
        assert sig.side == "buy"
        assert "Multi-timeframe consensus" in sig.reason


class TestConfigTimeframes:
    """Config parses PAPER_TRADING_STRATEGY_TIMEFRAMES."""

    def test_default_timeframes(self):
        from src.config import Config

        Config._instance = None
        cfg = Config._load_from_env()
        assert cfg.paper_trading_strategy_timeframes == ["1d"]
        Config._instance = None

    def test_env_timeframes_parsed(self, monkeypatch):
        from src.config import Config

        Config._instance = None
        monkeypatch.setenv("PAPER_TRADING_STRATEGY_TIMEFRAMES", "1d, 1W , 1m")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_strategy_timeframes == ["1d", "1w", "1m"]
        Config._instance = None
