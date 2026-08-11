# -*- coding: utf-8 -*-
"""P0 T5: backtest framework (BacktestEngine + walk-forward optimizer).

Public exports per the dev-plan: BacktestConfig / BacktestResult /
BacktestEngine / WalkforwardConfig / WalkforwardOptimizer, plus the snapshot
and per-window result types used by the public API.
"""

from .engine import BacktestConfig, BacktestEngine, BacktestResult, DailySnapshot
from .walkforward import (
    WalkforwardConfig,
    WalkforwardOptimizer,
    WalkforwardResult,
    WalkforwardWindow,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "DailySnapshot",
    "BacktestEngine",
    "WalkforwardConfig",
    "WalkforwardOptimizer",
    "WalkforwardResult",
    "WalkforwardWindow",
]
