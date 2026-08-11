# -*- coding: utf-8 -*-
"""pytest tests for SignalFusionEngine (signal fusion / conflict arbitration)."""

from __future__ import annotations

import math

import pytest

from paper_trading.signal_fusion import (
    FusedSignal,
    FusionMethod,
    SignalFusionEngine,
)
from paper_trading.strategies.engine.rule_engine import Signal


def _signal(side: str, strategy: str, code: str = "600000") -> Signal:
    return Signal(
        side=side,
        code=code,
        name=None,
        strategy_name=strategy,
        rule_name=None,
        trigger_price=10.0,
        suggested_quantity=100.0,
        reason="test",
    )


class TestMajorityVote:
    """多数投票：按信号数量判断多数方向。"""

    def test_buy_majority_wins(self):
        engine = SignalFusionEngine(method=FusionMethod.MAJORITY_VOTE)
        fused = engine.fuse(
            "600000",
            [_signal("buy", "ma"), _signal("buy", "rsi"), _signal("sell", "boll")],
        )
        assert fused is not None
        assert fused.side == "buy"
        assert fused.confidence == pytest.approx(2 / 3)
        assert fused.supporting_strategies == ["ma", "rsi"]
        assert fused.opposing_strategies == ["boll"]
        assert fused.weight == pytest.approx(2 / 3 * 0.5)
        assert fused.method == FusionMethod.MAJORITY_VOTE
        assert fused.code == "600000"

    def test_sell_majority_wins(self):
        engine = SignalFusionEngine(method=FusionMethod.MAJORITY_VOTE)
        fused = engine.fuse(
            "600000",
            [_signal("sell", "ma"), _signal("sell", "rsi"), _signal("buy", "boll")],
        )
        assert fused is not None
        assert fused.side == "sell"
        assert fused.confidence == pytest.approx(2 / 3)
        assert fused.supporting_strategies == ["ma", "rsi"]
        assert fused.opposing_strategies == ["boll"]

    def test_tie_returns_none(self):
        engine = SignalFusionEngine(method=FusionMethod.MAJORITY_VOTE)
        assert engine.fuse("600000", [_signal("buy", "a"), _signal("sell", "b")]) is None

    def test_none_side_signals_ignored(self):
        engine = SignalFusionEngine(method=FusionMethod.MAJORITY_VOTE)
        fused = engine.fuse(
            "600000", [_signal("buy", "a"), _signal("none", "b"), _signal("none", "c")]
        )
        assert fused is not None
        assert fused.side == "buy"
        assert fused.supporting_strategies == ["a"]
        assert fused.opposing_strategies == []

    def test_only_none_side_signals_return_none(self):
        engine = SignalFusionEngine(method=FusionMethod.MAJORITY_VOTE)
        assert engine.fuse("600000", [_signal("none", "a")]) is None

    def test_string_method_accepted(self):
        # FusionMethod 是 str Enum，字符串值可直接比较
        engine = SignalFusionEngine(method="majority_vote")
        fused = engine.fuse("600000", [_signal("buy", "a")])
        assert fused is not None
        assert fused.side == "buy"


