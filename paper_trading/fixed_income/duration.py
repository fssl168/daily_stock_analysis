# -*- coding: utf-8 -*-
"""Bond duration / convexity computation (T-04).

Standard fixed-coupon bond analytics. All functions are pure and take
*yield in percent* (e.g. 2.45 means 2.45%) for convenience.
"""

from __future__ import annotations

from typing import List, Tuple


def bond_cashflows(
    coupon_rate: float,
    years: float,
    face: float = 100.0,
    freq: int = 1,
) -> Tuple[List[float], List[float]]:
    """Return (times, cashflows) for a fixed-coupon bond.

    ``times`` are in years; the final cashflow includes the face value.
    ``years`` is rounded up to an integer number of coupon periods.
    """
    n = max(int(round(years * freq)), 1)
    times = [float(i) / freq for i in range(1, n + 1)]
    coupon = coupon_rate / 100.0 * face / freq
    cashflows = [coupon] * n
    cashflows[-1] += face
    return times, cashflows


def bond_price(
    times: List[float],
    cashflows: List[float],
    yield_rate: float,
) -> float:
    """Dirty price of the bond (present value of cashflows)."""
    y = yield_rate / 100.0
    return sum(cf / (1.0 + y) ** t for cf, t in zip(cashflows, times))


def macaulay_duration(
    times: List[float],
    cashflows: List[float],
    yield_rate: float,
) -> float:
    """Macaulay duration in years = weighted avg time to each cashflow."""
    y = yield_rate / 100.0
    price = bond_price(times, cashflows, yield_rate)
    if price <= 0:
        return 0.0
    return sum(t * cf / (1.0 + y) ** t for cf, t in zip(cashflows, times)) / price


def modified_duration(
    times: List[float],
    cashflows: List[float],
    yield_rate: float,
) -> float:
    """Modified duration = Macaulay / (1 + y)."""
    mac = macaulay_duration(times, cashflows, yield_rate)
    return mac / (1.0 + yield_rate / 100.0)


def convexity(
    times: List[float],
    cashflows: List[float],
    yield_rate: float,
) -> float:
    """Convexity of the bond (second derivative / price)."""
    y = yield_rate / 100.0
    price = bond_price(times, cashflows, yield_rate)
    if price <= 0:
        return 0.0
    return sum(
        t * (t + 1.0) * cf / (1.0 + y) ** (t + 2.0)
        for cf, t in zip(cashflows, times)
    ) / price


def price_impact(
    times: List[float],
    cashflows: List[float],
    yield_rate: float,
    delta_bps: float,
) -> float:
    """Approx price change % for a parallel yield shift of delta_bps."""
    mod = modified_duration(times, cashflows, yield_rate)
    conv = convexity(times, cashflows, yield_rate)
    dy = delta_bps / 10000.0
    return -mod * dy + 0.5 * conv * dy * dy
