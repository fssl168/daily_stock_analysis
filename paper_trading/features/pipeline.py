# -*- coding: utf-8 -*-
"""FeaturePipeline — 特征工程管线（P2 / T14）.

离线/实时特征计算管线：通过 FeatureRegistry 按名称注册特征计算函数，
FeaturePipeline 对每个 code 的日线 DataFrame 计算所有配置特征，
返回 ``(code, date)`` MultiIndex × 特征列的 DataFrame。

实现依据: docs/architecture/realtime_quant_system_design.md §4.4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FeatureConfig:
    """单特征定义 — 声明式描述一个待计算特征."""

    name: str
    category: str  # price / volume / momentum / volatility / fundamental / sentiment
    compute_fn: str  # 计算函数名（注册在 FeatureRegistry 中）
    params: Dict = field(default_factory=dict)
    requires_lookback_days: int = 20


class FeatureRegistry:
    """特征计算函数注册表.

    ``register(name)`` 作为装饰器把计算函数 ``fn(df, **params) -> pd.Series``
    注册到表中；``get`` / ``registered_names`` 供管线按名称查找。
    """

    _registry: Dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(fn: Callable) -> Callable:
            cls._registry[name] = fn
            return fn

        return decorator

    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        return cls._registry.get(name)

    @classmethod
    def registered_names(cls) -> List[str]:
        return sorted(cls._registry.keys())


@FeatureRegistry.register("sma_crossover")
def sma_crossover(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    """快线上穿慢线：fast_ma > slow_ma 且前值 fast_ma <= slow_ma → 1（int series）."""
    fast_ma = df["close"].rolling(fast).mean()
    slow_ma = df["close"].rolling(slow).mean()
    return ((fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))).astype(int)


@FeatureRegistry.register("rsi")
def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """标准 RSI：周期内平均涨幅 / 平均跌幅转为 0-100 的动量指标."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100.0 - (100.0 / (1.0 + rs))


@FeatureRegistry.register("volume_spike")
def volume_spike(df: pd.DataFrame, multiplier: float = 2.0) -> pd.Series:
    """量能突增：当日 volume > 20 日均量 × multiplier → 1（int series）."""
    avg_vol = df["volume"].rolling(20).mean()
    return (df["volume"] > avg_vol * multiplier).astype(int)


@FeatureRegistry.register("ma_alignment")
def ma_alignment(df: pd.DataFrame, short: int = 5, long: int = 20) -> pd.Series:
    """多头排列：short_ma > long_ma → 1（布尔 → int）."""
    short_ma = df["close"].rolling(short).mean()
    long_ma = df["close"].rolling(long).mean()
    return (short_ma > long_ma).astype(int)


class FeaturePipeline:
    """离线特征工程管线.

    对每个 code 计算所有配置特征；行数不足最大 requires_lookback_days 的 code
    跳过并记录到 ``skipped``。返回 ``(code, date)`` MultiIndex × 特征列的 DataFrame。
    单特征计算异常 → 该特征列填 NaN 并继续计算其余特征/其余 code。
    """

    def __init__(self, configs: List[FeatureConfig]):
        self.configs = list(configs)
        self.registry = FeatureRegistry
        self.skipped: List[str] = []

    def run(
        self,
        codes: List[str],
        daily_data: Dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        if not self.configs:
            return self._empty_result([])

        max_lookback = max(cfg.requires_lookback_days for cfg in self.configs)
        self.skipped = []
        frames = []

        for code in codes:
            df = daily_data.get(code)
            if df is None or len(df) < max_lookback:
                self.skipped.append(code)
                continue

            feature_df = pd.DataFrame(index=df.index)
            for cfg in self.configs:
                try:
                    fn = self.registry.get(cfg.compute_fn)
                    if fn is None:
                        series = pd.Series(np.nan, index=df.index)
                    else:
                        series = fn(df, **cfg.params)
                except Exception as exc:  # noqa: BLE001 — 单特征失败不应拖垮整个管线
                    logger.warning(
                        "feature %s failed for %s: %s", cfg.name, code, exc,
                    )
                    series = pd.Series(np.nan, index=df.index)
                feature_df[cfg.name] = series

            feature_df.index = pd.MultiIndex.from_product(
                [[code], df.index], names=["code", "date"],
            )
            frames.append(feature_df)

        if not frames:
            return self._empty_result([cfg.name for cfg in self.configs])
        return pd.concat(frames)

    @staticmethod
    def _empty_result(feature_names: List[str]) -> pd.DataFrame:
        """返回列结构完整、无行的空结果 DataFrame（code, date MultiIndex）."""
        index = pd.MultiIndex.from_arrays([[], []], names=["code", "date"])
        return pd.DataFrame(columns=feature_names, index=index)
