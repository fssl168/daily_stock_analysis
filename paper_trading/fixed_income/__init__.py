# -*- coding: utf-8 -*-
"""Fixed-income analytics module (T-04).

Minimal viable fixed-income capability: treasury yield curve, bond
duration/convexity, credit spread, and repo rates. Online data sources
(akshare) are wrapped with an offline stub fallback so the module stays
testable without network.

Design: pure computation (``duration.py`` / ``spread.py``) is separated from
data acquisition (``datasource.py``); ``service.py`` wires them together.
"""

from .models import (
    BondDurationResult,
    CreditSpreadResult,
    RepoRate,
    YieldCurve,
    YieldCurvePoint,
)
from .duration import (
    bond_cashflows,
    bond_price,
    convexity,
    macaulay_duration,
    modified_duration,
)
from .spread import credit_spread
from .datasource import FixedIncomeDataSource, STUB_TREASURY_CURVE, tenor_to_years
from .service import FixedIncomeService

__all__ = [
    "BondDurationResult",
    "CreditSpreadResult",
    "RepoRate",
    "YieldCurve",
    "YieldCurvePoint",
    "bond_cashflows",
    "bond_price",
    "convexity",
    "macaulay_duration",
    "modified_duration",
    "credit_spread",
    "FixedIncomeDataSource",
    "STUB_TREASURY_CURVE",
    "FixedIncomeService",
]
