# -*- coding: utf-8 -*-
"""T-10 regression tests: the reflection loop actually closes end-to-end.

Root cause fixed: the API ``start_listener`` built the listener without
threading the trade-executed callback, so a completed trade never triggered
``reflect_on_trade`` — ``paper_reflections`` stayed at 0 despite the feature
being enabled. ``build_full_listener`` now accepts ``on_trade_executed`` /
``on_signal_rejected`` and the API passes them through.

Verified here:
- A filled trade fires the callback → ``reflect_on_trade`` persists a note.
- Daily reflection persists a note.
- Persisted notes are retrievable for P0-E decision-context injection.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy import select

from paper_trading.account import PaperAccountManager
from paper_trading.reflection import ReflectionEngine, ReflectionNote
from paper_trading.strategies import Signal
from paper_trading.trading_engine import TradingEngine
from src.storage import Account, PaperReflection


class _FakeExecutor:
    """Minimal executor returning a well-formed reflection JSON payload."""

    def chat(self, message, session_id=None):
        return SimpleNamespace(
            content=(
                '{"subject": "600000 买入复盘", "summary": "按策略买入浦发银行", '
                '"takeaway": "控制单笔仓位，严格执行止损", "lessons": ["仓位控制"], '
                '"tags": "买入,仓位", "mood": "neutral"}'
            )
        )


def _create_paper_account(db, name: str, capital: float = 100000.0) -> int:
    mgr = PaperAccountManager(db_manager=db)
    mgr.get_or_create_account(name=name, initial_capital=capital)
    with db.session_scope() as session:
        acc = session.execute(
            select(Account).where(Account.name == name, Account.account_type == "paper")
        ).scalar_one()
        return int(acc.id)


def _reflection_rows(db):
    """Return reflection rows as plain dicts (avoid detached-ORM access)."""
    with db.session_scope() as session:
        rows = session.execute(select(PaperReflection)).scalars().all()
        return [
            {
                "scope": r.scope,
                "subject": r.subject or "",
                "summary": r.summary or "",
                "code": r.code,
                "trade_id": r.trade_id,
                "mood": r.mood or "neutral",
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# AC-1001: a filled trade triggers reflect_on_trade and persists a note
# ---------------------------------------------------------------------------

def test_trade_execution_triggers_reflection_persist(temp_db):
    acc_id = _create_paper_account(temp_db, "refl_loop")
    refl = ReflectionEngine(executor=_FakeExecutor(), db_manager=temp_db, account_id=acc_id)

    engine = TradingEngine(
        db_manager=temp_db,
        on_trade_executed=lambda result, trade_id=None: refl.reflect_on_trade(
            trade_id=trade_id, account_id=acc_id
        ),
    )
    signal = Signal(
        side="buy", code="600000", name="浦发银行", strategy_name="test",
        rule_name="test", trigger_price=10.0, suggested_quantity=100, reason="test",
    )
    result = engine.submit_signal(account_id=acc_id, signal=signal)
    assert result.status in ("filled", "executed"), result.reason

    rows = _reflection_rows(temp_db)
    assert len(rows) >= 1
    last = rows[-1]
    assert last["scope"] == "trade"
    assert last["code"] == "600000"
    assert last["trade_id"] is not None


# ---------------------------------------------------------------------------
# AC-1002: daily reflection persists a note
# ---------------------------------------------------------------------------

def test_daily_reflection_persists(temp_db):
    acc_id = _create_paper_account(temp_db, "refl_daily")
    refl = ReflectionEngine(executor=_FakeExecutor(), db_manager=temp_db, account_id=acc_id)

    note = refl.reflect_on_daily(account_id=acc_id, review_date=date.today())
    assert note.scope == "daily"
    assert note.subject  # parsed from the fake LLM payload

    rows = _reflection_rows(temp_db)
    daily = [r for r in rows if r["scope"] == "daily"]
    assert len(daily) >= 1


# ---------------------------------------------------------------------------
# AC-1003: persisted notes are retrievable for decision-context injection
# ---------------------------------------------------------------------------

def test_reflection_notes_retrievable_for_injection(temp_db):
    acc_id = _create_paper_account(temp_db, "refl_inject")
    refl = ReflectionEngine(executor=_FakeExecutor(), db_manager=temp_db, account_id=acc_id)
    refl._persist_note(
        ReflectionNote(
            scope="trade", subject="600000 买入复盘", summary="买入", takeaway="控制仓位",
            mood="neutral", account_id=acc_id, code="600000",
        )
    )
    notes = refl.get_relevant_notes(code="600000", account_id=acc_id)
    assert len(notes) >= 1
    assert notes[0].code == "600000"
