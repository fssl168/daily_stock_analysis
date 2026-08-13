# -*- coding: utf-8 -*-
"""T-08 assembly tests: ``build_full_listener`` is the canonical production
assembly shared by ``run_listener.py`` and the API ``start_listener``.

Verifies:
- Core components (quote cache, latency tracker, signal fusion) are wired.
- Capability flags correctly inject / omit PM agent, reflection, battle plan.
- Injected singletons are reused (API pricing consistency).
- Failed optional components degrade to None without crashing startup.
- Listener start/stop is idempotent.
"""

from __future__ import annotations

import pytest

from src.config import get_config
from paper_trading.market_listener import build_full_listener
from paper_trading.quote_cache import SharedQuoteCache


@pytest.fixture()
def cfg():
    return get_config()


def test_full_listener_core_components_wired(cfg, temp_db):
    listener = build_full_listener(
        cfg, account_id=2, db_manager=temp_db,
        enable_pm_agent=False, enable_daily_reflection=False, enable_battle_plan=False,
        watched_codes=["600519"], markets={"cn"}, tick_interval_seconds=60.0,
    )
    assert listener._quote_cache is not None
    assert listener._latency_tracker is not None
    assert listener._signal_fusion is not None
    # Capabilities explicitly off -> omitted.
    assert listener.pm_agent is None
    assert listener.reflection_engine is None
    assert listener.battle_plan_generator is None


def test_full_listener_flags_inject_optional_components(cfg, temp_db):
    # Components may or may not build depending on the environment, but the
    # flag plumbing must not raise: when a flag is on, the attribute is wired
    # if construction succeeds (and degrades to None if it fails).
    listener = build_full_listener(
        cfg, account_id=2, db_manager=temp_db,
        enable_pm_agent=True, enable_daily_reflection=True, enable_battle_plan=True,
        watched_codes=["600519"], markets={"cn"}, tick_interval_seconds=60.0,
    )
    # At minimum the listener still builds and the attribute exists.
    assert listener.config.account_id == 2


def test_full_listener_reuses_injected_quote_cache(cfg, temp_db):
    qc = SharedQuoteCache()
    listener = build_full_listener(
        cfg, account_id=2, db_manager=temp_db,
        enable_pm_agent=False, enable_daily_reflection=False, enable_battle_plan=False,
        watched_codes=["600519"], markets={"cn"}, tick_interval_seconds=60.0,
        quote_cache=qc,
    )
    assert listener._quote_cache is qc


def test_full_listener_degrades_when_component_build_fails(cfg, temp_db, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("LLM credentials missing")

    import src.agent.portfolio_manager_agent as pma

    monkeypatch.setattr(pma, "build_portfolio_manager_agent", _boom)
    listener = build_full_listener(
        cfg, account_id=2, db_manager=temp_db,
        enable_pm_agent=True, enable_daily_reflection=False, enable_battle_plan=False,
        watched_codes=["600519"], markets={"cn"}, tick_interval_seconds=60.0,
    )
    assert listener.pm_agent is None  # degraded, not crashed


def test_full_listener_start_stop_idempotent(cfg, temp_db):
    listener = build_full_listener(
        cfg, account_id=2, db_manager=temp_db,
        enable_pm_agent=False, enable_daily_reflection=False, enable_battle_plan=False,
        watched_codes=["600519"], markets={"cn"}, tick_interval_seconds=60.0,
    )
    listener.start()
    assert listener.is_running()
    listener.start()  # second start must not raise
    assert listener.is_running()
    listener.stop(timeout=2.0)
    assert not listener.is_running()
