# -*- coding: utf-8 -*-
"""Smoke test for paper trading signal integration (P1).

This test verifies the basic end-to-end flow from AI decision -> signal queue
-> market listener consumption without requiring a full system startup.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_signal_queue_push_and_pop(monkeypatch):
    """Verify AIAnalysisSignalQueue can push/pop signals."""
    from src.paper_trading_signal_queue import AIAnalysisSignal, AIAnalysisSignalQueue

    # Create queue with small size to test overflow handling
    q = AIAnalysisSignalQueue(maxsize=2)

    # Push two signals
    sig1 = AIAnalysisSignal(code="600519", side="buy", name="stock A", trigger_price=100.0, reason="test")
    sig2 = AIAnalysisSignal(code="300750", side="sell", name="stock B", trigger_price=50.0, reason="test")
    assert q.push(sig1)
    assert q.push(sig2)

    # Try pushing a third one - should cause oldest to be dropped
    sig3 = AIAnalysisSignal(code="000001", side="buy", name="stock C", trigger_price=200.0, reason="test")
    dropped = q.push(sig3)
    # The push should return True after dropping old one; verify sig1 is gone
    all_signals = q.pop_all()
    codes = {s.code for s in all_signals}
    assert "600519" not in codes  # oldest dropped
    assert "300750" in codes or "000001" in codes  # at least one remains

    # Verify pop_all consumes all
    assert len(q.pop_all()) == 0
    assert q.empty()


def test_hook_push_ai_signal_from_decision(monkeypatch):
    """Test that push_ai_signal_from_decision correctly builds and pushes signal."""
    from paper_trading.hooks import push_ai_signal_from_decision
    from src.paper_trading_signal_queue import get_signal_queue as get_q
    from src.config import Config

    # Mock config to enable AI signal source and set reasonable thresholds
    class MockConfig:
        paper_trading_enabled = True
        paper_trading_enable_ai_signal_source = True
        paper_trading_ai_signal_min_confidence = 0.5
        paper_trading_ai_signal_cooldown_seconds = 30

    def mock_get_config():
        return MockConfig()

    monkeypatch.setattr("src.config.get_config", mock_get_config)

    # Also ensure signal queue is initialized
    from paper_trading.hooks import init_paper_trading_signal_queue
    init_paper_trading_signal_queue(maxsize=10)

    # Clear any previous signals
    q = get_q()
    q.pop_all()

    # Define a simple decision object with required attributes
    class SimpleDecision:
        def __init__(self):
            self.code = "600519"
            self.side = "buy"
            self.name = "贵州茅台"
            self.trigger_price = 1500.0
            self.reason = "test decision"
            self.confidence = 0.85
            self.suggested_quantity = 100

    decision = SimpleDecision()

    # Call the hook
    push_ai_signal_from_decision(decision)

    # Verify signal was pushed
    assert not q.empty()
    signals = q.pop_all()
    assert len(signals) == 1
    pushed = signals[0]
    assert pushed.code == "600519"
    assert pushed.side == "buy"
    assert pushed.trigger_price == 1500.0
    assert pushed.confidence == 0.85


def test_hook_pulls_config(monkeypatch):
    """Test respect of min confidence threshold."""
    from paper_trading.hooks import push_ai_signal_from_decision
    from src.paper_trading_signal_queue import get_signal_queue as get_q
    from src.config import Config

    # First set low threshold, should accept
    class MockConfigLow:
        paper_trading_enabled = True
        paper_trading_enable_ai_signal_source = True
        paper_trading_ai_signal_min_confidence = 0.0
        paper_trading_ai_signal_cooldown_seconds = 30

    def mock_get_config_low():
        return MockConfigLow()

    monkeypatch.setattr("src.config.get_config", mock_get_config_low)

    from paper_trading.hooks import init_paper_trading_signal_queue
    init_paper_trading_signal_queue(maxsize=10)
    q = get_q()
    q.pop_all()

    class Decision:
        code = "600519"
        side = "sell"
        name = "test"
        trigger_price = 100.0
        reason = "low confidence test"
        confidence = 0.1  # Very low but allowed by 0.0 threshold
        suggested_quantity = None

    push_ai_signal_from_decision(Decision())
    assert not q.empty()

    # Now raise threshold to reject same decision
    class MockConfigHigh:
        paper_trading_enabled = True
        paper_trading_enable_ai_signal_source = True
        paper_trading_ai_signal_min_confidence = 0.5
        paper_trading_ai_signal_cooldown_seconds = 30

    monkeypatch.setattr("src.config.get_config", lambda: MockConfigHigh())
    q2 = get_q()
    q2.pop_all()

    push_ai_signal_from_decision(Decision())  # Same decision, now rejected
    assert q2.empty()  # No signal pushed
