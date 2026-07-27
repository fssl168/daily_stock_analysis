# -*- coding: utf-8 -*-
"""pytest tests for P0-B Portfolio Manager agent closed loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel
from paper_trading.order import OrderManager, OrderType
from paper_trading.position import PositionManager
from paper_trading.risk import RiskChecker
from paper_trading.trading_engine import TradingEngine
from src.agent.portfolio_manager_agent import (
    PortfolioManagerAgent,
    register_paper_trading_tools,
)
from src.agent.tools.registry import ToolRegistry
from src.storage import DatabaseManager, PaperAccount, PaperDecision
from strategies_v2.rule_engine import Signal


class _StubAgentResult:
    def __init__(self, content: str, success: bool = True, error: str = None):
        self.content = content
        self.success = success
        self.error = error


class _StubExecutor:
    """Returns canned responses; can be configured to raise."""

    def __init__(self, next_response: str = "", raise_on_call: bool = False):
        self.next_response = next_response
        self.raise_on_call = raise_on_call
        self.chat_calls: int = 0

    def chat(self, message: str = "", session_id: str = ""):
        self.chat_calls += 1
        if self.raise_on_call:
            raise RuntimeError("stub executor forced failure")
        return _StubAgentResult(content=self.next_response, success=True)


def _build_engine(db: DatabaseManager) -> tuple[TradingEngine, int]:
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(name="pytest_pm", initial_capital=1000.0)
    with db.session_scope() as session:
        acc = session.execute(
            select(PaperAccount).where(PaperAccount.name == "pytest_pm")
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
    )
    return engine, acc_id


@pytest.fixture
def engine_account(temp_db):
    return _build_engine(temp_db)


class TestPMAgentDecisionParsing:
    """make_decision parses executor output and persists decisions."""

    def test_buy_decision_is_parsed_and_persisted(self, engine_account):
        engine, acc_id = engine_account
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
        stub = _StubExecutor(next_response=buy_json)
        pm = PortfolioManagerAgent(
            config=None,
            executor=stub,
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
        assert decision.confidence == 0.75
        assert decision.used_fallback is False

        with engine.db.session_scope() as session:
            rows = session.execute(
                select(PaperDecision).where(PaperDecision.account_id == acc_id)
            ).scalars().all()
            assert any(r.action == "buy" and r.code == "600519" for r in rows)

    def test_executor_failure_falls_back_to_hold(self, engine_account):
        engine, acc_id = engine_account
        stub = _StubExecutor(raise_on_call=True)
        pm = PortfolioManagerAgent(
            config=None,
            executor=stub,
            trading_engine=engine,
            reflection_engine=None,
            account_id=acc_id,
            timeout_seconds=2.0,
            fallback_action="hold",
            max_retries=0,
        )
        pm._tools_registered = True

        decision = pm.make_decision(account_id=acc_id)
        assert decision.action == "hold"
        assert decision.used_fallback is True
        assert decision.error is not None

        with engine.db.session_scope() as session:
            rows = session.execute(
                select(PaperDecision).where(PaperDecision.account_id == acc_id)
            ).scalars().all()
            assert any(r.action == "hold" for r in rows)

    def test_empty_response_falls_back(self, engine_account):
        engine, acc_id = engine_account
        stub = _StubExecutor(next_response="")
        pm = PortfolioManagerAgent(
            config=None,
            executor=stub,
            trading_engine=engine,
            reflection_engine=None,
            account_id=acc_id,
            timeout_seconds=2.0,
            fallback_action="hold",
            max_retries=0,
        )
        pm._tools_registered = True
        decision = pm.make_decision(account_id=acc_id)
        assert decision.action == "hold"
        assert decision.used_fallback is True

    def test_keyword_fallback_detects_buy(self, engine_account):
        engine, acc_id = engine_account
        stub = _StubExecutor(next_response="经过分析，建议买入该股票，因为突破关键阻力位")
        pm = PortfolioManagerAgent(
            config=None,
            executor=stub,
            trading_engine=engine,
            reflection_engine=None,
            account_id=acc_id,
            timeout_seconds=2.0,
            fallback_action="hold",
            max_retries=0,
        )
        pm._tools_registered = True
        decision = pm.make_decision(account_id=acc_id)
        assert decision.action == "buy"
        assert decision.confidence == 0.3


class TestPMAgentToolRegistration:
    """paper_trading_* tools are registered and delegate correctly."""

    def test_all_expected_tools_registered(self, engine_account):
        engine, acc_id = engine_account
        registry = ToolRegistry()
        register_paper_trading_tools(
            registry=registry,
            engine=engine,
            account_id=acc_id,
            reflection_engine=None,
        )
        tool_names = {t.name for t in registry.list_tools()}
        expected = {
            "paper_trading_get_account_snapshot",
            "paper_trading_get_positions",
            "paper_trading_get_open_orders",
            "paper_trading_place_order",
            "paper_trading_cancel_order",
            "paper_trading_modify_order",
            "paper_trading_get_recent_reflections",
            "paper_trading_compute_sltp",
        }
        assert expected.issubset(tool_names)

    def test_place_order_tool_executes_buy(self, engine_account):
        engine, acc_id = engine_account
        registry = ToolRegistry()
        register_paper_trading_tools(
            registry=registry, engine=engine, account_id=acc_id, reflection_engine=None,
        )
        place_tool = next(
            t for t in registry.list_tools() if t.name == "paper_trading_place_order"
        )
        result = place_tool.handler(
            code="600519",
            side="buy",
            quantity=10.0,
            order_type="market",
            name="贵州茅台",
            entry_price=18.0,
        )
        assert result.get("status") == "executed"
        pos = engine.position_mgr.get_position(acc_id, "600519")
        assert pos is not None
        assert pos.quantity == 10.0

    def test_sltp_tool_returns_three_lines(self, engine_account):
        engine, acc_id = engine_account
        registry = ToolRegistry()
        register_paper_trading_tools(
            registry=registry, engine=engine, account_id=acc_id, reflection_engine=None,
        )
        sltp_tool = next(
            t for t in registry.list_tools() if t.name == "paper_trading_compute_sltp"
        )
        result = sltp_tool.handler(code="000001", entry_price=12.0)
        assert "stop_loss" in result
        assert "take_profit_1" in result
        assert "take_profit_2" in result
        assert result["stop_loss"] < result["entry_price"]


class TestPMAgentDecisionExecution:
    """Decision -> Signal -> TradingEngine execution."""

    def test_decision_can_be_submitted_as_signal(self, engine_account):
        engine, acc_id = engine_account
        buy_json = json.dumps(
            {
                "action": "buy",
                "code": "600519",
                "name": "贵州茅台",
                "params": {
                    "entry_price": 18.0,
                    "quantity": 10,
                    "order_type": "market",
                },
                "reason": "技术面突破",
                "confidence": 0.75,
            }
        )
        stub = _StubExecutor(next_response=buy_json)
        pm = PortfolioManagerAgent(
            config=None,
            executor=stub,
            trading_engine=engine,
            reflection_engine=None,
            account_id=acc_id,
            timeout_seconds=5.0,
            fallback_action="hold",
            max_retries=0,
        )
        pm._tools_registered = True
        decision = pm.make_decision(account_id=acc_id)

        signal = Signal(
            side="buy",
            code=decision.code,
            name=decision.name,
            strategy_name="pm_agent",
            rule_name="pm_autonomous",
            trigger_price=decision.params.get("entry_price", 0.0),
            suggested_quantity=decision.params.get("quantity", 0.0),
            reason=decision.reason,
        )
        result = engine.submit_signal(
            account_id=acc_id, signal=signal, order_type=OrderType.MARKET
        )
        assert result.status == "executed"
        assert result.fill_quantity == 10.0
