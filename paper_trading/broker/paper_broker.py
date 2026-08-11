# -*- coding: utf-8 -*-
"""PaperBroker: ``BaseBroker`` adapter over the local paper-trading managers.

Wraps ``OrderManager`` / ``PaperAccountManager`` / ``PositionManager`` so
upper layers can drive the paper-trading subsystem through the generic
broker interface instead of reaching into individual managers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from paper_trading.account import DEFAULT_INITIAL_CAPITAL, PaperAccountManager
from paper_trading.broker.base import BaseBroker
from paper_trading.order import OrderManager, OrderRequest
from paper_trading.position import PositionManager
from src.storage import DatabaseManager, get_db


class PaperBroker(BaseBroker):
    """``BaseBroker`` implementation backed by the local paper-trading managers."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        order_manager: Optional[OrderManager] = None,
        account_manager: Optional[PaperAccountManager] = None,
        position_manager: Optional[PositionManager] = None,
        account_name: str = "default",
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    ) -> None:
        self.db = db_manager or get_db()
        self.order_mgr = order_manager or OrderManager(self.db)
        self.account_mgr = account_manager or PaperAccountManager(self.db)
        self.position_mgr = position_manager or PositionManager(self.db)
        self.account_name = account_name
        self.initial_capital = initial_capital
        self._account_id: Optional[int] = None
        self._connected: bool = True

    # ------------------------------------------------------------------
    # Account resolution
    # ------------------------------------------------------------------

    def _ensure_account_id(self, account_id: Optional[int] = None) -> int:
        """Return an explicit account id, or lazily create / resolve the default."""
        if account_id is not None:
            return int(account_id)
        if self._account_id is None:
            account = self.account_mgr.get_or_create_account(
                name=self.account_name,
                initial_capital=self.initial_capital,
            )
            self._account_id = int(account.id)
        return self._account_id

    # ------------------------------------------------------------------
    # BaseBroker interface
    # ------------------------------------------------------------------

    def submit_order(self, order: OrderRequest, account_id: Optional[int] = None) -> int:
        """Create a paper order and return its order id.

        The order is persisted as ``pending`` (or ``conditional`` for
        conditional order types); filling is handled by the paper-trading
        engine / matcher.
        """
        resolved = self._ensure_account_id(account_id)
        submitted = replace(order, account_id=resolved)
        created = self.order_mgr.create_order(submitted)
        return int(created.id)

    def cancel_order(self, order_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a pending / partially-filled order and return its updated record."""
        canceled = self.order_mgr.cancel_order(int(order_id), reason=reason)
        return OrderManager._order_to_dict(canceled)

    def query_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Return the order record as a dict, or None when unknown."""
        order = self.order_mgr.get_order(int(order_id))
        if order is None:
            return None
        return OrderManager._order_to_dict(order)

    def query_positions(
        self,
        account_id: Optional[int] = None,
        include_zero: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return the open positions of the resolved account."""
        resolved = self._ensure_account_id(account_id)
        return self.position_mgr.list_positions(resolved, include_zero=include_zero)

    def query_account(self, account_id: Optional[int] = None) -> Dict[str, Any]:
        """Return the resolved account snapshot as a dict."""
        resolved = self._ensure_account_id(account_id)
        return self.account_mgr.snapshot(resolved).to_dict()

    def is_connected(self) -> bool:
        """A paper broker is always connected (local bookkeeping)."""
        return self._connected
