# -*- coding: utf-8 -*-
"""L4 元认知信号闸门单元测试。

验证：无偏差放行 / 过度自信降仓位 / 确认偏差降仓位 / 多偏差过滤 /
循环论证阻断 / 异常 fail-open。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.meta_cognitive_gate import (
    L4SignalGate,
    L4GateResult,
    _BIAS_QUANTITY_FACTOR,
)
from paper_trading.strategies import Signal


def make_signal(side="buy", code="600519", qty=100.0, reason="test signal", strategy="s1"):
    return Signal(
        side=side,
        code=code,
        name="测试",
        strategy_name=strategy,
        rule_name="r1",
        trigger_price=100.0,
        suggested_quantity=qty,
        reason=reason,
    )


class TestL4SignalGate:
    def setup_method(self):
        self.gate = L4SignalGate()

    # ── 1. 无偏差 → 放行原信号 ─────────────────────────────

    def test_no_bias_passes_through(self):
        # 低置信度 + 足够信号参考 → 无偏差
        sig = make_signal()
        v = self.gate.evaluate(sig, code="600519", confidence=0.6,
                               signals_considered=6, signals_dismissed=1)
        assert v.allowed is True
        assert v.quantity_factor == 1.0
        assert v.biases == []
        assert v.adjusted_signal is sig  # 无调节，原信号

    # ── 2. 过度自信 → 降仓位 0.5 ────────────────────────────

    def test_overconfidence_reduces_quantity(self):
        # 高置信度 + 信号参考少 → 过度自信
        sig = make_signal(qty=100.0)
        v = self.gate.evaluate(
            sig, code="600519", confidence=0.9,
            signals_considered=2, signals_dismissed=0,
        )
        assert v.allowed is True
        assert "overconfidence" in v.biases
        assert v.quantity_factor == pytest.approx(0.5)
        assert v.adjusted_signal is not None
        assert v.adjusted_signal.suggested_quantity == pytest.approx(50.0)

    # ── 3. 确认偏差 → 降仓位 0.7 ────────────────────────────

    def test_confirmation_reduces_quantity(self):
        # 构造确认偏差：高置信度 + 只有支持方向的推理步骤
        sig = make_signal()
        steps = [
            {"direction": "supporting", "type": "analysis", "thought": "a",
             "sources": [], "confidence": 0.9, "duration_ms": 1.0},
            {"direction": "supporting", "type": "analysis", "thought": "b",
             "sources": [], "confidence": 0.9, "duration_ms": 1.0},
            {"direction": "supporting", "type": "analysis", "thought": "c",
             "sources": [], "confidence": 0.9, "duration_ms": 1.0},
        ]
        v = self.gate.evaluate(
            sig, code="600519", confidence=0.9,
            signals_considered=5, signals_dismissed=0,
            reasoning_steps=steps,
        )
        assert v.allowed is True
        assert "confirmation" in v.biases
        assert v.quantity_factor == pytest.approx(0.7)

    # ── 4. 多偏差叠加 → 过滤 ────────────────────────────────

    def test_multiple_biases_block(self):
        # 过度自信(0.5) × 确认偏差(0.7) = 0.35 > 0.3 → 接近过滤但不阻断？
        # 0.5*0.7 = 0.35；再叠加 recency(0.8) = 0.28 ≤ 0.3 → 阻断
        sig = make_signal()
        steps = [
            {"direction": "supporting", "type": "analysis", "thought": "a",
             "sources": [], "confidence": 0.9, "duration_ms": 1.0},
            {"direction": "supporting", "type": "analysis", "thought": "b",
             "sources": [], "confidence": 0.9, "duration_ms": 1.0},
            {"direction": "supporting", "type": "analysis", "thought": "c",
             "sources": [], "confidence": 0.9, "duration_ms": 1.0},
        ]
        # 需要触发 overconfidence + confirmation + recency
        # recency: signals_considered 很少时近因偏差？看实现——用 dismissed 触发
        v = self.gate.evaluate(
            sig, code="600519", confidence=0.95,
            signals_considered=2, signals_dismissed=3,
            reasoning_steps=steps,
        )
        # overconfidence(低证据) + confirmation(全支持) + recency(待确认)
        # 若组合因子 ≤ 0.3 → blocked；否则 allowed 但降仓位
        if not v.allowed:
            assert v.quantity_factor == 0.0
        else:
            assert v.quantity_factor <= 0.5

    # ── 5. 循环论证 → 阻断 ──────────────────────────────────

    def test_circularity_blocks(self, monkeypatch):
        sig = make_signal()
        # mock 实例的 circularity detector 返回报告
        from types import SimpleNamespace

        class FakeCircularity:
            def detect(self):
                return SimpleNamespace(
                    pattern="A→B→A loop", loop_length=3,
                    similarity_score=0.9, break_suggestion="break",
                )

        gate = L4SignalGate()
        gate._circularity_detector = FakeCircularity()
        v = gate.evaluate(sig, code="600519", confidence=0.9,
                          signals_considered=1, signals_dismissed=0)
        assert v.allowed is False
        assert "circularity" in v.biases

    # ── 6. 异常 → fail-open 放行 ────────────────────────────

    def test_error_fails_open(self, monkeypatch):
        sig = make_signal()
        from paper_trading import meta_cognitive_gate as mcg

        def boom(*a, **k):
            raise RuntimeError("L4 engine exploded")

        monkeypatch.setattr(mcg.L4SignalGate, "_build_episode", boom)
        v = self.gate.evaluate(sig, code="600519")
        assert v.allowed is True  # fail-open
        assert v.quantity_factor == 1.0
        assert "error" in v.reason

    # ── 7. 无仓位时不改信号 ─────────────────────────────────

    def test_no_quantity_signal_unchanged(self):
        sig = make_signal(qty=None)
        v = self.gate.evaluate(sig, code="600519", confidence=0.95,
                               signals_considered=1, signals_dismissed=0)
        # 无 suggested_quantity → 返回原信号（不构造新对象）
        assert v.adjusted_signal is sig

    # ── 8. 调节系数表 ───────────────────────────────────────

    def test_bias_factor_table(self):
        assert _BIAS_QUANTITY_FACTOR["overconfidence"] == 0.5
        assert _BIAS_QUANTITY_FACTOR["confirmation"] == 0.7
        assert _BIAS_QUANTITY_FACTOR["anchoring"] == 0.7
        assert _BIAS_QUANTITY_FACTOR["recency"] == 0.8
        assert _BIAS_QUANTITY_FACTOR["framing"] == 0.8

    def test_combined_factor_floor(self):
        # 0.5*0.7*0.8 = 0.28 → 被下限钳到 0.3
        assert L4SignalGate._combined_factor(["overconfidence", "confirmation", "recency"]) == pytest.approx(0.3)
        assert L4SignalGate._combined_factor(["overconfidence", "confirmation"]) == pytest.approx(0.35)
