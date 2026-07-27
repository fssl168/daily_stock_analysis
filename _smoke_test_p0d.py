# -*- coding: utf-8 -*-
"""Smoke tests for P0-D ReflectionEngine.

Validates:
1. ReflectionNote dataclass construction + to_dict round-trip.
2. ReflectionEngine._parse_reflection for strict JSON, json_repair input,
   empty input, and unparseable input.
3. ReflectionEngine.format_notes_for_context with empty + non-empty list.
4. DB persistence round-trip (in-memory SQLite).
5. get_recent_notes + get_relevant_notes query paths.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime

# Ensure repo root is on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _make_engine_with_temp_db():
    """Build a ReflectionEngine backed by a fresh in-memory SQLite DB."""
    # Patch DatabaseManager to use a temporary SQLite file before any import
    # of paper_trading triggers get_db().
    from src import storage as storage_mod

    # Use in-memory sqlite for speed; shared cache so all sessions see the
    # same in-memory DB.
    test_db_url = "sqlite:///:memory:"
    db_manager = storage_mod.DatabaseManager(db_url=test_db_url)
    # Force schema creation.
    storage_mod.Base.metadata.create_all(db_manager._engine)

    from paper_trading.reflection import ReflectionEngine

    engine = ReflectionEngine(
        executor=None,  # lazy; not built in this test
        trading_engine=None,
        account_id=1,
        timeout_seconds=1.0,
        fallback_on_failure=True,
        db_manager=db_manager,
    )
    return engine, db_manager


def test_reflection_note_dataclass():
    from paper_trading.reflection import ReflectionNote

    note = ReflectionNote(
        scope="trade",
        subject="测试复盘",
        summary="这是一次测试",
        takeaway="验证数据类",
        lessons=["a", "b"],
        tags=["追高", "止损"],
        mood="bad",
        account_id=1,
        trade_id=10,
    )
    d = note.to_dict()
    assert d["scope"] == "trade"
    assert d["takeaway"] == "验证数据类"
    assert d["lessons"] == ["a", "b"]
    assert d["tags"] == ["追高", "止损"]
    print("[1] ReflectionNote dataclass OK")


def test_parse_strict_json():
    engine, _ = _make_engine_with_temp_db()
    raw = json.dumps({
        "subject": "买入测试",
        "summary": "在高位买入,被套",
        "takeaway": "避免追高",
        "lessons": ["lesson1", "lesson2"],
        "tags": "追高,被套",
        "mood": "bad",
    })
    note = engine._parse_reflection(raw, scope="trade")
    assert note.subject == "买入测试"
    assert note.takeaway == "避免追高"
    assert note.lessons == ["lesson1", "lesson2"]
    assert note.tags == ["追高", "被套"]
    assert note.mood == "bad"
    assert not note.used_fallback
    print("[2] parse_strict_json OK")


def test_parse_json_repair():
    engine, _ = _make_engine_with_temp_db()
    # Slightly malformed JSON (missing closing brace).
    raw = '{"subject": "测试", "summary": "ok", "takeaway": "fix", "lessons": ["a"], "tags": "tag1", "mood": "good"'
    note = engine._parse_reflection(raw, scope="trade")
    assert note.subject == "测试"
    assert note.takeaway == "fix"
    assert note.mood == "good"
    print("[3] parse_json_repair OK")


def test_parse_empty_and_unparseable():
    engine, _ = _make_engine_with_temp_db()
    note1 = engine._parse_reflection("", scope="trade")
    assert note1.used_fallback
    assert note1.subject == "empty response"

    note2 = engine._parse_reflection("this is plain text without json", scope="trade")
    assert note2.used_fallback
    assert note2.subject == "unparseable reflection"
    print("[4] parse_empty_and_unparseable OK")


def test_format_notes_for_context_empty():
    engine, _ = _make_engine_with_temp_db()
    result = engine.format_notes_for_context([])
    assert "无历史复盘笔记" in result
    print("[5] format_notes_for_context(empty) OK")


def test_persistence_and_query():
    engine, db_manager = _make_engine_with_temp_db()
    from src.storage import PaperReflection

    # Insert 3 notes.
    notes_data = [
        ReflectionNote_for_test("trade", "买入 600519", "good", ["追高"], "600519"),
        ReflectionNote_for_test("daily", "今日盈利", "good", ["仓位合理"], None),
        ReflectionNote_for_test("trade", "卖出 000001", "bad", ["止损过晚"], "000001"),
    ]
    from paper_trading.reflection import ReflectionNote

    for scope, subj, mood, tags, code in notes_data:
        note = ReflectionNote(
            scope=scope,
            subject=subj,
            summary=Subj_summary(subj),
            takeaway="test takeaway",
            lessons=["l1"],
            tags=tags,
            mood=mood,
            account_id=1,
            code=code,
        )
        engine._persist_note(note)
        assert note.row_id is not None, f"persist failed for {subj}"

    # Query recent notes.
    recent = engine.get_recent_notes(limit=10)
    assert len(recent) == 3, f"expected 3 notes, got {len(recent)}"

    # Query by scope.
    daily = engine.get_recent_notes(limit=10, scope="daily")
    assert len(daily) == 1
    assert daily[0].subject == "今日盈利"

    # Query by code.
    code_notes = engine.get_relevant_notes(code="600519", limit=5)
    assert len(code_notes) == 1
    assert code_notes[0].subject == "买入 600519"

    # Query by tag.
    tag_notes = engine.get_relevant_notes(tags=["追高"], limit=5)
    assert len(tag_notes) == 1
    assert tag_notes[0].subject == "买入 600519"

    # Format for context.
    fmt = engine.format_notes_for_context(recent)
    assert "买入 600519" in fmt
    assert "今日盈利" in fmt
    print("[6] persistence_and_query OK")


def ReflectionNote_for_test(scope, subject, mood, tags, code):
    """Helper returning test data tuple."""
    return (scope, subject, mood, tags, code)


def Subj_summary(subj):
    return f"summary for {subj}"


def main():
    print("=== P0-D ReflectionEngine smoke tests ===")
    test_reflection_note_dataclass()
    test_parse_strict_json()
    test_parse_json_repair()
    test_parse_empty_and_unparseable()
    test_format_notes_for_context_empty()
    test_persistence_and_query()
    print("=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    main()
