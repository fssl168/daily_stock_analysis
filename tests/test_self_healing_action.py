# -*- coding: utf-8 -*-
"""Unit tests for SelfHealingAction base class (Phase 2: L3 修复验证闭环)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# Test concrete SelfHealingAction subclass
# ---------------------------------------------------------------------------


class DummyRestartAction:
    """Concrete SelfHealingAction for testing — simulates a restart action."""

    from src.services.self_healing_action import SelfHealingAction

    class _Inner(SelfHealingAction):
        """Inner class because SelfHealingAction is ABC and can't be instantiated directly."""

        def __init__(self, *args, detect_side_effect=None, repair_side_effect=None,
                     verify_side_effect=None, **kwargs):
            from src.services.self_healing_action import SelfHealingAction
            # bypass __init__ to set custom params
            self._action_type = kwargs.pop("action_type", "restart")
            self._target = kwargs.pop("target", "test_module")
            super().__init__(self._action_type, self._target)
            self._detect_side_effect = detect_side_effect
            self._repair_side_effect = repair_side_effect
            self._verify_side_effect = verify_side_effect

        def _detect(self, context):
            if self._detect_side_effect is not None:
                return self._detect_side_effect(context)
            return context.get("need_repair", True)

        def _repair(self, context):
            if self._repair_side_effect is not None:
                return self._repair_side_effect(context)
            return True, "restarted OK"

        def _verify(self, context):
            if self._verify_side_effect is not None:
                return self._verify_side_effect(context)
            return True, "healthy"


def make_action(
    action_type="restart",
    target="test_module",
    escalate_chain=None,
    detect=None,
    repair=None,
    verify=None,
    on_escalate=None,
    on_complete=None,
):
    """Helper: create a concrete SelfHealingAction for testing."""
    from src.services.self_healing_action import SelfHealingAction

    class TestAction(SelfHealingAction):
        def __init__(self):
            super().__init__(
                action_type=action_type,
                target=target,
                on_escalate=on_escalate,
                on_complete=on_complete,
            )
            if escalate_chain:
                self.escalation_chain = escalate_chain

        def _detect(self, context):
            if detect:
                return detect(context)
            return context.get("need_repair", True)

        def _repair(self, context):
            if repair:
                return repair(context)
            return True, "repaired"

        def _verify(self, context):
            if verify:
                return verify(context)
            return True, "verified OK"

    return TestAction()


# ---------------------------------------------------------------------------
# RepairStatus & RepairRecord
# ---------------------------------------------------------------------------


def test_repair_status_values():
    """验证 RepairStatus 枚举值。"""
    from src.services.self_healing_action import RepairStatus
    assert RepairStatus.PENDING.value == "pending"
    assert RepairStatus.IN_PROGRESS.value == "in_progress"
    assert RepairStatus.SUCCESS.value == "success"
    assert RepairStatus.FAILED.value == "failed"
    assert RepairStatus.ESCALATED.value == "escalated"


def test_repair_record_defaults():
    """验证 RepairRecord 的默认字段。"""
    from src.services.self_healing_action import RepairRecord
    r = RepairRecord(
        repair_id="r1", action_type="restart", target="mod_a",
    )
    assert r.repair_id == "r1"
    assert r.action_type == "restart"
    assert r.target == "mod_a"
    assert r.status == "pending"
    assert r.verification_result is None
    assert r.escalation_level == 0
    assert r.escalated_to is None
    assert r.error_message == ""
    assert r.metadata == {}
    assert r.started_at is not None
    assert r.completed_at is None


# ---------------------------------------------------------------------------
# SelfHealingAction: execute() — happy path
# ---------------------------------------------------------------------------


def test_execute_detect_stop():
    """检测返回 False 时不执行修复，状态为 pending。"""
    action = make_action(
        detect=lambda ctx: False,
    )
    record = action.execute({"need_repair": False})
    assert record.status == "pending"
    assert "no repair needed" in record.verification_detail.lower()


def test_execute_repair_success_verify_success():
    """修复成功且验证通过 → success。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "healthy"),
    )
    record = action.execute({"need_repair": True})
    assert record.status == "success"
    assert record.verification_result is True
    assert "healthy" in record.verification_detail


def test_execute_repair_success_verify_fail():
    """修复成功但验证失败 → failed。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (False, "still broken"),
    )
    record = action.execute({"need_repair": True})
    assert record.status in ("failed", "escalated")
    assert record.verification_result is False
    assert "still broken" in record.verification_detail


