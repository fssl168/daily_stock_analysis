# -*- coding: utf-8 -*-
"""Position manager for paper trading (long-only).

Each (account_id, code) has at most one position row. Buys increase the
position with weighted-average cost; sells reduce the quantity and realize
PnL into the account cash via the OrderManager / AccountManager settle path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.storage import DatabaseManager, PaperPosition, get_db

logger = logging.getLogger(__name__)


@dataclass
class PositionSnapshot:
    """Read-only view of a position for API responses."""

    id: int
    account_id: int
    code: str
    name: Optional[str]
    quantity: float
    available_quantity: float
    avg_cost: float
    last_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    take_profit_2: Optional[float]
    sltp_reasoning: Optional[str]
    market_value: float
    floating_pnl: float
    floating_pnl_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "code": self.code,
            "name": self.name,
            "quantity": self.quantity,
            "available_quantity": self.available_quantity,
            "avg_cost": self.avg_cost,
            "last_price": self.last_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "take_profit_2": self.take_profit_2,
            "sltp_reasoning": self.sltp_reasoning,
            "market_value": self.market_value,
            "floating_pnl": self.floating_pnl,
            "floating_pnl_pct": self.floating_pnl_pct,
        }


class PositionManager:
    """CRUD + cost averaging for long positions."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or get_db()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def _get_position_row(
        self,
        account_id: int,
        code: str,
        session,
    ) -> Optional[PaperPosition]:
        return session.execute(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.code == code,
            )
        ).scalar_one_or_none()

    def get_position(self, account_id: int, code: str) -> Optional[PaperPosition]:
        """Return the PaperPosition for (account_id, code).

        The returned instance is expunged from the session so its attributes
        remain accessible after the session closes (avoids DetachedInstanceError
        when callers read .quantity / .available_quantity / .stop_loss etc.
        outside a session scope).
        """
        with self.db.session_scope() as session:
            pos = self._get_position_row(account_id, code, session)
            if pos is not None:
                # Expunge before session close so attribute access doesn't
                # trigger a refresh on a detached instance.
                session.expunge(pos)
            return pos

    def list_positions(
        self, account_id: int, include_zero: bool = False
    ) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(PaperPosition).where(PaperPosition.account_id == account_id)
            if not include_zero:
                stmt = stmt.where(PaperPosition.quantity > 0)
            stmt = stmt.order_by(desc(PaperPosition.quantity))
            rows = session.execute(stmt).scalars().all()
            return [self._snapshot(row).to_dict() for row in rows]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def apply_buy(
        self,
        account_id: int,
        code: str,
        quantity: float,
        price: float,
        name: Optional[str] = None,
    ) -> None:
        """Increase position by `quantity` at `price`, recompute avg cost.

        Note: T+1 rule — newly bought shares are added to `quantity` but NOT
        to `available_quantity`. The daily roll at end-of-trading will move
        them into available_quantity.
        """
        if quantity <= 0:
            raise ValueError(f"buy quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"buy price must be positive, got {price}")

        with self.db.session_scope() as session:
            pos = self._get_position_row(account_id, code, session)
            if pos is None:
                pos = PaperPosition(
                    account_id=account_id,
                    code=code,
                    name=name,
                    quantity=0.0,
                    available_quantity=0.0,
                    avg_cost=0.0,
                    last_price=price,
                )
                session.add(pos)

            old_qty = float(pos.quantity or 0.0)
            old_cost = float(pos.avg_cost or 0.0)
            new_qty = old_qty + quantity
            pos.avg_cost = (
                (old_qty * old_cost + quantity * price) / new_qty if new_qty else 0.0
            )
            pos.quantity = new_qty
            # Newly bought shares are NOT available today (T+1).
            if name:
                pos.name = name
            pos.last_price = price

    def apply_sell(
        self,
        account_id: int,
        code: str,
        quantity: float,
        price: float,
    ) -> float:
        """Reduce position by `quantity` at `price`.

        Returns realized PnL = (price - avg_cost) * quantity.

        Raises if there is no position or insufficient available_quantity.
        """
        if quantity <= 0:
            raise ValueError(f"sell quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"sell price must be positive, got {price}")

        with self.db.session_scope() as session:
            pos = self._get_position_row(account_id, code, session)
            if pos is None or float(pos.quantity or 0.0) <= 0:
                raise ValueError(f"No position to sell: code={code}")

            if float(pos.available_quantity or 0.0) < quantity - 1e-9:
                raise ValueError(
                    f"Insufficient available quantity: have {pos.available_quantity}, "
                    f"need {quantity} (T+1 may apply)"
                )

            avg_cost = float(pos.avg_cost or 0.0)
            realized_pnl = (price - avg_cost) * quantity

            pos.quantity = float(pos.quantity) - quantity
            pos.available_quantity = float(pos.available_quantity) - quantity
            pos.last_price = price
            if pos.quantity <= 1e-9:
                # Position fully closed — clear cost to avoid stale state.
                pos.quantity = 0.0
                pos.available_quantity = 0.0
                pos.avg_cost = 0.0
            return realized_pnl

    def unfreeze_quantity(
        self,
        account_id: int,
        code: str,
        quantity: float,
    ) -> None:
        """Release frozen quantity back to available (P0-C gap fill).

        In the current design, sell limit orders do NOT freeze position
        quantity (availability is checked at submit time via RiskChecker).
        However, if a future enhancement adds position freezing for sell
        limit orders, this method provides the symmetric unfreeze path
        required by OrderManager.cancel_order / modify_order.

        Args:
            account_id: Account id.
            code: Stock code.
            quantity: Quantity to unfreeze (must be non-negative).

        Raises:
            ValueError: If quantity is negative or no position exists.
        """
        if quantity < 0:
            raise ValueError(f"unfreeze quantity must be non-negative, got {quantity}")
        if quantity == 0:
            return
        with self.db.session_scope() as session:
            pos = self._get_position_row(account_id, code, session)
            if pos is None:
                raise ValueError(
                    f"No position to unfreeze: account={account_id} code={code}"
                )
            current_available = float(pos.available_quantity or 0.0)
            current_qty = float(pos.quantity or 0.0)
            # Restore available_quantity but never exceed total quantity.
            pos.available_quantity = min(current_available + quantity, current_qty)
            logger.info(
                "Position unfrozen: account=%s code=%s qty=%s available=%s->%s",
                account_id, code, quantity,
                current_available, pos.available_quantity,
            )

    def update_last_price(
        self, account_id: int, code: str, last_price: float
    ) -> None:
        """Sync the latest market price for PnL display."""
        if last_price < 0:
            return
        with self.db.session_scope() as session:
            pos = self._get_position_row(account_id, code, session)
            if pos is None:
                return
            pos.last_price = float(last_price)

    def update_stop_loss_take_profit(
        self,
        account_id: int,
        code: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        take_profit_2: Optional[float] = None,
        sltp_reasoning: Optional[str] = None,
    ) -> None:
        """Update SL/TP guards on a position (P1-A extended for TP2 + reasoning).

        Any field left as None is left untouched (not cleared), so callers can
        update just the fields they care about.
        """
        with self.db.session_scope() as session:
            pos = self._get_position_row(account_id, code, session)
            if pos is None:
                return
            if stop_loss is not None:
                pos.stop_loss = float(stop_loss)
            if take_profit is not None:
                pos.take_profit = float(take_profit)
            if take_profit_2 is not None:
                pos.take_profit_2 = float(take_profit_2)
            if sltp_reasoning is not None:
                pos.sltp_reasoning = str(sltp_reasoning)[:1000]

    # ------------------------------------------------------------------
    # Daily roll
    # ------------------------------------------------------------------

    def daily_roll_available(self, account_id: int) -> int:
        """Move all held quantity into available_quantity (call at end-of-trading).

        Returns the number of positions updated.
        """
        updated = 0
        with self.db.session_scope() as session:
            positions = session.execute(
                select(PaperPosition).where(PaperPosition.account_id == account_id)
            ).scalars().all()
            for pos in positions:
                if float(pos.quantity or 0.0) <= 0:
                    continue
                if float(pos.available_quantity) < float(pos.quantity):
                    pos.available_quantity = float(pos.quantity)
                    updated += 1
        return updated

    # ------------------------------------------------------------------
    # Snapshot helper
    # ------------------------------------------------------------------

    def _snapshot(self, pos: PaperPosition) -> PositionSnapshot:
        qty = float(pos.quantity or 0.0)
        last_price = float(pos.last_price or 0.0)
        avg_cost = float(pos.avg_cost or 0.0)
        market_value = qty * last_price
        floating_pnl = (last_price - avg_cost) * qty
        floating_pnl_pct = (
            ((last_price - avg_cost) / avg_cost * 100.0) if avg_cost > 0 else 0.0
        )
        return PositionSnapshot(
            id=pos.id,
            account_id=pos.account_id,
            code=pos.code,
            name=pos.name,
            quantity=qty,
            available_quantity=float(pos.available_quantity or 0.0),
            avg_cost=avg_cost,
            last_price=last_price,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            take_profit_2=pos.take_profit_2,
            sltp_reasoning=pos.sltp_reasoning,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=floating_pnl_pct,
        )
