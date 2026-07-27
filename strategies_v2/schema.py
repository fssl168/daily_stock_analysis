# -*- coding: utf-8 -*-
"""YAML rule schema for the programmatic strategy engine.

A rule strategy file looks like:

    name: ma_golden_cross
    display_name: 均线金叉
    description: MA5 上穿 MA10 时买入，MA5 下穿 MA10 时卖出
    indicators:
      - ma5
      - ma10
    entry_rules:
      - left: ma5
        op: cross_up
        right: ma10
    exit_rules:
      - left: ma5
        op: cross_down
        right: ma10
    params:
      lot_size: 100

`left` / `right` may be an indicator name (resolved from the computed
indicator dict) or a numeric literal. `op` is one of:
    >  <  >=  <=  ==  cross_up  cross_down

`cross_up`   : left was <= right on the previous bar and > right on the latest bar.
`cross_down` : left was >= right on the previous bar and < right on the latest bar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from strategies_v2.indicators import IndicatorSpec

logger = logging.getLogger(__name__)


VALID_OPS = {">", "<", ">=", "<=", "==", "cross_up", "cross_down"}


@dataclass
class Rule:
    """A single comparison rule of the form `left op right`."""

    left: str
    op: str
    right: str
    # Resolved values are stored separately to keep raw refs available for debugging.
    left_ref: Optional[IndicatorSpec] = None
    right_ref: Optional[IndicatorSpec] = None
    right_literal: Optional[float] = None
    left_literal: Optional[float] = None

    def __post_init__(self) -> None:
        if self.op not in VALID_OPS:
            raise ValueError(f"Invalid operator '{self.op}', must be one of {VALID_OPS}")

        # Try to parse left as indicator; if it fails, treat as numeric literal.
        self.left_ref = self._try_parse_indicator(self.left)
        if self.left_ref is None:
            self.left_literal = self._try_parse_number(self.left, "left")

        # Right side can be indicator or numeric literal.
        self.right_ref = self._try_parse_indicator(self.right)
        if self.right_ref is None:
            self.right_literal = self._try_parse_number(self.right, "right")

    @staticmethod
    def _try_parse_indicator(text: str) -> Optional[IndicatorSpec]:
        try:
            return IndicatorSpec.parse(text)
        except ValueError:
            return None

    @staticmethod
    def _try_parse_number(text: str, field_name: str) -> float:
        try:
            return float(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Rule {field_name}='{text}' is neither a known indicator nor a number"
            ) from exc

    def to_dict(self) -> Dict[str, Any]:
        return {"left": self.left, "op": self.op, "right": self.right}


@dataclass
class RuleStrategy:
    """Parsed strategy file with entry/exit rules."""

    name: str
    display_name: str
    description: str = ""
    indicators: List[IndicatorSpec] = field(default_factory=list)
    entry_rules: List[Rule] = field(default_factory=list)
    exit_rules: List[Rule] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    # Phase 3: multi-timeframe evaluation and template provenance.
    timeframes: List[str] = field(default_factory=lambda: ["1d"])
    template: Optional[str] = None

    def __post_init__(self) -> None:
        # Auto-include indicators referenced by rules so direct construction
        # (not just YAML loading) always has the data it needs.
        seen = {s.name for s in self.indicators}
        for rule in self.entry_rules + self.exit_rules:
            for ref in (rule.left_ref, rule.right_ref):
                if ref is not None and ref.name not in seen:
                    self.indicators.append(ref)
                    seen.add(ref.name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "indicators": [s.name for s in self.indicators],
            "entry_rules": [r.to_dict() for r in self.entry_rules],
            "exit_rules": [r.to_dict() for r in self.exit_rules],
            "params": dict(self.params),
            "timeframes": list(self.timeframes),
            "template": self.template,
        }


def _parse_rule(raw: Any) -> Rule:
    if not isinstance(raw, dict):
        raise ValueError(f"Rule must be a mapping, got {type(raw).__name__}")
    left = raw.get("left")
    op = raw.get("op")
    right = raw.get("right")
    if not left or not op or right is None:
        raise ValueError(
            f"Rule must contain 'left', 'op', 'right'; got {raw!r}"
        )
    return Rule(left=str(left), op=str(op), right=str(right))


def parse_strategy(data: Dict[str, Any]) -> RuleStrategy:
    """Parse a loaded YAML dict into a RuleStrategy."""
    if not isinstance(data, dict):
        raise ValueError(f"Strategy root must be a mapping, got {type(data).__name__}")

    name = data.get("name")
    if not name:
        raise ValueError("Strategy missing required field: name")
    display_name = data.get("display_name") or name

    indicators_raw = data.get("indicators") or []
    indicators: List[IndicatorSpec] = []
    for item in indicators_raw:
        indicators.append(IndicatorSpec.parse(str(item)))

    entry_raw = data.get("entry_rules") or []
    exit_raw = data.get("exit_rules") or []
    entry_rules = [_parse_rule(r) for r in entry_raw]
    exit_rules = [_parse_rule(r) for r in exit_raw]

    # Auto-include indicators referenced in rules (defensive: dedupe).
    referenced_specs: List[IndicatorSpec] = []
    for rule in entry_rules + exit_rules:
        if rule.left_ref is not None:
            referenced_specs.append(rule.left_ref)
        if rule.right_ref is not None:
            referenced_specs.append(rule.right_ref)

    seen = {s.name for s in indicators}
    for spec in referenced_specs:
        if spec.name not in seen:
            indicators.append(spec)
            seen.add(spec.name)

    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("Strategy 'params' must be a mapping")

    timeframes_raw = data.get("timeframes") or ["1d"]
    timeframes = [str(tf).strip().lower() for tf in timeframes_raw]

    return RuleStrategy(
        name=str(name),
        display_name=str(display_name),
        description=str(data.get("description") or ""),
        indicators=indicators,
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        params=params,
        timeframes=timeframes,
        template=str(data.get("template")) if data.get("template") else None,
    )


def load_strategy(path: Union[str, Path]) -> RuleStrategy:
    """Load a single strategy YAML file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Strategy file {path} did not contain a YAML mapping")
    strategy = parse_strategy(data)
    logger.info("Loaded strategy: %s from %s", strategy.name, path)
    return strategy


def load_strategies_from_dir(
    directory: Union[str, Path],
    exclude_names: Optional[set] = None,
) -> List[RuleStrategy]:
    """Load all `*.yaml` strategies from a directory (non-recursive)."""
    directory = Path(directory)
    if not directory.is_dir():
        logger.warning("Strategy directory not found: %s", directory)
        return []

    out: List[RuleStrategy] = []
    exclude = exclude_names or set()
    for yaml_path in sorted(directory.glob("*.yaml")):
        try:
            strategy = load_strategy(yaml_path)
        except Exception as exc:
            logger.error("Failed to load strategy %s: %s", yaml_path, exc)
            continue
        if strategy.name in exclude:
            continue
        out.append(strategy)
    return out
