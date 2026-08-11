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
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    OCO_PRIMARY = "oco_primary"
    OCO_SECONDARY = "oco_secondary"


class OrderStatus(str, Enum):
    PENDING = "pending"
    CONDITIONAL = "conditional"
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
    trigger_price: Optional[float] = None
    name: Optional[str] = None
    strategy_name: Optional[str] = None
    signal_id: Optional[int] = None
    linked_order_id: Optional[int] = None
    parent_order_id: Optional[int] = None
    reason: Optional[str] = None

    def validate(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"order quantity must be positive, got {self.quantity}")
        if self.order_type == OrderType.LIMIT and (self.price is None or self.price <= 0):
            raise ValueError("limit order requires a positive price")
        if self.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"invalid side: {self.side}")
        if self.order_type in (
            OrderType.STOP_LOSS,
            OrderType.TAKE_PROFIT,
            OrderType.OCO_PRIMARY,
            OrderType.OCO_SECONDARY,
        ):
            if self.trigger_price is None or self.trigger_price <= 0:
                raise ValueError(f"{self.order_type.value} order requires a positive trigger_price")


class OrderManager:
    """CRUD + state transitions for paper orders and trade fills."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or get_db()

    # ------------------------------------------------------------------
    # Create / cancel / reject
    # ------------------------------------------------------------------

    def create_order(self, req: OrderRequest) -> PaperOrder:
        """Persist a new order.

        Market/limit orders start in `pending`. Conditional orders
        (stop-loss / take-profit / OCO) start in `conditional` and are
        activated later by :meth:`match_conditional_orders`.
        """
        req.validate()
        is_conditional = req.order_type in (
            OrderType.STOP_LOSS,
            OrderType.TAKE_PROFIT,
            OrderType.OCO_PRIMARY,
            OrderType.OCO_SECONDARY,
        )
        status = OrderStatus.CONDITIONAL.value if is_conditional else OrderStatus.PENDING.value
        with self.db.session_scope() as session:
            order = PaperOrder(
                account_id=req.account_id,
                code=req.code,
                name=req.name,
                side=req.side.value,
                order_type=req.order_type.value,
                price=req.price,
                trigger_price=req.trigger_price,
                quantity=float(req.quantity),
                filled_quantity=0.0,
                filled_price_avg=0.0,
                status=status,
                strategy_name=req.strategy_name,
                signal_id=req.signal_id,
                reason=req.reason,
                linked_order_id=req.linked_order_id,
                parent_order_id=req.parent_order_id,
            )
            session.add(order)
            session.flush()
            order_id = order.id
            logger.info(
                "Order created: id=%s code=%s side=%s qty=%s type=%s status=%s",
                order_id,
                req.code,
                req.side.value,
                req.quantity,
                req.order_type.value,
                status,
            )
        return self._get_order(order_id)

    def create_conditional_order(
        self,
        account_id: int,
        code: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType,
        trigger_price: float,
        price: Optional[float] = None,
        name: Optional[str] = None,
        strategy_name: Optional[str] = None,
        linked_order_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> PaperOrder:
        """Create a conditional order in `conditional` status.

        Args:
            account_id: Target paper account.
            code: Stock code.
            side: buy | sell.
            quantity: Number of shares.
            order_type: STOP_LOSS | TAKE_PROFIT | OCO_PRIMARY | OCO_SECONDARY.
            trigger_price: Price at which the condition is evaluated.
            price: Optional limit price after trigger (None -> market order).
            name: Stock name.
            strategy_name: Originating strategy.
            linked_order_id: Sibling order id (OCO pair or SL/TP sibling).
            reason: Human-readable reason.

        Returns:
            The created PaperOrder row.
        """
        req = OrderRequest(
            account_id=account_id,
            code=code,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            trigger_price=trigger_price,
            name=name,
            strategy_name=strategy_name,
            linked_order_id=linked_order_id,
            reason=reason,
        )
        return self.create_order(req)

    def create_batch_orders(
        self, account_id: int, requests: List[OrderRequest]
    ) -> List[PaperOrder]:
        """Create multiple orders inside a single session.

        Each request is validated individually; the first invalid request
        aborts the whole batch and raises ValueError.

        Returns:
            List of created PaperOrder rows in the same order as the input.
        """
        if not requests:
            return []
        order_ids: List[int] = []
        with self.db.session_scope() as session:
            for req in requests:
                req.validate()
                is_conditional = req.order_type in (
                    OrderType.STOP_LOSS,
                    OrderType.TAKE_PROFIT,
                    OrderType.OCO_PRIMARY,
                    OrderType.OCO_SECONDARY,
                )
                status = (
                    OrderStatus.CONDITIONAL.value
                    if is_conditional
                    else OrderStatus.PENDING.value
                )
                order = PaperOrder(
                    account_id=account_id,
                    code=req.code,
                    name=req.name,
                    side=req.side.value,
                    order_type=req.order_type.value,
                    price=req.price,
                    trigger_price=req.trigger_price,
                    quantity=float(req.quantity),
                    filled_quantity=0.0,
                    filled_price_avg=0.0,
                    status=status,
                    strategy_name=req.strategy_name,
                    signal_id=req.signal_id,
                    reason=req.reason,
                    linked_order_id=req.linked_order_id,
                    parent_order_id=req.parent_order_id,
                )
                session.add(order)
                session.flush()
                order_ids.append(order.id)
            logger.info(
                "Batch orders created: account=%s count=%s",
                account_id, len(order_ids),
            )
        return [self._get_order(oid) for oid in order_ids]

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

    def cancel_order(self, order_id: int, reason: Optional[str] = None,
                      expected_version: Optional[int] = None) -> PaperOrder:  # T19
        """Cancel a pending or partially-filled order.

        Args:
            order_id: The id of the order to cancel.
            reason: Free-text cancel reason (recorded in cancel_reason).
            expected_version: (T19) If provided and mismatch, returns None.

        Returns:
            The canceled PaperOrder row, or None on version conflict.

        Raises:
            ValueError: If the order is not found or not in a cancellable status.
        """
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is None:
                raise ValueError(f"Order id={order_id} not found")
            # T19: version check
            if expected_version is not None and order.version != expected_version:
                logger.warning("Order %d cancel version mismatch", order_id)
                return None
            order.version += 1
            if order.status not in (
                OrderStatus.PENDING.value,
                OrderStatus.CONDITIONAL.value,
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
        """Mark a pending or conditional order as rejected (e.g., risk check failed)."""
        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is None:
                raise ValueError(f"Order id={order_id} not found")
            if order.status not in (
                OrderStatus.PENDING.value,
                OrderStatus.CONDITIONAL.value,
            ):
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
        expected_version: Optional[int] = None,  # T19: optimistic lock
    ) -> PaperTrade:
        """Fill (part of) an order at the given price.

        - If fill_quantity is None, fill the entire remaining quantity.
        - Creates a PaperTrade row, updates the order's filled state,
          and returns the trade record.
        - If expected_version is provided and does not match the order's
          current version, the fill is skipped (returns None) — this allows
          callers to detect concurrent modifications (T19).
        """
        if fill_price <= 0:
            raise ValueError(f"fill_price must be positive, got {fill_price}")

        with self.db.session_scope() as session:
            order = session.execute(
                select(PaperOrder).where(PaperOrder.id == order_id)
            ).scalar_one_or_none()
            if order is None:
                raise ValueError(f"Order id={order_id} not found")

            # T19: Optimistic-lock version check (skip on mismatch).
            if expected_version is not None and order.version != expected_version:
                logger.warning(
                    "Order %d version mismatch: expected %d, actual %d; skipping fill",
                    order_id, expected_version, order.version,
                )
                return None
            order.version += 1  # T19: bump version on every state change

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
    # Conditional orders
    # ------------------------------------------------------------------

    def match_conditional_orders(
        self,
        account_id: int,
        code: str,
        price: float,
    ) -> List[Dict[str, Any]]:
        """Evaluate conditional orders against a new market price.

        Trigger rules:
        - sell + STOP_LOSS / OCO_PRIMARY:  price <= trigger_price
        - sell + TAKE_PROFIT / OCO_SECONDARY: price >= trigger_price
        - buy  + STOP_LOSS / OCO_PRIMARY:  price >= trigger_price
        - buy  + TAKE_PROFIT / OCO_SECONDARY: price <= trigger_price

        When an OCO order triggers, its linked sibling is canceled
        automatically (One-Cancels-the-Other).

        Triggered orders are moved to ``pending`` status with
        ``order_type`` set to ``market`` (if no limit price was set) or
        ``limit`` (if ``price`` is set). The caller (usually
        :class:`TradingEngine`) is responsible for filling market orders
        immediately and driving limit orders through the normal matcher.

        Returns:
            List of activated order dicts (new status = pending).
        """
        triggered: List[Dict[str, Any]] = []
        with self.db.session_scope() as session:
            rows = session.execute(
                select(PaperOrder).where(
                    PaperOrder.account_id == account_id,
                    PaperOrder.code == code,
                    PaperOrder.status == OrderStatus.CONDITIONAL.value,
                )
            ).scalars().all()

            for order in rows:
                if not self._is_conditional_triggered(order, price):
                    continue

                # Determine post-trigger order type.
                new_order_type = (
                    OrderType.LIMIT.value
                    if order.price is not None and order.price > 0
                    else OrderType.MARKET.value
                )
                order.order_type = new_order_type
                order.status = OrderStatus.PENDING.value
                order.triggered_at = datetime.now()
                triggered.append(self._order_to_dict(order))

                # OCO behavior: cancel the linked sibling.
                if order.linked_order_id is not None:
                    self._cancel_linked_order(
                        session, order.linked_order_id,
                        reason=f"OCO sibling order={order.id} triggered",
                    )

        return triggered

    @staticmethod
    def _is_conditional_triggered(order: PaperOrder, price: float) -> bool:
        """Return True if a conditional order's trigger condition is met."""
        trigger = float(order.trigger_price or 0.0)
        if trigger <= 0:
            return False
        side = order.side
        order_type = order.order_type
        if side == "sell":
            if order_type in (
                OrderType.STOP_LOSS.value,
                OrderType.OCO_PRIMARY.value,
            ):
                return price <= trigger
            if order_type in (
                OrderType.TAKE_PROFIT.value,
                OrderType.OCO_SECONDARY.value,
            ):
                return price >= trigger
        else:  # buy
            if order_type in (
                OrderType.STOP_LOSS.value,
                OrderType.OCO_PRIMARY.value,
            ):
                return price >= trigger
            if order_type in (
                OrderType.TAKE_PROFIT.value,
                OrderType.OCO_SECONDARY.value,
            ):
                return price <= trigger
        return False

    @staticmethod
    def _cancel_linked_order(
        session: Any, linked_order_id: int, reason: str
    ) -> None:
        """Cancel an OCO/linked sibling order in the same session."""
        from sqlalchemy import select as _select
        linked = session.execute(
            _select(PaperOrder).where(PaperOrder.id == linked_order_id)
        ).scalar_one_or_none()
        if linked is None:
            return
        if linked.status in (
            OrderStatus.PENDING.value,
            OrderStatus.CONDITIONAL.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ):
            linked.status = OrderStatus.CANCELED.value
            linked.cancel_reason = reason
            linked.reject_reason = reason

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_orders(
        self,
        account_id: int,
        status: Optional[str] = None,
        side: Optional[str] = None,
        code: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(PaperOrder).where(PaperOrder.account_id == account_id)
            if status:
                stmt = stmt.where(PaperOrder.status == status)
            if side:
                stmt = stmt.where(PaperOrder.side == side)
            if code:
                stmt = stmt.where(PaperOrder.code == code)
            if from_date:
                stmt = stmt.where(PaperOrder.created_at >= from_date)
            if to_date:
                stmt = stmt.where(PaperOrder.created_at <= to_date)
            stmt = (
                stmt.order_by(desc(PaperOrder.created_at))
                .limit(limit)
                .offset(offset)
            )
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
            "trigger_price": o.trigger_price,
            "quantity": o.quantity,
            "filled_quantity": o.filled_quantity,
            "filled_price_avg": o.filled_price_avg,
            "status": o.status,
            "strategy_name": o.strategy_name,
            "signal_id": o.signal_id,
            "reason": o.reason,
            "reject_reason": o.reject_reason,
            "cancel_reason": o.cancel_reason,
            "linked_order_id": o.linked_order_id,
            "parent_order_id": o.parent_order_id,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            "triggered_at": o.triggered_at.isoformat() if o.triggered_at else None,
        }
