# -*- coding: utf-8 -*-
"""Unit tests for T14 FeaturePipeline (paper_trading/features/)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.features import (  # noqa: E402
    FeatureConfig,
    FeaturePipeline,
    FeatureRegistry,
    ma_alignment,
    rsi,
    sma_crossover,
    volume_spike,
)


def make_daily(close_values, volume=None, start="2026-01-01") -> pd.DataFrame:
    """构造测试用日线 DataFrame（date index + close/volume 列）."""
    idx = pd.bdate_range(start, periods=len(close_values), name="date")
    df = pd.DataFrame({"close": np.asarray(close_values, dtype=float)}, index=idx)
    if volume is not None:
        df["volume"] = np.asarray(volume, dtype=float)
    return df


class TestFeatureRegistry:
    def test_builtin_features_registered(self):
        names = FeatureRegistry.registered_names()
        for name in ("sma_crossover", "rsi", "volume_spike", "ma_alignment"):
            assert name in names

    def test_get_returns_registered_fn(self):
        assert FeatureRegistry.get("sma_crossover") is sma_crossover
        assert FeatureRegistry.get("rsi") is rsi
        assert FeatureRegistry.get("volume_spike") is volume_spike
        assert FeatureRegistry.get("ma_alignment") is ma_alignment

    def test_get_missing_returns_none(self):
        assert FeatureRegistry.get("no_such_feature") is None

    def test_register_decorator_adds_and_returns_fn(self):
        def dummy(df, **params):  # noqa: ARG001
            return pd.Series([1, 2, 3])

        registered = FeatureRegistry.register("test_dummy_feature")(dummy)
        assert registered is dummy
        assert FeatureRegistry.get("test_dummy_feature") is dummy
        assert "test_dummy_feature" in FeatureRegistry.registered_names()


class TestBuiltinFeatures:
    def test_sma_crossover_detects_upswing_cross(self):
        df = make_daily([1, 2, 3, 3, 3, 4, 5])
        out = sma_crossover(df, fast=2, slow=3)
        expected = pd.Series([0, 0, 0, 0, 0, 1, 0], index=df.index, dtype=int)
        pd.testing.assert_series_equal(out, expected, check_names=False)

    def test_sma_crossover_default_params_no_nan(self):
        df = make_daily(list(range(1, 41)))
        out = sma_crossover(df)  # fast=5, slow=20
        assert out.dtype == int
        assert len(out) == len(df)
        assert out.isna().sum() == 0

    def test_rsi_known_values(self):
        df = make_daily([10, 11, 12, 11, 13])
        out = rsi(df, period=2)
        assert np.isnan(out.iloc[0])
        assert out.iloc[1] == pytest.approx(100.0)
        assert out.iloc[2] == pytest.approx(100.0)
        assert out.iloc[3] == pytest.approx(50.0)
        assert out.iloc[4] == pytest.approx(100.0 - 100.0 / 3.0)

    def test_rsi_default_period_float_dtype(self):
        df = make_daily(list(range(1, 41)))
        out = rsi(df)  # period=14
        assert out.dtype == float
        assert out.isna().sum() == 13  # 前 13 行因滚动窗口不足为 NaN

    def test_volume_spike_flags_last_bar(self):
        volumes = [100.0] * 29 + [300.0]
        df = make_daily([10.0] * 30, volume=volumes)
        out = volume_spike(df, multiplier=2.0)
        assert out.dtype == int
        assert out.iloc[:-1].eq(0).all()
        assert out.iloc[-1] == 1

    def test_volume_spike_no_spike_all_zero(self):
        df = make_daily([10.0] * 30, volume=[100.0] * 30)
        out = volume_spike(df)
        assert out.dtype == int
        assert out.eq(0).all()

    def test_ma_alignment_bullish_ramp(self):
        df = make_daily([1, 2, 3, 4, 5])
        out = ma_alignment(df, short=2, long=3)
        expected = pd.Series([0, 0, 1, 1, 1], index=df.index, dtype=int)
        pd.testing.assert_series_equal(out, expected, check_names=False)

    def test_ma_alignment_default_int_dtype(self):
        df = make_daily(list(range(1, 41)))
        out = ma_alignment(df)
        assert out.dtype == int
        assert out.isna().sum() == 0


class TestFeaturePipeline:
    @staticmethod
    def _configs():
        return [
            FeatureConfig(name="sma", category="trend", compute_fn="sma_crossover"),
            FeatureConfig(name="rsi", category="momentum", compute_fn="rsi"),
        ]

    def test_run_multiindex_code_date_x_features(self):
        df_a = make_daily(list(range(1, 41)))
        df_b = make_daily([10.0 + i * 0.5 for i in range(40)], start="2026-03-01")
        pipeline = FeaturePipeline(self._configs())
        result = pipeline.run(["600519", "000001"], {"600519": df_a, "000001": df_b})

        assert result.index.names == ["code", "date"]
        assert result.columns.tolist() == ["sma", "rsi"]
        assert set(result.index.get_level_values("code")) == {"600519", "000001"}
        assert len(result) == 40 * 2

        sub_a = result.loc["600519"]
        assert list(sub_a.index) == list(df_a.index)
        pd.testing.assert_series_equal(
            sub_a["sma"], sma_crossover(df_a), check_names=False, check_freq=False,
        )
        pd.testing.assert_series_equal(
            sub_a["rsi"], rsi(df_a), check_names=False, check_freq=False,
        )

    def test_run_skips_code_with_insufficient_lookback(self):
        df_short = make_daily([1.0] * 10)
        df_long = make_daily(list(range(1, 41)))
        pipeline = FeaturePipeline(self._configs())  # requires_lookback_days=20
        result = pipeline.run(["SHORT", "LONG"], {"SHORT": df_short, "LONG": df_long})

        assert "SHORT" not in result.index.get_level_values("code")
        assert "LONG" in result.index.get_level_values("code")
        assert pipeline.skipped == ["SHORT"]
        assert len(result) == 40

    def test_run_skips_missing_df(self):
        df_long = make_daily(list(range(1, 41)))
        pipeline = FeaturePipeline(self._configs())
        result = pipeline.run(["MISSING", "LONG"], {"LONG": df_long})

        assert "MISSING" not in result.index.get_level_values("code")
        assert "LONG" in result.index.get_level_values("code")
        assert pipeline.skipped == ["MISSING"]

    def test_run_compute_exception_fills_nan_and_continues(self):
        @FeatureRegistry.register("test_raising_feature")
        def raising_feature(df, **params):  # noqa: ARG001
            raise RuntimeError("boom")

        configs = [
            FeatureConfig(
                name="sma", category="trend", compute_fn="sma_crossover",
                requires_lookback_days=5,
            ),
            FeatureConfig(
                name="bad", category="x", compute_fn="test_raising_feature",
                requires_lookback_days=5,
            ),
        ]
        df_a = make_daily(list(range(1, 31)))
        df_b = make_daily([5.0] * 30)
        pipeline = FeaturePipeline(configs)
        result = pipeline.run(["A", "B"], {"A": df_a, "B": df_b})

        assert set(result.index.get_level_values("code")) == {"A", "B"}
        assert result["bad"].isna().all()
        assert result["sma"].isna().sum() == 0  # 其余特征仍被完整计算
        assert len(result) == 30 * 2

    def test_run_unknown_compute_fn_fills_nan(self):
        configs = [
            FeatureConfig(
                name="ghost", category="x", compute_fn="no_such_fn",
                requires_lookback_days=5,
            ),
        ]
        df = make_daily(list(range(1, 30)))
        pipeline = FeaturePipeline(configs)
        result = pipeline.run(["A"], {"A": df})

        assert result.columns.tolist() == ["ghost"]
        assert result["ghost"].isna().all()
        assert pipeline.skipped == []

    def test_run_empty_data_returns_empty_df(self):
        pipeline = FeaturePipeline(self._configs())
        result = pipeline.run([], {})

        assert result.empty
        assert result.index.names == ["code", "date"]
        assert result.columns.tolist() == ["sma", "rsi"]

    def test_run_all_codes_skipped_returns_empty_df(self):
        pipeline = FeaturePipeline(self._configs())
        df_short = make_daily([1.0] * 3)
        result = pipeline.run(["A"], {"A": df_short})

        assert result.empty
        assert result.index.names == ["code", "date"]
        assert pipeline.skipped == ["A"]

    def test_run_no_configs_returns_empty_df(self):
        pipeline = FeaturePipeline([])
        result = pipeline.run(["A"], {"A": make_daily(list(range(1, 30)))})

        assert result.empty
        assert result.columns.tolist() == []

    def test_run_skipped_resets_each_run(self):
        pipeline = FeaturePipeline(self._configs())
        df_short = make_daily([1.0] * 5)
        df_long = make_daily(list(range(1, 41)))

        pipeline.run(["SHORT"], {"SHORT": df_short})
        assert pipeline.skipped == ["SHORT"]

        result = pipeline.run(["LONG"], {"LONG": df_long})
        assert pipeline.skipped == []
        assert len(result) == 40

    def test_run_respects_custom_lookback(self):
        configs = [
            FeatureConfig(
                name="sma", category="trend", compute_fn="sma_crossover",
                requires_lookback_days=5,
            ),
        ]
        df = make_daily(list(range(1, 10)))  # 9 行 >= 5 → 不跳过
        pipeline = FeaturePipeline(configs)
        result = pipeline.run(["A"], {"A": df})

        assert "A" in result.index.get_level_values("code")
        assert pipeline.skipped == []

    def test_run_custom_params_passed_through(self):
        df = make_daily([1, 2, 3, 3, 3, 4, 5])
        configs = [
            FeatureConfig(
                name="sma_cross", category="trend", compute_fn="sma_crossover",
                params={"fast": 2, "slow": 3}, requires_lookback_days=3,
            ),
        ]
        pipeline = FeaturePipeline(configs)
        result = pipeline.run(["A"], {"A": df})

        expected = sma_crossover(df, fast=2, slow=3)
        assert result["sma_cross"].tolist() == expected.tolist()
