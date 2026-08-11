# -*- coding: utf-8 -*-
"""Tests for src/services/graceful_degradation.py — GracefulDegradationEngine."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    from src.services.graceful_degradation import GracefulDegradationEngine
    return GracefulDegradationEngine()


@pytest.fixture
def engine_with_callback():
    from src.services.graceful_degradation import GracefulDegradationEngine
    calls = []

    def cb(from_level, to_level, reason):
        calls.append((from_level, to_level, reason))

    eng = GracefulDegradationEngine(on_level_change=cb)
    return eng, calls


# ---------------------------------------------------------------------------
# Singleton / empty state
# ---------------------------------------------------------------------------


def test_initial_level_is_normal(engine):
    assert engine.current_level.value == "normal"
    assert engine.disabled_capabilities == []


def test_stats_empty(engine):
    s = engine.stats()
    assert isinstance(s, dict)
    assert s["current_level"] == "normal"
    assert s["signal_count"] == 0


# ---------------------------------------------------------------------------
# Signal registration
# ---------------------------------------------------------------------------


def test_register_signal(engine):
    from src.services.graceful_degradation import HealthSignal

    sig = HealthSignal(
        source="health_check", metric="error_rate", value=0.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    )
    engine.register_signal(sig)
    assert engine.get_signal("health_check", "error_rate") is not None


def test_register_signal_updates(engine):
    from src.services.graceful_degradation import HealthSignal

    sig1 = HealthSignal(
        source="h", metric="e", value=0.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    )
    engine.register_signal(sig1)
    sig2 = HealthSignal(
        source="h", metric="e", value=2.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    )
    engine.register_signal(sig2)
    s = engine.get_signal("h", "e")
    assert s is not None
    assert s.value == 2.5


def test_clear_signals(engine):
    from src.services.graceful_degradation import HealthSignal
    sig = HealthSignal(
        source="h", metric="e", value=0.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    )
    engine.register_signal(sig)
    engine.clear_signals()
    assert engine.get_signal("h", "e") is None


# ---------------------------------------------------------------------------
# Level evaluation
# ---------------------------------------------------------------------------


def test_evaluate_level_normal(engine):
    from src.services.graceful_degradation import HealthSignal
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=0.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    assert engine.evaluate_level().value == "normal"


def test_evaluate_level_elevated(engine):
    from src.services.graceful_degradation import HealthSignal
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=1.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    assert engine.evaluate_level().value == "elevated"


def test_evaluate_level_high(engine):
    from src.services.graceful_degradation import HealthSignal
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=3.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    assert engine.evaluate_level().value == "high"


def test_evaluate_level_critical(engine):
    from src.services.graceful_degradation import HealthSignal
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=6.0,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    assert engine.evaluate_level().value == "critical"


def test_evaluate_level_worst_wins(engine):
    from src.services.graceful_degradation import HealthSignal
    engine.register_signal(HealthSignal(
        source="h", metric="a", value=0.5,  # normal
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    engine.register_signal(HealthSignal(
        source="h", metric="b", value=6.0,  # critical
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    # The critical signal should win
    assert engine.evaluate_level().value == "critical"


# ---------------------------------------------------------------------------
# EMA smoothing
# ---------------------------------------------------------------------------


def test_ema_smoothing(engine):
    from src.services.graceful_degradation import HealthSignal

    sig = HealthSignal(
        source="h", metric="e", value=5.0,  # spike at threshold boundary
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    )
    engine.register_signal(sig)

    # First evaluation: EMA=5.0, 5.0 > 5.0 is False → enters HIGH not CRITICAL
    assert engine.evaluate_level().value == "high"

    # Now send normal values — EMA should smooth them
    for _ in range(10):
        engine.register_signal(HealthSignal(
            source="h", metric="e", value=0.5,
            threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
        ))
    # After many normal inputs, EMA should drift down
    ema = engine._ema_values.get("h:e", 99)
    assert ema < 3.0  # should have decayed significantly


# ---------------------------------------------------------------------------
# Hysteresis — upgrade confirm
# ---------------------------------------------------------------------------


def test_upgrade_hysteresis(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="e", value=1.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    # First tick: detects elevated but only 1 confirm → no change
    event = engine.tick()
    assert event is None
    assert engine.current_level.value == "normal"

    # Second tick: 2 confirms → upgrade
    event = engine.tick()
    assert event is not None
    assert event.to_level.value == "elevated"


# ---------------------------------------------------------------------------
# Hysteresis — downgrade confirm (needs 5)
# ---------------------------------------------------------------------------


def test_downgrade_hysteresis(engine):
    from src.services.graceful_degradation import HealthSignal

    # Push to elevated
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=1.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "elevated"

    # Return to normal: clear old signal so EMA starts fresh at 0.5 (below all thresholds)
    engine.clear_signals()
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=0.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    # 4 ticks should not be enough to downgrade (need 5 confirms)
    for _ in range(4):
        event = engine.tick()
        assert engine.current_level.value == "elevated"

    # 5th tick: downgrade
    event = engine.tick()
    assert event is not None
    assert event.to_level.value == "normal"


# ---------------------------------------------------------------------------
# Capability disable / throttle
# ---------------------------------------------------------------------------


def test_is_enabled_normal(engine):
    assert engine.is_enabled("chip_distribution") is True
    assert engine.is_enabled("anything") is True


def test_is_enabled_after_upgrade(engine):
    from src.services.graceful_degradation import HealthSignal

    # Phase 3: news_fetch has fault_pattern={"dominant_metric_contains": "latency", "signal_count_min": 2}
    # so it only disables when latency is the dominant metric AND signal_count >= 2.
    # Register two signals both with latency in their metric name.
    engine.register_signal(HealthSignal(
        source="h1", metric="latency_check", value=3.5,  # high
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    engine.register_signal(HealthSignal(
        source="h2", metric="api_latency_ms", value=1.0,  # normal
        threshold_normal=2.0, threshold_elevated=5.0, threshold_high=10.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "high"

    # At HIGH with dominant metric containing "latency" and signal_count >= 2:
    # eastmoney_patch (no fault_pattern) and news_fetch (fault_pattern matches) are disabled
    assert engine.is_enabled("eastmoney_patch") is False
    assert engine.is_enabled("news_fetch") is False
    # fundamental_pipeline is throttled (ELEVATED rule)
    assert engine.is_enabled("fundamental_pipeline") is True  # not disabled, just throttled
    assert engine.get_throttle_ratio("fundamental_pipeline") == 0.5


def test_news_fetch_not_disabled_without_latency(engine):
    """Phase 3: news_fetch 的 fault_pattern 不匹配时不被 disable。"""
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="error_rate", value=3.5,  # HIGH — not latency
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "high"

    # eastmoney_patch should still be disabled (no fault_pattern)
    assert engine.is_enabled("eastmoney_patch") is False
    # news_fetch should NOT be disabled because dominant_metric != latency
    assert engine.is_enabled("news_fetch") is True


def test_critical_disables_core(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="e", value=6.0,  # critical
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "critical"

    assert engine.is_enabled("notification_push") is True  # throttled, not disabled
    assert engine.get_throttle_ratio("notification_push") == 0.3
    assert engine.is_enabled("multi_market_analysis") is False
    assert engine.is_enabled("non_core_data_sources") is False


# ---------------------------------------------------------------------------
# get_deferred_batch_size
# ---------------------------------------------------------------------------


def test_deferred_batch_size(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="e", value=1.5,  # elevated
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()

    bs = engine.get_deferred_batch_size("chip_distribution")
    assert bs == 20
    # Not deferred
    assert engine.get_deferred_batch_size("nonexistent") is None


# ---------------------------------------------------------------------------
# Manual lock
# ---------------------------------------------------------------------------


def test_lock_level(engine):
    from src.services.graceful_degradation import HealthSignal

    # First push to ELEVATED
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=1.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "elevated"

    # Lock at ELEVATED — prevents downgrade below this level
    engine.lock_level("elevated")
    assert engine.stats()["manual_lock"] is True

    # Signal says normal — but lock prevents downgrade
    engine.clear_signals()
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=0.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    # tick won't downgrade below lock
    for _ in range(10):
        engine.tick()
    assert engine.current_level.value != "normal"

    # Unlock — now it can downgrade
    engine.unlock()
    assert engine.stats()["manual_lock"] is False


def test_lock_allows_upgrade(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.lock_level("elevated")

    # Critical signal — lock should allow upgrade
    engine.register_signal(HealthSignal(
        source="h", metric="e", value=6.0,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "critical"


# ---------------------------------------------------------------------------
# Custom rules
# ---------------------------------------------------------------------------


def test_register_custom_rule(engine):
    from src.services.graceful_degradation import CapabilityRule

    rule = CapabilityRule(
        capability_id="custom_analysis",
        display_name="Custom Analysis",
        level="elevated",
        action="disable",
        priority=99,
    )
    engine.register_rule(rule)
    assert len(engine.get_rules()) >= 1
    assert any(r.capability_id == "custom_analysis" for r in engine.get_rules())


def test_unregister_rule(engine):
    engine.unregister_rule("chip_distribution")
    assert all(r.capability_id != "chip_distribution" for r in engine.get_rules())


# ---------------------------------------------------------------------------
# Event history
# ---------------------------------------------------------------------------


def test_event_history(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="e", value=3.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()

    events = engine.get_event_history()
    assert len(events) >= 1
    assert events[-1].to_level.value == "high"
    assert len(events[-1].capabilities_affected) > 0


# ---------------------------------------------------------------------------
# Degradation summary
# ---------------------------------------------------------------------------


def test_degradation_summary(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="e", value=6.0,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()

    summary = engine.get_degradation_summary()
    assert summary["current_level"] == "critical"
    assert summary["signal_count"] >= 1
    assert len(summary["disabled_capabilities"]) > 0
    assert len(summary["transitions"]) >= 1


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


def test_level_change_callback(engine_with_callback):
    from src.services.graceful_degradation import HealthSignal

    eng, calls = engine_with_callback
    eng.register_signal(HealthSignal(
        source="h", metric="e", value=1.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        eng.tick()

    assert len(calls) >= 1
    from_level, to_level, reason = calls[0]
    assert to_level.value == "elevated"


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset(engine):
    from src.services.graceful_degradation import HealthSignal

    engine.register_signal(HealthSignal(
        source="h", metric="e", value=6.0,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()
    assert engine.current_level.value == "critical"

    engine.reset()
    assert engine.current_level.value == "normal"
    assert engine.get_signal("h", "e") is None
    assert len(engine.get_event_history()) == 0
