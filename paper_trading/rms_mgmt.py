# -*- coding: utf-8 -*-
"""Risk Management System — pre-trade risk checks extracted from TradingEngine (T18 Step A).

Encapsulates:
1. RiskChecker (existing paper_trading/risk.py) — capital/position checks
2. AgentRiskReviewer (existing paper_trading/agent_risk.py) — LLM veto layer
3. Signal quantity resolution (extracted from submit_signal's quantity block)

TradingEngine.submit_signal now delegates to RMS pre_trade_check and agent_review
instead of inlining all the risk logic inline.  This is Step A of the 3-step
TradingEngine decomposition (A=RMS, B=OMS, C=thin engine).

来源: docs/architecture/realtime_quant_system_design.md §2.3 / §3.3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from paper_trading.order import OrderType
from paper_trading.risk import RiskChecker, RiskDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RMS result types
# ---------------------------------------------------------------------------

@dataclass
class RiskCheckResult:
    """Aggregated pre-trade risk check result.

    Mirrors the inline decision aggregation that was in TradingEngine.submit_signal.
    """
    passed: bool
    reason: str = ""
    risk_decisions: List[Dict[str, Any]] = field(default_factory=list)
    overall: Optional[RiskDecision] = None


@dataclass
class QuantityResult:
    """Resolved order quantity (mirrors the inline logic)."""
    quantity: float
    signal_id: Optional[int] = None
    error_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# RiskManagementSystem
# ---------------------------------------------------------------------------

class RiskManagementSystem:
    """Pre-trade risk checks: RiskChecker + optional AgentRiskReviewer + CircuitBreaker.

    Extracted from TradingEngine.submit_signal; the engine now delegates to these
    methods instead of duplicating the same logic inline.  All instance managers
    are injected at construction time so OMS and Engine can share the same
    account/position/risk instances.
    """

    def __init__(
        self,
        risk_checker: RiskChecker,
        account_manager: Any = None,
        position_manager: Any = None,
        agent_reviewer: Any = None,
    ):
        self.risk = risk_checker
        self.account_mgr = account_manager
        self.position_mgr = position_manager
        self.agent_reviewer = agent_reviewer

    # ------------------------------------------------------------------
    # Quantity resolution
    # ------------------------------------------------------------------

    def resolve_quantity(
        self,
        account_id: int,
        code: str,
        side: str,
        suggested_quantity: Optional[float],
        quantity_override: Optional[float],
    ) -> QuantityResult:
        """Resolve the order quantity (extracted from submit_signal inline logic).

        Returns a QuantityResult; if error_reason is set the caller should reject.
        """
        quantity = (
            float(quantity_override)
            if quantity_override is not None
            else float(suggested_quantity or 0.0)
        )
        if side == "sell" and quantity <= 0:
            # Default sell quantity = entire available position.
            if self.position_mgr is not None:
                pos = self.position_mgr.get_position(account_id, code)
                quantity = float(pos.available_quantity) if pos is not None else 0.0
            if quantity <= 0:
                return QuantityResult(quantity=0.0, error_reason="no available quantity to sell")
        return QuantityResult(quantity=quantity)

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------

    def pre_trade_check(
        self,
        account_id: int,
        code: str,
        price: float,
        quantity: float,
        side: str,
        risk_mandated: bool = False,
    ) -> RiskCheckResult:
        """Run RiskChecker buy/sell checks and aggregate into a single result.

        ``risk_mandated`` (T-09): True for risk-forced exits (stop-loss /
        liquidation). Such signals skip protection-only sell checks (e.g. the
        daily-loss limit) so a deep-unwater position can still be closed.

        Returns a RiskCheckResult whose ``passed`` field indicates whether the
        trade should proceed.
        """
        if side == "buy":
            decisions = self.risk.check_buy(account_id, code, price, quantity)
        else:
            decisions = self.risk.check_sell(
                account_id, code, price, quantity,
                skip_daily_loss=risk_mandated,
            )
        overall = self.risk.evaluate(decisions)
        return RiskCheckResult(
            passed=overall.passed,
            reason=overall.reason if not overall.passed else "",
            risk_decisions=[d.to_dict() for d in decisions],
            overall=overall,
        )

    # ------------------------------------------------------------------
    # Agent review (optional LLM veto)
    # ------------------------------------------------------------------

    def agent_review(
        self,
        account_id: int,
        signal: Any,
    ) -> Optional[Dict[str, Any]]:
        """Ask the AgentRiskReviewer to confirm or veto a signal.

        Returns a dict suitable for persistence (or None if reviewer not configured).
        On failure or veto the dict includes ``approved`` and ``reason`` fields.
        """
        if self.agent_reviewer is None:
            return None

        try:
            account_snap = self.account_mgr.snapshot(account_id) if self.account_mgr else None
            position_row = (
                self.position_mgr.get_position(account_id, signal.code)
                if self.position_mgr
                else None
            )
            verdict = self.agent_reviewer.review_signal(
                signal=signal,
                account_snapshot=account_snap,
                position=position_row,
            )
            return verdict.to_dict()
        except Exception as exc:
            logger.error(
                "Agent review raised for signal=%s code=%s: %s",
                getattr(signal, "side", "?"), getattr(signal, "code", "?"), exc,
                exc_info=True,
            )
            return {
                "approved": False,
                "reason": f"agent review raised: {exc}",
                "used_fallback": True,
                "error": str(exc),
            }
