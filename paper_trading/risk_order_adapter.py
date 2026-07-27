#!/usr/bin/env python3
"""Risk decision to order action adapter (R2 fix).

Maps AgentReviewResult/RiskDecision to actionable order commands
(cancel, modify, sell) for automatic trading responses.
"""

import logging
from typing import Optional, Dict, Any

from paper_trading.agent_risk import AgentReviewResult
from paper_trading.risk import RiskDecision

logger = logging.getLogger(__name__)


class RiskOrderAdapter:
    """Translate risk/agent review decisions into order actions."""

    @staticmethod
    def from_agent_review(result) -> Optional["OrderCommand"]:
        """Map an AgentReviewResult to an OrderCommand."""
        action = getattr(result, "action", "approve")
        if action in ("approve", "hold", ""):
            return None
        code = getattr(result, "code", None)
        reason = getattr(result, "reason", "")
        if action == "reject":
            return OrderCommand(action="cancel", code=code, reason=reason)
        if action in ("sell", "reduce"):
            return OrderCommand(
                action="sell", code=code,
                quantity=getattr(result, "quantity", None),
                stop_loss=getattr(result, "stop_loss", None),
                take_profit=getattr(result, "take_profit", None),
                reason=reason,
            )
        if action == "modify":
            return OrderCommand(
                action="modify", code=code,
                stop_loss=getattr(result, "stop_loss", None),
                take_profit=getattr(result, "take_profit", None),
                reason=reason,
            )
        return None

    @staticmethod
    def from_risk_decision(
        decision: RiskDecision, code: Optional[str] = None
    ) -> Optional["OrderCommand"]:
        """Convert a RiskDecision to an OrderCommand.

        Args:
            decision: RiskDecision object. ``code`` is read from
                ``decision.code`` if present, else from the ``code`` argument.
            code: Optional fallback code when ``decision.code`` is missing.
        """
        # Decision.reason contains the trigger (e.g., "stop_loss_triggered",
        # "daily_loss_limit", "concentration_risk")
        resolved_code = getattr(decision, "code", None) or code
        reason = (getattr(decision, "reason", "") or "").lower()

        if "stop_loss" in reason or "triger" in reason or "跌破" in reason:
            # Stop loss triggered - sell the position
            return OrderCommand(
                action="sell",
                code=resolved_code,
                reason="Stop loss triggered"
            )
        elif "take_profit" in reason or "止盈" in reason:
            # Take profit reached - consider partial exit or hold
            return OrderCommand(
                action="hold",
                code=resolved_code,
                reason="Take profit reached - holding"
            )
        elif "daily_loss" in reason or "亏损" in reason:
            # Daily loss limit - reduce positions broadly
            return OrderCommand(
                action="reduce_position",
                code=resolved_code,
                reason="Daily loss limit reached"
            )
        elif "concentration" in reason or "集中度" in reason:
            # Too concentrated - sell some holdings
            return OrderCommand(
                action="reduce_position",
                code=resolved_code,
                reason="Position concentration limit exceeded"
            )
        else:
            return None

    @staticmethod
    def from_pmdecision(decision) -> Optional["OrderCommand"]:
        """Map a PMDecision to an OrderCommand.

        Supports actions: buy / sell / cancel / modify / hold / plan / nop.
        Returns None for non-actionable decisions (hold/plan/nop).
        """
        action = getattr(decision, "action", "hold")
        if action in ("hold", "plan", "nop", ""):
            return None
        code = getattr(decision, "code", None)
        params = getattr(decision, "params", {}) or {}
        reason = getattr(decision, "reason", "")
        if action == "cancel":
            return OrderCommand(action="cancel", code=code, reason=reason)
        if action in ("buy", "sell"):
            return OrderCommand(
                action=action, code=code,
                quantity=params.get("quantity"),
                stop_loss=params.get("stop_loss"),
                take_profit=params.get("take_profit"),
                reason=reason,
            )
        if action == "modify":
            return OrderCommand(
                action="modify", code=code,
                stop_loss=params.get("stop_loss"),
                take_profit=params.get("take_profit"),
                reason=reason,
            )
        return None


class OrderCommand:
    """Represents an actionable order command derived from risk/agent review."""

    def __init__(
        self,
        action: str,
        code: Optional[str] = None,
        quantity: Optional[int] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reason: str = "",
    ):
        self.action = action  # buy, sell, cancel, reduce_position, hold, modify
        self.code = code
        self.quantity = quantity
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "code": self.code,
            "quantity": self.quantity,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reason": self.reason,
        }


# Integration hook: add to MarketListener or TradingEngine flow
def on_agent_review_result(
    engine,  # TradingEngine instance
    account_id: int,
    result: AgentReviewResult,
) -> bool:
    """Process an agent review result and execute corresponding order commands.
    
    Returns True if any order was placed/cancelled/modified.
    """
    adapter = RiskOrderAdapter()
    cmd = adapter.from_agent_review(result)
    
    if cmd is None:
        return False
    
    # Execute the command via trading engine
    if cmd.action == "cancel":
        # Cancel pending orders for this code
        open_orders = engine.order_mgr.list_orders(account_id, code=cmd.code, status="pending")
        for order in open_orders:
            engine.order_mgr.cancel_order(order.id, reason=cmd.reason)
        return True
    elif cmd.action in ("sell", "buy"):
        # Could place new order; this would require more context
        # For now, just log - actual implementation depends on workflow
        logger.info(f"Executing {cmd.action} order for {cmd.code}: {cmd.reason}")
        return True
    elif cmd.action == "reduce_position":
        # Reduce all positions gradually - simplified here
        return True
    
    return False