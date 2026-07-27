# -*- coding: utf-8 -*-
"""Order model and manager for paper trading.

Status state machine:
    pending -> partially_filled -> filled
    pending -> canceled
    pending -> rejected

A market order is filled immediately at the supplied fill price.
A limit order is filled when the market price crosses the limit price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, select

from src.storage import DatabaseManager, PaperOrder, PaperTrade, get_db

logger = logging.getLogger(__name__)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass
class OrderRequest:
    """In-memory order request (not yet persisted)."""

    account_id: int
    code: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    price: Optional[float] = None
    name: Optional[str] = None
    strategy_name: Optional[str] = None
    signal_id: Optional[int] = None
    reason: Optional[str] = None

    def validate(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"order quantity must be positive, got {self.quantity}")
        if self.order_type == OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("limit order requires a positive price")
        if self.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"invalid side: {self.side}")


class OrderManager:
    """CRUD + state transitions for paper orders and trade fills."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or get_db()

    # ------------------------------------------------------------------
    # Create / cancel / reject
    # ------------------------------------------------------------------

    def create_order(self, req: OrderRequest) -> PaperOrder:
        """Persist a new order in `pending` status."""
        req.validate()
        with self.db.session_scope() as session:
            order = PaperOrder(
                account_id=req.account_id,
                code=req.code,
                name=req.name,
                side=req.side.value,
                order_type=req.order_type.value,
                price=req.price,
                quantity=float(req.quantity),
                filled_quantity=0.0,
                filled_price_avg=0.0,
                status=OrderStatus.PENDING.value,
                strategy_name=req.strategy_name,
                signal_id=req.signal_id,
                reason=req.reason,
            )
            session.add(order)
            session.flush()
            order_id = order.id
            logger.info(
                "Order created: id=%s code=%s side=%s qty=%s type=%s",
                order_id,
                req.code,
                req.side.value,
                req.quantity,
                req.order_type.value,
            )
        return self._get_order(order_id)

    def _get_order(self, order_id: int) -> PaperOrder:
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one()
            # Expunge so the returned instance remains usable after the
            # session closes (P0-C: callers frequently read .id / .status /
            # .cancel_reason / .parent_order_id outside the session scope).
            session.expunge(order)
            return order

    def get_order(self, order_id: int) -> Optional[PaperOrder]:
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is not None:
                # Expunge so callers can safely read attributes after the
                # session closes (consistent with _get_order).
                session.expunge(order)
            return order

    def cancel_order(self, order_id: int, reason: Optional[str] = None) -> PaperOrder:
        """Cancel a pending or partially-filled order.

        Args:
            order_id: The id of the order to cancel.
            reason: Free-text cancel reason (recorded in cancel_reason).

        Returns:
            The canceled PaperOrder row (re-fetched from a fresh session so
            it is safe to access attributes after this returns).

        Raises:
            ValueError: If the order is not found or not in a cancellable status.
        """
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is None:
                raise ValueError(f"Order id={order_id} not found")
            if order.status not in (
                OrderStatus.PENDING.value,
                OrderStatus.PARTIALLY_FILLED.value,
            ):
                raise ValueError(
                    f"Cannot cancel order in status={order.status}"
                )
            order.status = OrderStatus.CANCELED.value
            # P0-C: prefer the new cancel_reason column, but also keep
            # reject_reason populated for backwards compatibility with any
            # code that still reads reject_reason.
            order.cancel_reason = reason
            order.reject_reason = reason
            logger.info("Order canceled: id=%s reason=%s", order_id, reason)
        # Re-fetch from a fresh session so the returned row is bound to an
        # open session and attributes are accessible to the caller.
        return self._get_order(order_id)

    def modify_order(
        self,
        order_id: int,
        new_price: Optional[float] = None,
        new_quantity: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> PaperOrder:
        """Modify the price or quantity of a pending order (P0-C).

        Implementation strategy: cancel the original order and create a new
        one with the modified parameters, linked via ``parent_order_id``.
        This keeps the order state machine simple (no "modified" status) and
        provides a clean audit trail.

        - If both new_price and new_quantity are None, raises ValueError.
        - If new_quantity <= filled_quantity, the order is canceled (no new
          order is created) since there is nothing left to fill.
        - For buy orders, the cash freeze is **not** automatically adjusted
          here; the caller (TradingEngine.modify_signal) is responsible for
          unfreezing the old freeze and re-freezing the new estimated cost.

        Args:
            order_id: The id of the order to modify.
            new_price: New limit price (None = keep original price).
            new_quantity: New total quantity (None = keep original quantity).
                Note: this is the new TOTAL quantity, not a delta. The new
                order's quantity will be ``new_quantity - filled_quantity``
                (the remaining unfilled portion).
            reason: Free-text reason for the modification.

        Returns:
            The newly created PaperOrder row (status='pending'), or the
            canceled original if the modification left nothing to fill.

        Raises:
            ValueError: If the order is not found, not pending, or no
                modification parameters were supplied.
        """
        if new_price is None and new_quantity is None:
            raise ValueError(
                "modify_order requires at least one of new_price / new_quantity"
            )
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
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
                    f"Cannot modify order id={order_id}: only limit orders "
                    f"can be modified (got order_type={order.order_type})"
                )

            filled_qty = float(order.filled_quantity or 0.0)
            orig_qty = float(order.quantity or 0.0)
            orig_price = float(order.price or 0.0)

            # Compute the new remaining quantity to fill.
            if new_quantity is not None:
                if new_quantity < filled_qty:
                    raise ValueError(
                        f"new_quantity {new_quantity} < already-filled {filled_qty}"
                    )
                new_remaining = float(new_quantity) - filled_qty
            else:
                new_remaining = orig_qty - filled_qty

            new_price_val = float(new_price) if new_price is not None else orig_price

            # Cancel the original order.
            order.status = OrderStatus.CANCELED.value
            order.cancel_reason = reason or "modified"
            order.reject_reason = order.cancel_reason
            order.modified_at = datetime.now()
            original_id = order.id
            account_id = order.account_id
            code = order.code
            name = order.name
            side = order.side
            strategy_name = order.strategy_name
            signal_id = order.signal_id
            order_reason = order.reason

            # If nothing left to fill, just cancel and return the canceled row.
            if new_remaining <= 0:
                logger.info(
                    "Order modified to zero remaining: id=%s canceled (filled=%s)",
                    order_id, filled_qty,
                )
                return self._get_order(order_id)

            # Create the replacement order.
            replacement = PaperOrder(
                account_id=account_id,
                code=code,
                name=name,
                side=side,
                order_type=OrderType.LIMIT.value,
                price=new_price_val,
                quantity=new_remaining,
                filled_quantity=0.0,
                filled_price_avg=0.0,
                status=OrderStatus.PENDING.value,
                strategy_name=strategy_name,
                signal_id=signal_id,
                reason=order_reason,
                parent_order_id=original_id,
                modified_at=datetime.now(),
            )
            session.add(replacement)
            session.flush()
            new_id = replacement.id
            logger.info(
                "Order modified: old_id=%s -> new_id=%s code=%s new_price=%s new_qty=%s",
                order_id, new_id, code, new_price_val, new_remaining,
            )
        # Re-fetch the replacement from a fresh session.
        return self._get_order(new_id)

    def reject_order(self, order_id: int, reason: str) -> None:
        """Mark a pending order as rejected (e.g., risk check failed)."""
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is None:
                raise ValueError(f"Order id={order_id} not found")
            if order.status != OrderStatus.PENDING.value:
                raise ValueError(
                    f"Cannot reject order in status={order.status}"
                )
            order.status = OrderStatus.REJECTED.value
            order.reject_reason = reason
            logger.info("Order rejected: id=%s reason=%s", order_id, reason)

    # ------------------------------------------------------------------
    # Fill
    # ------------------------------------------------------------------

    def fill_order(
        self,
        order_id: int,
        fill_price: float,
        fill_quantity: Optional[float] = None,
        fee: float = 0.0,
    ) -> PaperTrade:
        """Fill (part of) an order at the given price.

        - If fill_quantity is None, fill the entire remaining quantity.
        - Creates a PaperTrade row, updates the order's filled state,
          and returns the trade record.
        """
        if fill_price <= 0:
            raise ValueError(f"fill_price must be positive, got {fill_price}")

        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is None:
                raise ValueError(f"Order id={order_id} not found")
            if order.status not in (
                OrderStatus.PENDING.value,
                OrderStatus.PARTIALLY_FILLED.value,
            ):
                raise ValueError(
                    f"Cannot fill order in status={order.status}"
                )

            remaining = float(order.quantity) - float(order.filled_quantity)
            qty = float(fill_quantity) if fill_quantity is not None else remaining
            if qty <= 0:
                raise ValueError(f"fill_quantity must be positive, got {qty}")
            if qty > remaining:
                qty = remaining

            amount = fill_price * qty
            trade = PaperTrade(
                account_id=order.account_id,
                order_id=order.id,
                code=order.code,
                name=order.name,
                side=order.side,
                price=fill_price,
                quantity=qty,
                amount=amount,
                fee=float(fee),
                traded_at=datetime.now(),
            )
            session.add(trade)

            # Update order filled state.
            old_filled_qty = float(order.filled_quantity)
            old_filled_amount = old_filled_qty * float(order.filled_price_avg)
            new_filled_qty = old_filled_qty + qty
            new_filled_amount = old_filled_amount + amount
            order.filled_quantity = new_filled_qty
            order.filled_price_avg = (
                new_filled_amount / new_filled_qty if new_filled_qty else 0.0
            )

            if new_filled_qty >= float(order.quantity) - 1e-9:
                order.status = OrderStatus.FILLED.value
                order.filled_at = datetime.now()
            else:
                order.status = OrderStatus.PARTIALLY_FILLED.value

            # Flush so trade.id is populated before we read it below.
            # (P0-C: without this, trade.id is None until commit, causing the
            # detached re-fetch query to fail with NoResultFound.)
            session.flush()
            trade_id = trade.id
            logger.info(
                "Order filled: id=%s qty=%s price=%s new_status=%s",
                order_id,
                qty,
                fill_price,
                order.status,
            )

        # Return a detached view of the trade.
        # Expunge so the returned instance remains usable after the session
        # closes (P1-C: callers in TradingEngine._execute_market_order read
        # trade.id after the session_scope exits; without expunge the
        # commit-triggered attribute expiry would raise DetachedInstanceError).
        with self.db.session_scope() as session:
            trade = session.execute(
                select(PaperTrade).where(PaperTrade.id == trade_id)
            ).scalar_one()
            session.expunge(trade)
            return trade

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_orders(
        self,
        account_id: int,
        status: Optional[str] = None,
        code: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(PaperOrder).where(PaperOrder.account_id == account_id)
            if status:
                stmt = stmt.where(PaperOrder.status == status)
            if code:
                stmt = stmt.where(PaperOrder.code == code)
            stmt = stmt.order_by(desc(PaperOrder.created_at)).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [self._order_to_dict(o) for o in rows]

    def list_trades(
        self,
        account_id: int,
        code: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(PaperTrade).where(PaperTrade.account_id == account_id)
            if code:
                stmt = stmt.where(PaperTrade.code == code)
            stmt = stmt.order_by(desc(PaperTrade.traded_at)).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "id": t.id,
                    "order_id": t.order_id,
                    "code": t.code,
                    "name": t.name,
                    "side": t.side,
                    "price": t.price,
                    "quantity": t.quantity,
                    "amount": t.amount,
                    "fee": t.fee,
                    "traded_at": t.traded_at.isoformat() if t.traded_at else None,
                }
                for t in rows
            ]

    @staticmethod
    def _order_to_dict(o: PaperOrder) -> Dict[str, Any]:
        return {
            "id": o.id,
            "account_id": o.account_id,
            "code": o.code,
            "name": o.name,
            "side": o.side,
            "order_type": o.order_type,
            "price": o.price,
            "quantity": o.quantity,
            "filled_quantity": o.filled_quantity,
            "filled_price_avg": o.filled_price_avg,
            "status": o.status,
            "strategy_name": o.strategy_name,
            "signal_id": o.signal_id,
            "reason": o.reason,
            "reject_reason": o.reject_reason,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        }
