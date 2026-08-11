# -*- coding: utf-8 -*-
"""Unit tests for paper_trading/rms_mgmt.py (T18-A)."""

from paper_trading.rms_mgmt import RiskCheckResult, QuantityResult, RiskManagementSystem


class FakeRisk:
    """RiskChecker stub that always passes."""

    def check_buy(self, *a, **kw):
        from paper_trading.risk import RiskDecision
        return [RiskDecision(passed=True, check_name="buy", reason="ok")]

    def check_sell(self, *a, **kw):
        from paper_trading.risk import RiskDecision
        return [RiskDecision(passed=True, check_name="sell", reason="ok")]

    def evaluate(self, decisions):
        return decisions[0]


class FakeRejectingRisk(FakeRisk):
    """RiskChecker that always fails."""

    def evaluate(self, decisions):
        from paper_trading.risk import RiskDecision
        return RiskDecision(passed=False, check_name="reject", reason="too risky")


class FakePositionMgr:
    def get_position(self, aid, code):
        from paper_trading.position import PaperPosition
        return PaperPosition(account_id=aid, code=code, quantity=100, avg_cost=10.0,
                            available_quantity=50)


def test_pre_trade_check_passes():
    rms = RiskManagementSystem(risk_checker=FakeRisk())
    result = rms.pre_trade_check(1, "000001", 10.0, 100.0, "buy")
    assert result.passed
    assert len(result.risk_decisions) == 1


def test_pre_trade_check_fails():
    rms = RiskManagementSystem(risk_checker=FakeRejectingRisk())
    result = rms.pre_trade_check(1, "000001", 10.0, 100.0, "buy")
    assert not result.passed
    assert "too risky" in result.reason


def test_resolve_quantity_override():
    rms = RiskManagementSystem(risk_checker=FakeRisk())
    q = rms.resolve_quantity(1, "X", "buy", 100.0, 200.0)
    assert q.quantity == 200.0
    assert q.error_reason is None


def test_resolve_quantity_sell_defaults_to_position():
    rms = RiskManagementSystem(risk_checker=FakeRisk(), position_manager=FakePositionMgr())
    q = rms.resolve_quantity(1, "X", "sell", None, None)
    assert q.quantity == 50.0
    assert q.error_reason is None


def test_resolve_quantity_sell_no_position_errors():
    rms = RiskManagementSystem(risk_checker=FakeRisk())
    q = rms.resolve_quantity(1, "X", "sell", None, None)
    assert q.quantity == 0.0
    assert q.error_reason == "no available quantity to sell"


def test_agent_review_none_when_no_reviewer():
    rms = RiskManagementSystem(risk_checker=FakeRisk())
    result = rms.agent_review(1, signal=None)
    assert result is None


def test_pre_trade_check_sell():
    rms = RiskManagementSystem(risk_checker=FakeRisk())
    result = rms.pre_trade_check(1, "000001", 10.0, 100.0, "sell")
    assert result.passed
