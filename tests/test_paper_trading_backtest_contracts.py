# -*- coding: utf-8 -*-
"""Regression tests for backtest-scenario serialization + battle-plan contracts.

Covers:
- ``_fmt_iso_date_value`` handles date/datetime/str/None (fix for
  backtest-scenario 500 caused by calling .isoformat() on a str).
- ``PaperTradingToBacktestAdapter._fetch_net_values`` returns ISO date strings
  (fix for NetValuePoint.date: str validation failures).
- ``HoldingPlanItem`` requires ``current_price`` (battle-plans 500 root cause).
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.v1.endpoints.paper_trading import _fmt_iso_date_value, _paper_scenario_to_schema
from api.v1.schemas.paper_trading import HoldingPlanItem


# ---------------------------------------------------------------------------
# _fmt_iso_date_value
# ---------------------------------------------------------------------------


def test_fmt_iso_date_value_handles_date():
    assert _fmt_iso_date_value(date(2026, 8, 12)) == "2026-08-12"


def test_fmt_iso_date_value_handles_datetime():
    assert _fmt_iso_date_value(datetime(2026, 8, 12, 15, 30)) == "2026-08-12T15:30:00"


def test_fmt_iso_date_value_passes_through_str():
    assert _fmt_iso_date_value("2026-08-12") == "2026-08-12"


def test_fmt_iso_date_value_handles_none():
    assert _fmt_iso_date_value(None) is None


# ---------------------------------------------------------------------------
# _paper_scenario_to_schema
# ---------------------------------------------------------------------------


def test_paper_scenario_to_schema_serializes_dataclass_dates():
    from paper_trading.backtest_adapter import PaperTradingScenario

    scenario = PaperTradingScenario(
        account_id=1,
        strategy_name="default",
        base_date=date(2026, 8, 12),
        start_date=date(2026, 7, 13),
        end_date=date(2026, 8, 11),
        initial_capital=1000000.0,
        total_return_pct=-0.3,
        net_value_curve=[{"date": "2026-08-12", "net_value": 1.0, "cash": 800000.0,
                          "market_value": 200000.0, "return_pct": 0.0}],
        trades=[],
    )
    out = _paper_scenario_to_schema(scenario)
    assert out.base_date == "2026-08-12"
    assert out.start_date == "2026-07-13"
    assert out.end_date == "2026-08-11"
    assert out.net_value_curve[0].date == "2026-08-12"


def test_paper_scenario_to_schema_handles_string_dates():
    from paper_trading.backtest_adapter import PaperTradingScenario

    scenario = PaperTradingScenario(
        account_id=1,
        strategy_name="default",
        base_date="2026-08-12",
        start_date="2026-07-13",
        end_date="2026-08-11",
        net_value_curve=[],
        trades=[],
    )
    out = _paper_scenario_to_schema(scenario)
    assert out.base_date == "2026-08-12"
    assert out.start_date == "2026-07-13"


# ---------------------------------------------------------------------------
# PaperTradingToBacktestAdapter._fetch_net_values
# ---------------------------------------------------------------------------


def test_fetch_net_values_returns_iso_dates(temp_db):
    from paper_trading.account import PaperAccountManager
    from paper_trading.backtest_adapter import PaperTradingToBacktestAdapter
    from src.storage import PaperNetValue

    account_mgr = PaperAccountManager(db_manager=temp_db)
    account = account_mgr.get_or_create_account(name="nv_test", initial_capital=1000.0)

    with temp_db.session_scope() as session:
        session.add(PaperNetValue(
            account_id=account.id, date=date(2026, 8, 12),
            total_assets=1000.0, cash=1000.0, market_value=0.0,
            net_value=1.0, return_pct=0.0,
        ))

    adapter = PaperTradingToBacktestAdapter(account.id, db_manager=temp_db)
    nvs = adapter._fetch_net_values()
    assert nvs, "expected at least one net value row"
    assert isinstance(nvs[0]["date"], str), "date must be ISO string"
    assert nvs[0]["date"] == "2026-08-12"


# ---------------------------------------------------------------------------
# HoldingPlanItem contract (battle-plans 500)
# ---------------------------------------------------------------------------


def test_holding_plan_item_requires_current_price():
    with pytest.raises(ValidationError):
        HoldingPlanItem(code="600519")  # missing required current_price


def test_holding_plan_item_accepts_valid_entry():
    item = HoldingPlanItem(code="600519", current_price=1680.0)
    assert item.current_price == 1680.0
    assert item.name == ""
