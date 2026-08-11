# -*- coding: utf-8 -*-
"""Unit tests for RepairEffectivenessLog (Phase 3: 策略学习与路由)."""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# RepairOutcome & RepairEffectivenessEntry
# ---------------------------------------------------------------------------


def test_repair_outcome_values():
    """验证 RepairOutcome 枚举值。"""
    from src.services.repair_effectiveness_log import RepairOutcome
    assert RepairOutcome.RESTORED.value == "restored"
    assert RepairOutcome.DEGRADED_AFTER.value == "degraded_after"
    assert RepairOutcome.NO_EFFECT.value == "no_effect"
    assert RepairOutcome.MADE_WORSE.value == "made_worse"
    assert RepairOutcome.UNKNOWN.value == "unknown"


def test_entry_to_dict():
    """验证 RepairEffectivenessEntry 序列化。"""
    from src.services.repair_effectiveness_log import RepairEffectivenessEntry

    entry = RepairEffectivenessEntry(
        entry_id="eff_001",
        repair_id="repair_001",
        action_type="restart",
        target="test_module",
        pre_repair_health={"failures": 3},
        post_repair_health={"healthy": True},
    )
    d = entry.to_dict()
    assert d["entry_id"] == "eff_001"
    assert d["action_type"] == "restart"
    assert d["outcome"] == "unknown"


def test_entry_from_dict():
    """验证 RepairEffectivenessEntry 反序列化。"""
    from src.services.repair_effectiveness_log import RepairEffectivenessEntry

    data = {
        "entry_id": "eff_002",
        "repair_id": "repair_002",
        "action_type": "rollback",
        "target": "config",
        "performed_at": "2026-08-11T12:00:00",
        "outcome": "restored",
        "time_to_next_failure_seconds": 3600,
        "observation_window_seconds": 1800,
        "metadata": {"foo": "bar"},
    }
    entry = RepairEffectivenessEntry.from_dict(data)
    assert entry.entry_id == "eff_002"
    assert entry.outcome == "restored"
    assert entry.time_to_next_failure_seconds == 3600
    assert entry.observation_window_seconds == 1800
    assert entry.metadata == {"foo": "bar"}


# ---------------------------------------------------------------------------
# RepairEffectivenessLog: record
# ---------------------------------------------------------------------------


def test_record_creates_entry():
    """验证 record() 创建效果记录。"""
    from src.services.repair_effectiveness_log import RepairEffectivenessLog

    log = RepairEffectivenessLog()
    entry = log.record(
        repair_id="r1",
        action_type="restart",
        target="mod_a",
        pre_repair_health={"failures": 3},
        post_repair_health={"healthy": True},
    )
    assert entry.entry_id.startswith("eff_")
    assert entry.action_type == "restart"
    assert entry.target == "mod_a"
    assert entry.outcome == "unknown"  # default
    assert entry.pre_repair_health == {"failures": 3}
    assert entry.post_repair_health == {"healthy": True}


def test_update_outcome():
    """验证 update_outcome() 回填效果。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    entry = log.record(
        repair_id="r1",
        action_type="restart",
        target="mod_a",
    )

    updated = log.update_outcome(
        entry.entry_id,
        RepairOutcome.RESTORED,
        time_to_next_failure_seconds=7200,
    )
    assert updated is True

    # Verify
    entries = log.get_entries_by_target("mod_a")
    assert entries[0].outcome == "restored"
    assert entries[0].time_to_next_failure_seconds == 7200


def test_update_outcome_not_found():
    """验证 update_outcome() 对不存在的 entry_id 返回 False。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    updated = log.update_outcome("nonexistent", RepairOutcome.RESTORED)
    assert updated is False


# ---------------------------------------------------------------------------
# RepairEffectivenessLog: analyze_effectiveness
# ---------------------------------------------------------------------------


def test_analyze_empty():
    """验证空日志的分析报告。"""
    from src.services.repair_effectiveness_log import RepairEffectivenessLog

    log = RepairEffectivenessLog()
    report = log.analyze_effectiveness(window_hours=24)
    assert report.total_repairs == 0
    assert len(report.recommendations) == 1
    assert "insufficient data" in report.recommendations[0].lower()


