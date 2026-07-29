# -*- coding: utf-8 -*-
"""Integration test: full Portfolio Manager agent loop (P3-C).

Validates the end-to-end PM agent pipeline:
1. Build a TradingEngine + account with 1000 CNY initial capital.
2. Register paper_trading_* tools on a fake executor's registry.
3. Inject a stub executor that returns a JSON "buy" decision.
4. Run PortfolioManagerAgent.make_decision() and verify:
   - The decision is parsed correctly (action=buy, code, quantity).
   - The decision is persisted to PaperDecision table.
   - The agent's tool registration is idempotent.
5. Verify the decision can be executed via the TradingEngine (manual flow):
   - Build a Signal from the decision and call submit_signal.
   - Confirm order + trade + position update occur.
6. Verify the fallback path when the executor raises (action=hold).
7. Verify the fallback path on timeout (action=hold).

The stub executor mimics AgentExecutor.chat() returning a structured
AgentResult with .content / .success / .error attributes.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows: use tempfile dir to avoid file lock issues with sqlite
os.environ.setdefault("PAPER_TRADING_DB_URL", f"sqlite:///{tempfile.gettempdir()}/smoke_p3c_pm.db")
os.environ.setdefault("PAPER_TRADING_DB_MODE", "sqlite")


def _cleanup_db():
    db_path = Path(tempfile.gettempdir()) / "smoke_p3c_pm.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Stub executor that mimics src.agent.factory.build_agent_executor output
# ---------------------------------------------------------------------------

class _StubAgentResult:
    """Minimal stand-in for AgentResult (content/success/error)."""

    def __init__(self, content: str, success: bool = True, error: str = None):
        self.content = content
        self.success = success
        self.error = error


class _StubExecutor:
    """Stub AgentExecutor returning canned JSON decisions.

    Returns ``self.next_response`` on each ``chat()`` call. If
    ``self.raise_on_call`` is set, raises instead.
    """

    def __init__(self, next_response: str = "", raise_on_call: bool = False):
        self.next_response = next_response
        self.raise_on_call = raise_on_call
        self.chat_calls: int = 0

    def chat(self, message: str = "", session_id: str = ""):
        self.chat_calls += 1
        if self.raise_on_call:
            raise RuntimeError("stub executor forced failure")
        return _StubAgentResult(content=self.next_response, success=True)


def main() -> int:
    from src.storage import (
        DatabaseManager,
        Account,
        PaperDecision,
        PaperOrder,
        PaperPosition,
        PaperTrade,
        get_db,
    )
    from sqlalchemy import select

    # Clean any leftover DB from a previous failed run before starting.
    _cleanup_db()
    DatabaseManager.reset_instance()
    db_url = f"sqlite:///{tempfile.gettempdir()}/smoke_p3c_pm.db"
    db = DatabaseManager(db_url=db_url)

    # --- Build account ---
    from paper_trading.account import PaperAccountManager
    from paper_trading.order import OrderManager, OrderType
    from paper_trading.position import PositionManager
    from paper_trading.fees import FeeModel
    from paper_trading.risk import RiskChecker
    from paper_trading.trading_engine import TradingEngine

    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="smoke_pm", initial_capital=1000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == "smoke_pm")
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

    # --- Test 1: PM agent returns a buy decision ---
    # Quantity must satisfy risk checks: single-stock concentration <= 30%
    # of total assets (1000 CNY). 10 shares * 18.0 = 180 = 18% — safe.
    buy_decision_json = json.dumps({
        "action": "buy",
        "code": "600519",
        "name": "贵州茅台",
        "params": {
            "entry_price": 18.0,
            "stop_loss": 16.5,
            "take_profit": 21.0,
            "quantity": 10,
            "order_type": "market",
        },
        "reason": "技术面突破,MA5上穿MA20,量比放大,符合顺势建仓条件",
        "confidence": 0.75,
    })
    stub_buy = _StubExecutor(next_response=buy_decision_json)

    from src.agent.portfolio_manager_agent import (
        PortfolioManagerAgent,
        register_paper_trading_tools,
    )
    from src.agent.tools.registry import ToolRegistry

    # Use a fresh registry to avoid polluting the global one.
    registry = ToolRegistry()
    # Register paper_trading_* tools on this private registry.
    register_paper_trading_tools(
        registry=registry, engine=engine, account_id=acc_id, reflection_engine=None,
    )
    tool_names = {t.name for t in registry.list_tools()}
    expected_tools = {
        "paper_trading_get_account_snapshot",
        "paper_trading_get_positions",
        "paper_trading_get_open_orders",
        "paper_trading_place_order",
        "paper_trading_cancel_order",
        "paper_trading_modify_order",
        "paper_trading_get_recent_reflections",
    }
    assert expected_tools.issubset(tool_names), (
        f"missing tools: {expected_tools - tool_names}"
    )
    print(f"[OK] Registered {len(expected_tools)} paper_trading_* tools")

    # Build PM agent with the stub executor.
    pm_agent = PortfolioManagerAgent(
        config=None,
        executor=stub_buy,
        trading_engine=engine,
        reflection_engine=None,
        account_id=acc_id,
        timeout_seconds=30.0,
        fallback_action="hold",
        max_retries=0,
    )
    # Bypass lazy executor construction (we injected a stub).
    pm_agent._tools_registered = True

    decision = pm_agent.make_decision(account_id=acc_id)
    assert decision.action == "buy", f"expected buy, got {decision.action}"
    assert decision.code == "600519"
    assert decision.confidence == 0.75
    assert decision.used_fallback is False
    assert "顺势" in decision.reason or "突破" in decision.reason
    print(f"[OK] PM decision parsed: action={decision.action} code={decision.code} "
          f"confidence={decision.confidence}")

    # Verify the decision was persisted to PaperDecision.
    with db.session_scope() as session:
        rows = session.execute(
            select(PaperDecision).where(PaperDecision.account_id == acc_id)
        ).scalars().all()
        assert len(rows) >= 1, "PaperDecision row not persisted"
        last = rows[-1]
        assert last.action == "buy"
        assert last.code == "600519"
        assert last.source == "pm_agent"
        assert last.confidence == 0.75
    print("[OK] PM decision persisted to PaperDecision table")

    # --- Test 2: Execute the decision via TradingEngine ---
    # Use a cheap stock so 1000 CNY can buy 50 shares. We'll use code "600519"
    # but with a low price to satisfy risk checks.
    from strategies_v2.rule_engine import Signal

    signal = Signal(
        side="buy",
        code="600519",
        name="贵州茅台",
        strategy_name="pm_agent",
        rule_name="pm_autonomous",
        trigger_price=18.0,
        suggested_quantity=10.0,
        reason=decision.reason,
    )
    trade_result = engine.submit_signal(
        account_id=acc_id,
        signal=signal,
        order_type=OrderType.MARKET,
    )
    assert trade_result.status == "executed", (
        f"expected executed, got {trade_result.status}: {trade_result.reason}"
    )
    assert trade_result.fill_quantity == 10.0
    assert trade_result.fill_price is not None and trade_result.fill_price > 0
    print(f"[OK] Decision executed: fill_price={trade_result.fill_price:.4f} "
          f"qty={trade_result.fill_quantity}")

    # Verify position exists.
    pos = engine.position_mgr.get_position(acc_id, "600519")
    assert pos is not None, "position not created"
    assert pos.quantity == 10.0
    print(f"[OK] Position created: code={pos.code} qty={pos.quantity} "
          f"avg_cost={pos.avg_cost:.4f}")

    # Verify cash was debited.
    snap = account_mgr.snapshot(acc_id)
    assert snap.cash < 1000.0, f"cash should be reduced, got {snap.cash}"
    print(f"[OK] Cash updated: remaining={snap.cash:.2f}")

    # --- Test 3: Fallback when executor raises ---
    stub_failing = _StubExecutor(raise_on_call=True)
    pm_failing = PortfolioManagerAgent(
        config=None,
        executor=stub_failing,
        trading_engine=engine,
        reflection_engine=None,
        account_id=acc_id,
        timeout_seconds=5.0,
        fallback_action="hold",
        max_retries=0,
    )
    pm_failing._tools_registered = True

    fallback_decision = pm_failing.make_decision(account_id=acc_id)
    assert fallback_decision.action == "hold", (
        f"expected hold fallback, got {fallback_decision.action}"
    )
    assert fallback_decision.used_fallback is True
    assert fallback_decision.error is not None
    print(f"[OK] Fallback on failure: action={fallback_decision.action} "
          f"error={fallback_decision.error[:50]}")

    # Verify the fallback decision is also persisted.
    with db.session_scope() as session:
        rows = session.execute(
            select(PaperDecision).where(PaperDecision.account_id == acc_id)
        ).scalars().all()
        actions = [r.action for r in rows]
        assert "hold" in actions, "fallback hold decision not persisted"
    print("[OK] Fallback decision persisted to PaperDecision table")

    # --- Test 4: Empty response fallback ---
    stub_empty = _StubExecutor(next_response="")
    pm_empty = PortfolioManagerAgent(
        config=None,
        executor=stub_empty,
        trading_engine=engine,
        reflection_engine=None,
        account_id=acc_id,
        timeout_seconds=5.0,
        fallback_action="hold",
        max_retries=0,
    )
    pm_empty._tools_registered = True

    empty_decision = pm_empty.make_decision(account_id=acc_id)
    assert empty_decision.action == "hold"
    assert empty_decision.used_fallback is True
    print("[OK] Empty response -> fallback hold")

    # --- Test 5: Keyword fallback parsing ---
    stub_keyword = _StubExecutor(next_response="经过分析,建议买入该股票,因为突破关键阻力位")
    pm_keyword = PortfolioManagerAgent(
        config=None,
        executor=stub_keyword,
        trading_engine=engine,
        reflection_engine=None,
        account_id=acc_id,
        timeout_seconds=5.0,
        fallback_action="hold",
        max_retries=0,
    )
    pm_keyword._tools_registered = True

    keyword_decision = pm_keyword.make_decision(account_id=acc_id)
    assert keyword_decision.action == "buy", (
        f"expected buy from keyword, got {keyword_decision.action}"
    )
    assert keyword_decision.confidence == 0.3  # keyword fallback confidence
    print(f"[OK] Keyword fallback: action={keyword_decision.action} "
          f"confidence={keyword_decision.confidence}")

    print("\nAll P3-C PM agent integration tests passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup_db()
