# -*- coding: utf-8 -*-
"""Broker abstraction layer: the vendor-neutral broker interface.

Defines ``BrokerOrderStatus`` (unified broker-side order lifecycle statuses)
and ``BaseBroker`` (the ABC every concrete broker adapter implements).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional


class BrokerOrderStatus(str, Enum):
    """Unified broker order lifecycle statuses.

    Values are lowercase strings so they can be stored / compared directly
    against order payloads coming from different broker backends.
    """

    PENDING = "pending"
    QUEUED = "queued"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class BaseBroker(ABC):
    """Vendor-neutral broker interface.

    A broker adapter turns generic order / account / position operations
    into backend-specific calls (local paper bookkeeping, a real brokerage
    API, ...) while exposing a stable contract to upper layers.
    """

    @abstractmethod
    def submit_order(self, order: Any, **kwargs: Any) -> Any:
        """Submit an order and return a backend order identifier."""

    @abstractmethod
    def cancel_order(self, order_id: Any, reason: Optional[str] = None) -> Any:
        """Cancel a submitted order and return the updated order."""

    @abstractmethod
    def query_order(self, order_id: Any) -> Any:
        """Return the current state of an order, or None when unknown."""

    @abstractmethod
    def query_positions(self, account_id: Any = None) -> List[Dict[str, Any]]:
        """Return the open positions of an account."""

    @abstractmethod
    def query_account(self, account_id: Any = None) -> Any:
        """Return a summary of an account."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return whether the broker backend is reachable."""
