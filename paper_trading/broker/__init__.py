# -*- coding: utf-8 -*-
"""Broker abstraction layer (T16).

Public API:
- ``BaseBroker``: vendor-neutral broker interface (ABC).
- ``BrokerOrderStatus``: unified broker-side order lifecycle statuses.
- ``BrokerRouter``: named broker registry / resolver.
- ``PaperBroker``: ``BaseBroker`` adapter over the local paper-trading managers.
"""

from paper_trading.broker.base import BaseBroker, BrokerOrderStatus
from paper_trading.broker.paper_broker import PaperBroker
from paper_trading.broker.router import BrokerRouter

__all__ = [
    "BaseBroker",
    "BrokerOrderStatus",
    "BrokerRouter",
    "PaperBroker",
]
