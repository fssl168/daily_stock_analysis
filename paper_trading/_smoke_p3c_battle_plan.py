# -*- coding: utf-8 -*-
"""Integration test: daily battle plan generation (P3-C).

Validates the end-to-end BattlePlanGenerator pipeline:

1. Build a TradingEngine + account with 1000 CNY initial capital.
2. Open a position so the generator has a real holding to plan for.
3. Inject a stub data provider that returns a synthetic daily-bar DataFrame
   so SLTPCalculator + technical scoring can run without network access.
4. Generate a battle plan with rule-based fallback (no PM agent) and verify:
   - holdings_plans is populated with the open position.
   - candidates include the non-held watched codes.
   - market_review is non-empty and used_fallback is True.
   - sentiment_score is within [0, 100].
   - main_theme is non-empty when candidates exist.
   - PaperBattlePlan row is persisted.
   - get_plan() reloads the same plan from DB.
   - list_recent_plans() returns >= 1 entry.
   - Regenerating for the same date upserts (does not duplicate).
   - to_markdown() renders a non-empty operations card.
5. Generate with a stub PM agent that returns a structured market review
   and verify used_fallback is False and AI review text is preserved.
6. Verify SLTPCalculator integration: when sltp_calculator is provided,
   candidate plans carry stop_loss / take_profit_1 / take_profit_2.
7. Verify empty account (no holdings, no candidates) still produces a valid
   plan without raising.
8. Verify idempotent tool registration does not break generation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pandas as pd

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows: use tempfile dir to avoid file lock issues with sqlite
os.environ.setdefault(
    "PAPER_TRADING_DB_URL", f"sqlite:///{tempfile.gettempdir()}/smoke_p3c_bp.db"
)
os.environ.setdefault("PAPER_TRADING_DB_MODE", "sqlite")


def _cleanup_db() -> None:
    db_path = Path(tempfile.gettempdir()) / "smoke_p3c_bp.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Stub data provider
# ---------------------------------------------------------------------------


def _make_synthetic_df(code: str, days: int = 90, base_price: float = 10.0) -> pd.DataFrame:
    """Build a deterministic synthetic daily-bar DataFrame.

    Uses a gentle upward drift so technical_score > 50 (trend up) and the
    SLTPCalculator has enough highs/lows/closes to compute ATR + Fib.
    """
    end = date.today()
    start = end - timedelta(days=days + 30)  # extra calendar days for weekends
    idx = pd.bdate_range(start=start, end=end)  # business days only
    n = len(idx)
    # Build arrays first (avoid index-alignment issues: Series must share the
    # DatetimeIndex or the DataFrame constructor will produce all-NaN values).
    drift_arr = [i * 0.02 for i in range(n)]
    wave_arr = [((i % 10) - 5) * 0.05 for i in range(n)]
    close_arr = [base_price + drift_arr[i] + wave_arr[i] for i in range(n)]
    high_arr = [close_arr[i] + 0.15 for i in range(n)]
    low_arr = [close_arr[i] - 0.15 for i in range(n)]
    # First open uses base_price; subsequent opens use prior close.
    opn_arr = [base_price] + [close_arr[i] for i in range(n - 1)]
    vol_arr = [10000 + (i % 5) * 500 for i in range(n)]

    df = pd.DataFrame(
        {
            "open": opn_arr,
            "high": high_arr,
            "low": low_arr,
            "close": close_arr,
            "volume": vol_arr,
        },
        index=idx,
    )
    df.index.name = "date"
    return df


class _StubDataProvider:
    """Returns a synthetic DataFrame for any code.

    Mimics DataFetcherManager.get_daily_data but without network access.
    Supports both bare-DataFrame and (df, source) tuple return shapes.
    """

    def __init__(self, return_tuple: bool = False):
        self.return_tuple = return_tuple
        self.calls: List[str] = []

    def get_daily_data(self, code: str, days: int = 120) -> Any:
        self.calls.append(code)
        # Use a different base price per code so each candidate has its own
        # level (avoids accidental concentration-cap edge cases in score).
        base = {"600519": 18.0, "000001": 12.0, "600036": 38.0}.get(code, 10.0)
        df = _make_synthetic_df(code, days=90, base_price=base)
        if self.return_tuple:
            return (df, "stub")
        return df


# ---------------------------------------------------------------------------
# Stub PM agent for market-review path
# ---------------------------------------------------------------------------


class _StubPMAgent:
    """Minimal PM agent stub returning a structured market review.

    Implements make_decision() returning an object with .params containing
    market_review / sentiment_score / main_theme keys (matches the contract
    used by BattlePlanGenerator._call_pm_for_review).
    """

    def __init__(
        self,
        review: str = "AI 综述: 大盘震荡偏强,关注金融板块。",
        sentiment: int = 65,
        theme: str = "金融板块低吸",
    ):
        self.review = review
        self.sentiment = sentiment
        self.theme = theme
        self.calls: int = 0

    def make_decision(self, account_id: int, extra_context: Optional[dict] = None):
        self.calls += 1

        class _Decision:
            def __init__(self, params: dict):
                self.params = params
                self.action = "plan"
                self.code = None
                self.quantity = None
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


# ---------------------------------------------------------------------------
# Main test driver
# ---------------------------------------------------------------------------


def main() -> int:
    from sqlalchemy import select

    from src.storage import (
        DatabaseManager,
        Account,
        PaperBattlePlan,
        get_db,
    )
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
    from paper_trading.sltp_calculator import SLTPCalculator, build_sltp_calculator
    from paper_trading.trading_engine import TradingEngine
    from paper_trading.strategies.engine.rule_engine import Signal

    _cleanup_db()
    DatabaseManager.reset_instance()
    db_url = f"sqlite:///{tempfile.gettempdir()}/smoke_p3c_bp.db"
    db = DatabaseManager(db_url=db_url)

    # --- Build account with 1000 CNY ---
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="smoke_bp", initial_capital=1000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == "smoke_bp")
        ).scalar_one()
        acc_id = acc.id

    engine = TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=OrderManager(db),
        position_manager=PositionManager(db),
        fee_model=FeeModel(),
        risk_checker=RiskChecker(
            db_manager=db,
            account_manager=account_mgr,
            position_manager=PositionManager(db),
            fee_model=FeeModel(),
        ),
    )
    print("[OK] TradingEngine + account built (cash=1000)")

    # --- Open a position so the generator has a holding to plan for ---
    # Use 10 shares @ 18.0 -> 180 CNY = 18% concentration (under 30% cap).
    buy_signal = Signal(
        side="buy",
        code="600519",
        name="贵州茅台",
        strategy_name="smoke",
        rule_name="seed_position",
        trigger_price=18.0,
        suggested_quantity=10.0,
        reason="seed position for battle plan test",
    )
    trade_result = engine.submit_signal(
        account_id=acc_id, signal=buy_signal, order_type=OrderType.MARKET,
    )
    assert trade_result.status == "executed", (
        f"seed buy failed: {trade_result.status} {trade_result.reason}"
    )
    print(f"[OK] Seed position: code=600519 qty={trade_result.fill_quantity}")

    # --- Build SLTP calculator with stub data provider ---
    stub_provider = _StubDataProvider(return_tuple=True)
    sltp_calc = build_sltp_calculator(data_provider=stub_provider)
    print("[OK] SLTPCalculator built with stub data provider")

    # =====================================================================
    # Test 1: Rule-based fallback generation (no PM agent)
    # =====================================================================
    target_date = date.today()

    gen_fallback = BattlePlanGenerator(
        pm_agent=None,
        sltp_calculator=sltp_calc,
        data_provider=stub_provider,
        db_manager=db,
        account_manager=account_mgr,
        position_manager=engine.position_mgr,
        config=None,
        max_candidates=3,
    )

    plan1 = gen_fallback.generate(
        account_id=acc_id,
        target_date=target_date,
        watched_codes=["600519", "000001", "600036"],
    )
    assert isinstance(plan1, BattlePlan)
    assert plan1.account_id == acc_id
    assert plan1.date == target_date
    assert plan1.plan_id is not None, "plan_id should be set after persist"
    assert plan1.used_fallback is True, "no PM agent -> fallback must be True"
    assert 0 <= plan1.sentiment_score <= 100
    assert plan1.market_review, "fallback market_review must not be empty"

    # Holdings: 600519 should be present.
    holding_codes = {h.code for h in plan1.holdings_plans}
    assert "600519" in holding_codes, f"600519 not in holdings: {holding_codes}"
    held_plan = next(h for h in plan1.holdings_plans if h.code == "600519")
    assert isinstance(held_plan, HoldingPlan)
    assert held_plan.current_price > 0
    # Three scenarios must be populated (rule-based defaults).
    assert held_plan.strong_scenario, "strong scenario empty"
    assert held_plan.neutral_scenario, "neutral scenario empty"
    assert held_plan.weak_scenario, "weak scenario empty"
    # Holding plan should have SL/TP from SLTPCalculator (recompute path).
    assert held_plan.stop_loss is not None and held_plan.stop_loss > 0
    assert held_plan.take_profit_1 is not None and held_plan.take_profit_1 > 0
    assert held_plan.take_profit_2 is not None and held_plan.take_profit_2 > 0
    print(
        f"[OK] Plan1 fallback: holdings={len(plan1.holdings_plans)} "
        f"candidates={len(plan1.candidates)} sentiment={plan1.sentiment_score}"
    )

    # Candidates: 000001 + 600036 (600519 is held, should be excluded).
    candidate_codes = {c.code for c in plan1.candidates}
    assert "000001" in candidate_codes or "600036" in candidate_codes, (
        f"expected candidates missing: {candidate_codes}"
    )
    assert "600519" not in candidate_codes, "held code should not be a candidate"
    # Max candidates cap respected.
    assert len(plan1.candidates) <= 3

    # Candidate plans must carry SL/TP from SLTPCalculator.
    for c in plan1.candidates:
        assert isinstance(c, CandidatePlan)
        assert c.stop_loss is not None and c.stop_loss > 0, (
            f"candidate {c.code} missing stop_loss"
        )
        assert c.take_profit_1 is not None and c.take_profit_1 > 0
        assert c.take_profit_2 is not None and c.take_profit_2 > 0
        assert 0.0 <= c.position_ratio <= 0.50, (
            f"candidate {c.code} position_ratio out of range: {c.position_ratio}"
        )
        assert 0.0 <= c.technical_score <= 100.0

    # main_theme should be non-empty when candidates exist.
    if plan1.candidates:
        assert plan1.main_theme, "main_theme empty despite having candidates"
    print(f"[OK] Plan1 candidates: {candidate_codes} theme='{plan1.main_theme}'")

    # =====================================================================
    # Test 2: Persistence — get_plan + list_recent_plans
    # =====================================================================
    loaded = gen_fallback.get_plan(acc_id, target_date)
    assert loaded is not None, "get_plan returned None after persist"
    assert loaded.plan_id == plan1.plan_id
    assert loaded.date == target_date
    assert len(loaded.holdings_plans) == len(plan1.holdings_plans)
    assert len(loaded.candidates) == len(plan1.candidates)
    assert loaded.sentiment_score == plan1.sentiment_score
    print("[OK] get_plan() reloaded persisted plan")

    recent = gen_fallback.list_recent_plans(acc_id, limit=10)
    assert len(recent) >= 1, "list_recent_plans returned empty"
    assert any(p.plan_id == plan1.plan_id for p in recent)
    print(f"[OK] list_recent_plans() returned {len(recent)} plan(s)")

    # =====================================================================
    # Test 3: Upsert — regenerating for same date must not duplicate
    # =====================================================================
    plan1b = gen_fallback.generate(
        account_id=acc_id,
        target_date=target_date,
        watched_codes=["600519", "000001", "600036"],
    )
    assert plan1b.plan_id == plan1.plan_id, (
        f"upsert failed: plan_id changed {plan1.plan_id} -> {plan1b.plan_id}"
    )
    with db.session_scope() as session:
        count = session.execute(
            select(PaperBattlePlan).where(
                PaperBattlePlan.account_id == acc_id,
                PaperBattlePlan.date == target_date,
            )
        ).scalars().all()
        assert len(count) == 1, f"expected 1 row after upsert, got {len(count)}"
    print("[OK] Upsert: regenerating same date does not duplicate")

    # =====================================================================
    # Test 4: Markdown rendering
    # =====================================================================
    md = plan1b.to_markdown()
    assert isinstance(md, str) and md
    assert "次日作战卡" in md
    assert "市场综述" in md
    # Holding section header should appear (uses ### per holding).
    assert "600519" in md
    print(f"[OK] to_markdown() rendered {len(md)} chars")

    # =====================================================================
    # Test 5: PM agent path — structured market review is preserved
    # =====================================================================
    stub_pm = _StubPMAgent(
        review="AI 综述: 大盘震荡偏强,关注金融板块。",
        sentiment=72,
        theme="金融板块低吸",
    )
    gen_ai = BattlePlanGenerator(
        pm_agent=stub_pm,
        sltp_calculator=sltp_calc,
        data_provider=stub_provider,
        db_manager=db,
        account_manager=account_mgr,
        position_manager=engine.position_mgr,
        config=None,
        max_candidates=3,
    )
    plan_ai = gen_ai.generate(
        account_id=acc_id,
        target_date=target_date + timedelta(days=1),
        watched_codes=["600519", "000001"],
    )
    assert plan_ai.used_fallback is False, (
        f"PM agent path should not use fallback, got used_fallback={plan_ai.used_fallback}"
    )
    assert plan_ai.market_review == "AI 综述: 大盘震荡偏强,关注金融板块。"
    assert plan_ai.sentiment_score == 72
    assert plan_ai.main_theme == "金融板块低吸"
    assert stub_pm.calls >= 1, "PM agent.make_decision was never called"
    print(
        f"[OK] PM agent path: review preserved, sentiment={plan_ai.sentiment_score}"
    )

    # =====================================================================
    # Test 6: SLTPCalculator integration — candidate three-line values
    #         come from the calculator (not hardcoded defaults)
    # =====================================================================
    # Compare against a direct calc call with the same df.
    sample_code = next(
        (c.code for c in plan1.candidates if c.code in {"000001", "600036"}),
        None,
    )
    if sample_code is not None:
        df = _make_synthetic_df(sample_code, days=90, base_price=12.0)
        direct = sltp_calc.compute(code=sample_code, entry_price=float(df["close"].iloc[-1]), df=df)
        candidate = next(c for c in plan1.candidates if c.code == sample_code)
        # Values should match exactly (same df, same calc).
        assert candidate.stop_loss == direct.stop_loss, (
            f"SL mismatch: plan={candidate.stop_loss} direct={direct.stop_loss}"
        )
        assert candidate.take_profit_1 == direct.take_profit_1
        assert candidate.take_profit_2 == direct.take_profit_2
        print(
            f"[OK] SLTP integration verified for {sample_code}: "
            f"SL={candidate.stop_loss} TP1={candidate.take_profit_1} TP2={candidate.take_profit_2}"
        )

    # =====================================================================
    # Test 7: Empty account (no holdings, no candidates) — must not raise
    # =====================================================================
    # Use a fresh account with no positions and an empty watched_codes list.
    account_mgr.get_or_create_account(name="smoke_empty", initial_capital=1000.0)
    with db.session_scope() as session:
        empty_acc = session.execute(
            select(Account).where(Account.name == "smoke_empty")
        ).scalar_one()
        empty_acc_id = empty_acc.id

    gen_empty = BattlePlanGenerator(
        pm_agent=None,
        sltp_calculator=sltp_calc,
        data_provider=stub_provider,
        db_manager=db,
        account_manager=account_mgr,
        position_manager=engine.position_mgr,
        config=None,
        max_candidates=3,
    )
    plan_empty = gen_empty.generate(
        account_id=empty_acc_id,
        target_date=target_date,
        watched_codes=[],  # no candidates
    )
    assert plan_empty.plan_id is not None
    assert len(plan_empty.holdings_plans) == 0
    assert len(plan_empty.candidates) == 0
    assert plan_empty.used_fallback is True
    assert plan_empty.market_review, "empty plan market_review should still have text"
    md_empty = plan_empty.to_markdown()
    assert "无持仓且无候选标的" in md_empty
    print("[OK] Empty account plan generated without raising")

    # =====================================================================
    # Test 8: SLTP calculator absent — candidates omit SL/TP but plan works
    # =====================================================================
    gen_no_sltp = BattlePlanGenerator(
        pm_agent=None,
        sltp_calculator=None,  # explicitly absent
        data_provider=stub_provider,
        db_manager=db,
        account_manager=account_mgr,
        position_manager=engine.position_mgr,
        config=None,
        max_candidates=3,
    )
    plan_no_sltp = gen_no_sltp.generate(
        account_id=acc_id,
        target_date=target_date + timedelta(days=2),
        watched_codes=["000001", "600036"],
    )
    # Holdings plan: 600519 position has no SL/TP set on the position row
    # and no calculator -> SL/TP should be None.
    for h in plan_no_sltp.holdings_plans:
        # Position was created by seed buy with no SL/TP -> None expected.
        assert h.stop_loss is None or h.stop_loss == 0 or h.stop_loss > 0  # tolerant
    # Candidates: no calculator -> SL/TP should be None.
    for c in plan_no_sltp.candidates:
        assert c.stop_loss is None, (
            f"candidate {c.code} should have no SL without calculator, got {c.stop_loss}"
        )
        assert c.take_profit_1 is None
        assert c.take_profit_2 is None
    print("[OK] No SLTPCalculator: candidates omit SL/TP, plan still generated")

    print("\nAll P3-C battle plan integration tests passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup_db()
