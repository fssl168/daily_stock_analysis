# -*- coding: utf-8 -*-
"""Order Management System — extracted from TradingEngine.submit_signal (T18 Step B).

Encapsulates order creation, market execution, and limit-order handling
that was previously inlined in TradingEngine.submit_signal and
_execute_market_order.  TradingEngine now delegates to OMS instead of
duplicating the same logic.

来源: docs/architecture/realtime_quant_system_design.md §3.3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from paper_trading.order import OrderRequest, OrderSide, OrderType, OrderManager
from paper_trading.position import PositionManager
from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel

logger = logging.getLogger(__name__)


@dataclass
class OrderParams:
    """Aggregated order-creation parameters extracted from submit_signal inline logic."""
    account_id: int
    code: str
    side: str
    quantity: float
    order_type: OrderType
    limit_price: Optional[float]
    ref_price: float
    signal_id: int
    signal: Any  # Signal object (for name/strategy_name/reason)
    risk_decisions: List[Dict[str, Any]] = field(default_factory=list)
    agent_review: Optional[Dict[str, Any]] = None
    order_id: Optional[int] = None  # Set after create_order before execute_market


class OrderManagementSystem:
    """Order lifecycle: create, execute market, handle limit orders.

    All instance managers are injected at construction time so OMS, RMS,
    and Engine share the same account/position/order instances.
    """

    def __init__(
        self,
        order_mgr: OrderManager,
        account_mgr: PaperAccountManager,
        position_mgr: PositionManager,
        fee_model: FeeModel,
        broker_router: Optional[Any] = None,  # T-020: real-broker routing
    ):
        self.order_mgr = order_mgr
        self.account_mgr = account_mgr
        self.position_mgr = position_mgr
        self.fee_model = fee_model
        self.broker_router = broker_router

    # ------------------------------------------------------------------
    # Order creation
    # ------------------------------------------------------------------

    def create_order(self, params: OrderParams) -> TradeResult:
        """Create an order from signal parameters (extracted from submit_signal).

        Returns a TradeResult with the newly created order (status='pending').
        The caller must then either execute (market) or freeze (limit).
        """
        from paper_trading.trading_engine import TradeResult

        order_req = OrderRequest(
            account_id=params.account_id,
            code=params.code,
            side=OrderSide(params.side),
            quantity=params.quantity,
            order_type=params.order_type,
            price=params.limit_price if params.order_type == OrderType.LIMIT else None,
            name=getattr(params.signal, "name", None),
            strategy_name=getattr(params.signal, "strategy_name", ""),
            signal_id=params.signal_id,
            reason=getattr(params.signal, "reason", ""),
        )
        order = self.order_mgr.create_order(order_req)
        return TradeResult(
            signal_id=params.signal_id,
            order_id=order.id,
            side=params.side,
            code=params.code,
            status="pending",
            fill_price=None,
            fill_quantity=None,
            fee=None,
            reason="order created",
            risk_decisions=params.risk_decisions,
            agent_review=params.agent_review,
        )

    # ------------------------------------------------------------------
    # Market order execution
    # ------------------------------------------------------------------

    def execute_market(
        self,
        order_id: int,
        params: OrderParams,
    ) -> TradeResult:
        """Fill a market order at slippage-adjusted price and settle.

        Extracted from TradingEngine._execute_market_order.  Does NOT
        update signal status — the caller (TradingEngine) handles that.
        """
        from paper_trading.trading_engine import TradeResult

        side = params.side
        code = params.code
        ref_price = params.ref_price
        quantity = params.quantity
        eff_price = self.fee_model.apply_slippage(ref_price, side)
        fee = self.fee_model.compute_fee(side, eff_price, quantity)

        try:
            if side == "buy":
                estimated_cost = self.fee_model.estimate_buy_cost(ref_price, quantity)
                actual_cost = self.fee_model.estimate_buy_cost(eff_price, quantity)
                freeze_amount = max(estimated_cost, actual_cost)
                self.account_mgr.freeze_cash(params.account_id, freeze_amount)
                # T-006: optimistic-lock fill — get order version for concurrency safety.
                order = self.order_mgr.get_order(order_id)
                trade = self.order_mgr.fill_order(
                    order_id, eff_price, quantity, fee=fee,
                    expected_version=order.version if order else None,
                )
                if trade is None:
                    return TradeResult(
                        signal_id=params.signal_id, order_id=order_id,
                        side=side, code=code, status="retry",
                        reason="version conflict; retry",
                    )
                self.account_mgr.settle_buy(params.account_id, freeze_amount, actual_cost)
                self.position_mgr.apply_buy(
                    params.account_id, code, quantity, eff_price,
                    name=getattr(params.signal, "name", None),
                )
            else:
                realized_pnl = self.position_mgr.apply_sell(
                    params.account_id, code, quantity, eff_price,
                )
                order = self.order_mgr.get_order(order_id)
                trade = self.order_mgr.fill_order(
                    order_id, eff_price, quantity, fee=fee,
                    expected_version=order.version if order else None,
                )
                if trade is None:
                    return TradeResult(
                        signal_id=params.signal_id, order_id=order_id,
                        side=side, code=code, status="retry",
                        reason="version conflict; retry",
                    )
                self.account_mgr.settle_sell(params.account_id, eff_price, quantity, fee)

            return TradeResult(
                signal_id=params.signal_id,
                order_id=order_id,
                side=side,
                code=code,
                status="filled",
                fill_price=eff_price,
                fill_quantity=quantity,
                fee=fee,
                reason="market order filled",
                risk_decisions=params.risk_decisions,
                agent_review=params.agent_review,
            )
        except Exception as exc:
            logger.error("Market order failed: id=%s code=%s: %s", order_id, code, exc)
            return TradeResult(
                signal_id=params.signal_id,
                order_id=order_id,
                side=side,
                code=code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=str(exc),
                risk_decisions=params.risk_decisions,
                agent_review=params.agent_review,
            )

    # ------------------------------------------------------------------
    # Limit order handling
    # ------------------------------------------------------------------

    def handle_limit(
        self,
        order_id: int,
        params: OrderParams,
    ) -> Optional[TradeResult]:
        """Freeze cash for limit buys; sell-limit orders pass through.

        Returns a rejected TradeResult if the freeze fails, or None
        indicating the limit order is pending (caller drives matching).
        """
        from paper_trading.trading_engine import TradeResult

        if params.side == "buy":
            eff_price = params.limit_price or params.ref_price
            estimated_cost = self.fee_model.estimate_buy_cost(eff_price, params.quantity)
            try:
                self.account_mgr.freeze_cash(params.account_id, estimated_cost)
            except ValueError as exc:
                self.order_mgr.reject_order(order_id, reason=f"freeze failed: {exc}")
                return TradeResult(
                    signal_id=params.signal_id,
                    order_id=order_id,
                    side=params.side,
                    code=params.code,
                    status="rejected",
                    fill_price=None,
                    fill_quantity=None,
                    fee=None,
                    reason=f"freeze failed: {exc}",
                    risk_decisions=params.risk_decisions,
                    agent_review=params.agent_review,
                )
        return None  # limit order pending; caller invokes matching