def test_analyze_with_data():
    """验证有数据时的有效性分析。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    for i in range(5):
        entry = log.record(
            repair_id=f"r{i}",
            action_type="restart",
            target="mod_a",
        )
        log.update_outcome(entry.entry_id, RepairOutcome.RESTORED)

    for i in range(5, 8):
        entry = log.record(
            repair_id=f"r{i}",
            action_type="restart",
            target="mod_b",
        )
        log.update_outcome(entry.entry_id, RepairOutcome.DEGRADED_AFTER)

    report = log.analyze_effectiveness(window_hours=24)
    assert report.total_repairs == 8

    # restart: 5 restored, 3 degraded → score = (5 - 3) / 8 = 0.25
    restart_stats = report.by_action_type["restart"]
    assert restart_stats["total"] == 8
    assert restart_stats["restored"] == 5
    assert restart_stats["degraded_after"] == 3
    assert restart_stats["effectiveness_score"] == pytest.approx(0.25)


def test_analyze_negative_effectiveness():
    """验证负有效性分数的计算。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    for i in range(2):
        entry = log.record(repair_id=f"r{i}", action_type="degrade", target="sys")
        log.update_outcome(entry.entry_id, RepairOutcome.MADE_WORSE)

    entry = log.record(repair_id="r2", action_type="degrade", target="sys")
    log.update_outcome(entry.entry_id, RepairOutcome.DEGRADED_AFTER)

    report = log.analyze_effectiveness(window_hours=24)
    stats = report.by_action_type["degrade"]
    # score = (0 - 1 - 2) / 3 = -1.0
    assert stats["effectiveness_score"] == pytest.approx(-1.0)
    assert len(report.worst_performers) >= 1
    assert "degrade" in report.worst_performers


def test_analyze_high_effectiveness_generates_good_recommendation():
    """验证高有效性分数的策略生成 GOOD 建议。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    for i in range(10):
        entry = log.record(repair_id=f"r{i}", action_type="restart", target="mod")
        log.update_outcome(entry.entry_id, RepairOutcome.RESTORED)

    report = log.analyze_effectiveness(window_hours=24)
    stats = report.by_action_type["restart"]
    assert stats["effectiveness_score"] == 1.0
    assert any("GOOD" in r or "high effectiveness" in r for r in report.recommendations)


def test_analyze_critical_negative_generates_critical_recommendation():
    """验证极负有效性分数的策略生成 CRITICAL 建议。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    for i in range(5):
        entry = log.record(repair_id=f"r{i}", action_type="bad_fix", target="sys")
        log.update_outcome(entry.entry_id, RepairOutcome.MADE_WORSE)

    report = log.analyze_effectiveness(window_hours=24)
    assert any("CRITICAL" in r for r in report.recommendations)


def test_analyze_window_filter():
    """验证 analyze_effectiveness 窗口过滤——只统计窗口内的修复。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    # Record recent entry
    entry = log.record(repair_id="r_recent", action_type="restart", target="mod")
    log.update_outcome(entry.entry_id, RepairOutcome.RESTORED)

    # Manually set an old entry outside the window
    old_entry = log.record(repair_id="r_old", action_type="restart", target="mod")
    old_entry.performed_at = datetime.now() - timedelta(hours=48)

    report = log.analyze_effectiveness(window_hours=24)
    assert report.total_repairs == 1  # only the recent one


# ---------------------------------------------------------------------------
# RepairEffectivenessLog: by_target aggregation
# ---------------------------------------------------------------------------


def test_analyze_by_target():
    """验证按 target 聚合的有效性分数。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    for i in range(3):
        entry = log.record(repair_id=f"r{i}", action_type="restart", target="mod_a")
        log.update_outcome(entry.entry_id, RepairOutcome.RESTORED)

    for i in range(3, 6):
        entry = log.record(repair_id=f"r{i}", action_type="restart", target="mod_b")
        log.update_outcome(entry.entry_id, RepairOutcome.MADE_WORSE)

    report = log.analyze_effectiveness(window_hours=24)
    assert report.by_target["mod_a"]["effectiveness_score"] == 1.0
    assert report.by_target["mod_b"]["effectiveness_score"] == -1.0


# ---------------------------------------------------------------------------
# RepairEffectivenessLog: query & stats
# ---------------------------------------------------------------------------


def test_get_entries_by_target():
    """验证按目标查询效果记录。"""
    from src.services.repair_effectiveness_log import RepairEffectivenessLog

    log = RepairEffectivenessLog()
    log.record(repair_id="r1", action_type="restart", target="mod_a")
    log.record(repair_id="r2", action_type="restart", target="mod_b")
    log.record(repair_id="r3", action_type="rollback", target="mod_a")

    entries = log.get_entries_by_target("mod_a")
    assert len(entries) == 2

    entries = log.get_entries_by_target("mod_b")
    assert len(entries) == 1


def test_get_entries_by_action():
    """验证按类型查询效果记录。"""
    from src.services.repair_effectiveness_log import RepairEffectivenessLog

    log = RepairEffectivenessLog()
    log.record(repair_id="r1", action_type="restart", target="mod_a")
    log.record(repair_id="r2", action_type="rollback", target="mod_a")

    entries = log.get_entries_by_action("restart")
    assert len(entries) == 1
    assert entries[0].action_type == "restart"


def test_stats():
    """验证 stats() 统计信息。"""
    from src.services.repair_effectiveness_log import (
        RepairEffectivenessLog,
        RepairOutcome,
    )

    log = RepairEffectivenessLog()
    entry = log.record(repair_id="r1", action_type="restart", target="mod")
    log.update_outcome(entry.entry_id, RepairOutcome.RESTORED)

    stats = log.stats()
    assert stats["total_entries"] == 1
    assert stats["outcome_distribution"]["restored"] == 1