def test_execute_repair_raises_exception():
    """修复抛出异常 → failed + error_message。"""
    action = make_action(
        repair=lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    record = action.execute({"need_repair": True})
    assert record.status in ("failed", "escalated")
    assert record.verification_result is False
    assert "boom" in record.error_message


def test_execute_repair_returns_failure():
    """修复本身返回失败 → failed。"""
    action = make_action(
        repair=lambda ctx: (False, "repair failed"),
    )
    record = action.execute({"need_repair": True})
    assert record.status in ("failed", "escalated")
    assert record.verification_result is False
    assert "repair failed" in record.verification_detail


# ---------------------------------------------------------------------------
# SelfHealingAction: escalation chain
# ---------------------------------------------------------------------------


def test_escalation_chain_triggers():
    """验证失败时升级到链中的下一个策略。"""
    escalate_calls = []
    action = make_action(
        action_type="restart",
        escalate_chain=["restart", "rollback", "notify_human"],
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (False, "verification failed"),
        on_escalate=lambda next_act, level, reason: escalate_calls.append(
            (next_act, level, reason)
        ),
    )
    record = action.execute({"need_repair": True})
    assert record.status == "escalated"
    assert record.escalated_to == "rollback"
    assert len(escalate_calls) == 1
    assert escalate_calls[0][0] == "rollback"


def test_escalation_max_level():
    """验证达到 max_escalation_level 后不再升级。"""
    action = make_action(
        action_type="restart",
        escalate_chain=["restart", "rollback", "notify_human"],
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (False, "still broken"),
    )
    action.max_escalation_level = 0  # 禁止任何升级
    record = action.execute({"need_repair": True})
    assert record.status == "failed"  # 不升级
    assert record.escalated_to is None


def test_no_escalation_chain_no_escalate():
    """无升级链时失败不升级。"""
    action = make_action(
        action_type="restart",
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (False, "broken"),
    )
    # escalate_chain 默认为 []
    record = action.execute({"need_repair": True})
    assert record.status == "failed"
    assert record.escalated_to is None


# ---------------------------------------------------------------------------
# SelfHealingAction: history & stats
# ---------------------------------------------------------------------------


def test_get_history():
    """验证修复历史记录。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "ok"),
    )
    for _ in range(3):
        action.execute({"need_repair": True})

    history = action.get_history()
    assert len(history) == 3
    for r in history:
        assert r.status == "success"


def test_get_history_limit():
    """验证历史记录 limit 参数。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "ok"),
    )
    for _ in range(10):
        action.execute({"need_repair": True})

    assert len(action.get_history(limit=3)) == 3
    assert len(action.get_history(limit=20)) == 10


def test_stats():
    """验证 stats() 统计信息。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "ok"),  # always verify OK
    )

    # 2 successes
    action.execute({"need_repair": True})
    action.execute({"need_repair": True})

    # Override verify to simulate a failure
    action2 = make_action(
        action_type="restart",
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (False, "broken"),
    )
    action2.execute({"need_repair": True})

    stats = action.stats()
    assert stats["total_repairs"] == 2
    assert stats["successes"] == 2
    assert stats["failures"] == 0
    assert stats["success_rate"] == 1.0

    stats2 = action2.stats()
    assert stats2["total_repairs"] == 1
    assert stats2["failures"] >= 1  # could be failed or escalated


def test_stats_verification_rate():
    """验证 verification_rate 计算。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "ok"),
    )
    action.execute({"need_repair": True})
    stats = action.stats()
    assert stats["verification_rate"] == 1.0

    # Add a no-detect case (verification_result is None)
    action2 = make_action(detect=lambda ctx: False)
    action2.execute({"need_repair": False})
    stats2 = action2.stats()
    # verification_result is None for pending records, so verification_rate should be 0
    assert stats2["verification_rate"] == 0.0


# ---------------------------------------------------------------------------
# SelfHealingAction: callbacks
# ---------------------------------------------------------------------------


def test_on_complete_callback():
    """验证 on_complete 回调。"""
    completed = []

    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "ok"),
        on_complete=lambda r: completed.append(r.status),
    )
    action.execute({"need_repair": True})
    assert completed == ["success"]


def test_on_escalate_callback_failure_safe():
    """验证 on_escalate 回调异常不传播。"""
    action = make_action(
        action_type="restart",
        escalate_chain=["restart", "rollback"],
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (False, "broken"),
        on_escalate=lambda a, l, r: (_ for _ in ()).throw(RuntimeError("bad callback")),
    )
    record = action.execute({"need_repair": True})
    # Should not raise; status should be escalated despite callback error
    assert record.status in ("escalated", "failed")


def test_on_complete_callback_failure_safe():
    """验证 on_complete 回调异常不传播。"""
    action = make_action(
        repair=lambda ctx: (True, "fixed"),
        verify=lambda ctx: (True, "ok"),
        on_complete=lambda r: (_ for _ in ()).throw(RuntimeError("bad callback")),
    )
    record = action.execute({"need_repair": True})
    # Should not raise
    assert record.status == "success"


# ---------------------------------------------------------------------------
# Phase 2: module_restart enhanced verification
# ---------------------------------------------------------------------------


