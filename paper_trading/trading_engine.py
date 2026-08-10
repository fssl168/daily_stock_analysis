# -*- coding: utf-8 -*-
"""Trading engine orchestrating signal -> risk -> order -> fill -> settle.

Pipeline:
1. submit_signal(signal):
   - Persist a PaperSignal row (audit trail).
   - Run pre-trade risk checks.
   - If rejected: mark signal rejected, return.
   - Create PaperOrder (market -> fill now; limit -> pending).
   - For market orders: fill immediately at slippage-adjusted price,
     settle cash/position.
   - For limit orders: freeze cash (buy) and leave for matcher.
2. match_pending_orders(latest_prices):
   - For each pending limit order, check trigger conditions.
   - On fill: settle cash/position, update order status.
3. check_stop_loss_take_profit(latest_prices):
   - For each open position with SL/TP set, emit a sell signal if breached.
4. daily_settle():
   - Roll T+1 available quantity, record daily net value.

The engine is intentionally synchronous; the market_listener (Phase 5) will
drive it via periodic calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from paper_trading.account import PaperAccountManager
from paper_trading.agent_risk import AgentReviewResult, AgentRiskReviewer
from paper_trading.fees import FeeModel
from paper_trading.order import (
    OrderManager,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from paper_trading.position import PositionManager
from paper_trading.risk import RiskChecker, RiskDecision
from src.storage import (
    DatabaseManager,
    PaperOrder,
    PaperSignal,
    get_db,
)
from paper_trading.strategies import Signal

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """Outcome of submitting a signal — returned to the caller."""

    signal_id: int
    order_id: Optional[int]
    side: str
    code: str
    status: str  # executed / rejected / pending
    fill_price: Optional[float]
    fill_quantity: Optional[float]
    fee: Optional[float]
    reason: str
    risk_decisions: List[Dict[str, Any]] = field(default_factory=list)
    agent_review: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "order_id": self.order_id,
            "side": self.side,
            "code": self.code,
            "status": self.status,
            "fill_price": self.fill_price,
            "fill_quantity": self.fill_quantity,
            "fee": self.fee,
            "reason": self.reason,
            "risk_decisions": self.risk_decisions,
            "agent_review": self.agent_review,
        }


class TradingEngine:
    """Top-level orchestrator turning Signals into Trades."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        account_manager: Optional[PaperAccountManager] = None,
        order_manager: Optional[OrderManager] = None,
        position_manager: Optional[PositionManager] = None,
        fee_model: Optional[FeeModel] = None,
        risk_checker: Optional[RiskChecker] = None,
        agent_reviewer: Optional[AgentRiskReviewer] = None,
        sltp_calculator: Optional[Any] = None,
        enable_auto_sltp: bool = True,
        on_trade_executed: Optional[Any] = None,
        on_signal_rejected: Optional[Any] = None,
    ):
        self.db = db_manager or get_db()
        self.account_mgr = account_manager or PaperAccountManager(self.db)
        self.order_mgr = order_manager or OrderManager(self.db)
        self.position_mgr = position_manager or PositionManager(self.db)
        self.fee_model = fee_model or FeeModel()
        # If no explicit risk_checker is provided, create one using the main system config for parameter alignment.
        if risk_checker is None:
            from .risk_config_adapter import create_risk_config_from_main
            risk_cfg = create_risk_config_from_main()
            risk_checker = RiskChecker(
                db_manager=self.db,
                account_manager=self.account_mgr,
                position_manager=self.position_mgr,
                fee_model=self.fee_model,
                config=risk_cfg,
            )
        self.risk = risk_checker
        # Optional secondary confirmation layer. When None, signals bypass
        # agent review and flow directly to order creation after risk checks.
        self.agent_reviewer = agent_reviewer
        # Optional smart stop-loss/take-profit calculator (P1-A). When None,
        # positions keep whatever SL/TP the signal/agent explicitly set.
        self.sltp_calculator = sltp_calculator
        # G8: Optional switch to disable automatic SL/TP computation on every
        # BUY fill. When False, explicit signal/agent SL/TP values are kept.
        self.enable_auto_sltp = bool(enable_auto_sltp)
        # P1-C: Optional callback hooks for downstream subscribers (e.g.,
        # ReflectionEngine.reflect_on_trade). Callback receives the
        # TradeResult and (when available) the trade_id. Callbacks must be
        # fault-tolerant: exceptions are logged and swallowed so the engine
        # pipeline never breaks.
        self._on_trade_executed = on_trade_executed
        self._on_signal_rejected = on_signal_rejected

        # T18-A: Pre-trade RMS (Risk Management System) — delegated risk checks
        from paper_trading.rms_mgmt import RiskManagementSystem

        self.rms = RiskManagementSystem(
            risk_checker=self.risk,
            account_manager=self.account_mgr,
            position_manager=self.position_mgr,
            agent_reviewer=self.agent_reviewer,
        )

    # ------------------------------------------------------------------
    # Signal submission
    # ------------------------------------------------------------------

    def submit_signal(
        self,
        account_id: int,
        signal: Signal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        quantity_override: Optional[float] = None,
    ) -> TradeResult:
        """Process a Signal: persist it, run risk checks, place order.

        For market orders: fill immediately and settle.
        For limit orders: persist as pending (caller drives matching).
        """
        # Persist signal first (always — for audit even if rejected).
        signal_id = self._persist_signal(account_id, signal, status="pending")

        side = signal.side
        if side not in ("buy", "sell"):
            self._update_signal_status(signal_id, "rejected", reason=f"invalid side: {side}")
            return TradeResult(
                signal_id=signal_id,
                order_id=None,
                side=side,
                code=signal.code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"invalid side: {side}",
            )

        # Resolve quantity (signal.suggested_quantity may be None for sells).
        quantity = (
            float(quantity_override)
            if quantity_override is not None
            else float(signal.suggested_quantity or 0.0)
        )
        if side == "sell" and quantity <= 0:
            # Default sell quantity = entire available position.
            pos = self.position_mgr.get_position(account_id, signal.code)
            quantity = float(pos.available_quantity) if pos is not None else 0.0
            if quantity <= 0:
                reason = "no available quantity to sell"
                self._update_signal_status(signal_id, "rejected", reason=reason)
                return TradeResult(
                    signal_id=signal_id,
                    order_id=None,
                    side=side,
                    code=signal.code,
                    status="rejected",
                    fill_price=None,
                    fill_quantity=None,
                    fee=None,
                    reason=reason,
                )

        # Determine reference price for risk checks.
        if order_type == OrderType.LIMIT:
            ref_price = float(limit_price) if limit_price and limit_price > 0 else float(signal.trigger_price)
        else:
            ref_price = float(signal.trigger_price)

        # Run risk checks (delegated to RMS — T18-A).
        risk_result = self.rms.pre_trade_check(
            account_id, signal.code, ref_price, quantity, side
        )

        if not risk_result.passed:
            self._update_signal_status(
                signal_id, "rejected", reason=risk_result.reason
            )
            return TradeResult(
                signal_id=signal_id,
                order_id=None,
                side=side,
                code=signal.code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=risk_result.reason,
                risk_decisions=risk_result.risk_decisions,
            )

        # Optional Agent risk-control review (delegated to RMS — T18-A).
        # Verdict is persisted to PaperSignal for audit.
        agent_review_dict: Optional[Dict[str, Any]] = self.rms.agent_review(
            account_id, signal=signal,
        )

        # P0-C / R2: Map agent verdict to order actions (cancel/sell/modify).
        if agent_review_dict is not None:
            from paper_trading.agent_risk import AgentReviewResult
            verdict = AgentReviewResult(
                approved=agent_review_dict.get("approved", False),
                reason=agent_review_dict.get("reason", ""),
                error=agent_review_dict.get("error", ""),
                used_fallback=agent_review_dict.get("used_fallback", False),
            )
            self._maybe_trigger_order_action(account_id, verdict, signal)

            if not agent_review_dict.get("approved", True):
                self._update_signal_status(
                    signal_id, "rejected",
                    reason=f"agent veto: {agent_review_dict.get('reason', '')}",
                )
                logger.info(
                    "Signal vetoed by agent: signal_id=%s code=%s reason=%s",
                    signal_id, signal.code, agent_review_dict.get("reason", ""),
                )
                return TradeResult(
                    signal_id=signal_id,
                    order_id=None,
                    side=side,
                    code=signal.code,
                    status="rejected",
                    fill_price=None,
                    fill_quantity=None,
                    fee=None,
                    reason=f"agent veto: {agent_review_dict.get('reason', '')}",
                    risk_decisions=risk_result.risk_decisions,
                    agent_review=agent_review_dict,
                )

        # Create the order.
        order_req = OrderRequest(
            account_id=account_id,
            code=signal.code,
            side=OrderSide(side),
            quantity=quantity,
            order_type=order_type,
            price=limit_price if order_type == OrderType.LIMIT else None,
            name=signal.name,
            strategy_name=signal.strategy_name,
            signal_id=signal_id,
            reason=signal.reason,
        )
        order = self.order_mgr.create_order(order_req)
        order_id = order.id

        # Execute based on order type.
        if order_type == OrderType.MARKET:
            return self._execute_market_order(
                account_id=account_id,
                order_id=order_id,
                side=side,
                code=signal.code,
                name=signal.name,
                ref_price=ref_price,
                quantity=quantity,
                signal_id=signal_id,
                risk_decisions=decisions,
                agent_review=agent_review_dict,
            )

        # Limit order: freeze cash for buys and wait for matcher.
        if side == "buy":
            eff_price = limit_price or ref_price
            estimated_cost = self.fee_model.estimate_buy_cost(eff_price, quantity)
            try:
                self.account_mgr.freeze_cash(account_id, estimated_cost)
            except ValueError as exc:
                # Freeze failed (e.g., cash became insufficient between check and freeze).
                self.order_mgr.reject_order(order_id, reason=f"freeze failed: {exc}")
                self._update_signal_status(signal_id, "rejected", reason=str(exc))
                return TradeResult(
                    signal_id=signal_id,
                    order_id=order_id,
                    side=side,
                    code=signal.code,
                    status="rejected",
                    fill_price=None,
                    fill_quantity=None,
                    fee=None,
                    reason=f"freeze failed: {exc}",
                    risk_decisions=[d.to_dict() for d in decisions],
                    agent_review=agent_review_dict,
                )

        self._update_signal_status(signal_id, "pending", reason="limit order awaiting match")
        return TradeResult(
            signal_id=signal_id,
            order_id=order_id,
            side=side,
            code=signal.code,
            status="pending",
            fill_price=None,
            fill_quantity=None,
            fee=None,
            reason="limit order pending",
            risk_decisions=[d.to_dict() for d in decisions],
            agent_review=agent_review_dict,
        )

    # ------------------------------------------------------------------
    # Market order execution (immediate fill)
    # ------------------------------------------------------------------

    def _execute_market_order(
        self,
        account_id: int,
        order_id: int,
        side: str,
        code: str,
        name: Optional[str],
        ref_price: float,
        quantity: float,
        signal_id: int,
        risk_decisions: List[RiskDecision],
        agent_review: Optional[Dict[str, Any]] = None,
    ) -> TradeResult:
        """Fill a market order at slippage-adjusted price and settle."""
        eff_price = self.fee_model.apply_slippage(ref_price, side)
        fee = self.fee_model.compute_fee(side, eff_price, quantity)

        try:
            if side == "buy":
                # Freeze the estimated cost, then settle with actual.
                estimated_cost = self.fee_model.estimate_buy_cost(ref_price, quantity)
                # Re-check cash after slippage (it can push cost above frozen).
                actual_cost = self.fee_model.estimate_buy_cost(eff_price, quantity)
                # Freeze the larger of (estimated, actual) to be safe.
                freeze_amount = max(estimated_cost, actual_cost)
                self.account_mgr.freeze_cash(account_id, freeze_amount)
                # Fill the order (creates PaperTrade, updates order status).
                trade = self.order_mgr.fill_order(order_id, eff_price, quantity, fee=fee)
                # Settle: release frozen, debit actual.
                self.account_mgr.settle_buy(account_id, freeze_amount, actual_cost)
                # Update position (T+1: not available today).
                self.position_mgr.apply_buy(account_id, code, quantity, eff_price, name=name)
                # P1-A: Auto-compute stop-loss / take-profit for the new position.
                self._apply_sltp_to_position(account_id, code, eff_price)
            else:  # sell
                # apply_sell atomically reduces quantity & available_quantity.
                realized_pnl = self.position_mgr.apply_sell(account_id, code, quantity, eff_price)
                # Fill the order.
                trade = self.order_mgr.fill_order(order_id, eff_price, quantity, fee=fee)
                # Credit proceeds (amount - fee).
                proceeds = self.fee_model.estimate_sell_proceeds(eff_price, quantity)
                self.account_mgr.settle_sell(account_id, proceeds)
                logger.info(
                    "Sell executed: code=%s qty=%s price=%.4f pnl=%.2f fee=%.2f",
                    code, quantity, eff_price, realized_pnl, fee,
                )
        except Exception as exc:
            logger.error(
                "Market order execution failed: order_id=%s side=%s code=%s err=%s",
                order_id, side, code, exc, exc_info=True,
            )
            # Best-effort cleanup: reject the order and signal.
            try:
                self.order_mgr.reject_order(order_id, reason=f"execution error: {exc}")
            except Exception:
                pass
            self._update_signal_status(signal_id, "rejected", reason=str(exc))
            return TradeResult(
                signal_id=signal_id,
                order_id=order_id,
                side=side,
                code=code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"execution error: {exc}",
                risk_decisions=[d.to_dict() for d in risk_decisions],
                agent_review=agent_review,
            )

        self._update_signal_status(signal_id, "executed", reason="market order filled")
        logger.info(
            "Market order executed: order_id=%s side=%s code=%s qty=%s price=%.4f fee=%.2f",
            order_id, side, code, quantity, eff_price, fee,
        )
        result = TradeResult(
            signal_id=signal_id,
            order_id=order_id,
            side=side,
            code=code,
            status="executed",
            fill_price=eff_price,
            fill_quantity=quantity,
            fee=fee,
            reason="market order filled",
            risk_decisions=[d.to_dict() for d in risk_decisions],
            agent_review=agent_review,
        )
        # P1-C: fire trade-executed callback (e.g., reflection trigger).
        trade_id = getattr(trade, "id", None) if trade is not None else None
        self._fire_callback(self._on_trade_executed, result, trade_id=trade_id)
        return result

    # ------------------------------------------------------------------
    # Limit order matching (driven by market_listener)
    # ------------------------------------------------------------------

    def tick_market_price(
        self, account_id: int, code: str, price: float
    ) -> List[TradeResult]:
        """Drive one price tick through conditional + pending order matchers.

        This is the entry point used by the MarketListener and by tests:
        it first evaluates conditional orders, executes any that trigger as
        market orders immediately, then runs the normal limit-order matcher.

        Returns:
            TradeResult list for any orders filled or rejected this tick.
        """
        results: List[TradeResult] = []
        triggered = self.order_mgr.match_conditional_orders(account_id, code, price)

        for order in triggered:
            if order.get("order_type") == OrderType.MARKET.value:
                result = self._execute_triggered_market_order(order, fill_price=price)
                results.append(result)

        # Run the normal pending limit matcher.
        results.extend(self.match_pending_orders({code: price}))
        return results

    def match_pending_orders(
        self, latest_prices: Dict[str, float]
    ) -> List[TradeResult]:
        """Check all pending limit orders against latest prices and fill if triggered.

        Args:
            latest_prices: {code: latest_market_price}

        Returns:
            List of TradeResult for orders that matched (filled or rejected).
        """
        results: List[TradeResult] = []
        pending_orders = self._list_pending_orders()

        for order in pending_orders:
            price = latest_prices.get(order["code"])
            if price is None:
                continue

            # Market orders that are pending (e.g., from a conditional trigger
            # that has not yet been executed) should not sit here — they are
            # executed immediately by tick_market_price. Defensive skip.
            if order.get("order_type") == OrderType.MARKET.value:
                continue

            limit_price = float(order["price"] or 0.0)
            if limit_price <= 0:
                continue

            side = order["side"]
            should_fill = False
            if side == "buy" and price <= limit_price:
                should_fill = True
            elif side == "sell" and price >= limit_price:
                should_fill = True

            if not should_fill:
                continue

            result = self._fill_limit_order(order, fill_price=limit_price)
            results.append(result)

        return results

    def _execute_triggered_market_order(
        self, order: Dict[str, Any], fill_price: float
    ) -> TradeResult:
        """Fill a pending market order that was activated from a conditional order.

        This path bypasses the signal pipeline (conditional orders do not
        carry a PaperSignal). It performs minimal settlement and fires the
        trade-executed callback.
        """
        order_id = int(order["id"])
        account_id = int(order["account_id"])
        side = order["side"]
        code = order["code"]
        name = order.get("name")
        quantity = float(order["quantity"])
        fee = self.fee_model.compute_fee(side, fill_price, quantity)

        try:
            if side == "buy":
                actual_cost = self.fee_model.estimate_buy_cost(fill_price, quantity)
                self.account_mgr.settle_buy(account_id, actual_cost, actual_cost)
                trade = self.order_mgr.fill_order(order_id, fill_price, quantity, fee=fee)
                self.position_mgr.apply_buy(account_id, code, quantity, fill_price, name=name)
                self._apply_sltp_to_position(account_id, code, fill_price)
            else:  # sell
                pos = self.position_mgr.get_position(account_id, code)
                if pos is None or float(pos.available_quantity or 0.0) < quantity - 1e-6:
                    self.order_mgr.reject_order(
                        order_id, reason="insufficient available quantity at fill time"
                    )
                    rejected = TradeResult(
                        signal_id=order.get("signal_id") or 0,
                        order_id=order_id,
                        side=side,
                        code=code,
                        status="rejected",
                        fill_price=None,
                        fill_quantity=None,
                        fee=None,
                        reason="insufficient available quantity at fill time",
                    )
                    self._fire_callback(self._on_signal_rejected, rejected)
                    return rejected
                realized_pnl = self.position_mgr.apply_sell(account_id, code, quantity, fill_price)
                trade = self.order_mgr.fill_order(order_id, fill_price, quantity, fee=fee)
                proceeds = self.fee_model.estimate_sell_proceeds(fill_price, quantity)
                self.account_mgr.settle_sell(account_id, proceeds)
                logger.info(
                    "Triggered market sell executed: code=%s qty=%s price=%.4f pnl=%.2f",
                    code, quantity, fill_price, realized_pnl,
                )
        except Exception as exc:
            logger.error(
                "Triggered market order execution failed: order_id=%s err=%s",
                order_id, exc, exc_info=True,
            )
            try:
                self.order_mgr.reject_order(order_id, reason=f"execution error: {exc}")
            except Exception:
                pass
            return TradeResult(
                signal_id=order.get("signal_id") or 0,
                order_id=order_id,
                side=side,
                code=code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"execution error: {exc}",
            )

        result = TradeResult(
            signal_id=order.get("signal_id") or 0,
            order_id=order_id,
            side=side,
            code=code,
            status="executed",
            fill_price=fill_price,
            fill_quantity=quantity,
            fee=fee,
            reason="conditional order triggered and filled as market",
        )
        trade_id = getattr(trade, "id", None) if trade is not None else None
        self._fire_callback(self._on_trade_executed, result, trade_id=trade_id)
        return result

    def _fill_limit_order(self, order: Dict[str, Any], fill_price: float) -> TradeResult:
        """Fill a pending limit order at the given price (no slippage)."""
        order_id = int(order["id"])
        account_id = int(order["account_id"])
        side = order["side"]
        code = order["code"]
        name = order.get("name")
        quantity = float(order["quantity"])
        signal_id = order.get("signal_id")

        fee = self.fee_model.compute_fee(side, fill_price, quantity)

        try:
            if side == "buy":
                # Cash was frozen at submit time — settle now.
                # Re-derive the frozen amount using the original limit price.
                estimated_cost = self.fee_model.estimate_buy_cost(fill_price, quantity)
                actual_cost = self.fee_model.estimate_buy_cost(fill_price, quantity)
                trade = self.order_mgr.fill_order(order_id, fill_price, quantity, fee=fee)
                self.account_mgr.settle_buy(account_id, estimated_cost, actual_cost)
                self.position_mgr.apply_buy(account_id, code, quantity, fill_price, name=name)
                # P1-A: Auto-compute stop-loss / take-profit for the new position.
                self._apply_sltp_to_position(account_id, code, fill_price)
            else:  # sell
                # Re-check availability at fill time (T+1 may have changed).
                pos = self.position_mgr.get_position(account_id, code)
                if pos is None or float(pos.available_quantity or 0.0) < quantity - 1e-6:
                    self.order_mgr.reject_order(
                        order_id, reason="insufficient available quantity at fill time"
                    )
                    if signal_id:
                        self._update_signal_status(
                            signal_id, "rejected",
                            reason="insufficient available quantity at fill time",
                        )
                    rejected_result = TradeResult(
                        signal_id=signal_id or 0,
                        order_id=order_id,
                        side=side,
                        code=code,
                        status="rejected",
                        fill_price=None,
                        fill_quantity=None,
                        fee=None,
                        reason="insufficient available quantity at fill time",
                    )
                    self._fire_callback(self._on_signal_rejected, rejected_result)
                    return rejected_result
                realized_pnl = self.position_mgr.apply_sell(account_id, code, quantity, fill_price)
                trade = self.order_mgr.fill_order(order_id, fill_price, quantity, fee=fee)
                proceeds = self.fee_model.estimate_sell_proceeds(fill_price, quantity)
                self.account_mgr.settle_sell(account_id, proceeds)
                logger.info(
                    "Limit sell filled: code=%s qty=%s price=%.4f pnl=%.2f",
                    code, quantity, fill_price, realized_pnl,
                )
        except Exception as exc:
            logger.error(
                "Limit order fill failed: order_id=%s err=%s",
                order_id, exc, exc_info=True,
            )
            try:
                self.order_mgr.reject_order(order_id, reason=f"fill error: {exc}")
            except Exception:
                pass
            if signal_id:
                self._update_signal_status(signal_id, "rejected", reason=str(exc))
            return TradeResult(
                signal_id=signal_id or 0,
                order_id=order_id,
                side=side,
                code=code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"fill error: {exc}",
            )

        if signal_id:
            self._update_signal_status(signal_id, "executed", reason="limit order filled")
        result = TradeResult(
            signal_id=signal_id or 0,
            order_id=order_id,
            side=side,
            code=code,
            status="executed",
            fill_price=fill_price,
            fill_quantity=quantity,
            fee=fee,
            reason="limit order filled",
        )
        # P1-C: fire trade-executed callback (e.g., reflection trigger).
        trade_id = getattr(trade, "id", None) if trade is not None else None
        self._fire_callback(self._on_trade_executed, result, trade_id=trade_id)
        return result

    def _list_pending_orders(self) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(PaperOrder).where(PaperOrder.status == OrderStatus.PENDING.value)
            ).scalars().all()
            return [
                {
                    "id": o.id,
                    "account_id": o.account_id,
                    "code": o.code,
                    "name": o.name,
                    "side": o.side,
                    "order_type": o.order_type,
                    "price": o.price,
                    "quantity": float(o.quantity),
                    "signal_id": o.signal_id,
                }
                for o in rows
            ]

    # ------------------------------------------------------------------
    # Stop-loss / take-profit guard
    # ------------------------------------------------------------------

    def check_stop_loss_take_profit(
        self, latest_prices: Dict[str, float], account_id: Optional[int] = None
    ) -> List[TradeResult]:
        """Auto-emit sell signals for positions breaching SL/TP.

        Triggered by the market_listener on each price update. Returns the
        list of resulting TradeResults (may be empty).

        P1-A: now considers ``take_profit_2`` (mid-term target). Trigger
        priority: stop_loss > take_profit_2 > take_profit. When TP2 is hit
        we sell the full remaining position (final exit); when TP1 is hit
        we also sell out (paper-trading simplification — no partial scaling
        here, that's an agent-level decision).
        """
        results: List[TradeResult] = []
        positions = self._list_positions_with_sl_tp(account_id)
        for pos in positions:
            price = latest_prices.get(pos["code"])
            if price is None:
                continue
            sl = pos.get("stop_loss")
            tp1 = pos.get("take_profit")
            tp2 = pos.get("take_profit_2")
            triggered = None
            if sl is not None and price <= float(sl):
                triggered = "stop_loss"
            elif tp2 is not None and price >= float(tp2):
                triggered = "take_profit_2"
            elif tp1 is not None and price >= float(tp1):
                triggered = "take_profit"
            if triggered is None:
                continue

            # Update last price for accurate PnL display.
            self.position_mgr.update_last_price(pos["account_id"], pos["code"], price)

            signal = Signal(
                side="sell",
                code=pos["code"],
                name=pos.get("name"),
                strategy_name="risk_guard",
                rule_name=triggered,
                trigger_price=float(price),
                suggested_quantity=pos.get("available_quantity"),
                reason=f"{triggered} triggered at {price:.4f} (SL={sl}, TP1={tp1}, TP2={tp2})",
            )
            logger.info(
                "SL/TP triggered: account=%s code=%s trigger=%s price=%.4f",
                pos["account_id"], pos["code"], triggered, price,
            )
            result = self.submit_signal(
                account_id=pos["account_id"],
                signal=signal,
                order_type=OrderType.MARKET,
            )
            results.append(result)
        return results

    def _list_positions_with_sl_tp(
        self, account_id: Optional[int]
    ) -> List[Dict[str, Any]]:
        from src.storage import PaperPosition

        with self.db.session_scope() as session:
            stmt = select(PaperPosition).where(PaperPosition.quantity > 0)
            if account_id is not None:
                stmt = stmt.where(PaperPosition.account_id == account_id)
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "account_id": p.account_id,
                    "code": p.code,
                    "name": p.name,
                    "quantity": float(p.quantity or 0.0),
                    "available_quantity": float(p.available_quantity or 0.0),
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "take_profit_2": p.take_profit_2,
                    "last_price": float(p.last_price or 0.0),
                }
                for p in rows
            ]

    # ------------------------------------------------------------------
    # Smart stop-loss / take-profit (P1-A)
    # ------------------------------------------------------------------

    def _apply_sltp_to_position(
        self,
        account_id: int,
        code: str,
        entry_price: float,
    ) -> Optional[Dict[str, Any]]:
        """Compute and persist a three-line SL/TP plan onto the position.

        Called automatically after each BUY fill when ``self.sltp_calculator``
        is configured and ``self.enable_auto_sltp`` is True. If the calculator
        returns a result, the position's ``stop_loss`` / ``take_profit`` (TP1)
        / ``take_profit_2`` (TP2) and ``sltp_reasoning`` fields are updated.

        Existing SL/TP set by the signal or agent is **not** overwritten —
        this method only fills in missing values, so explicit strategy rules
        always take precedence over the auto-calculator.

        Returns the SLTPResult dict (for logging / testing), or None if the
        calculator was not configured, the switch is off, or the position was
        not found.
        """
        if not self.enable_auto_sltp or self.sltp_calculator is None:
            return None

        # Read existing SL/TP inside a session_scope to avoid
        # DetachedInstanceError when the returned PaperPosition is accessed
        # after the session closes.
        from src.storage import PaperPosition
        with self.db.session_scope() as session:
            existing = session.execute(
                select(PaperPosition).where(
                    PaperPosition.account_id == account_id,
                    PaperPosition.code == code,
                )
            ).scalar_one_or_none()
            if existing is None:
                return None
            existing_sl = existing.stop_loss
            existing_tp = existing.take_profit

        if existing_sl is not None and existing_tp is not None:
            # Both already set — respect the strategy/agent's explicit values.
            return None

        try:
            result = self.sltp_calculator.compute(
                code=code,
                entry_price=float(entry_price),
            )
        except Exception as exc:
            logger.warning(
                "SLTPCalculator.compute failed for code=%s entry=%.4f: %s",
                code, entry_price, exc,
            )
            return None

        if result is None:
            return None

        self.position_mgr.update_stop_loss_take_profit(
            account_id=account_id,
            code=code,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit_1,
            take_profit_2=result.take_profit_2,
            sltp_reasoning=result.reasoning,
        )
        logger.info(
            "SLTP applied: account=%s code=%s entry=%.4f SL=%.4f TP1=%.4f TP2=%.4f (%s)",
            account_id, code, entry_price,
            result.stop_loss, result.take_profit_1, result.take_profit_2,
            result.method,
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # Daily settlement
    # ------------------------------------------------------------------

    def daily_settle(
        self,
        account_id: int,
        target_date: Optional[date] = None,
        latest_prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """End-of-trading-day housekeeping.

        1. Update last_price for all positions (if latest_prices provided).
        2. Roll T+1: move quantity -> available_quantity.
        3. Record daily net-value snapshot.
        """
        if latest_prices:
            for code, price in latest_prices.items():
                self.position_mgr.update_last_price(account_id, code, price)

        rolled = self.position_mgr.daily_roll_available(account_id)
        self.account_mgr.record_daily_net_value(account_id, target_date=target_date)

        logger.info(
            "Daily settle complete: account=%s positions_rolled=%s",
            account_id, rolled,
        )
        return {
            "account_id": account_id,
            "positions_rolled": rolled,
            "date": (target_date or date.today()).isoformat(),
        }

    # ------------------------------------------------------------------
    # Signal persistence helpers
    # ------------------------------------------------------------------

    def _persist_signal(
        self, account_id: int, signal: Signal, status: str = "pending"
    ) -> int:
        with self.db.session_scope() as session:
            row = PaperSignal(
                account_id=account_id,
                code=signal.code,
                name=signal.name,
                side=signal.side,
                trigger_price=float(signal.trigger_price),
                suggested_quantity=(
                    float(signal.suggested_quantity)
                    if signal.suggested_quantity is not None
                    else None
                ),
                strategy_name=signal.strategy_name,
                rule_name=signal.rule_name,
                reason=signal.reason,
                status=status,
                created_at=datetime.now(),
            )
            session.add(row)
            session.flush()
            return int(row.id)

    def _update_signal_status(
        self, signal_id: int, status: str, reason: Optional[str] = None
    ) -> None:
        with self.db.session_scope() as session:
            row = session.execute(
                select(PaperSignal).where(PaperSignal.id == signal_id)
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            row.reviewed_at = datetime.now()
            if reason:
                # Append to existing reason for full audit trail.
                existing = row.reason or ""
                row.reason = f"{existing}\n[{status}] {reason}" if existing else reason

    def _persist_agent_verdict(
        self, signal_id: int, verdict: AgentReviewResult
    ) -> None:
        """Write the agent's review verdict onto the PaperSignal row.

        Stored fields:
        - agent_confirmed: True (approved) / False (vetoed) / None (no review).
        - agent_reason: short reason from the agent (truncated for safety).
        - reviewed_at: timestamp of the review.
        """
        # Build a compact reason including concerns if present.
        parts: List[str] = []
        if verdict.reason:
            parts.append(verdict.reason)
        if verdict.concerns:
            parts.append("concerns: " + "; ".join(verdict.concerns))
        if verdict.used_fallback:
            parts.append(f"[fallback] error={verdict.error}")
        agent_reason = " | ".join(parts)

        with self.db.session_scope() as session:
            row = session.execute(
                select(PaperSignal).where(PaperSignal.id == signal_id)
            ).scalar_one_or_none()
            if row is None:
                return
            row.agent_confirmed = bool(verdict.approved)
            row.agent_reason = agent_reason
            row.reviewed_at = datetime.now()

    def _maybe_trigger_order_action(
        self,
        account_id: int,
        verdict: AgentReviewResult,
        signal: Signal,
    ) -> None:
        """Map an agent review verdict to order actions (P0-C / R2).

        Uses RiskOrderAdapter to translate the verdict's action field into
        concrete order commands (cancel/sell/modify) executed against the
        TradingEngine. Fault-tolerant: failures are logged and never break
        the main signal pipeline.
        """
        try:
            from paper_trading.risk_order_adapter import RiskOrderAdapter
            cmd = RiskOrderAdapter.from_agent_review(verdict)
            if cmd is None:
                return
            if cmd.action == "cancel" and cmd.code:
                try:
                    with self.db.session_scope() as session:
                        from src.storage import PaperOrder
                        rows = session.execute(
                            select(PaperOrder).where(
                                PaperOrder.account_id == account_id,
                                PaperOrder.code == cmd.code,
                                PaperOrder.status.in_(["pending", "partially_filled"]),
                            )
                        ).scalars().all()
                        for row in rows:
                            if hasattr(self.order_mgr, "cancel_order"):
                                self.order_mgr.cancel_order(
                                    row.id, reason=f"agent_action: {cmd.reason}"
                                )
                                logger.info(
                                    "Order canceled by risk action: order_id=%s code=%s",
                                    row.id, cmd.code,
                                )
                except Exception as exc:
                    logger.warning("Risk action cancel failed for code=%s: %s", cmd.code, exc)
            elif cmd.action == "sell" and cmd.code:
                try:
                    from strategies_v2.rule_engine import Signal as V2Signal
                    sell_signal = V2Signal(
                        side="sell",
                        code=cmd.code,
                        strategy_name="risk_action",
                        rule_name="agent_review",
                        trigger_price=cmd.stop_loss or 0.0,
                        suggested_quantity=cmd.quantity or 0.0,
                        reason=cmd.reason or "agent risk action",
                    )
                    self.submit_signal(
                        account_id=account_id,
                        signal=sell_signal,
                    )
                    logger.info("Sell signal emitted by risk action: code=%s", cmd.code)
                except Exception as exc:
                    logger.warning("Risk action sell failed for code=%s: %s", cmd.code, exc)
            elif cmd.action == "modify" and cmd.code:
                try:
                    self.position_mgr.update_stop_loss_take_profit(
                        account_id=account_id,
                        code=cmd.code,
                        stop_loss=cmd.stop_loss,
                        take_profit=cmd.take_profit,
                    )
                    logger.info(
                        "Position modified by risk action: code=%s SL=%s TP=%s",
                        cmd.code, cmd.stop_loss, cmd.take_profit,
                    )
                except Exception as exc:
                    logger.warning("Risk action modify failed for code=%s: %s", cmd.code, exc)
        except Exception as exc:
            logger.warning("RiskOrderAdapter hook failed: %s", exc)

    # ------------------------------------------------------------------
    # Callback dispatch (P1-C)
    # ------------------------------------------------------------------

    def _fire_callback(
        self,
        callback: Optional[Any],
        result: TradeResult,
        trade_id: Optional[int] = None,
    ) -> None:
        """Invoke a downstream callback if configured.

        Callbacks receive ``result`` (TradeResult) and optionally ``trade_id``.
        Exceptions are logged and swallowed — the engine pipeline must never
        be broken by a subscriber failure.
        """
        if callback is None:
            return
        try:
            # Accept either a single callable or an object implementing
            # ``on_trade_executed(result, trade_id=...)`` /
            # ``on_signal_rejected(result)`` methods. This makes it easy to
            # wire a ReflectionEngine instance directly.
            if callable(callback):
                try:
                    callback(result, trade_id=trade_id)
                except TypeError:
                    # Fallback: callable doesn't accept trade_id kwarg.
                    callback(result)
                return
            method_name = (
                "on_trade_executed"
                if result.status == "executed"
                else "on_signal_rejected"
            )
            method = getattr(callback, method_name, None)
            if method is None:
                # Generic dispatcher: try __call__ with result + trade_id.
                if hasattr(callback, "__call__"):
                    try:
                        callback(result, trade_id=trade_id)
                    except TypeError:
                        callback(result)
                return
            try:
                method(result, trade_id=trade_id)
            except TypeError:
                method(result)
        except Exception as exc:
            logger.warning(
                "[TradingEngine] callback %s raised: %s",
                getattr(callback, "__name__", type(callback).__name__), exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Signal cancel / modify (P0-C)
    # ------------------------------------------------------------------

    def cancel_signal(
        self,
        signal_id: int,
        reason: Optional[str] = None,
    ) -> TradeResult:
        """Cancel a pending signal and its associated pending order (P0-C).

        Behavior:
        - Loads the signal row. If not found, raises ValueError.
        - If the signal is already in a terminal status (executed / rejected /
          canceled), returns a TradeResult describing the no-op.
        - Finds the most recent PaperOrder linked to this signal that is in
          a cancellable status (pending / partially_filled).
        - For buy limit orders: unfreezes the cash that was frozen at submit
          time (estimated using the order's price * quantity + fee model).
        - Cancels the order via OrderManager.cancel_order (which sets
          cancel_reason, status='canceled').
        - Updates the signal status to 'rejected' with a cancel note (we keep
          the signal state machine simple: no 'canceled' status; the reason
          field carries the audit trail).

        Args:
            signal_id: The id of the signal to cancel.
            reason: Free-text cancel reason.

        Returns:
            TradeResult describing the cancellation outcome.

        Raises:
            ValueError: If the signal_id does not exist.
        """
        # Load signal.
        with self.db.session_scope() as session:
            sig = session.execute(
                select(PaperSignal).where(PaperSignal.id == signal_id)
            ).scalar_one_or_none()
            if sig is None:
                raise ValueError(f"Signal id={signal_id} not found")
            sig_code = sig.code
            sig_side = sig.side
            sig_status = sig.status

        # Terminal-status no-op.
        if sig_status in ("executed", "rejected", "canceled", "expired"):
            return TradeResult(
                signal_id=signal_id,
                order_id=None,
                side=sig_side,
                code=sig_code,
                status=sig_status,
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"signal already in terminal status={sig_status}",
            )

        # Find the linked pending order.
        order = self._find_cancellable_order_for_signal(signal_id)

        if order is None:
            # No cancellable order — just mark the signal.
            cancel_note = reason or "signal canceled (no pending order)"
            self._update_signal_status(signal_id, "rejected", reason=cancel_note)
            return TradeResult(
                signal_id=signal_id,
                order_id=None,
                side=sig_side,
                code=sig_code,
                status="rejected",
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=cancel_note,
            )

        order_id = int(order["id"])
        side = order["side"]
        code = order["code"]
        price = float(order["price"] or 0.0)
        quantity = float(order["quantity"] or 0.0)
        filled_qty = float(order["filled_quantity"] or 0.0)
        remaining_qty = max(0.0, quantity - filled_qty)

        # Unfreeze cash for buy limit orders (only the remaining unfilled portion).
        if side == "buy" and remaining_qty > 0 and price > 0:
            try:
                frozen_amount = self.fee_model.estimate_buy_cost(price, remaining_qty)
                self.account_mgr.unfreeze_cash(order["account_id"], frozen_amount)
            except Exception as exc:
                logger.warning(
                    "Unfreeze failed during cancel: signal=%s order=%s err=%s",
                    signal_id, order_id, exc,
                )

        # Cancel the order (sets cancel_reason, status='canceled').
        self.order_mgr.cancel_order(order_id, reason=reason)

        # Mark the signal.
        cancel_note = reason or "signal canceled by user/agent"
        self._update_signal_status(signal_id, "rejected", reason=cancel_note)
        logger.info(
            "Signal canceled: signal_id=%s order_id=%s code=%s reason=%s",
            signal_id, order_id, code, cancel_note,
        )

        return TradeResult(
            signal_id=signal_id,
            order_id=order_id,
            side=side,
            code=code,
            status="rejected",
            fill_price=None,
            fill_quantity=None,
            fee=None,
            reason=cancel_note,
        )

    def modify_signal(
        self,
        signal_id: int,
        new_price: Optional[float] = None,
        new_quantity: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> TradeResult:
        """Modify the price/quantity of a pending signal's order (P0-C).

        Behavior:
        - Loads the signal row. If not found or not pending, raises/returns.
        - Finds the linked pending limit order (market orders cannot be
          modified — they fill immediately).
        - For buy orders: unfreezes the cash frozen against the OLD order's
          remaining quantity, then re-freezes against the NEW order's
          remaining quantity. If the new freeze fails, the modification is
          rolled back into a cancel (caller sees status='rejected').
        - Delegates to OrderManager.modify_order which cancels the old order
          and creates a replacement linked via parent_order_id.
        - Updates the signal reason to record the modification.

        Args:
            signal_id: The id of the signal to modify.
            new_price: New limit price (None = keep original).
            new_quantity: New TOTAL quantity (None = keep original).
            reason: Free-text modification reason.

        Returns:
            TradeResult describing the new (replacement) order.

        Raises:
            ValueError: If the signal_id does not exist, the signal is not
                pending, or no modifiable order is found.
        """
        if new_price is None and new_quantity is None:
            raise ValueError(
                "modify_signal requires at least one of new_price / new_quantity"
            )

        # Load signal.
        with self.db.session_scope() as session:
            sig = session.execute(
                select(PaperSignal).where(PaperSignal.id == signal_id)
            ).scalar_one_or_none()
            if sig is None:
                raise ValueError(f"Signal id={signal_id} not found")
            sig_code = sig.code
            sig_side = sig.side
            sig_status = sig.status

        if sig_status in ("executed", "rejected", "canceled", "expired"):
            return TradeResult(
                signal_id=signal_id,
                order_id=None,
                side=sig_side,
                code=sig_code,
                status=sig_status,
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"signal already in terminal status={sig_status}",
            )

        # Find the linked pending order (must be a limit order).
        order = self._find_cancellable_order_for_signal(signal_id)
        if order is None:
            raise ValueError(
                f"No cancellable order found for signal_id={signal_id}"
            )
        if order.get("order_type") != OrderType.LIMIT.value:
            raise ValueError(
                f"Cannot modify signal={signal_id}: only limit orders can be modified "
                f"(got order_type={order.get('order_type')})"
            )

        order_id = int(order["id"])
        account_id = int(order["account_id"])
        side = order["side"]
        code = order["code"]
        old_price = float(order["price"] or 0.0)
        old_qty = float(order["quantity"] or 0.0)
        filled_qty = float(order["filled_quantity"] or 0.0)
        old_remaining = max(0.0, old_qty - filled_qty)

        # Compute new remaining quantity for freeze accounting.
        if new_quantity is not None:
            if new_quantity < filled_qty:
                raise ValueError(
                    f"new_quantity {new_quantity} < already-filled {filled_qty}"
                )
            new_remaining = float(new_quantity) - filled_qty
        else:
            new_remaining = old_remaining
        new_price_val = float(new_price) if new_price is not None else old_price

        # For buy orders: unfreeze old, freeze new.
        if side == "buy":
            # Unfreeze the old remaining portion.
            if old_remaining > 0 and old_price > 0:
                try:
                    old_frozen = self.fee_model.estimate_buy_cost(old_price, old_remaining)
                    self.account_mgr.unfreeze_cash(account_id, old_frozen)
                except Exception as exc:
                    logger.warning(
                        "Unfreeze old order failed during modify: signal=%s err=%s",
                        signal_id, exc,
                    )

            # Freeze the new estimated cost (if anything left to fill).
            if new_remaining > 0 and new_price_val > 0:
                try:
                    new_frozen = self.fee_model.estimate_buy_cost(new_price_val, new_remaining)
                    self.account_mgr.freeze_cash(account_id, new_frozen)
                except ValueError as exc:
                    # Re-freeze failed (insufficient cash). Cancel the order
                    # instead of leaving it pending without frozen cash.
                    logger.warning(
                        "Re-freeze failed during modify; canceling: signal=%s err=%s",
                        signal_id, exc,
                    )
                    return self.cancel_signal(
                        signal_id,
                        reason=f"modify re-freeze failed: {exc}",
                    )

        # Delegate to OrderManager.modify_order (cancels old, creates replacement).
        new_order = self.order_mgr.modify_order(
            order_id=order_id,
            new_price=new_price,
            new_quantity=new_quantity,
            reason=reason,
        )

        # Update signal audit trail.
        mod_note = (
            f"modified: old_order={order_id} -> new_order={new_order.id} "
            f"new_price={new_price_val} new_total_qty={new_quantity}"
        )
        if reason:
            mod_note = f"{reason} | {mod_note}"
        self._update_signal_status(signal_id, "pending", reason=mod_note)
        logger.info(
            "Signal modified: signal_id=%s old_order=%s new_order=%s code=%s",
            signal_id, order_id, new_order.id, code,
        )

        return TradeResult(
            signal_id=signal_id,
            order_id=int(new_order.id),
            side=side,
            code=code,
            status="pending",
            fill_price=None,
            fill_quantity=None,
            fee=None,
            reason=mod_note,
        )

    # Order cancel / modify (P0-C/G5) — direct order-id API.
    # ------------------------------------------------------------------

    def cancel_order(
        self,
        order_id: int,
        reason: Optional[str] = None,
    ) -> TradeResult:
        """Cancel a pending order by its id (G5).

        Mirrors :meth:`cancel_signal` but operates directly on the order. If
        the order is linked to a signal, the signal status is updated to
        'rejected' with a cancel note.

        Args:
            order_id: The id of the order to cancel.
            reason: Free-text cancel reason.

        Returns:
            TradeResult describing the cancellation outcome.

        Raises:
            ValueError: If the order_id does not exist or is not cancellable.
        """
        order = self.order_mgr.get_order(order_id)
        if order is None:
            raise ValueError(f"Order id={order_id} not found")
        if order.status not in (
            OrderStatus.PENDING.value,
            OrderStatus.CONDITIONAL.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ):
            return TradeResult(
                signal_id=int(order.signal_id) if order.signal_id else None,
                order_id=order_id,
                side=order.side,
                code=order.code,
                status=order.status,
                fill_price=None,
                fill_quantity=None,
                fee=None,
                reason=f"order already in terminal status={order.status}",
            )

        account_id = int(order.account_id)
        side = order.side
        code = order.code
        price = float(order.price or 0.0)
        quantity = float(order.quantity or 0.0)
        filled_qty = float(order.filled_quantity or 0.0)
        remaining_qty = max(0.0, quantity - filled_qty)
        signal_id = int(order.signal_id) if order.signal_id else None

        # Unfreeze cash for buy limit orders (only the remaining unfilled portion).
        # Conditional orders do not freeze cash until they trigger, so this is
        # a no-op for them.
        if side == "buy" and remaining_qty > 0 and price > 0:
            try:
                frozen_amount = self.fee_model.estimate_buy_cost(price, remaining_qty)
                self.account_mgr.unfreeze_cash(account_id, frozen_amount)
            except Exception as exc:
                logger.warning(
                    "Unfreeze failed during cancel_order: order=%s err=%s",
                    order_id, exc,
                )

        self.order_mgr.cancel_order(order_id, reason=reason)
        cancel_note = reason or "order canceled by user/agent"

        # Update linked signal audit trail if present.
        if signal_id is not None:
            self._update_signal_status(signal_id, "rejected", reason=cancel_note)

        logger.info(
            "Order canceled by id: order_id=%s signal_id=%s code=%s reason=%s",
            order_id, signal_id, code, cancel_note,
        )

        return TradeResult(
            signal_id=signal_id,
            order_id=order_id,
            side=side,
            code=code,
            status="rejected",
            fill_price=None,
            fill_quantity=None,
            fee=None,
            reason=cancel_note,
        )

    def modify_order(
        self,
        order_id: int,
        new_price: Optional[float] = None,
        new_quantity: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> TradeResult:
        """Modify a pending limit order by its id (G5).

        Mirrors :meth:`modify_signal` but operates directly on the order. If
        the order is linked to a signal, the signal reason is updated to
        record the modification.

        Args:
            order_id: The id of the order to modify.
            new_price: New limit price (None = keep original).
            new_quantity: New TOTAL quantity (None = keep original).
            reason: Free-text modification reason.

        Returns:
            TradeResult describing the new (replacement) order.

        Raises:
            ValueError: If the order_id does not exist, the order is not
                modifiable, or no modification parameters were supplied.
        """
        if new_price is None and new_quantity is None:
            raise ValueError(
                "modify_order requires at least one of new_price / new_quantity"
            )

        order = self.order_mgr.get_order(order_id)
        if order is None:
            raise ValueError(f"Order id={order_id} not found")
        if order.status not in (
            OrderStatus.PENDING.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ):
            raise ValueError(
                f"Cannot modify order in status={order.status}"
            )
        if order.order_type != OrderType.LIMIT.value:
            raise ValueError(
                f"Cannot modify order id={order_id}: only limit orders can be modified"
            )

        account_id = int(order.account_id)
        side = order.side
        code = order.code
        old_price = float(order.price or 0.0)
        old_qty = float(order.quantity or 0.0)
        filled_qty = float(order.filled_quantity or 0.0)
        old_remaining = max(0.0, old_qty - filled_qty)
        signal_id = int(order.signal_id) if order.signal_id else None

        if new_quantity is not None:
            if new_quantity < filled_qty:
                raise ValueError(
                    f"new_quantity {new_quantity} < already-filled {filled_qty}"
                )
            new_remaining = float(new_quantity) - filled_qty
        else:
            new_remaining = old_remaining
        new_price_val = float(new_price) if new_price is not None else old_price

        # For buy orders: unfreeze old, freeze new.
        if side == "buy":
            if old_remaining > 0 and old_price > 0:
                try:
                    old_frozen = self.fee_model.estimate_buy_cost(old_price, old_remaining)
                    self.account_mgr.unfreeze_cash(account_id, old_frozen)
                except Exception as exc:
                    logger.warning(
                        "Unfreeze old order failed during modify_order: order=%s err=%s",
                        order_id, exc,
                    )

            if new_remaining > 0 and new_price_val > 0:
                try:
                    new_frozen = self.fee_model.estimate_buy_cost(new_price_val, new_remaining)
                    self.account_mgr.freeze_cash(account_id, new_frozen)
                except ValueError as exc:
                    logger.warning(
                        "Re-freeze failed during modify_order; canceling: order=%s err=%s",
                        order_id, exc,
                    )
                    return self.cancel_order(
                        order_id,
                        reason=f"modify re-freeze failed: {exc}",
                    )

        new_order = self.order_mgr.modify_order(
            order_id=order_id,
            new_price=new_price,
            new_quantity=new_quantity,
            reason=reason,
        )

        mod_note = (
            f"modified: old_order={order_id} -> new_order={new_order.id} "
            f"new_price={new_price_val} new_total_qty={new_quantity}"
        )
        if reason:
            mod_note = f"{reason} | {mod_note}"

        # Update linked signal audit trail if present.
        if signal_id is not None:
            self._update_signal_status(signal_id, "pending", reason=mod_note)

        logger.info(
            "Order modified by id: old_order=%s new_order=%s code=%s",
            order_id, new_order.id, code,
        )

        return TradeResult(
            signal_id=signal_id,
            order_id=int(new_order.id),
            side=side,
            code=code,
            status="pending",
            fill_price=None,
            fill_quantity=None,
            fee=None,
            reason=mod_note,
        )

    def _find_cancellable_order_for_signal(
        self, signal_id: int
    ) -> Optional[Dict[str, Any]]:
        """Find the most recent cancellable order linked to a signal.

        Returns a dict snapshot of the order, or None if no cancellable
        (pending / partially_filled) order is found.
        """
        with self.db.session_scope() as session:
            row = session.execute(
                select(PaperOrder)
                .where(
                    PaperOrder.signal_id == signal_id,
                    PaperOrder.status.in_([
                        OrderStatus.PENDING.value,
                        OrderStatus.PARTIALLY_FILLED.value,
                    ]),
                )
                .order_by(PaperOrder.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "account_id": row.account_id,
                "code": row.code,
                "name": row.name,
                "side": row.side,
                "order_type": row.order_type,
                "price": row.price,
                "quantity": float(row.quantity or 0.0),
                "filled_quantity": float(row.filled_quantity or 0.0),
                "status": row.status,
                "signal_id": row.signal_id,
            }