# ---------------------------------------------------------------------------
# Phase 3: fault_pattern matching in graceful_degradation
# ---------------------------------------------------------------------------


def test_match_fault_pattern_exact():
    """验证精确匹配 fault_pattern。"""
    from src.services.graceful_degradation import GracefulDegradationEngine

    engine = GracefulDegradationEngine()
    features = {"pressure_level": "high", "dominant_metric": "health_check:error_rate"}
    pattern = {"pressure_level": "high"}
    assert engine._match_fault_pattern(pattern, features) is True

    pattern = {"pressure_level": "normal"}
    assert engine._match_fault_pattern(pattern, features) is False


def test_match_fault_pattern_min_max():
    """验证 min/max 运算符。"""
    from src.services.graceful_degradation import GracefulDegradationEngine

    engine = GracefulDegradationEngine()
    features = {"signal_count": 5, "max_ema_value": 3.5}

    assert engine._match_fault_pattern({"signal_count_min": 3}, features) is True
    assert engine._match_fault_pattern({"signal_count_min": 10}, features) is False
    assert engine._match_fault_pattern({"max_ema_value_max": 5.0}, features) is True
    assert engine._match_fault_pattern({"max_ema_value_max": 2.0}, features) is False


def test_match_fault_pattern_contains():
    """验证 _contains 运算符。"""
    from src.services.graceful_degradation import GracefulDegradationEngine

    engine = GracefulDegradationEngine()
    features = {"dominant_metric": "health_check:latency"}

    assert engine._match_fault_pattern(
        {"dominant_metric_contains": "latency"}, features
    ) is True
    assert engine._match_fault_pattern(
        {"dominant_metric_contains": "error"}, features
    ) is False


def test_match_fault_pattern_in():
    """验证 _in 运算符。"""
    from src.services.graceful_degradation import GracefulDegradationEngine

    engine = GracefulDegradationEngine()
    features = {"pressure_level": "elevated"}

    assert engine._match_fault_pattern(
        {"pressure_level_in": ["elevated", "high"]}, features
    ) is True
    assert engine._match_fault_pattern(
        {"pressure_level_in": ["critical"]}, features
    ) is False


def test_match_fault_pattern_missing_key():
    """验证匹配 key 在 features 中不存在时返回 False。"""
    from src.services.graceful_degradation import GracefulDegradationEngine

    engine = GracefulDegradationEngine()
    features = {"a": 1}

    assert engine._match_fault_pattern({"b": 2}, features) is False


def test_apply_rules_respects_fault_pattern():
    """验证 _apply_rules() 在 fault_pattern 不匹配时跳过规则。"""
    from src.services.graceful_degradation import (
        GracefulDegradationEngine,
        CapabilityRule,
        PressureLevel,
        HealthSignal,
    )

    engine = GracefulDegradationEngine()
    engine.reset()

    # Register a rule with fault_pattern that won't match
    engine.register_rule(CapabilityRule(
        capability_id="test_cap",
        display_name="Test Capability",
        level=PressureLevel.ELEVATED,
        action="disable",
        priority=0,
        fault_pattern={"dominant_metric_contains": "latency"},
    ))

    # Set up signals where dominant metric is error_rate (not latency)
    engine.register_signal(HealthSignal(
        source="test", metric="error_rate", value=3.5,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=5.0,
    ))
    for _ in range(3):
        engine.tick()

    # Rule should NOT have been applied because fault_pattern doesn't match
    assert engine.is_enabled("test_cap") is True


def test_apply_rules_matches_fault_pattern():
    """验证 _apply_rules() 在 fault_pattern 匹配时应用规则。"""
    from src.services.graceful_degradation import (
        GracefulDegradationEngine,
        CapabilityRule,
        PressureLevel,
        HealthSignal,
    )

    engine = GracefulDegradationEngine()
    engine.reset()

    # Register a rule with fault_pattern that WILL match
    engine.register_rule(CapabilityRule(
        capability_id="test_cap",
        display_name="Test Capability",
        level=PressureLevel.ELEVATED,
        action="disable",
        priority=0,
        fault_pattern={"dominant_metric_contains": "latency"},
    ))

    # Set up signals where dominant metric IS latency
    engine.register_signal(HealthSignal(
        source="test", metric="latency", value=5.0,
        threshold_normal=1.0, threshold_elevated=2.0, threshold_high=10.0,
    ))
    for _ in range(3):
        engine.tick()

    # Rule should have been applied
    assert engine.is_enabled("test_cap") is False


def test_news_fetch_rule_uses_fault_pattern():
    """验证默认新闻抓取规则使用了 fault_pattern。"""
    from src.services.graceful_degradation import GracefulDegradationEngine

    engine = GracefulDegradationEngine()
    rule = engine._rules.get("news_fetch")
    assert rule is not None
    assert rule.fault_pattern is not None
    assert "dominant_metric_contains" in rule.fault_pattern
    assert rule.fault_pattern["dominant_metric_contains"] == "latency"
