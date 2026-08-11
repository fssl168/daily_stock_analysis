# -*- coding: utf-8 -*-
"""Virtual account manager for paper trading.

Responsibilities:
1. Create / fetch the canonical paper-trading account (default 1000 CNY).
2. Maintain cash / frozen_cash / status with transactional safety.
3. Snapshot account state for API responses.
4. Persist daily net-value curve.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_, delete, desc, select

from src.storage import (
    DatabaseManager,
    Account,
    PaperBattlePlan,
    PaperDecision,
    PaperNetValue,
    PaperOrder,
    PaperPosition,
    PaperReflection,
    PaperSignal,
    PaperTrade,
    get_db,
)

logger = logging.getLogger(__name__)


# Default initial capital (CNY) per project requirement.
DEFAULT_INITIAL_CAPITAL = 1000.0


@dataclass
class AccountSnapshot:
    """Read-only view of an account for API responses."""

    id: int
    name: str
    initial_capital: float
    cash: float
    frozen_cash: float
    status: str
    market_value: float = 0.0
    total_assets: float = 0.0
    pnl_pct: float = 0.0
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "frozen_cash": self.frozen_cash,
            "status": self.status,
            "market_value": self.market_value,
            "total_assets": self.total_assets,
            "pnl_pct": self.pnl_pct,
            "config": self.config,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PaperAccountManager:
    """Manage the canonical paper-trading account and its net-value curve."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or get_db()

    # ------------------------------------------------------------------
    # Account lifecycle
    # ------------------------------------------------------------------

    def get_or_create_account(
        self,
        name: str = "default",
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        config: Optional[Dict[str, Any]] = None,
    ) -> Account:
        """Return the named account, creating it on first call.

        If the account already exists, `initial_capital` and `config` are
        ignored — call `reset_account` to reinitialize.
        """
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.name == name)
            ).scalar_one_or_none()

            if account is not None:
                # Return a fresh, properly-bound instance by re-fetching
                return self._get_account_by_id(account.id)

            account = Account(
                name=name,
                initial_capital=float(initial_capital),
                cash=float(initial_capital),
                frozen_cash=0.0,
                status="active",
                config_json=json.dumps(config or {}, ensure_ascii=False),
            )
            session.add(account)
            session.flush()
            account_id = account.id
            logger.info(
                "Paper account created: name=%s capital=%.2f", name, float(initial_capital)
            )

        # Re-fetch to return a detached instance attached to a fresh session scope.
        return self._get_account_by_id(account_id)

    def _get_account_by_id(self, account_id: int) -> Account:
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            # Expunge before closing session so attribute access doesn't trigger refresh
            # on a detached/ expired instance.
            session.expunge(account)
            return account

    def get_account(self, name: str = "default") -> Optional[Account]:
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.name == name)
            ).scalar_one_or_none()
            if account is not None:
                session.expunge(account)
            return account

    def list_accounts(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Account]:
        """List paper trading accounts ordered by id ascending."""
        with self.db.session_scope() as session:
            query = select(Account).where(Account.account_type == 'paper')
            if status:
                query = query.where(Account.status == status)
            query = query.order_by(Account.id.asc()).limit(limit).offset(offset)
            rows = session.execute(query).scalars().all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def set_status(self, account_id: int, status: str) -> None:
        """Update account status: active / paused / stopped."""
        if status not in {"active", "paused", "stopped"}:
            raise ValueError(f"Invalid account status: {status}")
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            account.status = status

    def reset_account(self, account_id: int, new_capital: Optional[float] = None) -> None:
        """Reset account to initial state (clear cash/positions/orders).

        WARNING: This does NOT delete historical orders/trades/net-values —
        they are kept for audit. Only the live state (cash, positions) is reset.
        Call `purge_account` instead if you want a full wipe.
        """
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")

            target_capital = (
                float(new_capital) if new_capital is not None else float(account.initial_capital)
            )
            account.initial_capital = target_capital
            account.cash = target_capital
            account.frozen_cash = 0.0
            account.status = "active"

            # Clear all open positions (use bulk delete to avoid ORM relationship issues).
            session.execute(
                delete(PaperPosition).where(PaperPosition.account_id == account_id)
            )

            logger.info("Paper account reset: id=%s capital=%.2f", account_id, target_capital)

    def update_account(
        self,
        account_id: int,
        *,
        name: Optional[str] = None,
        initial_capital: Optional[float] = None,
    ) -> Account:
        """Update paper account metadata.

        - ``name`` renames the account (must be unique).
        - ``initial_capital`` only updates the stored initial capital;
          it does NOT reset live cash/positions. Use ``reset_account``
          to reinitialize live state.
        """
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")

            if name is not None and name != account.name:
                existing = session.execute(
                    select(Account).where(Account.name == name, Account.id != account_id)
                ).scalar_one_or_none()
                if existing is not None:
                    raise ValueError(f"Account name '{name}' already exists")
                account.name = name

            if initial_capital is not None:
                account.initial_capital = float(initial_capital)

            session.flush()
            account_id = account.id

        return self._get_account_by_id(account_id)

    def delete_account(self, account_id: int) -> None:
        """Permanently delete a paper account and all its paper-trading data.

        Only ``account_type == 'paper'`` accounts may be deleted to avoid
        accidentally destroying linked portfolio records. Related paper
        tables are cleaned up explicitly because the schema does not use
        cascading deletes.
        """
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            if account.account_type != "paper":
                raise ValueError(
                    f"Account id={account_id} is not a paper account (type={account.account_type})"
                )

            # Delete paper-trading related rows in dependency order.
            for model in (
                PaperBattlePlan,
                PaperReflection,
                PaperDecision,
                PaperSignal,
                PaperTrade,
                PaperOrder,
                PaperNetValue,
                PaperPosition,
            ):
                session.execute(delete(model).where(model.account_id == account_id))

            session.delete(account)
            logger.info("Paper account deleted: id=%s name=%s", account_id, account.name)

    # ------------------------------------------------------------------
    # Cash operations
    # ------------------------------------------------------------------

    def freeze_cash(self, account_id: int, amount: float) -> None:
        """Freeze cash for a pending buy order."""
        if amount < 0:
            raise ValueError("freeze amount must be non-negative")
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            if account.cash < amount:
                raise ValueError(
                    f"Insufficient cash: have {account.cash:.2f}, need {amount:.2f}"
                )
            account.cash -= amount
            account.frozen_cash += amount

    def unfreeze_cash(self, account_id: int, amount: float) -> None:
        """Release frozen cash back to available (e.g., order canceled)."""
        if amount < 0:
            raise ValueError("unfreeze amount must be non-negative")
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            actual = min(amount, account.frozen_cash)
            account.frozen_cash -= actual
            account.cash += actual

    def settle_buy(self, account_id: int, frozen_amount: float, actual_cost: float) -> None:
        """After a buy fills: release frozen cash and debit actual cost.

        - If actual_cost < frozen_amount: leftover returns to cash.
        - If actual_cost > frozen_amount: extra is debited from cash
          (should not happen under normal lot-controlled ordering).
        """
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            # Release the frozen portion first.
            account.frozen_cash = max(0.0, account.frozen_cash - frozen_amount)
            # Debit actual cost from cash.
            account.cash -= actual_cost
            # If we over-debited (rare), pull from frozen then warn.
            if account.cash < 0:
                logger.warning(
                    "Account %s cash went negative after buy settle: cash=%.2f",
                    account_id,
                    account.cash,
                )

    def settle_sell(self, account_id: int, proceeds: float) -> None:
        """Credit sell proceeds (amount net of fees already deducted)."""
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")
            account.cash += proceeds

    # ------------------------------------------------------------------
    # Snapshot / valuation
    # ------------------------------------------------------------------

    def snapshot(self, account_id: int) -> AccountSnapshot:
        """Build a valuation snapshot using current position.last_price."""
        with self.db.session_scope() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if account is None:
                raise ValueError(f"Paper account id={account_id} not found")

            positions = session.execute(
                select(PaperPosition).where(PaperPosition.account_id == account_id)
            ).scalars().all()

            market_value = 0.0
            for pos in positions:
                market_value += float(pos.quantity) * float(pos.last_price or 0.0)

            total_assets = float(account.cash) + float(account.frozen_cash) + market_value
            initial = float(account.initial_capital) or 1.0
            pnl_pct = (total_assets - initial) / initial * 100.0

            try:
                config = json.loads(account.config_json) if account.config_json else {}
            except (TypeError, ValueError):
                config = {}

            return AccountSnapshot(
                id=account.id,
                name=account.name,
                initial_capital=account.initial_capital,
                cash=account.cash,
                frozen_cash=account.frozen_cash,
                status=account.status,
                market_value=market_value,
                total_assets=total_assets,
                pnl_pct=pnl_pct,
                config=config,
                created_at=account.created_at,
                updated_at=account.updated_at,
            )

    # ------------------------------------------------------------------
    # Net-value curve
    # ------------------------------------------------------------------

    def record_daily_net_value(self, account_id: int, target_date: Optional[date] = None) -> None:
        """Persist (or upsert) today's net-value snapshot.

        Idempotent: re-running on the same date overwrites the row.
        """
        target_date = target_date or date.today()
        snap = self.snapshot(account_id)
        initial = snap.initial_capital or 1.0
        net_value = snap.total_assets / initial

        # Compute daily return pct vs the previous recorded day.
        daily_return_pct = 0.0
        with self.db.session_scope() as session:
            prev = session.execute(
                select(PaperNetValue)
                .where(
                    and_(
                        PaperNetValue.account_id == account_id,
                        PaperNetValue.date < target_date,
                    )
                )
                .order_by(desc(PaperNetValue.date))
                .limit(1)
            ).scalar_one_or_none()
            if prev is not None and prev.total_assets:
                daily_return_pct = (
                    (snap.total_assets - float(prev.total_assets)) / float(prev.total_assets) * 100.0
                )

            existing = session.execute(
                select(PaperNetValue).where(
                    and_(
                        PaperNetValue.account_id == account_id,
                        PaperNetValue.date == target_date,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.total_assets = snap.total_assets
                existing.cash = snap.cash + snap.frozen_cash
                existing.market_value = snap.market_value
                existing.net_value = net_value
                existing.return_pct = snap.pnl_pct
                existing.daily_return_pct = daily_return_pct
            else:
                session.add(
                    PaperNetValue(
                        account_id=account_id,
                        date=target_date,
                        total_assets=snap.total_assets,
                        cash=snap.cash + snap.frozen_cash,
                        market_value=snap.market_value,
                        net_value=net_value,
                        return_pct=snap.pnl_pct,
                        daily_return_pct=daily_return_pct,
                    )
                )

    def get_net_value_series(
        self, account_id: int, limit: int = 90
    ) -> list[Dict[str, Any]]:
        """Return net-value history ordered by date ascending."""
        with self.db.session_scope() as session:
            rows = session.execute(
                select(PaperNetValue)
                .where(PaperNetValue.account_id == account_id)
                .order_by(desc(PaperNetValue.date))
                .limit(limit)
            ).scalars().all()
            rows = list(reversed(rows))
            return [
                {
                    "date": r.date.isoformat() if r.date else None,
                    "total_assets": r.total_assets,
                    "cash": r.cash,
                    "market_value": r.market_value,
                    "net_value": r.net_value,
                    "return_pct": r.return_pct,
                    "daily_return_pct": r.daily_return_pct,
                }
                for r in rows
            ]