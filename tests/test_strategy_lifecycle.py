# -*- coding: utf-8 -*-
"""Unit tests for T10 StrategyLifecycle (paper_trading/strategy_lifecycle.py)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.strategy_lifecycle import (
    LifecycleTransitionError,
    StrategyLifecycle,
    StrategyState,
)


def make_lifecycle(**kwargs) -> StrategyLifecycle:
    return StrategyLifecycle(**kwargs)


def advance_to_live(lc: StrategyLifecycle, name: str = "s1") -> None:
    """沿主链路推进到 LIVE."""
    for state in (
        StrategyState.BACKTEST,
        StrategyState.PAPER,
        StrategyState.REVIEW,
        StrategyState.LIVE,
    ):
        lc.transition(name, state)


# ---------------------------------------------------------------------------
# 枚举与状态机表
# ---------------------------------------------------------------------------


class TestStrategyState:
    def test_values(self):
        assert StrategyState.DRAFT.value == "DRAFT"
        assert StrategyState.LIVE.value == "LIVE"
        assert StrategyState.RETIRED.value == "RETIRED"
        assert StrategyState.DRAFT == "DRAFT"  # str Enum 可直接与字符串比较

    def test_all_states_present(self):
        assert {s for s in StrategyState} == {
            StrategyState.DRAFT,
            StrategyState.BACKTEST,
            StrategyState.PAPER,
            StrategyState.REVIEW,
            StrategyState.LIVE,
            StrategyState.PAUSED,
            StrategyState.RETIRED,
        }


class TestStateMachineTable:
    def test_table_matches_spec(self):
        lc = make_lifecycle()
        assert lc.state_machine[StrategyState.DRAFT] == frozenset(
            {StrategyState.BACKTEST, StrategyState.DRAFT}
        )
        assert lc.state_machine[StrategyState.BACKTEST] == frozenset(
            {StrategyState.PAPER, StrategyState.DRAFT}
        )
        assert lc.state_machine[StrategyState.PAPER] == frozenset(
            {StrategyState.REVIEW, StrategyState.DRAFT}
        )
        assert lc.state_machine[StrategyState.REVIEW] == frozenset(
            {StrategyState.LIVE, StrategyState.DRAFT}
        )
        assert lc.state_machine[StrategyState.LIVE] == frozenset(
            {StrategyState.PAUSED, StrategyState.DRAFT}
        )
        assert lc.state_machine[StrategyState.PAUSED] == frozenset(
            {StrategyState.LIVE, StrategyState.DRAFT}
        )
        assert lc.state_machine[StrategyState.RETIRED] == frozenset({StrategyState.DRAFT})


# ---------------------------------------------------------------------------
# 合法转移：完整生命周期
# ---------------------------------------------------------------------------


class TestValidTransitions:
    def test_full_lifecycle_forward(self):
        lc = make_lifecycle()
        assert lc.transition("s1", StrategyState.BACKTEST) == StrategyState.BACKTEST
        assert lc.transition("s1", StrategyState.PAPER) == StrategyState.PAPER
        assert lc.transition("s1", StrategyState.REVIEW) == StrategyState.REVIEW
        assert lc.transition("s1", StrategyState.LIVE) == StrategyState.LIVE
        assert lc.transition("s1", StrategyState.PAUSED) == StrategyState.PAUSED
        # PAUSED 可回到 LIVE
        assert lc.transition("s1", StrategyState.LIVE) == StrategyState.LIVE
        assert lc.get_state("s1") == StrategyState.LIVE

    def test_paused_to_live_and_live_to_paused_loop(self):
        lc = make_lifecycle()
        advance_to_live(lc)
        lc.transition("s1", StrategyState.PAUSED)
        lc.transition("s1", StrategyState.LIVE)
        lc.transition("s1", StrategyState.PAUSED)
        assert lc.get_state("s1") == StrategyState.PAUSED

    def test_draft_self_transition(self):
        lc = make_lifecycle()
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT
        assert lc.get_state("s1") == StrategyState.DRAFT

    def test_revert_to_draft_from_every_state(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST)
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT
        lc.transition("s1", StrategyState.BACKTEST)
        lc.transition("s1", StrategyState.PAPER)
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT
        lc.transition("s1", StrategyState.BACKTEST)
        lc.transition("s1", StrategyState.PAPER)
        lc.transition("s1", StrategyState.REVIEW)
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT
        advance_to_live(lc)
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT
        advance_to_live(lc)
        lc.transition("s1", StrategyState.PAUSED)
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT

    def test_retired_to_draft(self):
        lc = make_lifecycle(storage=lambda: {"s1": StrategyState.RETIRED})
        assert lc.get_state("s1") == StrategyState.RETIRED
        assert lc.transition("s1", StrategyState.DRAFT) == StrategyState.DRAFT

    def test_transition_accepts_string_state(self):
        lc = make_lifecycle()
        assert lc.transition("s1", "BACKTEST") == StrategyState.BACKTEST
        assert lc.get_state("s1") == StrategyState.BACKTEST


# ---------------------------------------------------------------------------
# 非法转移
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    def test_draft_to_paper_raises(self):
        lc = make_lifecycle()
        with pytest.raises(LifecycleTransitionError) as exc_info:
            lc.transition("s1", StrategyState.PAPER)
        assert "DRAFT" in str(exc_info.value)
        assert "PAPER" in str(exc_info.value)

    def test_error_exposes_current_and_target(self):
        lc = make_lifecycle()
        with pytest.raises(LifecycleTransitionError) as exc_info:
            lc.transition("s1", StrategyState.LIVE)
        err = exc_info.value
        assert err.strategy_name == "s1"
        assert err.current == StrategyState.DRAFT
        assert err.target == StrategyState.LIVE

    def test_backtest_to_live_raises(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST)
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.LIVE)

    def test_paper_to_live_skips_review(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST)
        lc.transition("s1", StrategyState.PAPER)
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.LIVE)

    def test_live_to_backtest_raises(self):
        lc = make_lifecycle()
        advance_to_live(lc)
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.BACKTEST)

    def test_review_to_retired_raises(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST)
        lc.transition("s1", StrategyState.PAPER)
        lc.transition("s1", StrategyState.REVIEW)
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.RETIRED)

    def test_retired_only_to_draft(self):
        lc = make_lifecycle(storage=lambda: {"s1": StrategyState.RETIRED})
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.BACKTEST)
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.LIVE)

    def test_illegal_transition_leaves_state_and_history_unchanged(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST, operator="a")
        lc.transition("s1", StrategyState.PAPER, operator="b")
        with pytest.raises(LifecycleTransitionError):
            lc.transition("s1", StrategyState.LIVE)  # 非法: 跳过 REVIEW
        assert lc.get_state("s1") == StrategyState.PAPER
        assert len(lc.get_approval_history("s1")) == 2

    def test_invalid_string_state_raises_value_error(self):
        lc = make_lifecycle()
        with pytest.raises(ValueError):
            lc.transition("s1", "NOT_A_STATE")


# ---------------------------------------------------------------------------
# 未知策略
# ---------------------------------------------------------------------------


class TestUnknownStrategy:
    def test_unknown_defaults_to_draft(self):
        lc = make_lifecycle()
        assert lc.get_state("unknown") == StrategyState.DRAFT

    def test_unknown_is_registered_by_get_state(self):
        lc = make_lifecycle()
        lc.get_state("unknown")
        assert lc.list_strategies()["unknown"] == StrategyState.DRAFT

    def test_unknown_can_transition_from_draft(self):
        lc = make_lifecycle()
        assert lc.transition("unknown", StrategyState.BACKTEST) == StrategyState.BACKTEST

    def test_existing_state_not_reset(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST)
        lc.transition("s1", StrategyState.PAPER)
        assert lc.get_state("s1") == StrategyState.PAPER


# ---------------------------------------------------------------------------
# is_live
# ---------------------------------------------------------------------------


class TestIsLive:
    def test_not_live_by_default(self):
        lc = make_lifecycle()
        assert lc.is_live("s1") is False

    def test_live_after_reaching_live(self):
        lc = make_lifecycle()
        advance_to_live(lc)
        assert lc.is_live("s1") is True

    def test_not_live_after_pause(self):
        lc = make_lifecycle()
        advance_to_live(lc)
        lc.transition("s1", StrategyState.PAUSED)
        assert lc.is_live("s1") is False

    def test_live_only_for_target_strategy(self):
        lc = make_lifecycle()
        advance_to_live(lc, name="live_one")
        assert lc.is_live("live_one") is True
        assert lc.is_live("other") is False


# ---------------------------------------------------------------------------
# list_strategies
# ---------------------------------------------------------------------------


class TestListStrategies:
    def test_empty_by_default(self):
        lc = make_lifecycle()
        assert lc.list_strategies() == {}

    def test_returns_current_states(self):
        lc = make_lifecycle()
        lc.transition("alpha", StrategyState.BACKTEST)
        lc.transition("beta", StrategyState.BACKTEST)
        lc.transition("beta", StrategyState.PAPER)
        assert lc.list_strategies() == {
            "alpha": StrategyState.BACKTEST,
            "beta": StrategyState.PAPER,
        }

    def test_returns_copy_not_internal_reference(self):
        lc = make_lifecycle()
        lc.transition("alpha", StrategyState.BACKTEST)
        states = lc.list_strategies()
        states["alpha"] = StrategyState.LIVE
        assert lc.get_state("alpha") == StrategyState.BACKTEST


# ---------------------------------------------------------------------------
# 审批历史
# ---------------------------------------------------------------------------


class TestApprovalHistory:
    def test_records_operator_and_timestamp(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST, operator="alice")
        history = lc.get_approval_history("s1")
        assert len(history) == 1
        entry = history[0]
        assert entry["strategy_name"] == "s1"
        assert entry["from"] == StrategyState.DRAFT
        assert entry["to"] == StrategyState.BACKTEST
        assert entry["operator"] == "alice"
        assert isinstance(entry["timestamp"], datetime)

    def test_history_in_order_with_from_state(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST, operator="a")
        lc.transition("s1", StrategyState.PAPER, operator="b")
        lc.transition("s1", StrategyState.DRAFT, operator="c")
        history = lc.get_approval_history("s1")
        assert [h["to"] for h in history] == [
            StrategyState.BACKTEST,
            StrategyState.PAPER,
            StrategyState.DRAFT,
        ]
        assert [h["operator"] for h in history] == ["a", "b", "c"]
        assert history[1]["from"] == StrategyState.BACKTEST

    def test_history_filtered_by_strategy(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST, operator="a")
        lc.transition("s2", StrategyState.BACKTEST, operator="b")
        assert len(lc.get_approval_history("s1")) == 1
        assert lc.get_approval_history("s1")[0]["operator"] == "a"
        assert lc.get_approval_history("s2")[0]["operator"] == "b"

    def test_no_history_for_untouched_strategy(self):
        lc = make_lifecycle()
        assert lc.get_approval_history("s1") == []

    def test_approvals_attribute_is_appendable(self):
        lc = make_lifecycle()
        lc.transition("s1", StrategyState.BACKTEST, operator="a")
        assert len(lc.approvals) == 1
        assert lc.approvals[0]["strategy_name"] == "s1"


# ---------------------------------------------------------------------------
# 存储注入与隔离
# ---------------------------------------------------------------------------


class TestInjectedStorage:
    def test_default_instances_are_isolated(self):
        lc1 = make_lifecycle()
        lc2 = make_lifecycle()
        lc1.transition("s1", StrategyState.BACKTEST)
        assert "s1" not in lc2.list_strategies()  # 状态未泄漏到另一实例
        assert lc2.get_state("s1") == StrategyState.DRAFT  # 另一实例独立默认 DRAFT

    def test_injected_storage_factory_is_used(self):
        store: dict = {}
        lc = make_lifecycle(storage=lambda: store)
        lc.transition("s1", StrategyState.BACKTEST)
        assert store["s1"] == StrategyState.BACKTEST

    def test_injected_storage_isolation(self):
        store_a: dict = {}
        store_b: dict = {}
        lc_a = make_lifecycle(storage=lambda: store_a)
        lc_b = make_lifecycle(storage=lambda: store_b)
        lc_a.transition("s1", StrategyState.BACKTEST)
        assert store_b == {}
        assert lc_b.get_state("s1") == StrategyState.DRAFT

    def test_injected_storage_can_be_shared(self):
        shared: dict = {}

        def factory() -> dict:
            return shared

        lc_a = make_lifecycle(storage=factory)
        lc_b = make_lifecycle(storage=factory)
        lc_a.transition("s1", StrategyState.BACKTEST)
        assert lc_b.get_state("s1") == StrategyState.BACKTEST

    def test_direct_dict_storage(self):
        store: dict = {}
        lc = make_lifecycle(storage=store)
        lc.transition("s1", StrategyState.BACKTEST)
        assert store["s1"] == StrategyState.BACKTEST

    def test_seeded_retired_state_via_storage(self):
        lc = make_lifecycle(storage=lambda: {"retired_one": StrategyState.RETIRED})
        assert lc.get_state("retired_one") == StrategyState.RETIRED

    def test_invalid_storage_type_raises(self):
        with pytest.raises(TypeError):
            make_lifecycle(storage=42)
