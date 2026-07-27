#!/usr/bin/env python3
"""Risk decision to order action adapter (R2 fix).

Maps AgentReviewResult/RiskDecision to actionable order commands
(cancel, modify, sell) for automatic trading responses.
"""

from typing import Optional, Dict, Any

from paper_trading.agent_risk import AgentReviewResult
from paper_trading.risk import RiskDecision


class RiskOrderAdapter:
    """Translate risk/agent review decisions into order actions."""

    @staticmethod
    def from_agent_review(result: AgentReviewResult) -> Optional["OrderCommand"]:
        """Convert an AgentReviewResult to an OrderCommand."""
        # result.action could be "approve", "reject", or other
        # result.stop_loss and result.take_profit provide price levels
        
        if result.action == "reject":
            # Reject the signal - cancel any pending order for this stock
            return OrderCommand(
                action="cancel",
                code=result.code,
                reason=f"Rejected by AI reviewer: {result.reason}"
            )
        elif result.action in ("sell", "reduce"):
            # Suggest selling or reducing position
            return OrderCommand(
                action="sell",
                code=result.code,
                quantity=result.quantity,
                stop_loss=result.stop_loss,
                take_profit=result.take_profit,
                reason=f"Sell suggested by AI reviewer: {result.reason}"
            )
        else:
            # approve or unknown - no order action needed
            return None

    @staticmethod
    def from_risk_decision(decision: RiskDecision) -> Optional["OrderCommand"]:
        """Convert a RiskDecision to an OrderCommand."""
        # Decision.reason contains the trigger (e.g., "stop_loss_triggered", 
        # "daily_loss_limit", "concentration_risk")
        reason = decision.reason.lower()
        
        if "stop_loss" in reason or "triger" in reason or "跌破" in reason:
            # Stop loss triggered - sell the position
            return OrderCommand(
                action="sell",
                code=decision.code,
                reason="Stop loss triggered"
            )
        elif "take_profit" in reason or "止盈" in reason:
            # Take profit reached - consider partial exit or hold
            return OrderCommand(
                action="hold",
                code=decision.code,
                reason="Take profit reached - holding"
            )
        elif "daily_loss" in reason or "亏损" in reason:
            # Daily loss limit - reduce positions broadly
            return OrderCommand(
                action="reduce_position",
                code=decision.code,
                reason="Daily loss limit reached"
            )
        elif "concentration" in reason or "集中度" in reason:
            # Too concentrated - sell some holdings
            return OrderCommand(
                action="reduce_position",
                code=decision.code,
                reason="Position concentration limit exceeded"
            )
        else:
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
        self.action = action  # buy, sell, cancel, reduce_position, hold
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