"""Tests for paper_trading.risk_order_adapter."""
import types
from paper_trading.risk_order_adapter import RiskOrderAdapter, OrderCommand


def _make_review(approved=True, action="approve", code=None, reason="",
                 stop_loss=None, take_profit=None, quantity=None):
    return types.SimpleNamespace(
        approved=approved, action=action, code=code, reason=reason,
        stop_loss=stop_loss, take_profit=take_profit, quantity=quantity,
    )


def _make_decision(passed=True, check_name="", reason="", code=None):
    return types.SimpleNamespace(
        passed=passed, check_name=check_name, reason=reason, code=code,
    )


def _make_pmdecision(action="hold", code=None, params=None, reason=""):
    return types.SimpleNamespace(
        action=action, code=code, params=params or {}, reason=reason,
    )


def test_from_agent_review_approve_returns_none():
    result = _make_review(action="approve")
    assert RiskOrderAdapter.from_agent_review(result) is None


def test_from_agent_review_reject_returns_cancel():
    result = _make_review(action="reject", code="600519", reason="too risky")
    cmd = RiskOrderAdapter.from_agent_review(result)
    assert cmd is not None
    assert cmd.action == "cancel"
    assert cmd.code == "600519"


def test_from_agent_review_sell_returns_sell_command():
    result = _make_review(action="sell", code="600519", stop_loss=1800.0, take_profit=2000.0, quantity=100)
    cmd = RiskOrderAdapter.from_agent_review(result)
    assert cmd is not None
    assert cmd.action == "sell"
    assert cmd.stop_loss == 1800.0
    assert cmd.take_profit == 2000.0


def test_from_agent_review_modify_returns_modify_command():
    result = _make_review(action="modify", code="600519", stop_loss=1850.0)
    cmd = RiskOrderAdapter.from_agent_review(result)
    assert cmd is not None
    assert cmd.action == "modify"
    assert cmd.stop_loss == 1850.0


def test_from_agent_review_hold_returns_none():
    result = _make_review(action="hold")
    assert RiskOrderAdapter.from_agent_review(result) is None


def test_from_risk_decision_stop_loss_keyword():
    decision = _make_decision(reason="stop_loss triggered 跌破支撑")
    cmd = RiskOrderAdapter.from_risk_decision(decision, code="600519")
    assert cmd is not None
    assert cmd.action == "sell"
    assert cmd.code == "600519"


def test_from_risk_decision_take_profit_keyword():
    decision = _make_decision(reason="take_profit 止盈触发")
    cmd = RiskOrderAdapter.from_risk_decision(decision, code="600519")
    assert cmd is not None
    assert cmd.action == "hold"


def test_from_pmdecision_buy():
    decision = _make_pmdecision(action="buy", code="600519", params={"quantity": 100, "stop_loss": 1800.0})
    cmd = RiskOrderAdapter.from_pmdecision(decision)
    assert cmd is not None
    assert cmd.action == "buy"
    assert cmd.code == "600519"


def test_from_pmdecision_hold_returns_none():
    decision = _make_pmdecision(action="hold")
    assert RiskOrderAdapter.from_pmdecision(decision) is None


def test_order_command_to_dict():
    cmd = OrderCommand(action="sell", code="600519", quantity=100, stop_loss=1800.0, take_profit=2000.0, reason="test")
    d = cmd.to_dict()
    assert d["action"] == "sell"
    assert d["code"] == "600519"
    assert d["quantity"] == 100
    assert d["stop_loss"] == 1800.0
    assert d["take_profit"] == 2000.0
    assert d["reason"] == "test"
