# -*- coding: utf-8 -*-
"""Tests for AdjustmentEngine (L4 intervention mode, gated)."""

import unittest

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.services.adjustment_engine import (
    SAFE_PARAMS,
    AdjustmentEngine,
)
from src.services.bootstrap_event_bus import bootstrap_event_bus
from src.services.event_bus import SystemEventBus, SystemEventType


def _reset() -> SystemEventBus:
    SystemEventBus.reset_instance()
    return bootstrap_event_bus()


class HintMappingTestCase(unittest.TestCase):
    """improvement_hints → 调整指令映射。"""

    def setUp(self) -> None:
        self.bus = _reset()

    def test_counterargument_maps_to_max_steps(self):
        """反面论证建议应映射到 AGENT_MAX_STEPS（加深分析），不再自动干预技能。"""
        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["在每次分析中强制加入「反面论证」环节"])
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0].param_name, "AGENT_MAX_STEPS")

    def test_confidence_hint_maps_to_max_steps(self):
        """置信度建议应映射到 AGENT_MAX_STEPS。"""
        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["系统置信度持续下降，检查数据源质量"])
        self.assertEqual(cmds[0].param_name, "AGENT_MAX_STEPS")

    def test_circularity_maps_to_compression(self):
        """思维循环应映射到上下文压缩档位。"""
        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["检测到思维循环"])
        self.assertEqual(cmds[0].param_name, "AGENT_CONTEXT_COMPRESSION_PROFILE")

    def test_no_match_returns_empty(self):
        """无匹配建议应返回空（不产生无依据调整）。"""
        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["今日天气不错"])
        self.assertEqual(cmds, [])


class WhitelistTestCase(unittest.TestCase):
    """安全白名单校验。"""

    def test_no_trading_params_in_whitelist(self):
        """白名单绝不能包含订单/仓位/风控参数。"""
        for p in SAFE_PARAMS:
            self.assertNotIn("ORDER", p)
            self.assertNotIn("POSITION", p)
            self.assertNotIn("RISK", p)
            self.assertNotIn("SLTP", p)
            self.assertNotIn("BREAKER", p)


class PersistenceTestCase(unittest.TestCase):
    """REV-201: 调整必须持久化到 .env（重启不丢失）。"""

    def test_apply_persists_to_env(self):
        """AGENT_MAX_STEPS 应用后应写入临时 .env 文件。"""
        import tempfile
        from pathlib import Path

        from src.core.config_manager import ConfigManager

        tmp_dir = tempfile.mkdtemp()
        env_path = Path(tmp_dir) / ".env"
        env_path.write_text("AGENT_MAX_STEPS=10\n", encoding="utf-8")
        manager = ConfigManager(env_path=env_path)

        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["系统置信度持续下降，检查数据源质量"])
        self.assertEqual(cmds[0].param_name, "AGENT_MAX_STEPS")
        ok = eng._apply_param("AGENT_MAX_STEPS", 12, manager=manager)
        self.assertTrue(ok)

        content = env_path.read_text(encoding="utf-8")
        self.assertIn("AGENT_MAX_STEPS=12", content)

    def test_apply_persists_compression_profile(self):
        """AGENT_CONTEXT_COMPRESSION_PROFILE 应用后应写入临时 .env。"""
        import tempfile
        from pathlib import Path

        from src.core.config_manager import ConfigManager

        tmp_dir = tempfile.mkdtemp()
        env_path = Path(tmp_dir) / ".env"
        env_path.write_text("AGENT_CONTEXT_COMPRESSION_PROFILE=balanced\n", encoding="utf-8")
        manager = ConfigManager(env_path=env_path)

        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["检测到思维循环"])
        self.assertEqual(cmds[0].param_name, "AGENT_CONTEXT_COMPRESSION_PROFILE")
        ok = eng._apply_param("AGENT_CONTEXT_COMPRESSION_PROFILE", "conservative", manager=manager)
        self.assertTrue(ok)

        content = env_path.read_text(encoding="utf-8")
        self.assertIn("AGENT_CONTEXT_COMPRESSION_PROFILE=conservative", content)


class EventEmissionTestCase(unittest.TestCase):
    """事件发布：提案/应用/拒绝。"""

    def setUp(self) -> None:
        self.bus = _reset()

    def test_propose_emits_adjustment_proposed(self):
        """propose 应发布 ADJUSTMENT_PROPOSED 事件。"""
        eng = AdjustmentEngine(auto_apply=False)
        eng.propose(["检测到思维循环"], reflection_id="r1")
        events = [
            e for e in self.bus.get_recent_events(limit=10)
            if e.event_type == SystemEventType.ADJUSTMENT_PROPOSED
        ]
        self.assertGreaterEqual(len(events), 1)

    def test_apply_emits_adjustment_applied(self):
        """apply 应发布 ADJUSTMENT_APPLIED 事件。"""
        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["检测到思维循环"])
        eng.apply(cmds[0], actor="test")
        events = [
            e for e in self.bus.get_recent_events(limit=10)
            if e.event_type == SystemEventType.ADJUSTMENT_APPLIED
        ]
        self.assertEqual(len(events), 1)

    def test_reject_emits_adjustment_rejected(self):
        """reject 应发布 ADJUSTMENT_REJECTED 事件。"""
        eng = AdjustmentEngine(auto_apply=False)
        cmds = eng.derive_commands(["检测到思维循环"])
        eng.reject(cmds[0], actor="test")
        events = [
            e for e in self.bus.get_recent_events(limit=10)
            if e.event_type == SystemEventType.ADJUSTMENT_REJECTED
        ]
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
