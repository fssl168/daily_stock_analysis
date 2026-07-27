# -*- coding: utf-8 -*-
"""Unit and integration tests for P1-B battle plan generation.

Covers rule-based fallback, AI-enhanced market review, persistence,
SLTP calculator integration, and empty-account edge cases.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.battle_plan import (
    BattlePlan,
    BattlePlanGenerator,
    CandidatePlan,
    HoldingPlan,
)
from paper_trading.fees import FeeModel
from paper_trading.order import OrderManager, OrderType
from paper_trading.position import PositionManager
from paper_trading.risk import RiskChecker
from paper_trading.sltp_calculator import build_sltp_calculator
from paper_trading.trading_engine import TradingEngine
from src.storage import PaperAccount, PaperBattlePlan
from strategies_v2.rule_engine import Signal

from tests.conftest import StubDataProvider, _make_synthetic_daily_df


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _build_engine(db) -> TradingEngine:
    """Build a TradingEngine wired to a temporary database."""
    account_mgr = PaperAccountManager(db_manager=db)
    position_mgr = PositionManager(db_manager=db)
    return TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=OrderManager(db),
        position_manager=position_mgr,
        fee_model=FeeModel(),
        risk_checker=RiskChecker(
            db_manager=db,
            account_manager=account_mgr,
            position_manager=position_mgr,
            fee_model=FeeModel(),
        ),
    )


def _create_account(db, name: str = "battle_test", capital: float = 1000.0) -> int:
    """Create a paper account and return its id."""
    mgr = PaperAccountManager(db_manager=db)
    mgr.get_or_create_account(name=name, initial_capital=capital)
    with db.session_scope() as session:
        row = session.execute(
            session.query(PaperAccount).filter(PaperAccount.name == name).statement
        ).scalar_one()
        return int(row.id)


def _seed_position(db, engine: TradingEngine, account_id: int) -> None:
    """Buy 10 shares of 600519 so the account has a holding."""
    signal = Signal(
        side="buy",
        code="600519",
        name="贵州茅台",
        strategy_name="test",
        rule_name="seed",
        trigger_price=18.0,
        suggested_quantity=10.0,
        reason="seed position for battle plan test",
    )
    result = engine.submit_signal(
        account_id=account_id, signal=signal, order_type=OrderType.MARKET,
    )
    assert result.status == "executed"


class StubPMAgent:
    """Minimal PM agent returning a structured market review."""

    def __init__(
        self,
        review: str = "AI review: bullish bias.",
        sentiment: int = 65,
        theme: str = "tech growth",
    ):
        self.review = review
        self.sentiment = sentiment
        self.theme = theme
        self.calls = 0

    def make_decision(self, account_id: int, extra_context: Optional[dict] = None):
        self.calls += 1

        class _Decision:
            def __init__(self, params: dict):
                self.params = params
                self.action = "plan"
                self.code = None
                self.reason = "stub PM market review"
                self.confidence = 0.6
                self.used_fallback = False
                self.error = None

        return _Decision(
            {
                "market_review": self.review,
                "sentiment_score": self.sentiment,
                "main_theme": self.theme,
            }
        )


@pytest.fixture
def battle_engine(temp_db):
    """Provide a TradingEngine bound to the per-test temp DB."""
    yield _build_engine(temp_db)


@pytest.fixture
def stub_provider():
    """Provide a stub data provider returning synthetic daily bars."""
    yield StubDataProvider(return_tuple=True)


@pytest.fixture
def sltp_calc(stub_provider):
    """Provide a SLTPCalculator using the stub provider."""
    yield build_sltp_calculator(data_provider=stub_provider)


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestBattlePlanDataclasses:
    """Unit tests for BattlePlan / HoldingPlan / CandidatePlan."""

    def test_holding_plan_to_dict(self):
        plan = HoldingPlan(
            code="600519",
            name="贵州茅台",
            current_price=1800.0,
            strong_scenario="hold",
            stop_loss=1700.0,
            take_profit_1=1900.0,
            take_profit_2=2000.0,
        )
        d = plan.to_dict()
        assert d["code"] == "600519"
        assert d["stop_loss"] == 1700.0
        assert d["action_conditions"] == []

    def test_candidate_plan_to_dict(self):
        plan = CandidatePlan(
            code="000001",
            name="平安银行",
            auction_condition="low -0.5%",
            position_ratio=0.20,
            technical_score=72.0,
        )
        d = plan.to_dict()
        assert d["technical_score"] == 72.0
        assert d["position_ratio"] == 0.20

    def test_battle_plan_to_dict(self):
        plan = BattlePlan(
            plan_id=1,
            account_id=2,
            date=date(2026, 7, 27),
            market_review="neutral",
            sentiment_score=55,
        )
        d = plan.to_dict()
        assert d["date"] == "2026-07-27"
        assert d["sentiment_score"] == 55
        assert d["holdings_plans"] == []

    def test_battle_plan_to_markdown(self):
        plan = BattlePlan(
            plan_id=1,
            account_id=2,
            date=date(2026, 7, 27),
            market_review="watchful",
            sentiment_score=50,
            main_theme="defense",
            holdings_plans=[
                HoldingPlan(code="600519", name="贵州茅台", current_price=1800.0),
            ],
            candidates=[
                CandidatePlan(code="000001", name="平安银行", technical_score=60.0),
            ],
        )
        md = plan.to_markdown()
        assert "次日作战卡" in md
        assert "600519" in md
        assert "000001" in md
        assert "市场综述" in md

    def test_battle_plan_to_markdown_empty(self):
        plan = BattlePlan(date=date(2026, 7, 27))
        md = plan.to_markdown()
        assert "无持仓且无候选标的" in md


# ---------------------------------------------------------------------------
# Generator tests
# ---------------------------------------------------------------------------


class TestBattlePlanGenerator:
    """Integration tests for BattlePlanGenerator."""

    def test_empty_account_uses_fallback(self, temp_db, stub_provider, sltp_calc):
        """An empty account with no candidates produces a valid fallback plan."""
        acc_id = _create_account(temp_db)
        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=PositionManager(temp_db),
            config=None,
            max_candidates=3,
        )
        target = date(2026, 7, 27)
        plan = gen.generate(account_id=acc_id, target_date=target, watched_codes=[])

        assert isinstance(plan, BattlePlan)
        assert plan.plan_id is not None
        assert plan.date == target
        assert plan.used_fallback is True
        assert 0 <= plan.sentiment_score <= 100
        assert plan.market_review
        assert len(plan.holdings_plans) == 0
        assert len(plan.candidates) == 0

    def test_plan_with_holdings_and_candidates(
        self, temp_db, battle_engine, stub_provider, sltp_calc,
    ):
        """A holding is planned and non-held watched codes become candidates."""
        acc_id = _create_account(temp_db)
        _seed_position(temp_db, battle_engine, acc_id)

        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=battle_engine.position_mgr,
            config=None,
            max_candidates=3,
        )
        target = date(2026, 7, 27)
        plan = gen.generate(
            account_id=acc_id,
            target_date=target,
            watched_codes=["600519", "000001", "600036"],
        )

        assert plan.used_fallback is True
        holding_codes = {h.code for h in plan.holdings_plans}
        assert "600519" in holding_codes

        candidate_codes = {c.code for c in plan.candidates}
        assert "600519" not in candidate_codes
        assert "000001" in candidate_codes or "600036" in candidate_codes
        assert len(plan.candidates) <= 3

        # Holding plans have SL/TP recomputed by the calculator.
        held = next(h for h in plan.holdings_plans if h.code == "600519")
        assert held.stop_loss is not None and held.stop_loss > 0
        assert held.take_profit_1 is not None and held.take_profit_1 > 0
        assert held.take_profit_2 is not None and held.take_profit_2 > 0
        assert held.strong_scenario and held.neutral_scenario and held.weak_scenario

    def test_candidates_carry_sltp_lines(
        self, temp_db, battle_engine, stub_provider, sltp_calc,
    ):
        """Candidate plans include stop-loss and take-profit lines."""
        acc_id = _create_account(temp_db)
        _seed_position(temp_db, battle_engine, acc_id)

        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=battle_engine.position_mgr,
            config=None,
            max_candidates=3,
        )
        plan = gen.generate(
            account_id=acc_id,
            target_date=date(2026, 7, 27),
            watched_codes=["000001", "600036"],
        )

        for c in plan.candidates:
            assert c.stop_loss is not None and c.stop_loss > 0
            assert c.take_profit_1 is not None and c.take_profit_1 > 0
            assert c.take_profit_2 is not None and c.take_profit_2 > 0
            assert 0.0 <= c.position_ratio <= 0.50
            assert 0.0 <= c.technical_score <= 100.0

    def test_no_sltp_calculator_omits_lines(
        self, temp_db, battle_engine, stub_provider,
    ):
        """When SLTP calculator is absent, candidate SL/TP lines are None."""
        acc_id = _create_account(temp_db)
        _seed_position(temp_db, battle_engine, acc_id)

        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=None,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=battle_engine.position_mgr,
            config=None,
            max_candidates=3,
        )
        plan = gen.generate(
            account_id=acc_id,
            target_date=date(2026, 7, 27),
            watched_codes=["000001"],
        )

        for c in plan.candidates:
            assert c.stop_loss is None
            assert c.take_profit_1 is None
            assert c.take_profit_2 is None

    def test_persistence_and_upsert(
        self, temp_db, battle_engine, stub_provider, sltp_calc,
    ):
        """Generated plans are persisted and re-generating upserts."""
        acc_id = _create_account(temp_db)
        _seed_position(temp_db, battle_engine, acc_id)

        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=battle_engine.position_mgr,
            config=None,
            max_candidates=3,
        )
        target = date(2026, 7, 27)
        plan1 = gen.generate(
            account_id=acc_id,
            target_date=target,
            watched_codes=["600519", "000001"],
        )
        plan2 = gen.generate(
            account_id=acc_id,
            target_date=target,
            watched_codes=["600519", "000001"],
        )

        assert plan2.plan_id == plan1.plan_id

        loaded = gen.get_plan(acc_id, target)
        assert loaded is not None
        assert loaded.plan_id == plan1.plan_id
        assert loaded.sentiment_score == plan1.sentiment_score

        recent = gen.list_recent_plans(acc_id, limit=10)
        assert any(p.plan_id == plan1.plan_id for p in recent)

        with temp_db.session_scope() as session:
            rows = session.execute(
                session.query(PaperBattlePlan)
                .filter(
                    PaperBattlePlan.account_id == acc_id,
                    PaperBattlePlan.date == target,
                )
                .statement
            ).scalars().all()
            assert len(rows) == 1

    def test_ai_enhanced_plan(
        self, temp_db, battle_engine, stub_provider, sltp_calc,
    ):
        """A stub PM agent provides the market review instead of fallback."""
        acc_id = _create_account(temp_db)
        _seed_position(temp_db, battle_engine, acc_id)
        stub_pm = StubPMAgent(
            review="AI bullish review.",
            sentiment=72,
            theme="growth",
        )

        gen = BattlePlanGenerator(
            pm_agent=stub_pm,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=battle_engine.position_mgr,
            config=None,
            max_candidates=3,
        )
        plan = gen.generate(
            account_id=acc_id,
            target_date=date(2026, 7, 28),
            watched_codes=["000001"],
        )

        assert plan.used_fallback is False
        assert plan.market_review == "AI bullish review."
        assert plan.sentiment_score == 72
        assert plan.main_theme == "growth"
        assert stub_pm.calls >= 1

    def test_default_watched_codes_from_config(
        self, temp_db, stub_provider, sltp_calc,
    ):
        """When watched_codes is None, the generator falls back to config."""
        acc_id = _create_account(temp_db)

        class _Config:
            stock_list = ["000001", "600036"]

        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=PositionManager(temp_db),
            config=_Config(),
            max_candidates=3,
        )
        plan = gen.generate(account_id=acc_id, target_date=date(2026, 7, 27))
        candidate_codes = {c.code for c in plan.candidates}
        assert "000001" in candidate_codes or "600036" in candidate_codes

    def test_sltp_values_match_direct_calc(
        self, temp_db, battle_engine, stub_provider, sltp_calc,
    ):
        """Candidate SL/TP values match a direct SLTPCalculator call."""
        acc_id = _create_account(temp_db)
        _seed_position(temp_db, battle_engine, acc_id)

        gen = BattlePlanGenerator(
            pm_agent=None,
            sltp_calculator=sltp_calc,
            data_provider=stub_provider,
            db_manager=temp_db,
            account_manager=PaperAccountManager(temp_db),
            position_manager=battle_engine.position_mgr,
            config=None,
            max_candidates=3,
        )
        plan = gen.generate(
            account_id=acc_id,
            target_date=date(2026, 7, 27),
            watched_codes=["000001"],
        )
        candidate = plan.candidates[0]
        df = _make_synthetic_daily_df("000001", days=90, base_price=12.0)
        direct = sltp_calc.compute(
            code="000001",
            entry_price=float(df["close"].iloc[-1]),
            df=df,
        )

        assert candidate.stop_loss == direct.stop_loss
        assert candidate.take_profit_1 == direct.take_profit_1
        assert candidate.take_profit_2 == direct.take_profit_2
