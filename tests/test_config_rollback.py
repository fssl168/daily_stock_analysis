# -*- coding: utf-8 -*-
"""Tests for src/services/config_rollback.py — ConfigAutoRollback."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_env():
    """Create a temporary .env file and snapshot directory."""
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text(
            "DB_HOST=localhost\nDB_PORT=5432\nAPI_KEY=abc123\n", encoding="utf-8"
        )
        snapshot_dir = Path(td) / ".snapshots"
        yield env_path, snapshot_dir


@pytest.fixture
def rollback_engine(temp_env):
    from src.services.config_rollback import ConfigAutoRollback

    env_path, snap_dir = temp_env
    return ConfigAutoRollback(env_path=env_path, snapshot_dir=snap_dir)


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------


def test_create_snapshot(rollback_engine):
    snap = rollback_engine.create_snapshot(trigger="manual")
    assert snap.trigger == "manual"
    assert len(snap.checksum) == 16
    assert snap.snapshot_id.endswith(snap.checksum)
    # Layer 1 (memory) should have the content
    assert snap.snapshot_id in rollback_engine._memory_snapshots
    # Layer 2 (file) should exist
    assert Path(snap.layer2_file).exists()


def test_create_snapshot_without_env(rollback_engine, temp_env):
    env_path, _ = temp_env
    env_path.unlink()
    snap = rollback_engine.create_snapshot(trigger="manual")
    assert snap.snapshot_id in rollback_engine._memory_snapshots
    assert rollback_engine._memory_snapshots[snap.snapshot_id] == ""


def test_list_snapshots(rollback_engine):
    snap1 = rollback_engine.create_snapshot(trigger="manual")
    time.sleep(0.01)
    snap2 = rollback_engine.create_snapshot(trigger="manual")

    snaps = rollback_engine.list_snapshots()
    assert len(snaps) >= 2
    # Most recent first
    ids = [s.snapshot_id for s in snaps]
    assert ids[0] == snap2.snapshot_id
    assert ids[1] == snap1.snapshot_id


def test_get_snapshot(rollback_engine):
    snap = rollback_engine.create_snapshot(trigger="manual")
    result = rollback_engine.get_snapshot(snap.snapshot_id)
    assert result is not None
    assert result.checksum == snap.checksum


def test_get_snapshot_not_found(rollback_engine):
    assert rollback_engine.get_snapshot("nonexistent_id") is None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def test_diff_snapshots(rollback_engine, temp_env):
    env_path, _ = temp_env
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")

    # Modify .env
    env_path.write_text(
        "DB_HOST=production\nDB_PORT=5432\nAPI_KEY=xyz789\nNEW_KEY=hello\n",
        encoding="utf-8",
    )
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    diffs = rollback_engine.diff_snapshots(snap_a.snapshot_id, snap_b.snapshot_id)
    assert "DB_HOST" in diffs
    assert diffs["DB_HOST"] == ("localhost", "production")
    assert "API_KEY" in diffs
    assert diffs["API_KEY"] == ("abc123", "xyz789")
    assert "NEW_KEY" in diffs
    assert diffs["NEW_KEY"] == (None, "hello")


def test_diff_same_snapshot(rollback_engine):
    snap = rollback_engine.create_snapshot(trigger="manual")
    diffs = rollback_engine.diff_snapshots(snap.snapshot_id, snap.snapshot_id)
    assert diffs == {}


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


def test_detect_regression_no_change(rollback_engine):
    snap = rollback_engine.create_snapshot(trigger="manual")
    signal = rollback_engine.detect_regression(
        snap.snapshot_id, snap.snapshot_id,
        health_metrics={},
    )
    assert signal is None


def test_detect_regression_error_rate(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    signal = rollback_engine.detect_regression(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "error_rate_before": 0.5,
            "error_rate_after": 1.5,  # 3x increase → triggers
        },
    )
    assert signal is not None
    assert "error_rate" in signal.indicators[0]


def test_detect_regression_module_health(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    signal = rollback_engine.detect_regression(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "modules_healthy_before": {"listener": True, "data_source": True},
            "modules_healthy_after": {"listener": True, "data_source": False},
        },
    )
    assert signal is not None
    assert signal.severity == "critical"
    assert signal.auto_rollback_eligible is False  # only 1 indicator


def test_detect_regression_critical_auto_eligible(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    signal = rollback_engine.detect_regression(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "error_rate_before": 0.5,
            "error_rate_after": 1.5,
            "modules_healthy_before": {"listener": True},
            "modules_healthy_after": {"listener": False},
        },
    )
    assert signal is not None
    assert signal.severity == "critical"
    assert signal.auto_rollback_eligible is True


def test_detect_regression_latency(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    signal = rollback_engine.detect_regression(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "latency_p95_before": 200,
            "latency_p95_after": 500,  # 2.5x → triggers
        },
    )
    assert signal is not None
    assert "latency" in str(signal.indicators[0])


def test_detect_regression_task_failure(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    signal = rollback_engine.detect_regression(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "task_failure_rate_before": 0.05,
            "task_failure_rate_after": 0.30,  # +0.25 → triggers
        },
    )
    assert signal is not None
    assert signal.severity == "warning"


def test_detect_regression_data_source(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    signal = rollback_engine.detect_regression(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "source_failure_rates_before": {"eastmoney": 0.0},
            "source_failure_rates_after": {"eastmoney": 0.25},
        },
    )
    assert signal is not None


# ---------------------------------------------------------------------------
# Rollback execution
# ---------------------------------------------------------------------------


def test_execute_rollback_restores_config(rollback_engine, temp_env):
    env_path, _ = temp_env

    original = env_path.read_text(encoding="utf-8")
    snap = rollback_engine.create_snapshot(trigger="pre_change")

    # Change the .env
    env_path.write_text("DB_HOST=new_host\n# completely different\n", encoding="utf-8")

    # Rollback
    result = rollback_engine.execute_rollback(snap.snapshot_id)
    assert result.success is True
    assert result.verified is True
    assert "DB_HOST" in result.restored_keys

    # Verify restored
    restored = env_path.read_text(encoding="utf-8")
    assert restored == original


def test_execute_rollback_nonexistent_snapshot(rollback_engine):
    result = rollback_engine.execute_rollback("does_not_exist")
    assert result.success is False
    assert "not found" in result.error.lower()


def test_rollback_from_file_layer(rollback_engine):
    """Verify rollback works from Layer 2 (file) when Layer 1 is cleared."""
    snap = rollback_engine.create_snapshot(trigger="manual")
    # Clear memory layer
    rollback_engine._memory_snapshots.clear()

    result = rollback_engine.execute_rollback(snap.snapshot_id)
    assert result.success is True
    assert result.layer_used == "file"


# ---------------------------------------------------------------------------
# Auto rollback
# ---------------------------------------------------------------------------


def test_auto_rollback_if_needed_no_regression(rollback_engine):
    snap = rollback_engine.create_snapshot(trigger="manual")
    result = rollback_engine.auto_rollback_if_needed(
        snap.snapshot_id, snap.snapshot_id, health_metrics={}
    )
    assert result is None


def test_auto_rollback_if_needed_not_eligible(rollback_engine):
    snap_a = rollback_engine.create_snapshot(trigger="pre_change")
    time.sleep(0.01)
    snap_b = rollback_engine.create_snapshot(trigger="post_change")

    # Only 1 indicator → not auto_rollback_eligible
    result = rollback_engine.auto_rollback_if_needed(
        snap_a.snapshot_id, snap_b.snapshot_id,
        health_metrics={
            "error_rate_before": 0.5,
            "error_rate_after": 1.5,
        },
    )
    assert result is None


# ---------------------------------------------------------------------------
# Pre/post change hooks
# ---------------------------------------------------------------------------


def test_pre_change_hook(rollback_engine):
    sid = rollback_engine.pre_change_hook()
    assert sid
    snap = rollback_engine.get_snapshot(sid)
    assert snap is not None
    assert snap.trigger == "pre_change"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_cleanup_old_snapshots(rollback_engine):
    # Create snapshots
    for _ in range(5):
        rollback_engine.create_snapshot(trigger="manual")
        time.sleep(0.01)

    initial = len(rollback_engine._snapshot_index)
    # With max_age_days=0, all should be cleaned, but keep_min=3
    removed = rollback_engine.cleanup_old_snapshots(max_age_days=0, keep_min=3)
    assert removed == initial - 3
    assert len(rollback_engine._snapshot_index) == 3


def test_cleanup_respects_keep_min(rollback_engine):
    for _ in range(3):
        rollback_engine.create_snapshot(trigger="manual")
        time.sleep(0.01)

    removed = rollback_engine.cleanup_old_snapshots(max_age_days=0, keep_min=5)
    assert removed == 0
    assert len(rollback_engine._snapshot_index) == 3


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats(rollback_engine):
    rollback_engine.create_snapshot(trigger="manual")
    s = rollback_engine.stats()
    assert isinstance(s, dict)
    assert s["snapshot_count"] >= 1
    assert "snapshot_dir" in s
    assert "env_path" in s


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_index_persistence(temp_env):
    from src.services.config_rollback import ConfigAutoRollback

    env_path, snap_dir = temp_env
    r1 = ConfigAutoRollback(env_path=env_path, snapshot_dir=snap_dir)
    r1.create_snapshot(trigger="scheduled")

    # New instance should load from index
    r2 = ConfigAutoRollback(env_path=env_path, snapshot_dir=snap_dir)
    assert r2.stats()["snapshot_count"] >= 1
    assert len(r2._snapshot_index) >= 1


# ---------------------------------------------------------------------------
# Rollback verification
# ---------------------------------------------------------------------------


def test_verify_rollback_valid(rollback_engine, temp_env):
    env_path, _ = temp_env
    ok, msg = rollback_engine._verify_rollback()
    assert ok is True
    assert msg == "OK"


def test_verify_rollback_empty(rollback_engine, temp_env):
    env_path, _ = temp_env
    env_path.write_text("", encoding="utf-8")
    ok, msg = rollback_engine._verify_rollback()
    assert ok is False
    assert "empty" in msg.lower()


def test_verify_rollback_bad_syntax(rollback_engine, temp_env):
    env_path, _ = temp_env
    env_path.write_text("VALID_KEY=ok\nTHIS_IS_BROKEN\n", encoding="utf-8")
    ok, msg = rollback_engine._verify_rollback()
    assert ok is False
    assert "missing '='" in msg.lower()
