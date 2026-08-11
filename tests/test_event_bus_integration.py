# -*- coding: utf-8 -*-
"""Integration tests for SystemEventBus ↔ L3/L4 module integration (Phase 1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# EventBus + GracefulDegradation
# ---------------------------------------------------------------------------


def test_degradation_publishes_event():
    """验证 GracefulDegradationEngine.tick() 触发降级时发布 SystemEvent。"""
    from src.services.graceful_degradation import (
        GracefulDegradationEngine,
        HealthSignal,
    )
    from src.services.event_bus import SystemEventBus, SystemEventType

    bus = SystemEventBus.instance()
    bus.reset()

    received_events = []

    @bus.on(SystemEventType.DEGRADATION_TRANSITION)
    def handler(event):
        received_events.append(event)

    engine = GracefulDegradationEngine()
    engine.register_signal(HealthSignal(
        source="health_check", metric="error_rate", value=1.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))

    # First tick: no change (need 2 confirms)
    engine.tick()
    # Second tick: should trigger upgrade from normal → elevated
    event = engine.tick()
    assert event is not None
    assert event.to_level.value == "elevated"

    # Should have published a SystemEvent
    assert len(received_events) >= 1
    sys_event = received_events[-1]
    assert sys_event.event_type == SystemEventType.DEGRADATION_TRANSITION
    assert sys_event.payload["from_level"] == "normal"
    assert sys_event.payload["to_level"] == "elevated"


def test_degradation_event_payload_correct():
    """验证发布的降级事件 payload 包含 capabilities_affected 和 trigger_signals。"""
    from src.services.graceful_degradation import (
        GracefulDegradationEngine,
        HealthSignal,
    )
    from src.services.event_bus import SystemEventBus, SystemEventType

    bus = SystemEventBus.instance()
    bus.reset()

    received_events = []

    @bus.on(SystemEventType.DEGRADATION_TRANSITION)
    def handler(event):
        received_events.append(event)

    engine = GracefulDegradationEngine()
    engine.register_signal(HealthSignal(
        source="health_check", metric="error_rate", value=3.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))

    for _ in range(3):
        engine.tick()

    assert len(received_events) >= 1
    sys_event = received_events[-1]
    assert sys_event.payload["from_level"] == "normal"
    assert sys_event.payload["to_level"] == "high"
    assert len(sys_event.payload["capabilities_affected"]) > 0
    assert len(sys_event.payload["trigger_signals"]) > 0


def test_no_degradation_no_event():
    """验证无降级时不发布事件。"""
    from src.services.graceful_degradation import GracefulDegradationEngine
    from src.services.event_bus import SystemEventBus

    bus = SystemEventBus.instance()
    bus.reset()

    engine = GracefulDegradationEngine()
    # No signals at all — should stay at NORMAL
    engine.tick()
    engine.tick()

    # No degradation events should have been published
    from src.services.event_bus import SystemEventType
    degradation_events = bus.get_recent_events(
        event_type=SystemEventType.DEGRADATION_TRANSITION
    )
    assert len(degradation_events) == 0


# ---------------------------------------------------------------------------
# EventBus + MetaCognitiveEngine
# ---------------------------------------------------------------------------


def test_meta_engine_receives_degradation_event():
    """验证 MetaCognitiveEngine.on_system_event() 处理降级事件。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)
    assert len(engine._system_observations) == 0

    event = SystemEvent(
        event_id="test_001",
        event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=EventSeverity.WARNING,
        source="graceful_degradation",
        payload={
            "from_level": "normal",
            "to_level": "elevated",
            "capabilities_affected": ["chip_distribution"],
            "trigger_signals": ["health_check:error_rate"],
        },
    )
    engine.on_system_event(event)

    assert len(engine._system_observations) == 1
    obs = engine._system_observations[0]
    assert obs["type"] == "degradation"
    assert obs["from_level"] == "normal"
    assert obs["to_level"] == "elevated"


def test_meta_engine_receives_rollback_event():
    """验证 MetaCognitiveEngine.on_system_event() 处理回滚事件。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    event = SystemEvent(
        event_id="test_002",
        event_type=SystemEventType.CONFIG_ROLLBACK_EXECUTED,
        severity=EventSeverity.WARNING,
        source="config_rollback",
        payload={
            "snapshot_id": "snap_123",
            "success": True,
            "restored_keys": ["KEY_A", "KEY_B"],
        },
    )
    engine.on_system_event(event)

    assert len(engine._system_observations) == 1
    obs = engine._system_observations[0]
    assert obs["type"] == "rollback"
    assert obs["success"] is True


def test_meta_engine_receives_module_restart_event():
    """验证 MetaCognitiveEngine.on_system_event() 处理模块重启事件。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    event = SystemEvent(
        event_id="test_003",
        event_type=SystemEventType.MODULE_RESTARTED,
        severity=EventSeverity.INFO,
        source="module_restart",
        payload={
            "module_name": "market_listener",
            "message": "restarted OK",
        },
    )
    engine.on_system_event(event)

    assert len(engine._system_observations) == 1
    obs = engine._system_observations[0]
    assert obs["type"] == "module_restart"
    assert obs["success"] is True
    assert obs["module"] == "market_listener"


