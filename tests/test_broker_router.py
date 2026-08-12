# -*- coding: utf-8 -*-
"""BrokerRouter routing tests (T-07 prep).

Verifies account-based broker resolution: a registered non-paper broker is
selected when the account's ``broker`` field names it; unknown/empty broker
names fall back to the paper broker.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import update

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.account import PaperAccountManager
from paper_trading.broker.base import BaseBroker
from paper_trading.broker.router import BrokerRouter
from src.storage import Account


class FakeBroker(BaseBroker):
    name = "fake"

    def submit_order(self, order: Any, account_id: Optional[int] = None) -> Dict[str, Any]:
        return {"id": 1, "broker": self.name}

    def cancel_order(self, order_id: Any, reason: Optional[str] = None) -> Dict[str, Any]:
        return {"id": order_id, "status": "cancelled", "broker": self.name}

    def query_order(self, order_id: Any) -> Dict[str, Any]:
        return {"id": order_id, "broker": self.name}

    def query_positions(self, account_id: Any = None) -> List[Dict[str, Any]]:
        return []

    def query_account(self, account_id: Any = None) -> Dict[str, Any]:
        return {"id": account_id, "broker": self.name}

    def is_connected(self) -> bool:
        return True


def _account_with_broker(temp_db, broker_name: Optional[str]) -> int:
    mgr = PaperAccountManager(db_manager=temp_db)
    acc = mgr.get_or_create_account(name=f"broker_test_{broker_name or 'none'}", initial_capital=1000.0)
    with temp_db.session_scope() as session:
        session.execute(
            update(Account).where(Account.id == acc.id).values(broker=broker_name)
        )
    return acc.id


def test_register_and_resolve_by_name():
    router = BrokerRouter()
    fb = FakeBroker()
    router.register("fake", fb)
    assert router.resolve("fake") is fb


def test_resolve_by_account_routes_to_registered_broker(temp_db):
    aid = _account_with_broker(temp_db, "fake")
    router = BrokerRouter()
    fb = FakeBroker()
    router.register("fake", fb)
    mgr = PaperAccountManager(db_manager=temp_db)
    assert router.resolve_by_account(aid, account_mgr=mgr) is fb


def test_resolve_by_account_falls_back_to_paper_for_unknown_broker(temp_db):
    aid = _account_with_broker(temp_db, "nonexistent_broker")
    router = BrokerRouter()  # registers paper broker by default
    mgr = PaperAccountManager(db_manager=temp_db)
    broker = router.resolve_by_account(aid, account_mgr=mgr)
    assert type(broker).__name__ == "PaperBroker"


def test_resolve_by_account_defaults_to_paper_when_unset(temp_db):
    aid = _account_with_broker(temp_db, None)
    router = BrokerRouter()
    mgr = PaperAccountManager(db_manager=temp_db)
    broker = router.resolve_by_account(aid, account_mgr=mgr)
    assert type(broker).__name__ == "PaperBroker"


def test_duplicate_register_rejected():
    router = BrokerRouter()
    fb = FakeBroker()
    router.register("fake", fb)
    with pytest.raises(ValueError):
        router.register("fake", FakeBroker())
