# -*- coding: utf-8 -*-
"""End-to-end integration test for the AI paper-trading loop.

Covers:
  PM Agent decision -> order placement -> simulated fill
  -> auto SLTP write -> reflection note generation
  -> memory injection influencing the next PM Agent decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel
from paper_trading.order import OrderManager, OrderType
from paper_trading.position import PositionManager
from paper_trading.reflection import ReflectionEngine
from paper_trading.risk import RiskChecker
from paper_trading.sltp_calculator import build_sltp_calculator
from paper_trading.trading_engine import TradingEngine
from src.agent.portfolio_manager_agent import PortfolioManagerAgent
from src.storage import DatabaseManager, PaperAccount, PaperDecision, PaperReflection, PaperTrade
from strategies_v2.rule_engine import Signal

from tests.conftest import StubDataProvider


class _StubAgentResult:
    def __init__(self, content: str = "", success: bool = True, error: Optional[str] = None):
        self.content = content
        self.success = success
        self.error = error


class _StubExecutor:
    """Returns a canned response on every chat() call."""

    def __init__(self, response: str):
        self.response = response
        self.chat_calls: list[tuple[str, str]] = []  # (message, session_id)

    def chat(self, message: str = "", session_id: str = ""):
        self.chat_calls.append((message, session_id))
        return _StubAgentResult(content=self.response, success=True)


class _PromptCaptureExecutor:
    """Wraps another executor and records the prompt for later assertions."""

    def __init__(self, inner: _StubExecutor):
        self.inner = inner
        self.captured_messages: list[str] = []

    def chat(self, message: str = "", session_id: str = ""):
        self.captured_messages.append(message)
        return self.inner.chat(message=message, session_id=session_id)


def _build_engine(db: DatabaseManager) -> tuple[TradingEngine, int]:
    """Create an account + engine wired to a temp DB with SLTP enabled."""
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="pytest_e2e", initial_capital=1000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(PaperAccount).where(PaperAccount.name == "pytest_e2e")
        ).scalar_one()
        acc_id = acc.id

    fee_model = FeeModel()
    pos_mgr = PositionManager(db)
    engine = TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=OrderManager(db),
        position_manager=pos_mgr,
        fee_model=fee_model,
        risk_checker=RiskChecker(
            db_manager=db,
            account_manager=account_mgr,
            position_manager=pos_mgr,
            fee_model=fee_model,
        ),
        sltp_calculator=build_sltp_calculator(data_provider=StubDataProvider(return_tuple=False)),
        enable_auto_sltp=True,
    )
    return engine, acc_id


@pytest.fixture
def engine_account(temp_db):
    return _build_engine(temp_db)


class TestPaperTradingE2E:
    """Full loop: PM decision -> order -> fill -> SLTP -> reflection -> memory."""

    def test_full_pm_decision_to_memory_loop(self, engine_account):
        engine, acc_id = engine_account
        db = engine.db

        # ------------------------------------------------------------------
        # 1. PM Agent makes a BUY decision.
        # ------------------------------------------------------------------
        buy_json = json.dumps(
            {
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
                "reason": "技术面突破，顺势建仓",
                "confidence": 0.75,
            }
        )
        pm_stub = _StubExecutor(response=buy_json)
        pm = PortfolioManagerAgent(
            config=None,
            executor=pm_stub,
            trading_engine=engine,
            reflection_engine=None,
            account_id=acc_id,
            timeout_seconds=5.0,
            fallback_action="hold",
            max_retries=0,
        )
        pm._tools_registered = True

        decision = pm.make_decision(account_id=acc_id)
        assert decision.action == "buy"
        assert decision.code == "600519"
        assert decision.params.get("quantity") == 10

        # Decision persisted.
        with db.session_scope() as session:
            rows = session.execute(
                select(PaperDecision).where(PaperDecision.account_id == acc_id)
            ).scalars().all()
            assert any(r.action == "buy" and r.code == "600519" for r in rows)

        # ------------------------------------------------------------------
        # 2. Submit signal -> market order -> immediate fill.
        # ------------------------------------------------------------------
        signal = Signal(
            side="buy",
            code="600519",
            name="贵州茅台",
            strategy_name="e2e_pm",
            rule_name="pm_buy_signal",
            trigger_price=decision.params.get("entry_price", 18.0),
            suggested_quantity=decision.params.get("quantity", 10.0),
            reason=decision.reason,
        )
        result = engine.submit_signal(
            account_id=acc_id,
            signal=signal,
            order_type=OrderType.MARKET,
        )
        assert result.status == "executed"
        assert result.fill_quantity == 10.0
        assert result.order_id is not None

        # Look up the trade row created by the fill.
        with db.session_scope() as session:
            trade_row = session.execute(
                select(PaperTrade).where(PaperTrade.order_id == result.order_id)
            ).scalar_one()
            trade_id = trade_row.id
        assert trade_id is not None

        # ------------------------------------------------------------------
        # 3. Auto SLTP written to the new position.
        # ------------------------------------------------------------------
        pos = engine.position_mgr.get_position(acc_id, "600519")
        assert pos is not None
        assert pos.quantity == 10.0
        assert pos.stop_loss is not None and pos.stop_loss > 0
        assert pos.stop_loss < result.fill_price
        assert pos.take_profit is not None and pos.take_profit > result.fill_price
        assert pos.take_profit_2 is not None and pos.take_profit_2 > pos.take_profit

        # ------------------------------------------------------------------
        # 4. Reflection on the trade generates a note.
        # ------------------------------------------------------------------
        distinctive_takeaway = "E2E_TEST: avoid chasing breakouts without volume confirmation"
        reflection_json = json.dumps(
            {
                "subject": "breakout chase review",
                "summary": "bought at breakout but volume was thin",
                "takeaway": distinctive_takeaway,
                "lessons": ["wait for volume spike", "tighten stop loss"],
                "tags": "e2e,test,breakout",
                "mood": "neutral",
            }
        )
        reflection_engine = ReflectionEngine(
            executor=_StubExecutor(response=reflection_json),
            trading_engine=engine,
            account_id=acc_id,
            db_manager=db,
            timeout_seconds=5.0,
            fallback_on_failure=True,
        )
        note = reflection_engine.reflect_on_trade(
            trade_id=trade_id,
            account_id=acc_id,
            decision_context=decision.reason,
        )
        assert note.takeaway == distinctive_takeaway
        assert note.row_id is not None

        with db.session_scope() as session:
            rows = session.execute(
                select(PaperReflection).where(PaperReflection.account_id == acc_id)
            ).scalars().all()
            assert any(r.takeaway == distinctive_takeaway for r in rows)

        # ------------------------------------------------------------------
        # 5. Next PM Agent decision receives the reflection memory.
        # ------------------------------------------------------------------
        hold_json = json.dumps(
            {
                "action": "hold",
                "code": None,
                "params": {},
                "reason": "awaiting clearer signal",
                "confidence": 0.5,
            }
        )
        inner_executor = _StubExecutor(response=hold_json)
        capture_executor = _PromptCaptureExecutor(inner=inner_executor)
        pm2 = PortfolioManagerAgent(
            config=None,
            executor=capture_executor,
            trading_engine=engine,
            reflection_engine=reflection_engine,
            account_id=acc_id,
            timeout_seconds=5.0,
            fallback_action="hold",
            max_retries=0,
        )
        pm2._tools_registered = True

        decision2 = pm2.make_decision(account_id=acc_id)
        assert decision2.action == "hold"

        # The prompt sent to the agent must contain the injected reflection.
        assert capture_executor.captured_messages
        prompt = capture_executor.captured_messages[0]
        assert distinctive_takeaway in prompt
