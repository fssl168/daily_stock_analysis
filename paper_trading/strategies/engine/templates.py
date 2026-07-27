# -*- coding: utf-8 -*-
"""Pre-built strategy templates for the rule strategy engine.

These templates are intentionally simple and deterministic. They can be used
programmatically, exported to YAML, or referenced by name in strategy configs.
"""

from __future__ import annotations

from typing import Callable, Dict

from .schema import Rule, RuleStrategy


def golden_cross_template() -> RuleStrategy:
    """MA5 / MA10 golden-cross template.

    Entry: MA5 crosses above MA10.
    Exit:  MA5 crosses below MA10.
    """
    return RuleStrategy(
        name="golden_cross",
        display_name="均线金叉",
        description="MA5 上穿 MA10 买入，MA5 下穿 MA10 卖出",
        indicators=[],
        entry_rules=[Rule(left="ma5", op="cross_up", right="ma10")],
        exit_rules=[Rule(left="ma5", op="cross_down", right="ma10")],
        params={"lot_size": 100},
        template="golden_cross",
    )


def rsi_reversal_template() -> RuleStrategy:
    """RSI14 mean-reversion template.

    Entry: RSI14 falls below 30 (oversold).
    Exit:  RSI14 rises above 70 (overbought).
    """
    return RuleStrategy(
        name="rsi_reversal",
        display_name="RSI 反转",
        description="RSI14 低于 30 买入，高于 70 卖出",
        indicators=[],
        entry_rules=[Rule(left="rsi14", op="<", right="30")],
        exit_rules=[Rule(left="rsi14", op=">", right="70")],
        params={"lot_size": 100},
        template="rsi_reversal",
    )


def boll_breakout_template() -> RuleStrategy:
    """Bollinger band breakout template.

    Entry: close crosses above the upper band.
    Exit:  close crosses below the lower band.
    """
    return RuleStrategy(
        name="boll_breakout",
        display_name="布林带突破",
        description="收盘价上穿布林上轨买入，下穿布林下轨卖出",
        indicators=[],
        entry_rules=[Rule(left="close", op="cross_up", right="boll_upper")],
        exit_rules=[Rule(left="close", op="cross_down", right="boll_lower")],
        params={"lot_size": 100},
        template="boll_breakout",
    )


def macd_momentum_template() -> RuleStrategy:
    """MACD histogram momentum template.

    Entry: MACD histogram crosses above zero.
    Exit:  MACD histogram crosses below zero.
    """
    return RuleStrategy(
        name="macd_momentum",
        display_name="MACD 动量",
        description="MACD 柱由负转正买入，由正转负卖出",
        indicators=[],
        entry_rules=[Rule(left="macd_hist", op="cross_up", right="0")],
        exit_rules=[Rule(left="macd_hist", op="cross_down", right="0")],
        params={"lot_size": 100},
        template="macd_momentum",
    )


# Registry of template factories by template id.
TEMPLATES: Dict[str, Callable[[], RuleStrategy]] = {
    "golden_cross": golden_cross_template,
    "rsi_reversal": rsi_reversal_template,
    "boll_breakout": boll_breakout_template,
    "macd_momentum": macd_momentum_template,
}


def get_template(name: str) -> RuleStrategy:
    """Return a fresh instance of a named template.

    Raises:
        ValueError: if ``name`` is not a known template.
    """
    factory = TEMPLATES.get(name)
    if factory is None:
        raise ValueError(f"Unknown strategy template: {name}")
    return factory()
