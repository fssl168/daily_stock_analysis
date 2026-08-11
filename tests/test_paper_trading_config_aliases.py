# -*- coding: utf-8 -*-
"""Tests for paper-trading env-var aliases (M6/G13)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config


@pytest.fixture(autouse=True)
def _reset_config_instance(monkeypatch):
    """Reset the Config singleton before/after each test."""
    Config._instance = None
    # Clear alias env vars to avoid cross-test pollution.
    for key in (
        "PAPER_TRADING_ENABLE_REFLECTION",
        "PAPER_TRADING_LISTENER_ENABLE_DAILY_REFLECTION",
        "PAPER_TRADING_ENABLE_BATTLE_PLAN",
        "PAPER_TRADING_LISTENER_ENABLE_BATTLE_PLAN",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    Config._instance = None


class TestReflectionBattlePlanAliases:
    """PAPER_TRADING_ENABLE_REFLECTION / ENABLE_BATTLE_PLAN aliases."""

    def test_alias_reflection_false_overrides_listener_default(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADING_ENABLE_REFLECTION", "false")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_listener_enable_daily_reflection is False

    def test_alias_reflection_true_overrides_listener_false(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADING_ENABLE_REFLECTION", "true")
        monkeypatch.setenv("PAPER_TRADING_LISTENER_ENABLE_DAILY_REFLECTION", "false")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_listener_enable_daily_reflection is True

    def test_listener_name_still_works(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADING_LISTENER_ENABLE_DAILY_REFLECTION", "false")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_listener_enable_daily_reflection is False

    def test_alias_battle_plan_false_overrides_listener_default(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADING_ENABLE_BATTLE_PLAN", "false")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_listener_enable_battle_plan is False

    def test_alias_battle_plan_true_overrides_listener_false(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADING_ENABLE_BATTLE_PLAN", "true")
        monkeypatch.setenv("PAPER_TRADING_LISTENER_ENABLE_BATTLE_PLAN", "false")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_listener_enable_battle_plan is True

    def test_listener_battle_plan_name_still_works(self, monkeypatch):
        monkeypatch.setenv("PAPER_TRADING_LISTENER_ENABLE_BATTLE_PLAN", "false")
        cfg = Config._load_from_env()
        assert cfg.paper_trading_listener_enable_battle_plan is False
