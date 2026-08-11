# -*- coding: utf-8 -*-
"""
Tests for src/services/health_check.py
Covers: HealthStatus 默认值、注册/启动/停止生命周期、连续失败阈值告警、
恢复重置计数、检查异常隔离、系统资源检查（mock psutil）、
任务队列检查、NTP 同步检查、psutil 缺失降级。
"""

import builtins
import importlib
import sys
import time
import types
from types import SimpleNamespace


import src.services.health_check as health_check
from src.utils.exchange_clock import ExchangeClock


def _make_daemon(interval=30.0):
    alerts = []
    daemon = health_check.HealthCheckDaemon(
        on_alert=lambda level, msg: alerts.append((level, msg)),
        check_interval=interval,
    )
    return daemon, alerts


def _wait_thread_alive(daemon, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        thread = daemon._thread
        if thread is not None and thread.is_alive():
            return True
        time.sleep(0.005)
    return False


class FakePsutil:
    def __init__(self, mem=50.0, cpu=30.0, disk=50.0):
        self._mem = mem
        self._cpu = cpu
        self._disk = disk

    def virtual_memory(self):
        return SimpleNamespace(percent=self._mem)

    def cpu_percent(self, interval=None):
        return self._cpu

    def disk_usage(self, path):
        return SimpleNamespace(percent=self._disk)


class FakeTaskQueue:
    def __init__(self, pending=0, stats=None, raise_on_inspect=False):
        self._pending = pending
        self._stats = stats or {}
        self._raise = raise_on_inspect

    def list_pending_tasks(self):
        if self._raise:
            raise RuntimeError("inspection failed")
        return [object() for _ in range(self._pending)]

    def get_task_stats(self):
        return dict(self._stats)


def _patch_psutil(monkeypatch, mem=50.0, cpu=30.0, disk=50.0):
    monkeypatch.setattr(health_check, "psutil", FakePsutil(mem=mem, cpu=cpu, disk=disk))


def _patch_task_queue_getter(monkeypatch, getter):
    """用假模块替换 src.services.task_queue，避免引入真实模块的重依赖。"""
    fake_module = types.ModuleType("src.services.task_queue")
    fake_module.get_task_queue = getter
    monkeypatch.setitem(sys.modules, "src.services.task_queue", fake_module)


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


class TestHealthStatus:
    def test_defaults(self):
        status = health_check.HealthStatus(component="x", healthy=True, message="ok")
        assert status.component == "x"
        assert status.healthy is True
        assert status.message == "ok"
        assert isinstance(status.last_checked, type(health_check.datetime.now()))
        assert status.metadata == {}


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestDaemonLifecycle:
    def test_register_appends_checks(self):
        daemon, _ = _make_daemon()
        daemon.register(
            lambda: health_check.HealthStatus(component="a", healthy=True, message="")
        )
        daemon.register(
            lambda: health_check.HealthStatus(component="b", healthy=True, message="")
        )
        assert len(daemon._checks) == 2

    def test_start_stop_lifecycle(self):
        daemon, _ = _make_daemon(interval=0.01)
        daemon.register(
            lambda: health_check.HealthStatus(component="ok", healthy=True, message="fine")
        )
        assert daemon._thread is None
        daemon.start()
        assert _wait_thread_alive(daemon)
        time.sleep(0.05)
        daemon.stop()
        assert daemon._thread is None

    def test_start_twice_is_noop(self):
        daemon, _ = _make_daemon(interval=30.0)
        daemon.start()
        assert _wait_thread_alive(daemon)
        first = daemon._thread
        daemon.start()
        assert daemon._thread is first
        daemon.stop()

    def test_stop_without_start_is_noop(self):
        daemon, _ = _make_daemon()
        daemon.stop()
        assert daemon._thread is None

    def test_stop_twice_is_noop(self):
        daemon, _ = _make_daemon(interval=0.01)
        daemon.start()
        daemon.stop()
        daemon.stop()
        assert daemon._thread is None

    def test_daemon_loop_alerts_over_time(self):
        daemon, alerts = _make_daemon(interval=0.01)
        daemon.register(
            lambda: health_check.HealthStatus(component="db", healthy=False, message="down")
        )
        daemon.start()
        time.sleep(0.12)
        daemon.stop()
        assert len(alerts) >= 1
        assert alerts[0][0] == "CRITICAL"
        assert "[db]" in alerts[0][1]


# ---------------------------------------------------------------------------
# 连续失败阈值告警 / 恢复重置
# ---------------------------------------------------------------------------


class TestAlertThreshold:
    def test_two_failures_no_alert(self):
        daemon, alerts = _make_daemon()
        daemon.register(
            lambda: health_check.HealthStatus(component="db", healthy=False, message="down")
        )
        daemon._run_checks()
        daemon._run_checks()
        assert alerts == []
        assert daemon._past_failures["db"] == 2

    def test_three_consecutive_failures_triggers_alert(self):
        daemon, alerts = _make_daemon()
        daemon.register(
            lambda: health_check.HealthStatus(component="db", healthy=False, message="down")
        )
        daemon._run_checks()
        daemon._run_checks()
        assert alerts == []
        daemon._run_checks()
        assert len(alerts) == 1
        assert alerts[0] == ("CRITICAL", "[db] down")
        assert daemon._past_failures["db"] == 3

    def test_alert_retriggers_on_subsequent_failures(self):
        daemon, alerts = _make_daemon()
        daemon.register(
            lambda: health_check.HealthStatus(component="db", healthy=False, message="down")
        )
        for _ in range(5):
            daemon._run_checks()
        # 第 3 次失败起，每次达到阈值都触发（连续失败 5 次 → 3 次告警）
        assert len(alerts) == 3

    def test_recovery_resets_failure_counter(self):
        daemon, alerts = _make_daemon()
        state = {"healthy": False}

        def check():
            if state["healthy"]:
                return health_check.HealthStatus(component="db", healthy=True, message="ok")
            return health_check.HealthStatus(component="db", healthy=False, message="down")

        daemon.register(check)
        daemon._run_checks()
        daemon._run_checks()
        assert alerts == []
        assert daemon._past_failures["db"] == 2

        state["healthy"] = True
        daemon._run_checks()
        assert daemon._past_failures["db"] == 0

        state["healthy"] = False
        daemon._run_checks()
        daemon._run_checks()
        assert alerts == []
        daemon._run_checks()
        assert len(alerts) == 1  # 恢复后需要重新连续失败 3 次才告警


# ---------------------------------------------------------------------------
# 检查异常隔离
# ---------------------------------------------------------------------------


class TestExceptionIsolation:
    def test_exception_in_one_check_does_not_affect_others(self):
        daemon, alerts = _make_daemon()
        calls = []

        def boom():
            raise RuntimeError("check exploded")

        def ok_check():
            calls.append("ok")
            return health_check.HealthStatus(component="ok", healthy=True, message="fine")

        def bad_check():
            calls.append("bad")
            return health_check.HealthStatus(component="bad", healthy=False, message="broken")

        daemon.register(boom)
        daemon.register(ok_check)
        daemon.register(bad_check)
        statuses = daemon._run_checks()  # 必须不抛异常
        assert len(statuses) == 2
        assert calls == ["ok", "bad"]
        assert daemon._past_failures["bad"] == 1
        assert alerts == []


# ---------------------------------------------------------------------------
# 系统资源检查（mock psutil）
# ---------------------------------------------------------------------------


class TestSystemResources:
    def test_all_ok(self, monkeypatch):
        _patch_psutil(monkeypatch)
        status = health_check.check_system_resources()
        assert status.healthy is True
        assert status.message == "OK"
        assert status.metadata == {
            "memory_pct": 50.0,
            "cpu_pct": 30.0,
            "disk_pct": 50.0,
        }

    def test_memory_over_threshold(self, monkeypatch):
        _patch_psutil(monkeypatch, mem=90.0)
        status = health_check.check_system_resources()
        assert status.healthy is False
        assert "memory=90.0%" in status.message

    def test_cpu_over_threshold(self, monkeypatch):
        _patch_psutil(monkeypatch, cpu=95.0)
        status = health_check.check_system_resources()
        assert status.healthy is False
        assert "cpu=95.0%" in status.message

    def test_disk_over_threshold(self, monkeypatch):
        _patch_psutil(monkeypatch, disk=95.0)
        status = health_check.check_system_resources()
        assert status.healthy is False
        assert "disk=95.0%" in status.message

    def test_multiple_issues_combined(self, monkeypatch):
        _patch_psutil(monkeypatch, mem=90.0, cpu=95.0, disk=95.0)
        status = health_check.check_system_resources()
        assert status.healthy is False
        assert "memory=90.0%" in status.message
        assert "cpu=95.0%" in status.message
        assert "disk=95.0%" in status.message

    def test_threshold_boundary_not_breached(self, monkeypatch):
        _patch_psutil(monkeypatch, mem=85.0, cpu=90.0, disk=90.0)
        status = health_check.check_system_resources()
        assert status.healthy is True
        assert status.message == "OK"

    def test_psutil_missing_degrades_to_healthy(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "psutil":
                raise ImportError("psutil not installed")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        reloaded = importlib.reload(health_check)
        assert reloaded.psutil is None
        status = reloaded.check_system_resources()
        assert status.component == "system_resources"
        assert status.healthy is True
        assert status.metadata == {"psutil_available": False}
        monkeypatch.undo()
        importlib.reload(health_check)
        assert health_check.psutil is not None


# ---------------------------------------------------------------------------
# 任务队列检查
# ---------------------------------------------------------------------------


class TestTaskQueue:
    def test_queue_not_initialized_healthy(self, monkeypatch):
        def _boom():
            raise RuntimeError("queue not initialized")

        _patch_task_queue_getter(monkeypatch, _boom)
        status = health_check.check_task_queue()
        assert status.component == "task_queue"
        assert status.healthy is True
        assert "not initialized" in status.message
        assert status.metadata == {"pending": 0}

    def test_queue_none_healthy(self, monkeypatch):
        _patch_task_queue_getter(monkeypatch, lambda: None)
        status = health_check.check_task_queue()
        assert status.healthy is True
        assert "not initialized" in status.message

    def test_queue_inspection_exception_healthy(self, monkeypatch):
        _patch_task_queue_getter(
            monkeypatch, lambda: FakeTaskQueue(raise_on_inspect=True)
        )
        status = health_check.check_task_queue()
        assert status.healthy is True
        assert "queue unavailable" in status.message

    def test_pending_ok(self, monkeypatch):
        _patch_task_queue_getter(
            monkeypatch,
            lambda: FakeTaskQueue(pending=5, stats={"pending": 5, "total": 10}),
        )
        status = health_check.check_task_queue()
        assert status.healthy is True
        assert status.message == "pending=5"
        assert status.metadata["pending"] == 5
        assert status.metadata["stats"]["total"] == 10

    def test_pending_at_threshold_ok(self, monkeypatch):
        _patch_task_queue_getter(monkeypatch, lambda: FakeTaskQueue(pending=20))
        status = health_check.check_task_queue()
        assert status.healthy is True

    def test_pending_over_threshold_unhealthy(self, monkeypatch):
        _patch_task_queue_getter(monkeypatch, lambda: FakeTaskQueue(pending=21))
        status = health_check.check_task_queue()
        assert status.healthy is False
        assert status.message == "pending=21"
        assert status.metadata["pending"] == 21


# ---------------------------------------------------------------------------
# NTP 同步检查
# ---------------------------------------------------------------------------


class TestNtpSync:
    def test_synced_healthy(self, monkeypatch):
        monkeypatch.setattr(ExchangeClock, "is_synced", lambda: True)
        status = health_check.check_ntp_sync()
        assert status.component == "ntp"
        assert status.healthy is True
        assert status.message == "synced"

    def test_not_synced_unhealthy(self, monkeypatch):
        monkeypatch.setattr(ExchangeClock, "is_synced", lambda: False)
        status = health_check.check_ntp_sync()
        assert status.healthy is False
        assert status.message == "NOT SYNCHRONIZED"

    def test_metadata_offset_ms(self, monkeypatch):
        monkeypatch.setattr(ExchangeClock, "is_synced", lambda: True)
        monkeypatch.setattr(ExchangeClock, "_offset_ms", -12.5)
        status = health_check.check_ntp_sync()
        assert status.metadata["offset_ms"] == -12.5


# ---------------------------------------------------------------------------
# 守护进程集成：模块级检查项注册后一起执行
# ---------------------------------------------------------------------------


class TestDaemonWithModuleChecks:
    def test_runs_module_level_checks(self, monkeypatch):
        monkeypatch.setattr(ExchangeClock, "is_synced", lambda: True)
        _patch_task_queue_getter(monkeypatch, lambda: FakeTaskQueue(pending=5))
        _patch_psutil(monkeypatch)
        daemon, alerts = _make_daemon()
        daemon.register(health_check.check_system_resources)
        daemon.register(health_check.check_task_queue)
        daemon.register(health_check.check_ntp_sync)
        statuses = daemon._run_checks()
        assert len(statuses) == 3
        assert all(status.healthy for status in statuses)
        assert alerts == []
