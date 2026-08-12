# -*- coding: utf-8 -*-
"""Fixed-income module tests (T-04)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.fixed_income import (
    FixedIncomeDataSource,
    FixedIncomeService,
    bond_cashflows,
    bond_price,
    convexity,
    credit_spread,
    macaulay_duration,
    modified_duration,
    tenor_to_years,
)


# ---------------------------------------------------------------------------
# Duration / convexity pure functions
# ---------------------------------------------------------------------------


def test_zero_coupon_bond_analytics():
    times, cashflows = bond_cashflows(0.0, 1.0)  # zero-coupon, 1y
    assert times == [1.0]
    assert cashflows == [100.0]
    assert bond_price(times, cashflows, 5.0) == pytest.approx(95.238095, rel=1e-5)
    assert macaulay_duration(times, cashflows, 5.0) == pytest.approx(1.0)
    assert modified_duration(times, cashflows, 5.0) == pytest.approx(1.0 / 1.05, rel=1e-5)


def test_par_bond_duration_and_convexity():
    times, cashflows = bond_cashflows(5.0, 10.0)  # 5% coupon, 10y
    # At YTM == coupon the bond prices at par.
    assert bond_price(times, cashflows, 5.0) == pytest.approx(100.0, rel=1e-4)
    assert macaulay_duration(times, cashflows, 5.0) == pytest.approx(8.1078, rel=1e-3)
    assert modified_duration(times, cashflows, 5.0) == pytest.approx(7.7217, rel=1e-3)
    assert convexity(times, cashflows, 5.0) == pytest.approx(74.9977, rel=1e-3)


def test_premium_bond_with_higher_coupon():
    times, cashflows = bond_cashflows(8.0, 6.0)
    assert bond_price(times, cashflows, 10.0) == pytest.approx(91.2895, rel=1e-3)
    assert macaulay_duration(times, cashflows, 10.0) == pytest.approx(4.9403, rel=1e-3)
    assert modified_duration(times, cashflows, 10.0) == pytest.approx(4.4912, rel=1e-3)


def test_duration_decreases_with_higher_coupon():
    # Higher coupon -> shorter duration for same maturity/yield.
    t1, c1 = bond_cashflows(2.0, 10.0)
    t2, c2 = bond_cashflows(8.0, 10.0)
    d1 = macaulay_duration(t1, c1, 5.0)
    d2 = macaulay_duration(t2, c2, 5.0)
    assert d2 < d1


# ---------------------------------------------------------------------------
# Credit spread
# ---------------------------------------------------------------------------


def test_credit_spread_bps():
    res = credit_spread(5.2, 3.1)
    assert res.spread_bps == pytest.approx(210.0)
    assert res.spread_pct == pytest.approx(2.1)


def test_credit_spread_negative_when_credit_tight():
    res = credit_spread(2.8, 3.0)
    assert res.spread_bps == pytest.approx(-20.0)


# ---------------------------------------------------------------------------
# Tenor parsing
# ---------------------------------------------------------------------------


def test_tenor_to_years():
    assert tenor_to_years("3月") == 0.25
    assert tenor_to_years("6月") == 0.5
    assert tenor_to_years("1年") == 1.0
    assert tenor_to_years("10年") == 10.0
    assert tenor_to_years("30年") == 30.0
    assert tenor_to_years("bogus") is None


# ---------------------------------------------------------------------------
# Data source stub fallback + service
# ---------------------------------------------------------------------------


def test_datasource_stub_fallback_offline():
    ds = FixedIncomeDataSource(use_online=False)
    curve = ds.fetch_treasury_curve()
    assert curve.used_fallback is True
    assert curve.source == "stub"
    assert curve.points
    # sorted by tenor years ascending
    years = [p.tenor_years for p in curve.points]
    assert years == sorted(years)
    # curve should contain 1y and 10y
    labels = {p.tenor for p in curve.points}
    assert "1年" in labels and "10年" in labels


def test_repo_rates_stub_offline():
    ds = FixedIncomeDataSource(use_online=False)
    rates = ds.fetch_repo_rates()
    assert rates
    assert all(r.code and r.rate > 0 for r in rates)


def test_service_get_bond_duration():
    svc = FixedIncomeService(datasource=FixedIncomeDataSource(use_online=False))
    res = svc.get_bond_duration(coupon_rate=5.0, years=10.0, yield_rate=5.0)
    assert res.bond_price == pytest.approx(100.0, rel=1e-3)
    assert res.macaulay_duration == pytest.approx(8.1078, rel=1e-3)
    assert res.convexity > 0


def test_service_get_curve_and_spread():
    svc = FixedIncomeService(datasource=FixedIncomeDataSource(use_online=False))
    curve = svc.get_treasury_curve()
    assert curve.used_fallback
    spread = svc.get_credit_spread(4.5, 3.1)
    assert spread.spread_bps == pytest.approx(140.0)
