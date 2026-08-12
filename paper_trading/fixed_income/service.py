# -*- coding: utf-8 -*-
"""Fixed-income analytics service (T-04)."""

from __future__ import annotations

from typing import List, Optional

from .datasource import FixedIncomeDataSource
from .duration import bond_cashflows, bond_price, convexity, macaulay_duration, modified_duration
from .models import BondDurationResult, CreditSpreadResult, RepoRate, YieldCurve
from .spread import credit_spread


class FixedIncomeService:
    """Aggregate fixed-income analytics for the API layer."""

    def __init__(self, datasource: Optional[FixedIncomeDataSource] = None) -> None:
        self._datasource = datasource or FixedIncomeDataSource()

    @property
    def datasource(self) -> FixedIncomeDataSource:
        return self._datasource

    # ------------------------------------------------------------------
    # Curve / repo
    # ------------------------------------------------------------------

    def get_treasury_curve(self, curve_name: str = "中债国债收益率曲线") -> YieldCurve:
        return self._datasource.fetch_treasury_curve(curve_name)

    def get_repo_rates(self) -> List[RepoRate]:
        return self._datasource.fetch_repo_rates()

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_bond_duration(
        self,
        coupon_rate: float,
        years: float,
        yield_rate: float,
        face: float = 100.0,
    ) -> BondDurationResult:
        """Compute duration/convexity for a fixed-coupon bond."""
        times, cashflows = bond_cashflows(coupon_rate, years, face)
        price = bond_price(times, cashflows, yield_rate)
        return BondDurationResult(
            coupon_rate=coupon_rate,
            years=years,
            yield_rate=yield_rate,
            bond_price=round(price, 4),
            macaulay_duration=round(macaulay_duration(times, cashflows, yield_rate), 4),
            modified_duration=round(modified_duration(times, cashflows, yield_rate), 4),
            convexity=round(convexity(times, cashflows, yield_rate), 4),
        )

    def get_credit_spread(
        self, corporate_yield: float, treasury_yield: float
    ) -> CreditSpreadResult:
        return credit_spread(corporate_yield, treasury_yield)
