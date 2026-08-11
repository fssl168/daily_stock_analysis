# -*- coding: utf-8 -*-
"""Tests for src/services/module_restart.py — ModuleAutoRestarter."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple

import pytest

# Ensure src/ is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class DummyListener:
    """Simulate a thread/daemon that can be started and stopped."""

    def __init__(self):
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def stop(self):
        self._alive = False

    def restart(self) -> Tuple[bool, str]:
        self._alive = True
        return True, "restarted"

    def fail_restart(self) -> Tuple[bool, str]:
        return False, "callback failed"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def restarter():
    from src.services.module_restart import (
        ModuleAutoRestarter,
        ModuleDef,
        RestartPolicy,
    )
    return ModuleAutoRestarter()


@pytest.fixture
def dummy_listener():
    return DummyListener()


@pytest.fixture
def sample_module_def(dummy_listener):
    from src.services.module_restart import ModuleDef, RestartPolicy
    return ModuleDef(
        module_id="test_listener",
        display_name="Test Listener",
        policy=RestartPolicy.THREAD,
        restart_callback=dummy_listener.restart,
        is_alive_check=dummy_listener.is_alive,
        restart_on_consecutive_failures=2,
        cooldown_seconds=5,
        max_restarts_per_hour=10,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_module(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    assert "test_listener" in restarter.registered_modules
    st = restarter.get_module_status("test_listener")
    assert st is not None
    assert st.module_id == "test_listener"
    assert st.healthy is True


def test_register_duplicate_is_idempotent(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.register_module(sample_module_def)  # no-op on state
    assert restarter.registered_modules == ["test_listener"]


def test_unregister_module(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.unregister_module("test_listener")
    assert "test_listener" not in restarter.registered_modules
    assert restarter.get_module_status("test_listener") is None


# ---------------------------------------------------------------------------
# Health update
# ---------------------------------------------------------------------------


def test_update_health_healthy_resets_failures(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.update_health("test_listener", False, "fail")
    restarter.update_health("test_listener", False, "fail again")
    st = restarter.get_module_status("test_listener")
    assert st.consecutive_failures == 2

    restarter.update_health("test_listener", True, "recovered")
    st = restarter.get_module_status("test_listener")
    assert st.consecutive_failures == 0
    assert st.healthy is True


def test_update_health_unknown_module_noop(restarter):
    # should not raise
    restarter.update_health("nonexistent", False, "msg")


# ---------------------------------------------------------------------------
# Restart decision
# ---------------------------------------------------------------------------


def test_should_restart_below_threshold(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.update_health("test_listener", False, "fail")  # failure #1
    # threshold is 2, so 1 failure should NOT trigger restart
    assert restarter.should_restart("test_listener") is False


def test_should_restart_at_threshold(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.update_health("test_listener", False, "fail 1")
    restarter.update_health("test_listener", False, "fail 2")  # threshold=2
    assert restarter.should_restart("test_listener") is True


def test_should_restart_unknown_module(restarter):
    assert restarter.should_restart("nonexistent") is False


# ---------------------------------------------------------------------------
# Restart execution — thread policy
# ---------------------------------------------------------------------------


def test_restart_thread_success(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.update_health("test_listener", False, "fail 1")
    restarter.update_health("test_listener", False, "fail 2")

    ok, msg = restarter.restart_module("test_listener")
    assert ok is True
    assert "restarted" in msg

    st = restarter.get_module_status("test_listener")
    assert st.consecutive_failures == 0
    assert st.restart_count_total == 1


def test_restart_thread_callback_returns_failure(restarter, dummy_listener):
    from src.services.module_restart import ModuleDef, RestartPolicy
    md = ModuleDef(
        module_id="failer",
        display_name="Failer",
        policy=RestartPolicy.THREAD,
        restart_callback=dummy_listener.fail_restart,
        restart_on_consecutive_failures=2,
    )
    restarter.register_module(md)
    restarter.update_health("failer", False, "fail 1")
    restarter.update_health("failer", False, "fail 2")

    ok, msg = restarter.restart_module("failer")
    assert ok is False
    assert "callback failed" in msg


def test_restart_thread_no_callback(restarter):
    from src.services.module_restart import ModuleDef, RestartPolicy
    md = ModuleDef(
        module_id="no_cb",
        display_name="No Callback",
        policy=RestartPolicy.THREAD,
        restart_callback=None,
        restart_on_consecutive_failures=1,
    )
    restarter.register_module(md)
    restarter.update_health("no_cb", False, "fail")

    ok, msg = restarter.restart_module("no_cb")
    assert ok is False


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


def test_cooldown_blocks_restart(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.update_health("test_listener", False, "fail 1")
    restarter.update_health("test_listener", False, "fail 2")

    restarter.restart_module("test_listener")  # successful restart → enters cooldown

    # simulate failure during cooldown
    restarter.update_health("test_listener", False, "fail after restart")
    restarter.update_health("test_listener", False, "fail after restart")
    assert restarter.should_restart("test_listener") is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_blocks_excessive_restarts(restarter, dummy_listener):
    from src.services.module_restart import ModuleDef, RestartPolicy
    md = ModuleDef(
        module_id="flaky",
        display_name="Flaky Module",
        policy=RestartPolicy.THREAD,
        restart_callback=dummy_listener.restart,
        restart_on_consecutive_failures=1,
        max_restarts_per_hour=3,
        cooldown_seconds=0,  # no cooldown — rate limit is the only guard
    )
    restarter.register_module(md)

    for i in range(4):
        restarter.update_health("flaky", False, f"fail {i}")
        ok, _ = restarter.restart_module("flaky")

    # After 4 restarts with max 3/hour, should be rate limited
    st = restarter.get_module_status("flaky")
    assert st.restart_count_total == 4
    assert restarter.should_restart("flaky") is False


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_check_dependencies_healthy(restarter):
    from src.services.module_restart import ModuleDef, RestartPolicy
    dep_md = ModuleDef(
        module_id="bottom",
        display_name="Bottom",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        restart_on_consecutive_failures=3,
    )
    top_md = ModuleDef(
        module_id="top",
        display_name="Top",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        depends_on=["bottom"],
        restart_on_consecutive_failures=3,
    )
    restarter.register_module(dep_md)
    restarter.register_module(top_md)

    ok, bad = restarter._check_dependencies("top")
    assert ok is True
    assert bad == []


def test_check_dependencies_unhealthy(restarter):
    from src.services.module_restart import ModuleDef, RestartPolicy
    dep_md = ModuleDef(
        module_id="bottom",
        display_name="Bottom",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        restart_on_consecutive_failures=3,
    )
    top_md = ModuleDef(
        module_id="top",
        display_name="Top",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        depends_on=["bottom"],
        restart_on_consecutive_failures=3,
    )
    restarter.register_module(dep_md)
    restarter.register_module(top_md)
    restarter.update_health("bottom", False, "dep down")

    ok, bad = restarter._check_dependencies("top")
    assert ok is False
    assert "bottom" in bad


# ---------------------------------------------------------------------------
# get_restart_summary
# ---------------------------------------------------------------------------


def test_get_restart_summary(restarter, sample_module_def):
    restarter.register_module(sample_module_def)
    restarter.update_health("test_listener", False, "fail 1")
    restarter.update_health("test_listener", False, "fail 2")
    restarter.restart_module("test_listener")

    summary = restarter.get_restart_summary(hours=24)
    assert summary["total_restarts"] >= 1
    assert summary["by_module"].get("test_listener", 0) >= 1
    assert "success_rate" in summary


# ---------------------------------------------------------------------------
# stats() interface
# ---------------------------------------------------------------------------


def test_stats(restarter):
    assert isinstance(restarter.stats(), dict)
    assert "total_restarts" in restarter.stats()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_and_load_state(restarter, sample_module_def, tmp_path):
    state_file = str(tmp_path / "restart_state.json")

    # Create restarter with persistence
    from src.services.module_restart import ModuleAutoRestarter
    r2 = ModuleAutoRestarter(state_file=state_file)
    r2.register_module(sample_module_def)
    r2.update_health("test_listener", False, "fail 1")
    r2.update_health("test_listener", False, "fail 2")
    r2.restart_module("test_listener")
    assert os.path.exists(state_file)

    # Load into new restarter
    r3 = ModuleAutoRestarter(state_file=state_file)
    # Module defs are NOT persisted (only state), so register first
    r3.register_module(sample_module_def)
    assert r3.get_module_status("test_listener").restart_count_total >= 1


# ---------------------------------------------------------------------------
# setup_module_restarter
# ---------------------------------------------------------------------------


def test_setup_module_restarter():
    from src.services.module_restart import setup_module_restarter, ModuleAutoRestarter
    alerts = []
    r = setup_module_restarter(notify_fn=lambda level, msg: alerts.append((level, msg)))
    assert isinstance(r, ModuleAutoRestarter)
    assert r.registered_modules == []
