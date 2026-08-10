# -*- coding: utf-8 -*-
"""Broker registry / router.

``BrokerRouter`` maps logical broker names (e.g. ``"paper"``) to
``BaseBroker`` instances so application code can resolve a broker without
hard-coding a concrete implementation.
"""

from __future__ import annotations

from typing import Dict, List

from paper_trading.broker.base import BaseBroker


class BrokerRouter:
    """Registry of named broker adapters."""

    def __init__(self) -> None:
        self._brokers: Dict[str, BaseBroker] = {}

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
        return broker

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

    def names(self) -> List[str]:
        """Return the sorted list of registered broker names."""
        return sorted(self._brokers)

    def __contains__(self, name: object) -> bool:
        return str(name).strip().lower() in self._brokers

    def __len__(self) -> int:
        return len(self._brokers)
