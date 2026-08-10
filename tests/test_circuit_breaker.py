# -*- coding: utf-8 -*-
"""Unit tests for T2 CircuitBreaker (paper_trading/circuit_breaker.py)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paper_trading.circuit_breaker as cb_module
from paper_trading.circuit_breaker import (
    BreakerConfig,
    BreakerLevel,
    BreakerState,
    CircuitBreaker,
)


class FakeClock:
    """可手动推进的假时钟，用于冷却期相关测试."""

    def __init__(self, start: datetime | None = None):
        self.value = start or datetime(2026, 1, 1, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


def make_breaker(config: BreakerConfig | None = None, **callbacks) -> CircuitBreaker:
    return CircuitBreaker(config or BreakerConfig(), account_id=1, **callbacks)


def spy() -> list:
    """返回一个记录调用参数的回调."""

    def _cb(*args):
        _cb.calls.append(args)

    _cb.calls = []
    return _cb


# ---------------------------------------------------------------------------
# 基础结构与默认值
# ---------------------------------------------------------------------------


class TestBasics:
    def test_enum_values(self):
        assert BreakerLevel.NORMAL == "normal"
        assert BreakerLevel.SOFT == "soft"
        assert BreakerLevel.HARD == "hard"
        assert BreakerLevel.LIQUIDATION == "liquidate"
        assert BreakerLevel.NORMAL.value == "normal"

    def test_config_defaults(self):
        cfg = BreakerConfig()
        assert cfg.soft_threshold_pct == 0.03
        assert cfg.hard_threshold_pct == 0.05
        assert cfg.liquidation_threshold_pct == 0.08
        assert cfg.var_threshold_pct == 0.10
        assert cfg.cooling_period_hours == 24
        assert cfg.enable_auto_reset_soft is True
        assert cfg.check_interval_seconds == 1.0

    def test_state_defaults(self):
        state = BreakerState()
        assert state.level == BreakerLevel.NORMAL
        assert state.triggered_at is None
        assert state.daily_pnl == 0.0
        assert state.initial_capital == 0.0
        assert state.reason == ""

    def test_breaker_initial_state(self):
        cb = make_breaker()
        assert cb.account_id == 1
        assert cb.state.level == BreakerLevel.NORMAL
        assert cb.allow_new_position() is True
        assert cb.allow_any_trade() is True

    def test_normal_when_pnl_below_all_thresholds(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-100.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.NORMAL
        assert state.daily_pnl == -100.0
        assert state.initial_capital == 10000.0

    def test_zero_pnl_keeps_normal(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=0.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.NORMAL


# ---------------------------------------------------------------------------
# 三级阈值触发
# ---------------------------------------------------------------------------


class TestThresholdTriggers:
    def test_soft_trigger_at_exact_threshold(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-300.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.SOFT
        assert state.triggered_at is not None
        assert "soft" in state.reason

    def test_soft_trigger_above_threshold(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.SOFT
        assert cb.allow_new_position() is False
        assert cb.allow_any_trade() is True

    def test_hard_trigger_at_exact_threshold(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-500.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.HARD
        assert "hard" in state.reason
        assert cb.allow_any_trade() is False

    def test_liquidation_trigger_at_exact_threshold(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-800.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.LIQUIDATION
        assert "liquidation" in state.reason

    def test_escalation_soft_to_hard(self):
        cb = make_breaker()
        cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        state = cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.HARD

    def test_escalation_hard_to_liquidation(self):
        cb = make_breaker()
        cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        state = cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.LIQUIDATION


# ---------------------------------------------------------------------------
# VaR 触发
# ---------------------------------------------------------------------------


class TestVarTrigger:
    def test_var_triggers_liquidation(self):
        cb = make_breaker()
        state = cb.evaluate(
            current_pnl=-100.0, initial_capital=10000.0, current_var=1200.0
        )
        assert state.level == BreakerLevel.LIQUIDATION
        assert "VaR" in state.reason

    def test_var_at_exact_threshold_triggers(self):
        cb = make_breaker()
        state = cb.evaluate(
            current_pnl=0.0, initial_capital=10000.0, current_var=1000.0
        )
        assert state.level == BreakerLevel.LIQUIDATION

    def test_var_below_threshold_no_trigger(self):
        cb = make_breaker()
        state = cb.evaluate(
            current_pnl=-100.0, initial_capital=10000.0, current_var=500.0
        )
        assert state.level == BreakerLevel.NORMAL

    def test_negative_var_uses_abs(self):
        cb = make_breaker()
        state = cb.evaluate(
            current_pnl=-100.0, initial_capital=10000.0, current_var=-1100.0
        )
        assert state.level == BreakerLevel.LIQUIDATION

    def test_zero_var_does_not_trigger(self):
        cb = make_breaker()
        state = cb.evaluate(
            current_pnl=-100.0, initial_capital=10000.0, current_var=0.0
        )
        assert state.level == BreakerLevel.NORMAL

    def test_none_var_does_not_trigger(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-100.0, initial_capital=10000.0, current_var=None)
        assert state.level == BreakerLevel.NORMAL


# ---------------------------------------------------------------------------
# 回调调用
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_on_soft_callback_invoked(self):
        on_soft = spy()
        cb = make_breaker(on_soft_trigger=on_soft)
        cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert len(on_soft.calls) == 1
        level, reason = on_soft.calls[0]
        assert level == BreakerLevel.SOFT
        assert "soft" in reason

    def test_on_hard_callback_invoked(self):
        on_hard = spy()
        cb = make_breaker(on_hard_trigger=on_hard)
        cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        assert len(on_hard.calls) == 1
        assert on_hard.calls[0][0] == BreakerLevel.HARD

    def test_on_liquidation_callback_invoked(self):
        on_liq = spy()
        cb = make_breaker(on_liquidation=on_liq)
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert len(on_liq.calls) == 1
        assert on_liq.calls[0][0] == BreakerLevel.LIQUIDATION

    def test_escalation_invokes_each_level_callback_once(self):
        on_soft, on_hard, on_liq = spy(), spy(), spy()
        cb = make_breaker(
            on_soft_trigger=on_soft,
            on_hard_trigger=on_hard,
            on_liquidation=on_liq,
        )
        cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert len(on_soft.calls) == 1
        assert len(on_hard.calls) == 1
        assert len(on_liq.calls) == 1

    def test_callback_exception_safety(self):
        def boom(*_args):
            raise RuntimeError("callback exploded")

        cb = make_breaker(
            on_soft_trigger=boom,
            on_hard_trigger=boom,
            on_liquidation=boom,
        )
        # 回调抛异常不影响熔断主逻辑，也不向调用方传播
        state = cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.SOFT
        state = cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.HARD
        state = cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.LIQUIDATION


# ---------------------------------------------------------------------------
# 冷却期锁定
# ---------------------------------------------------------------------------


class TestCoolingPeriod:
    def test_liquidation_locked_within_cooling(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(cb_module, "_now", clock)
        cb = make_breaker()
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert cb.state.level == BreakerLevel.LIQUIDATION

        clock.advance(hours=1)
        # 冷却期内即使盈亏恢复也保持锁定
        state = cb.evaluate(current_pnl=-100.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.LIQUIDATION
        assert cb.allow_new_position() is False
        assert cb.allow_any_trade() is False

    def test_liquidation_callback_not_repeated_within_cooling(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(cb_module, "_now", clock)
        on_liq = spy()
        cb = make_breaker(on_liquidation=on_liq)
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert len(on_liq.calls) == 1
        clock.advance(hours=1)
        cb.evaluate(current_pnl=-950.0, initial_capital=10000.0)
        assert len(on_liq.calls) == 1

    def test_after_cooling_loss_recovered_stays_locked(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(cb_module, "_now", clock)
        cb = make_breaker()
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        clock.advance(hours=25)
        state = cb.evaluate(current_pnl=-100.0, initial_capital=10000.0)
        # LIQUIDATION 需人工确认解除，冷却期结束后仍保持锁定
        assert state.level == BreakerLevel.LIQUIDATION
        assert cb.allow_any_trade() is False

    def test_after_cooling_still_over_threshold_relocks(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(cb_module, "_now", clock)
        on_liq = spy()
        cb = make_breaker(on_liquidation=on_liq)
        first_trigger = cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        clock.advance(hours=25)
        state = cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.LIQUIDATION
        assert state.triggered_at == clock.value
        # 冷却期结束后的再次超限视为新触发事件
        assert len(on_liq.calls) == 2

    def test_cooling_uses_configured_period(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr(cb_module, "_now", clock)
        cfg = BreakerConfig(cooling_period_hours=2)
        on_liq = spy()
        cb = make_breaker(config=cfg, on_liquidation=on_liq)
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert len(on_liq.calls) == 1

        clock.advance(hours=1)
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert len(on_liq.calls) == 1  # 冷却期(2h)内锁定，不重复触发

        clock.advance(hours=2)  # 累计 3h > 2h 冷却期
        state = cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        assert state.level == BreakerLevel.LIQUIDATION
        assert len(on_liq.calls) == 2  # 冷却期结束、仍超限 → 重新触发



# ---------------------------------------------------------------------------
# 每日重置
# ---------------------------------------------------------------------------


class TestDailyReset:
    def test_reset_daily_from_soft(self):
        cb = make_breaker()
        cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert cb.state.level == BreakerLevel.SOFT
        cb.reset_daily()
        assert cb.state.level == BreakerLevel.NORMAL
        assert cb.state.triggered_at is None
        assert cb.state.reason == ""
        assert cb.allow_new_position() is True

    def test_reset_daily_from_hard(self):
        cb = make_breaker()
        cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        assert cb.state.level == BreakerLevel.HARD
        cb.reset_daily()
        assert cb.state.level == BreakerLevel.NORMAL
        assert cb.allow_any_trade() is True

    def test_reset_daily_does_not_reset_liquidation(self):
        cb = make_breaker()
        cb.evaluate(current_pnl=-900.0, initial_capital=10000.0)
        cb.reset_daily()
        assert cb.state.level == BreakerLevel.LIQUIDATION
        assert cb.allow_any_trade() is False
        assert cb.allow_new_position() is False

    def test_reset_daily_from_normal_is_noop(self):
        cb = make_breaker()
        cb.reset_daily()
        assert cb.state.level == BreakerLevel.NORMAL


# ---------------------------------------------------------------------------
# 交易许可闸门
# ---------------------------------------------------------------------------


class TestGates:
    def test_allow_new_position_only_normal(self):
        assert CircuitBreaker(BreakerConfig(), 1).allow_new_position() is True
        for level in (BreakerLevel.SOFT, BreakerLevel.HARD, BreakerLevel.LIQUIDATION):
            cb = CircuitBreaker(BreakerConfig(), 1)
            cb.state = BreakerState(level=level)
            assert cb.allow_new_position() is False

    def test_allow_any_trade_normal_and_soft(self):
        cb = CircuitBreaker(BreakerConfig(), 1)
        cb.state = BreakerState(level=BreakerLevel.NORMAL)
        assert cb.allow_any_trade() is True
        cb.state = BreakerState(level=BreakerLevel.SOFT)
        assert cb.allow_any_trade() is True
        cb.state = BreakerState(level=BreakerLevel.HARD)
        assert cb.allow_any_trade() is False
        cb.state = BreakerState(level=BreakerLevel.LIQUIDATION)
        assert cb.allow_any_trade() is False


# ---------------------------------------------------------------------------
# 不重复触发
# ---------------------------------------------------------------------------


class TestNoDoubleTrigger:
    def test_soft_not_retriggered_on_repeated_evaluate(self):
        on_soft = spy()
        cb = make_breaker(on_soft_trigger=on_soft)
        first = cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        second = cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert second.level == BreakerLevel.SOFT
        assert second.triggered_at == first.triggered_at
        assert len(on_soft.calls) == 1

    def test_hard_not_retriggered_on_repeated_evaluate(self):
        on_hard = spy()
        cb = make_breaker(on_hard_trigger=on_hard)
        first = cb.evaluate(current_pnl=-600.0, initial_capital=10000.0)
        second = cb.evaluate(current_pnl=-700.0, initial_capital=10000.0)
        assert second.level == BreakerLevel.HARD
        assert second.triggered_at == first.triggered_at
        assert len(on_hard.calls) == 1

    def test_soft_not_rearmed_after_temporary_recovery(self):
        on_soft = spy()
        cb = make_breaker(on_soft_trigger=on_soft)
        cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        cb.evaluate(current_pnl=-50.0, initial_capital=10000.0)
        cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert cb.state.level == BreakerLevel.SOFT
        assert len(on_soft.calls) == 1


# ---------------------------------------------------------------------------
# 边界条件
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_zero_initial_capital_does_not_crash(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-500.0, initial_capital=0.0)
        assert state.level == BreakerLevel.NORMAL

    def test_negative_initial_capital_does_not_crash(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-500.0, initial_capital=-100.0)
        assert state.level == BreakerLevel.NORMAL

    def test_var_with_zero_initial_capital_does_not_crash(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=0.0, initial_capital=0.0, current_var=500.0)
        assert state.level == BreakerLevel.NORMAL

    def test_state_records_pnl_and_capital(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-250.0, initial_capital=10000.0)
        assert state.daily_pnl == -250.0
        assert state.initial_capital == 10000.0

    def test_trigger_sets_triggered_at(self):
        cb = make_breaker()
        state = cb.evaluate(current_pnl=-400.0, initial_capital=10000.0)
        assert state.triggered_at is not None
        assert isinstance(state.triggered_at, datetime)

