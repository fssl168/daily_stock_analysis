# -*- coding: utf-8 -*-
"""Deterministic rule evaluator producing buy/sell Signals.

The engine takes a RuleStrategy + a daily-bar DataFrame (at least 2 rows,
indexed by date ascending), computes the requested indicators, and checks
the entry / exit rules on the LATEST bar (with cross_up / cross_down using
the previous bar for comparison).

Output: a Signal with side=buy/sell/none, the trigger price (latest close),
a suggested quantity (derived from `params.lot_size` and the strategy's
position sizing rule), and a human-readable reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from strategies_v2.indicators import compute_indicators
from strategies_v2.schema import Rule, RuleStrategy

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """Result of evaluating a strategy on the latest bar."""

    side: str  # buy / sell / none
    code: str
    name: Optional[str]
    strategy_name: str
    rule_name: Optional[str]
    trigger_price: float
    suggested_quantity: Optional[float]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side,
            "code": self.code,
            "name": self.name,
            "strategy_name": self.strategy_name,
            "rule_name": self.rule_name,
            "trigger_price": self.trigger_price,
            "suggested_quantity": self.suggested_quantity,
            "reason": self.reason,
        }


class RuleEngine:
    """Evaluate a RuleStrategy against the latest two bars."""

    def evaluate(
        self,
        strategy: RuleStrategy,
        df: pd.DataFrame,
        code: str,
        name: Optional[str] = None,
    ) -> Signal:
        """Return a Signal describing whether to buy / sell / hold on the latest bar."""
        if df is None or len(df) < 2:
            return self._no_signal(
                strategy, code, name, reason="insufficient history (<2 bars)"
            )

        try:
            indicators = compute_indicators(df, strategy.indicators)
        except Exception as exc:
            logger.error("Indicator compute failed for %s: %s", strategy.name, exc)
            return self._no_signal(strategy, code, name, reason=f"indicator error: {exc}")

        latest_idx = df.index[-1]
        prev_idx = df.index[-2]
        latest_close = float(df["close"].iloc[-1])

        # Check exit rules first (existing positions take precedence).
        if strategy.exit_rules:
            matched = self._match_all(strategy.exit_rules, indicators, prev_idx, latest_idx)
            if matched is not None:
                return Signal(
                    side="sell",
                    code=code,
                    name=name,
                    strategy_name=strategy.name,
                    rule_name=matched,
                    trigger_price=latest_close,
                    suggested_quantity=self._suggested_quantity(strategy, latest_close, side="sell"),
                    reason=f"Exit rule '{matched}' matched for strategy {strategy.name}",
                )

        # Then check entry rules.
        if strategy.entry_rules:
            matched = self._match_all(strategy.entry_rules, indicators, prev_idx, latest_idx)
            if matched is not None:
                return Signal(
                    side="buy",
                    code=code,
                    name=name,
                    strategy_name=strategy.name,
                    rule_name=matched,
                    trigger_price=latest_close,
                    suggested_quantity=self._suggested_quantity(strategy, latest_close, side="buy"),
                    reason=f"Entry rule '{matched}' matched for strategy {strategy.name}",
                )

        return self._no_signal(strategy, code, name, reason="no rule matched")

    # ------------------------------------------------------------------
    # Rule matching
    # ------------------------------------------------------------------

    def _match_all(
        self,
        rules: list[Rule],
        indicators: Dict[str, pd.Series],
        prev_idx: Any,
        latest_idx: Any,
    ) -> Optional[str]:
        """Return the first matched rule's text repr, or None.

        All rules must match (AND semantics). We return the *first* rule's
        repr as the "rule_name" for labeling — adequate for audit logs.
        """
        if not rules:
            return None
        for rule in rules:
            if not self._match_one(rule, indicators, prev_idx, latest_idx):
                return None
        # Use the first rule's "left op right" as the label.
        first = rules[0]
        return f"{first.left} {first.op} {first.right}"

    def _match_one(
        self,
        rule: Rule,
        indicators: Dict[str, pd.Series],
        prev_idx: Any,
        latest_idx: Any,
    ) -> bool:
        left_now, left_prev = self._resolve_series(rule, "left", indicators, prev_idx, latest_idx)
        right_now, right_prev = self._resolve_series(rule, "right", indicators, prev_idx, latest_idx)

        if left_now is None or right_now is None:
            return False

        op = rule.op
        if op == ">":
            return left_now > right_now
        if op == "<":
            return left_now < right_now
        if op == ">=":
            return left_now >= right_now
        if op == "<=":
            return left_now <= right_now
        if op == "==":
            return abs(left_now - right_now) < 1e-9
        if op == "cross_up":
            # left was <= right on prev bar, > right on latest bar.
            if left_prev is None or right_prev is None:
                return False
            return left_prev <= right_prev and left_now > right_now
        if op == "cross_down":
            if left_prev is None or right_prev is None:
                return False
            return left_prev >= right_prev and left_now < right_now
        return False

    def _resolve_series(
        self,
        rule: Rule,
        side: str,
        indicators: Dict[str, pd.Series],
        prev_idx: Any,
        latest_idx: Any,
    ) -> tuple[Optional[float], Optional[float]]:
        """Resolve a rule side to (latest_value, prev_value)."""
        if side == "left":
            ref = rule.left_ref
            literal = rule.left_literal
        else:
            ref = rule.right_ref
            literal = rule.right_literal

        if ref is not None:
            series = indicators.get(ref.name)
            if series is None or series.empty:
                return None, None
            try:
                latest = series.loc[latest_idx] if latest_idx in series.index else None
                prev = series.loc[prev_idx] if prev_idx in series.index else None
            except KeyError:
                return None, None
            if pd.isna(latest):
                return None, None
            return float(latest), (float(prev) if not pd.isna(prev) else None)

        # Numeric literal — same value for both bars.
        return float(literal), float(literal)

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _suggested_quantity(
        self,
        strategy: RuleStrategy,
        price: float,
        side: str,
    ) -> Optional[float]:
        """Suggest a quantity based on lot_size param.

        Default behaviour for buys: round down to the nearest lot.
        For sells: None means "let the engine sell all available".
        """
        if side == "sell":
            return None
        lot_size = int(strategy.params.get("lot_size", 100))
        if lot_size <= 0:
            lot_size = 100
        if price <= 0:
            return float(lot_size)
        # Default: 1 lot per entry. Caller (engine) can scale this against
        # account cash / max position pct.
        return float(lot_size)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _no_signal(
        strategy: RuleStrategy,
        code: str,
        name: Optional[str],
        reason: str,
    ) -> Signal:
        return Signal(
            side="none",
            code=code,
            name=name,
            strategy_name=strategy.name,
            rule_name=None,
            trigger_price=0.0,
            suggested_quantity=None,
            reason=reason,
        )
