# -*- coding: utf-8 -*-
"""Fixed-income data models (T-04)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class YieldCurvePoint:
    """One point on a yield curve (tenor -> annualised yield %)."""

    tenor: str  # e.g. "3月", "1年", "10年"
    tenor_years: float  # numeric years (e.g. 3月 -> 0.25)
    yield_rate: float  # annualised yield in percent (e.g. 2.45)


@dataclass
class YieldCurve:
    """A named yield curve (e.g. China treasury)."""

    name: str
    date: Optional[str]
    points: List[YieldCurvePoint] = field(default_factory=list)
    source: str = "stub"
    used_fallback: bool = False


@dataclass
class BondDurationResult:
    """Duration / convexity analytics for a fixed-coupon bond."""

    coupon_rate: float  # annual coupon in percent
    years: float  # time to maturity in years
    yield_rate: float  # yield-to-maturity in percent
    bond_price: float  # dirty price (per 100 face)
    macaulay_duration: float
    modified_duration: float
    convexity: float


@dataclass
class CreditSpreadResult:
    """Credit spread between a corporate and a treasury bond yield."""

    corporate_yield: float  # %
    treasury_yield: float  # %
    spread_bps: float  # basis points (corporate - treasury) * 100
    spread_pct: float  # percentage points


@dataclass
class RepoRate:
    """A money-market repo reference rate."""

    code: str
    name: str
    rate: float  # annualised %
    date: Optional[str] = None
