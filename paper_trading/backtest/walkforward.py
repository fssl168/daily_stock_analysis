# -*- coding: utf-8 -*-
"""Walk-forward optimizer for rule strategies (T5).

Rolling train/test windows: grid-search the strategy params on each train
window, then evaluate the best params out-of-sample on the following test
window. Aggregated out-of-sample metrics expose whether performance is robust
or an artifact of parameter over-fitting.
"""

from __future__ import annotations

import copy
import itertools
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from paper_trading.strategies.engine.schema import RuleStrategy

from .engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class WalkforwardConfig:
    """Walk-forward window configuration (in trading bars)."""

    train_window_days: int = 504   # training window length
    test_window_days: int = 126    # out-of-sample test window length
    step_days: int = 63            # window slide step
    param_grid: Dict[str, List[Any]] = field(default_factory=dict)


@dataclass
class WalkforwardWindow:
    """One rolling window: train range, chosen params, out-of-sample metrics."""

    train_start: Any
    train_end: Any
    test_start: Any
    test_end: Any
    best_params: Dict[str, Any]
    sharpe: float
    total_return: float
    max_drawdown: float


@dataclass
class WalkforwardResult:
    """Aggregated walk-forward outcome (out-of-sample metrics)."""

    config: WalkforwardConfig
    windows: List[WalkforwardWindow]
    out_of_sample_sharpe: float   # mean OOS sharpe across windows
    out_of_sample_return: float   # mean OOS total return across windows
    best_params: Dict[str, Any]   # most frequently chosen params across windows
    param_stability: Dict[str, float]  # fraction of windows choosing best_params


class WalkforwardOptimizer:
    """Rolling train -> out-of-sample test optimizer.

    Dependency injection: an optional :class:`BacktestEngine` can be supplied
    so tests can substitute a mock engine.
    """

    def __init__(self, engine: Optional[BacktestEngine] = None, code: str = "000001"):
        self.engine = engine if engine is not None else BacktestEngine()
        self.code = code

    def run(
        self,
        strategy: RuleStrategy,
        data: pd.DataFrame,
        config: WalkforwardConfig,
    ) -> WalkforwardResult:
        """Run the rolling window optimization on ``data``.

        For each window: grid-search ``config.param_grid`` on the train slice
        (scored by Sharpe, ties broken by total return), then evaluate the best
        params on the following test slice (out-of-sample).
        """
        if data is None or not isinstance(data, pd.DataFrame) or data.empty:
            return self._empty_result(config)

        df = data.sort_index()
        n = len(df)
        train = max(int(config.train_window_days), 2)
        test = max(int(config.test_window_days), 2)
        step = max(int(config.step_days), 1)

        windows: List[WalkforwardWindow] = []
        i = 0
        while i + train + test <= n:
            train_df = df.iloc[i : i + train]
            test_df = df.iloc[i + train : i + train + test]
            best_params, _ = self._grid_search(strategy, train_df, config.param_grid)
            oos = self._evaluate(strategy, test_df, best_params)
            windows.append(
                WalkforwardWindow(
                    train_start=train_df.index[0],
                    train_end=train_df.index[-1],
                    test_start=test_df.index[0],
                    test_end=test_df.index[-1],
                    best_params=best_params,
                    sharpe=oos.sharpe_ratio,
                    total_return=oos.total_return,
                    max_drawdown=oos.max_drawdown,
                )
            )
            i += step

        if not windows:
            return self._empty_result(config)

        oos_sharpes = [w.sharpe for w in windows]
        oos_returns = [w.total_return for w in windows]
        best_params = self._most_common_params([w.best_params for w in windows])

        stability: Dict[str, float] = {}
        if config.param_grid:
            for key in config.param_grid:
                chosen = [w.best_params.get(key) for w in windows]
                if chosen:
                    stability[key] = chosen.count(best_params.get(key)) / len(chosen)

        return WalkforwardResult(
            config=config,
            windows=windows,
            out_of_sample_sharpe=float(np.mean(oos_sharpes)),
            out_of_sample_return=float(np.mean(oos_returns)),
            best_params=best_params,
            param_stability=stability,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _grid_search(
        self,
        strategy: RuleStrategy,
        train_df: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
    ) -> tuple[Dict[str, Any], tuple[float, float]]:
        """Grid search params on the train slice; return (best_params, best_score)."""
        if not param_grid:
            best_params = dict(strategy.params)
            return best_params, self._score(self._evaluate(strategy, train_df, best_params))

        keys = list(param_grid.keys())
        best_params: Dict[str, Any] = {}
        best_score: tuple[float, float] = (-math.inf, -math.inf)
        for combo in itertools.product(*[param_grid[k] for k in keys]):
            params = dict(zip(keys, combo))
            score = self._score(self._evaluate(strategy, train_df, params))
            if score > best_score:
                best_score = score
                best_params = params
        return best_params, best_score

    def _evaluate(self, strategy: RuleStrategy, df: pd.DataFrame, params: Dict[str, Any]) -> BacktestResult:
        """Run a backtest on ``df`` with the strategy params overridden by ``params``."""
        s = copy.deepcopy(strategy)
        merged = dict(s.params)
        merged.update(params)
        s.params = merged
        return self.engine.run([self.code], [s], {self.code: df})

    @staticmethod
    def _score(result: BacktestResult) -> tuple[float, float]:
        """Score a backtest: (sharpe, total_return), ties broken by return."""
        return (result.sharpe_ratio, result.total_return)

    @staticmethod
    def _most_common_params(param_list: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """Most frequent param combo across windows (values must be scalars)."""
        counter: Dict[tuple, int] = {}
        for params in param_list:
            key = tuple(sorted((k, v) for k, v in params.items()))
            counter[key] = counter.get(key, 0) + 1
        if not counter:
            return {}
        best_key = max(counter.items(), key=lambda kv: kv[1])[0]
        return dict(best_key)

    @staticmethod
    def _empty_result(config: WalkforwardConfig) -> WalkforwardResult:
        return WalkforwardResult(
            config=config,
            windows=[],
            out_of_sample_sharpe=0.0,
            out_of_sample_return=0.0,
            best_params={},
            param_stability={},
        )
