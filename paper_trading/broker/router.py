# -*- coding: utf-8 -*-
"""Broker registry / router.

``BrokerRouter`` maps logical broker names (e.g. ``"paper"``) to
``BaseBroker`` instances so application code can resolve a broker without
hard-coding a concrete implementation.

Supports account-based resolution: given an ``account_id``, the router
reads the account's ``broker`` field to select the correct adapter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from paper_trading.broker.base import BaseBroker

logger = logging.getLogger(__name__)


class BrokerRouter:
    """Registry of named broker adapters with account-based resolution."""

    def __init__(self, register_defaults: bool = True) -> None:
        self._brokers: Dict[str, BaseBroker] = {}
        if register_defaults:
            self._register_defaults()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, broker: BaseBroker) -> BaseBroker:
        """Register a broker under a normalized name (lowercase, stripped).

        Returns the registered broker for chaining convenience.

        Raises:
            TypeError: If ``broker`` is not a ``BaseBroker``.
            ValueError: If the name is empty or already registered.
        """
        if not isinstance(broker, BaseBroker):
            raise TypeError(f"broker must be a BaseBroker, got {type(broker).__name__}")
        key = str(name).strip().lower()
        if not key:
            raise ValueError("broker name must not be empty")
        if key in self._brokers:
            raise ValueError(f"broker {key!r} is already registered")
        self._brokers[key] = broker
        logger.info("Broker registered: %s → %s", key, type(broker).__name__)
        return broker

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, name: str) -> BaseBroker:
        """Return the broker registered under ``name`` (case-insensitive).

        Raises:
            KeyError: If no broker is registered under that name.
        """
        key = str(name).strip().lower()
        try:
            return self._brokers[key]
        except KeyError:
            raise KeyError(f"no broker registered under {name!r}") from None

    def resolve_by_account(
        self, account_id: int, account_mgr: Optional[Any] = None
    ) -> BaseBroker:
        """Resolve a broker by account id.

        Queries ``PaperAccountManager`` for the account's ``broker``
        field (defaults to ``"paper"``) and returns the corresponding
        adapter. Falls back to the ``"paper"`` broker when the named
        broker is not registered.

        ``account_mgr`` may be injected (e.g. a test instance bound to a
        temp DB); it defaults to the global manager.
        """
        from paper_trading.account import PaperAccountManager

        mgr = account_mgr or PaperAccountManager()
        account = mgr._get_account_by_id(account_id)
        broker_name = str(getattr(account, "broker", "paper") or "paper").strip().lower()
        broker = self._brokers.get(broker_name)
        if broker is None:
            logger.warning(
                "Broker %r not registered for account %s; falling back to paper",
                broker_name, account_id,
            )
            broker = self._brokers.get("paper")
            if broker is None:
                raise KeyError(
                    f"no broker registered for account {account_id} "
                    f"(wanted {broker_name!r}, paper not registered)"
                )
        return broker

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def names(self) -> List[str]:
        """Return the sorted list of registered broker names."""
        return sorted(self._brokers)

    def __contains__(self, name: object) -> bool:
        return str(name).strip().lower() in self._brokers

    def __len__(self) -> int:
        return len(self._brokers)

    # ------------------------------------------------------------------
    # Default registrations
    # ------------------------------------------------------------------

    def _register_defaults(self) -> None:
        """Register the built-in PaperBroker as the ``"paper"`` default.

        Called automatically from ``__init__`` when ``register_defaults``
        is True (the default).
        """
        if "paper" not in self._brokers:
            from paper_trading.broker.paper_broker import PaperBroker

            self.register("paper", PaperBroker())
