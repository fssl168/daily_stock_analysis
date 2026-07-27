# -*- coding: utf-8 -*-
"""Smoke tests for P0-E memory loop (PM agent <-> ReflectionEngine).

Validates:
1. PortfolioManagerAgent._fetch_reflections_summary returns recent notes when
   no positions are held.
2. With positions held, position-relevant notes are merged in.
3. De-duplication works when the same note appears in both lists.
4. Fallback paths when reflection_engine is None or raises.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _make_pm_agent_with_reflection_engine(notes_recent, notes_per_code, positions=None):
    """Build a PM agent with a stubbed reflection_engine + trading_engine."""
    from src.agent.portfolio_manager_agent import PortfolioManagerAgent

    class _StubReflectionEngine:
        def get_recent_notes(self, limit=5, scope=None, account_id=None):
            return notes_recent[:limit]

        def get_relevant_notes(self, code=None, tags=None, limit=5, account_id=None):
            return notes_per_code.get(code, [])[:limit]

    class _StubPositionMgr:
        def list_positions(self, account_id):
            return positions or []

    class _StubTradingEngine:
        db = None
        account_mgr = None
        position_mgr = _StubPositionMgr()

    return PortfolioManagerAgent(
        executor=None,
        trading_engine=_StubTradingEngine(),
        reflection_engine=_StubReflectionEngine(),
        account_id=1,
    )


def _make_note(row_id, scope, subject, takeaway, code=None, created_at=None):
    """Build a lightweight ReflectionNote-like object."""
    from paper_trading.reflection import ReflectionNote

    return ReflectionNote(
        scope=scope,
        subject=subject,
        takeaway=takeaway,
        code=code,
        row_id=row_id,
        created_at=created_at or datetime(2026, 7, 26, 10, 30),
    )


def test_no_reflection_engine_returns_disabled_message():
    from src.agent.portfolio_manager_agent import PortfolioManagerAgent

    agent = PortfolioManagerAgent(
        executor=None,
        trading_engine=None,
        reflection_engine=None,
        account_id=1,
    )
    summary = agent._fetch_reflections_summary(account_id=1)
    assert "未启用" in summary
    print("[1] no reflection_engine fallback OK")


def test_no_notes_returns_empty_message():
    agent = _make_pm_agent_with_reflection_engine(
        notes_recent=[], notes_per_code={}, positions=[]
    )
    summary = agent._fetch_reflections_summary(account_id=1)
    assert "暂无复盘笔记" in summary
    print("[2] no notes fallback OK")


def test_recent_notes_only():
    notes = [
        _make_note(1, "daily", "今日复盘", "避免追高"),
        _make_note(2, "trade", "买入测试", "分批建仓"),
    ]
    agent = _make_pm_agent_with_reflection_engine(
        notes_recent=notes, notes_per_code={}, positions=[]
    )
    summary = agent._fetch_reflections_summary(account_id=1)
    assert "避免追高" in summary
    assert "分批建仓" in summary
    assert "[daily]" in summary
    assert "[trade]" in summary
    print("[3] recent notes only OK")


def test_position_relevant_notes_merged():
    recent_notes = [
        _make_note(1, "daily", "今日复盘", "整体仓位合理"),
    ]
    position_notes = {
        "600519": [_make_note(2, "trade", "茅台买入", "止损过晚", code="600519")],
        "000001": [_make_note(3, "trade", "平安卖出", "止损及时", code="000001")],
    }
    positions = [
        SimpleNamespace(code="600519", name="贵州茅台"),
        SimpleNamespace(code="000001", name="平安银行"),
    ]
    agent = _make_pm_agent_with_reflection_engine(
        notes_recent=recent_notes,
        notes_per_code=position_notes,
        positions=positions,
    )
    summary = agent._fetch_reflections_summary(account_id=1)
    assert "整体仓位合理" in summary
    assert "止损过晚" in summary
    assert "止损及时" in summary
    assert "[600519]" in summary
    assert "[000001]" in summary
    print("[4] position-relevant notes merged OK")


def test_deduplication():
    """The same note appearing in both recent + relevant should show only once."""
    shared_note = _make_note(7, "trade", "共享笔记", "共享教训", code="600519")
    recent_notes = [shared_note, _make_note(8, "daily", "今日", "今日教训")]
    position_notes = {
        "600519": [shared_note],  # same row_id=7
    }
    positions = [SimpleNamespace(code="600519", name="贵州茅台")]
    agent = _make_pm_agent_with_reflection_engine(
        notes_recent=recent_notes,
        notes_per_code=position_notes,
        positions=positions,
    )
    summary = agent._fetch_reflections_summary(account_id=1)
    # Count occurrences of "共享教训" — should be exactly 1.
    assert summary.count("共享教训") == 1, (
        f"dedup failed, summary has {summary.count('共享教训')} copies:\n{summary}"
    )
    print("[5] deduplication OK")


def main():
    print("=== P0-E memory loop smoke tests ===")
    test_no_reflection_engine_returns_disabled_message()
    test_no_notes_returns_empty_message()
    test_recent_notes_only()
    test_position_relevant_notes_merged()
    test_deduplication()
    print("=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    main()
