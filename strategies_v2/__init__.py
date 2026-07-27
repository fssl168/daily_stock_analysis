# -*- coding: utf-8 -*-
"""Programmatic trading-strategy rule engine (strategies_v2).

Unlike the natural-language YAML files under `strategies/` which are read by
the LLM Agent, the rules here are deterministic and executable: each rule
is parsed into a structured comparison and evaluated against computed
indicators, producing concrete buy/sell signals for the paper-trading engine.

Modules:
- indicators: technical indicator calculators (MA, EMA, RSI, MACD, BOLL)
- schema: YAML rule schema dataclasses + parser
- rule_engine: deterministic rule evaluator producing Signal objects
"""

from strategies_v2.indicators import (
    compute_atr,
    compute_fibonacci_retracement,
    compute_indicators,
    compute_support_resistance,
    FIB_RATIOS,
    IndicatorSpec,
    RAW_PRICE_COLUMNS,
)
from strategies_v2.schema import (
    Rule,
    RuleStrategy,
    load_strategy,
    load_strategies_from_dir,
)
from strategies_v2.rule_engine import RuleEngine, Signal

__all__ = [
    "compute_indicators",
    "compute_atr",
    "compute_fibonacci_retracement",
    "compute_support_resistance",
    "FIB_RATIOS",
    "RAW_PRICE_COLUMNS",
    "IndicatorSpec",
    "Rule",
    "RuleStrategy",
    "load_strategy",
    "load_strategies_from_dir",
    "RuleEngine",
    "Signal",
]
