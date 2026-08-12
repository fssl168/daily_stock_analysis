# -*- coding: utf-8 -*-
"""Credit spread computation (T-04)."""

from __future__ import annotations

from .models import CreditSpreadResult


def credit_spread(
    corporate_yield: float,
    treasury_yield: float,
) -> CreditSpreadResult:
    """Compute the credit spread between a corporate and a treasury yield.

    ``corporate_yield`` / ``treasury_yield`` are annualised yields in percent.
    Returns basis points (1% = 100 bps) and percentage points.
    """
    diff_pct = corporate_yield - treasury_yield
    return CreditSpreadResult(
        corporate_yield=corporate_yield,
        treasury_yield=treasury_yield,
        spread_bps=round(diff_pct * 100.0, 2),
        spread_pct=round(diff_pct, 4),
    )