class TestWeightedVote:
    """加权投票：优势方向占比 >= 0.60 才产生信号，否则 None (hold)。"""

    def test_buy_consensus_above_threshold(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        # 未设置权重 -> 每策略 1.0；2 buy vs 1 sell -> buy 占比 2/3 >= 0.60
        fused = engine.fuse(
            "600000",
            [_signal("buy", "ma"), _signal("buy", "rsi"), _signal("sell", "boll")],
        )
        assert fused is not None
        assert fused.side == "buy"
        assert fused.confidence == pytest.approx(2 / 3)
        assert fused.supporting_strategies == ["ma", "rsi"]
        assert fused.opposing_strategies == ["boll"]

    def test_sell_consensus_above_threshold(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        fused = engine.fuse(
            "600000",
            [_signal("sell", "ma"), _signal("sell", "rsi"), _signal("buy", "boll")],
        )
        assert fused is not None
        assert fused.side == "sell"
        assert fused.confidence == pytest.approx(2 / 3)
        assert fused.opposing_strategies == ["boll"]

    def test_no_consensus_returns_none(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        # 1 buy vs 1 sell -> 0.5 < 0.60 -> hold
        assert engine.fuse("600000", [_signal("buy", "a"), _signal("sell", "b")]) is None

    def test_custom_consensus_threshold(self):
        engine = SignalFusionEngine(
            method=FusionMethod.WEIGHTED_VOTE, consensus_threshold=0.8
        )
        # 2 buy vs 1 sell -> 2/3 < 0.8 -> hold
        assert (
            engine.fuse(
                "600000",
                [_signal("buy", "ma"), _signal("buy", "rsi"), _signal("sell", "boll")],
            )
            is None
        )

    def test_weights_drive_decision(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        engine._strategy_weights = {"strong": 0.9, "weak": 0.1}
        # 数量 1:1 平票，但权重 0.9 vs 0.1 -> buy 占比 0.9 >= 0.60
        fused = engine.fuse("600000", [_signal("buy", "strong"), _signal("sell", "weak")])
        assert fused is not None
        assert fused.side == "buy"
        assert fused.confidence == pytest.approx(0.9)

    def test_details_include_ratios(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        fused = engine.fuse(
            "600000",
            [_signal("buy", "a"), _signal("buy", "b"), _signal("sell", "c")],
        )
        assert fused is not None
        assert fused.details["buy_ratio"] == pytest.approx(2 / 3)
        assert fused.details["sell_ratio"] == pytest.approx(1 / 3)
        assert fused.details["threshold"] == pytest.approx(0.6)


class TestWeightNormalization:
    """update_weights_from_metrics: Sharpe -> SoftMax 归一化。"""

    def test_softmax_normalizes_to_one(self):
        engine = SignalFusionEngine()
        engine.update_weights_from_metrics({"a": 2.0, "b": 1.0, "c": 0.0})
        assert set(engine._strategy_weights) == {"a", "b", "c"}
        assert sum(engine._strategy_weights.values()) == pytest.approx(1.0)
        assert engine._strategy_weights["a"] > engine._strategy_weights["b"]
        assert engine._strategy_weights["b"] > engine._strategy_weights["c"]

    def test_expected_softmax_values(self):
        engine = SignalFusionEngine()
        engine.update_weights_from_metrics({"a": 1.0, "b": 0.0})
        exp_a = math.exp(1.0) / (math.exp(1.0) + math.exp(0.0))
        assert engine._strategy_weights["a"] == pytest.approx(exp_a)
        assert engine._strategy_weights["b"] == pytest.approx(1.0 - exp_a)

    def test_single_strategy_weight_is_one(self):
        engine = SignalFusionEngine()
        engine.update_weights_from_metrics({"only": 5.0})
        assert engine._strategy_weights["only"] == pytest.approx(1.0)

    def test_negative_and_zero_metrics(self):
        engine = SignalFusionEngine()
        engine.update_weights_from_metrics({"a": -1.0, "b": 0.0})
        assert sum(engine._strategy_weights.values()) == pytest.approx(1.0)
        assert engine._strategy_weights["b"] > engine._strategy_weights["a"]

    def test_empty_metrics_clears_weights(self):
        engine = SignalFusionEngine()
        engine.update_weights_from_metrics({"a": 1.0})
        engine.update_weights_from_metrics({})
        assert engine._strategy_weights == {}


class TestConfidenceThreshold:
    """置信度门槛：信号置信度（策略权重）>= 阈值才参与融合。"""

    def test_high_confidence_signal_passes(self):
        engine = SignalFusionEngine(
            method=FusionMethod.CONFIDENCE_THRESHOLD, consensus_threshold=0.6
        )
        engine._strategy_weights = {"strong": 0.8, "weak": 0.2}
        fused = engine.fuse("600000", [_signal("buy", "strong")])
        assert fused is not None
        assert fused.side == "buy"
        assert fused.supporting_strategies == ["strong"]
        assert fused.confidence == pytest.approx(1.0)

    def test_low_confidence_signal_suppressed(self):
        engine = SignalFusionEngine(
            method=FusionMethod.CONFIDENCE_THRESHOLD, consensus_threshold=0.6
        )
        engine._strategy_weights = {"strong": 0.8, "weak": 0.2}
        assert engine.fuse("600000", [_signal("buy", "weak")]) is None

    def test_buy_confidence_beats_sell(self):
        engine = SignalFusionEngine(
            method=FusionMethod.CONFIDENCE_THRESHOLD, consensus_threshold=0.5
        )
        engine._strategy_weights = {"a": 0.6, "b": 0.7, "c": 0.55}
        fused = engine.fuse(
            "600000",
            [_signal("buy", "a"), _signal("buy", "b"), _signal("sell", "c")],
        )
        assert fused is not None
        assert fused.side == "buy"
        assert fused.confidence == pytest.approx(1.3 / 1.85)
        assert fused.opposing_strategies == ["c"]

    def test_sell_confidence_beats_buy(self):
        engine = SignalFusionEngine(
            method=FusionMethod.CONFIDENCE_THRESHOLD, consensus_threshold=0.5
        )
        engine._strategy_weights = {"a": 0.55, "b": 0.6, "c": 0.65}
        fused = engine.fuse(
            "600000",
            [_signal("buy", "a"), _signal("sell", "b"), _signal("sell", "c")],
        )
        assert fused is not None
        assert fused.side == "sell"
        assert fused.confidence == pytest.approx(1.25 / 1.8)
        assert fused.opposing_strategies == ["a"]

    def test_confidence_tie_returns_none(self):
        engine = SignalFusionEngine(
            method=FusionMethod.CONFIDENCE_THRESHOLD, consensus_threshold=0.5
        )
        engine._strategy_weights = {"a": 0.6, "b": 0.6}
        assert engine.fuse("600000", [_signal("buy", "a"), _signal("sell", "b")]) is None

    def test_all_signals_below_threshold_returns_none(self):
        engine = SignalFusionEngine(
            method=FusionMethod.CONFIDENCE_THRESHOLD, consensus_threshold=0.6
        )
        engine._strategy_weights = {"a": 0.1, "b": 0.2}
        assert (
            engine.fuse("600000", [_signal("buy", "a"), _signal("sell", "b")]) is None
        )


class TestEnsemble:
    """集成模式：每个方向独立产生，不要求 60% 共识门槛。"""

    def test_single_buy_signal_emits(self):
        engine = SignalFusionEngine(method=FusionMethod.ENSEMBLE)
        fused = engine.fuse("600000", [_signal("buy", "ma")])
        assert fused is not None
        assert fused.side == "buy"
        assert fused.confidence == pytest.approx(1.0)

    def test_single_sell_signal_emits(self):
        engine = SignalFusionEngine(method=FusionMethod.ENSEMBLE)
        fused = engine.fuse("600000", [_signal("sell", "ma")])
        assert fused is not None
        assert fused.side == "sell"
        assert fused.confidence == pytest.approx(1.0)
        assert fused.opposing_strategies == []

    def test_no_consensus_requirement(self):
        engine = SignalFusionEngine(method=FusionMethod.ENSEMBLE)
        engine._strategy_weights = {"a": 0.45, "b": 0.55}
        # 55/45 即可胜出，无需 60% 共识
        fused = engine.fuse("600000", [_signal("buy", "a"), _signal("sell", "b")])
        assert fused is not None
        assert fused.side == "sell"
        assert fused.confidence == pytest.approx(0.55)

    def test_tie_returns_none(self):
        engine = SignalFusionEngine(method=FusionMethod.ENSEMBLE)
        assert engine.fuse("600000", [_signal("buy", "a"), _signal("sell", "b")]) is None


class TestEmptyAndEdge:
    """空输入与边界情况。"""

    @pytest.mark.parametrize("method", list(FusionMethod))
    def test_empty_signals_return_none(self, method):
        engine = SignalFusionEngine(method=method)
        assert engine.fuse("600000", []) is None
        assert engine.fuse("600000", None) is None

    @pytest.mark.parametrize("method", list(FusionMethod))
    def test_only_none_side_signals_return_none(self, method):
        engine = SignalFusionEngine(method=method)
        assert engine.fuse("600000", [_signal("none", "a")]) is None

    def test_fused_signal_details_default_empty(self):
        fs = FusedSignal(
            code="600000",
            side="buy",
            confidence=0.5,
            supporting_strategies=[],
            opposing_strategies=[],
            weight=0.25,
            method=FusionMethod.MAJORITY_VOTE,
        )
        assert fs.details == {}


class TestUnknownMethod:
    """未知融合方式的处理。"""

    def test_unknown_method_raises_on_fuse(self):
        engine = SignalFusionEngine(method="bogus")
        with pytest.raises(ValueError):
            engine.fuse("600000", [_signal("buy", "a")])

    def test_unknown_enum_value_raises(self):
        with pytest.raises(ValueError):
            FusionMethod("bogus")