def test_module_restart_verify_with_alive_check():
    """验证 _verify_restart() 调用 is_alive_check。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()

    alive_results = []
    md = ModuleDef(
        module_id="test_alive",
        display_name="Test Alive",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: alive_results.append(True) or True,
    )
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert ok
    assert "alive_check" in msg
    assert len(alive_results) == 1


def test_module_restart_verify_alive_check_false():
    """验证 is_alive_check 返回 False 时验证失败。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_dead",
        display_name="Test Dead",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: False,
    )
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert not ok
    assert "alive_check_false" in msg


def test_module_restart_verify_with_health_probe():
    """验证 _verify_restart() 调用 health_probe。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()

    probe_results = []
    md = ModuleDef(
        module_id="test_probe",
        display_name="Test Probe",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        health_probe=lambda: probe_results.append(True) or True,
    )
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert ok
    assert "health_probe" in msg
    assert len(probe_results) == 1


def test_module_restart_verify_health_probe_false():
    """验证 health_probe 返回 False 时验证失败。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_probe_fail",
        display_name="Test Probe Fail",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        health_probe=lambda: False,
    )
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert not ok
    assert "health_probe_false" in msg


def test_module_restart_verify_health_probe_exception():
    """验证 health_probe 抛出异常时不崩溃。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_probe_err",
        display_name="Test Probe Error",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        health_probe=lambda: (_ for _ in ()).throw(RuntimeError("probe error")),
    )
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert not ok
    assert "health_probe_error" in msg


def test_module_restart_verify_alive_check_exception():
    """验证 is_alive_check 抛出异常时不崩溃。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_alive_err",
        display_name="Test Alive Error",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: (_ for _ in ()).throw(RuntimeError("alive error")),
    )
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert not ok
    assert "alive_check_error" in msg


def test_module_restart_no_health_probe_backward_compat():
    """验证无 health_probe 的旧 ModuleDef 仍正常工作（向后兼容）。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_old",
        display_name="Test Old",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: True,
    )
    # No health_probe — should not crash
    restarter.register_module(md)

    ok, msg = restarter._verify_restart(md)
    assert ok
    assert "alive_check" in msg
    assert "health_probe" not in msg  # no probe means no probe check


# ---------------------------------------------------------------------------
# Phase 2: _escalate_repair_strategy
# ---------------------------------------------------------------------------


def test_escalate_repair_after_failed_restarts():
    """验证连续 2 次失败重启后触发升级。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy, RestartRecord,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_esc",
        display_name="Test Escalate",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: True,
    )
    restarter.register_module(md)

    # Simulate 2 consecutive failed restarts
    st = restarter._states["test_esc"]
    for i in range(2):
        st.restarts.append(RestartRecord(
            record_id=f"r{i}", module_id="test_esc",
            timestamp="2026-08-11T00:00:00",
            policy="thread", success=False,
            message=f"fail {i}", trigger_reason="test",
        ))

    last = st.restarts[-1]
    escalation = restarter._escalate_repair_strategy("test_esc", last)
    assert escalation is not None
    assert "degrade" in escalation.lower()


def test_escalate_notify_human_after_repeated_failures():
    """验证连续 4 次失败后触发人工通知。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy, RestartRecord,
    )

    alert_calls = []

    restarter = ModuleAutoRestarter(
        on_alert=lambda level, msg: alert_calls.append((level, msg)),
    )
    md = ModuleDef(
        module_id="test_human",
        display_name="Test Human",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: True,
    )
    restarter.register_module(md)

    # Simulate 4 consecutive failed restarts
    st = restarter._states["test_human"]
    for i in range(4):
        st.restarts.append(RestartRecord(
            record_id=f"r{i}", module_id="test_human",
            timestamp="2026-08-11T00:00:00",
            policy="thread", success=False,
            message=f"fail {i}", trigger_reason="test",
        ))

    last = st.restarts[-1]
    escalation = restarter._escalate_repair_strategy("test_human", last)
    assert escalation is not None
    assert "notify_human" in (escalation or "")
    assert len(alert_calls) >= 1
    assert alert_calls[0][0] == "CRITICAL"
    assert "human" in alert_calls[0][1].lower() or "manual" in alert_calls[0][1].lower()


def test_escalate_none_for_healthy_module():
    """验证健康模块不触发升级。"""
    from src.services.module_restart import (
        ModuleAutoRestarter, ModuleDef, RestartPolicy, RestartRecord,
    )

    restarter = ModuleAutoRestarter()
    md = ModuleDef(
        module_id="test_healthy",
        display_name="Test Healthy",
        policy=RestartPolicy.THREAD,
        restart_callback=lambda: (True, "ok"),
        is_alive_check=lambda: True,
    )
    restarter.register_module(md)

    # Only 1 failure — not enough for escalation
    st = restarter._states["test_healthy"]
    st.restarts.append(RestartRecord(
        record_id="r0", module_id="test_healthy",
        timestamp="2026-08-11T00:00:00",
        policy="thread", success=False,
        message="fail 0", trigger_reason="test",
    ))

    last = st.restarts[-1]
    escalation = restarter._escalate_repair_strategy("test_healthy", last)
    assert escalation is None