def test_meta_engine_receives_restart_failure_event():
    """验证重启失败事件记录正确。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    event = SystemEvent(
        event_id="test_004",
        event_type=SystemEventType.MODULE_RESTART_FAILED,
        severity=EventSeverity.ERROR,
        source="module_restart",
        payload={
            "module_name": "data_provider",
            "message": "connection timeout",
        },
    )
    engine.on_system_event(event)

    obs = engine._system_observations[0]
    assert obs["type"] == "module_restart"
    assert obs["success"] is False


def test_get_system_observations_filters_by_type():
    """验证 get_system_observations() 按类型筛选功能。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    # Push mixed events
    engine.on_system_event(SystemEvent(
        event_id="t1", event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=EventSeverity.WARNING, source="gd",
        payload={"from_level": "normal", "to_level": "elevated",
                  "capabilities_affected": [], "trigger_signals": []},
    ))
    engine.on_system_event(SystemEvent(
        event_id="t2", event_type=SystemEventType.CONFIG_ROLLBACK_EXECUTED,
        severity=EventSeverity.WARNING, source="cr",
        payload={"snapshot_id": "s1", "success": True, "restored_keys": []},
    ))
    engine.on_system_event(SystemEvent(
        event_id="t3", event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=EventSeverity.ERROR, source="gd",
        payload={"from_level": "elevated", "to_level": "high",
                  "capabilities_affected": [], "trigger_signals": []},
    ))

    deg_obs = engine.get_system_observations(observation_type="degradation")
    assert len(deg_obs) == 2

    rb_obs = engine.get_system_observations(observation_type="rollback")
    assert len(rb_obs) == 1


def test_get_system_observations_limit():
    """验证 get_system_observations() 的 limit 参数。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    for i in range(10):
        engine.on_system_event(SystemEvent(
            event_id=f"t{i}", event_type=SystemEventType.DEGRADATION_TRANSITION,
            severity=EventSeverity.WARNING, source="gd",
            payload={"from_level": "normal", "to_level": "elevated",
                      "capabilities_affected": [], "trigger_signals": []},
        ))

    assert len(engine.get_system_observations(limit=3)) == 3
    assert len(engine.get_system_observations(limit=50)) == 10


def test_self_report_includes_observations():
    """验证 get_self_report() 包含 system_observations 字段。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    engine.on_system_event(SystemEvent(
        event_id="t1", event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=EventSeverity.WARNING, source="gd",
        payload={"from_level": "normal", "to_level": "elevated",
                  "capabilities_affected": [], "trigger_signals": []},
    ))

    report = engine.get_self_report()
    assert "system_observations_count" in report
    assert report["system_observations_count"] == 1
    assert "recent_system_observations" in report
    assert len(report["recent_system_observations"]) == 1


def test_system_observations_bounded():
    """验证 _system_observations 不会无限增长。"""
    from src.services.meta_cognitive import MetaCognitiveEngine
    from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity

    engine = MetaCognitiveEngine(auto_reflect=False)

    for i in range(250):
        engine.on_system_event(SystemEvent(
            event_id=f"t{i}", event_type=SystemEventType.DEGRADATION_TRANSITION,
            severity=EventSeverity.WARNING, source="gd",
            payload={"from_level": "normal", "to_level": "elevated",
                      "capabilities_affected": [], "trigger_signals": []},
        ))

    assert len(engine._system_observations) <= 200


# ---------------------------------------------------------------------------
# EventBus → EventBus Core (basic sanity)
# ---------------------------------------------------------------------------


def test_event_bus_wildcard_subscription():
    """验证通配订阅能收到所有类型事件。"""
    from src.services.event_bus import (
        SystemEventBus, SystemEvent, SystemEventType, EventSeverity,
    )

    bus = SystemEventBus.instance()
    bus.reset()

    all_events = []
    bus.subscribe_all(lambda e: all_events.append(e))

    bus.publish(SystemEvent(
        event_id="e1", event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=EventSeverity.WARNING, source="test",
    ))
    bus.publish(SystemEvent(
        event_id="e2", event_type=SystemEventType.MODULE_RESTARTED,
        severity=EventSeverity.INFO, source="test",
    ))

    assert len(all_events) == 2


def test_event_bus_stats():
    """验证事件总线统计接口。"""
    from src.services.event_bus import (
        SystemEventBus, SystemEvent, SystemEventType, EventSeverity,
    )

    bus = SystemEventBus.instance()
    bus.reset()

    bus.publish(SystemEvent(
        event_id="e1", event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=EventSeverity.WARNING, source="test",
    ))

    stats = bus.stats()
    assert stats["total_events"] >= 1
    assert "type_distribution" in stats


def test_event_bus_get_recent_filtered():
    """验证按类型和来源筛选最近事件。"""
    from src.services.event_bus import (
        SystemEventBus, SystemEvent, SystemEventType, EventSeverity,
    )

    bus = SystemEventBus.instance()
    bus.reset()

    for i in range(5):
        bus.publish(SystemEvent(
            event_id=f"e{i}", event_type=SystemEventType.DEGRADATION_TRANSITION,
            severity=EventSeverity.WARNING, source="gd",
        ))

    bus.publish(SystemEvent(
        event_id="e_rollback", event_type=SystemEventType.CONFIG_ROLLBACK_EXECUTED,
        severity=EventSeverity.ERROR, source="cr",
    ))

    deg_events = bus.get_recent_events(
        event_type=SystemEventType.DEGRADATION_TRANSITION, limit=10,
    )
    assert len(deg_events) == 5

    cr_events = bus.get_recent_events(source="cr", limit=10)
    assert len(cr_events) == 1
