# -*- coding: utf-8 -*-
"""Unified strategies entry point.

This re-exports the strategy engine and configuration modules under a single
namespace, providing backward-compatible API with a cleaner structure.
"""


from .engine.indicators import (
    compute_atr,
    compute_fibonacci_retracement,
    compute_indicators,
    compute_support_resistance,
    FIB_RATIOS,
    RAW_PRICE_COLUMNS,
    IndicatorSpec,
)
from .engine.rule_engine import RuleEngine, RuleStrategy, Signal
from .engine.schema import load_strategy, load_strategies_from_dir
from .engine.templates import TEMPLATES, get_template

__all__ = [
    "compute_atr"
    "compute_fibonacci_retracement"
    "compute_indicators"
    "compute_support_resistance"
    "FIB_RATIOS"
    "RAW_PRICE_COLUMNS"
    "IndicatorSpec"
    "RuleEngine"
    "RuleStrategy"
    "Signal"
    "load_strategy"
    "load_strategies_from_dir"
    "TEMPLATES"
    "get_template"
]
