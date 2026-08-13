# -*- coding: utf-8 -*-
"""T-13 tests: order-level idempotency (client_request_id) and the scheduled
decision-signal outcome job.

Root cause addressed: submit_signal had no idempotency key, so a repeated
API/UI submission could place a duplicate order. A request-level guard is now
applied before persisting anything.
"""

from __future__ import annotations

from sqlalchemy import select

from src.config import get_config
from src.storage import PaperOrder
from paper_trading.account import PaperAccountManager
from paper_trading.strategies import Signal
from paper_trading.trading_engine import TradingEngine
from src.storage import Account


def _create_paper_account(db, name: str, capital: float = 100000.0) -> int:
    mgr = PaperAccountManager(db_manager=db)
    mgr.get_or_create_account(name=name, initial_capital=capital)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == name, Account.account_type == "paper")
        ).scalar_one()
        return int(acc.id)


def _buy_signal(code: str = "600000") -> Signal:
    return Signal(
        side="buy", code=code, name="浦发银行", strategy_name="test",
        rule_name="test", trigger_price=10.0, suggested_quantity=100, reason="test",
    )


def test_idempotent_submit_skips_duplicate(temp_db):
    acc_id = _create_paper_account(temp_db, "idem")
    engine = TradingEngine(db_manager=temp_db)

    r1 = engine.submit_signal(acc_id, _buy_signal(), client_request_id="req-1")
    assert r1.status in ("filled", "executed"), r1.reason

    r2 = engine.submit_signal(acc_id, _buy_signal(), client_request_id="req-1")
    assert r2.status == "skipped", r2.reason
    assert "duplicate" in r2.reason

    with temp_db.session_scope() as session:
        n_orders = len(session.execute(select(PaperOrder)).scalars().all())
    assert n_orders == 1  # no duplicate order


def test_different_client_request_ids_are_not_deduped(temp_db):
    acc_id = _create_paper_account(temp_db, "idem2")
    engine = TradingEngine(db_manager=temp_db)

    r1 = engine.submit_signal(acc_id, _buy_signal(), client_request_id="req-a")
    r2 = engine.submit_signal(acc_id, _buy_signal(), client_request_id="req-b")
    assert r1.status in ("filled", "executed")
    assert r2.status in ("filled", "executed")

    with temp_db.session_scope() as session:
        n_orders = len(session.execute(select(PaperOrder)).scalars().all())
    assert n_orders == 2


def test_outcome_task_registered_in_scheduler():
    from src.services.runtime_scheduler import RuntimeSchedulerService

    scheduler = RuntimeSchedulerService(config_provider=lambda: get_config())
    tasks = scheduler._current_background_tasks(get_config())
    names = {t.get("name") for t in tasks}
    assert "decision_signal_outcome" in names
    outcome_task = next(t for t in tasks if t.get("name") == "decision_signal_outcome")
    assert callable(outcome_task["task"])
