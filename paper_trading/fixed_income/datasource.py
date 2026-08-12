# -*- coding: utf-8 -*-
"""Fixed-income data sources (T-04).

Online fetch via akshare with an offline stub fallback so the module works
and is testable without network access. Every fetch returns ``used_fallback``
so callers can tell synthetic from real data.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from .models import RepoRate, YieldCurve, YieldCurvePoint

logger = logging.getLogger(__name__)

_TENOR_YEARS: Dict[str, float] = {
    "3月": 0.25,
    "6月": 0.5,
    "9月": 0.75,
    "1年": 1.0,
    "2年": 2.0,
    "3年": 3.0,
    "5年": 5.0,
    "7年": 7.0,
    "10年": 10.0,
    "15年": 15.0,
    "20年": 20.0,
    "30年": 30.0,
}

# Offline fallback: China treasury yield curve (indicative, from CBOT/中债 style levels).
STUB_TREASURY_CURVE: Dict[str, float] = {
    "3月": 2.03,
    "6月": 2.14,
    "1年": 2.45,
    "3年": 2.76,
    "5年": 2.94,
    "7年": 3.11,
    "10年": 3.12,
    "30年": 3.72,
}

STUB_REPO_RATES: List[Tuple[str, str, float]] = [
    ("GC001", "上交所隔夜回购", 1.85),
    ("GC007", "上交所7天回购", 1.92),
    ("GC014", "上交所14天回购", 2.05),
    ("DR007", "银行间7天质押式回购", 1.90),
    ("R007", "银行间7天回购", 1.95),
]


def tenor_to_years(tenor: str) -> Optional[float]:
    """Map a Chinese tenor label ('3月'/'1年') to numeric years."""
    t = str(tenor).strip()
    if t in _TENOR_YEARS:
        return _TENOR_YEARS[t]
    # "1年"/"10年" style
    for suffix, mult in (("年", 1.0), ("月", 1.0 / 12.0)):
        if t.endswith(suffix) and t[: -len(suffix)].strip().isdigit():
            return float(t[: -len(suffix)].strip()) * mult
    return None


class FixedIncomeDataSource:
    """Fetch fixed-income market data, falling back to bundled stubs."""

    def __init__(self, use_online: bool = True) -> None:
        self.use_online = use_online

    # ------------------------------------------------------------------
    # Treasury yield curve
    # ------------------------------------------------------------------

    def fetch_treasury_curve(
        self, curve_name: str = "中债国债收益率曲线"
    ) -> YieldCurve:
        if self.use_online:
            try:
                points, curve_date = self._fetch_curve_online(curve_name)
                if points:
                    return YieldCurve(
                        name=curve_name,
                        date=curve_date,
                        points=points,
                        source="akshare",
                        used_fallback=False,
                    )
                logger.warning("[fixed_income] online curve empty; using stub")
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("[fixed_income] online curve failed (%s); using stub", exc)
        return self._stub_curve(curve_name)

    def _fetch_curve_online(
        self, curve_name: str
    ) -> Tuple[List[YieldCurvePoint], Optional[str]]:
        import akshare as ak

        df = ak.bond_china_yield()
        if df is None or df.empty or "曲线名称" not in df.columns:
            return [], None
        row = df[df["曲线名称"] == curve_name]
        if row.empty:
            row = df[df["曲线名称"].astype(str).str.contains("国债")].tail(1)
        if row.empty:
            return [], None
        r = row.iloc[-1]
        curve_date = str(r.get("日期") or "") or None
        points: List[YieldCurvePoint] = []
        for tenor in df.columns:
            if tenor in ("曲线名称", "日期"):
                continue
            years = tenor_to_years(tenor)
            if years is None:
                continue
            try:
                val = float(r.get(tenor))
            except (TypeError, ValueError):
                continue
            if val == val and val > 0:  # not NaN
                points.append(YieldCurvePoint(tenor=tenor, tenor_years=years, yield_rate=val))
        points.sort(key=lambda p: p.tenor_years)
        return points, curve_date

    def _stub_curve(self, name: str) -> YieldCurve:
        points = [
            YieldCurvePoint(tenor=t, tenor_years=tenor_to_years(t) or 0.0, yield_rate=v)
            for t, v in STUB_TREASURY_CURVE.items()
        ]
        points.sort(key=lambda p: p.tenor_years)
        return YieldCurve(name=name, date=date.today().isoformat(), points=points,
                          source="stub", used_fallback=True)

    # ------------------------------------------------------------------
    # Repo rates
    # ------------------------------------------------------------------

    def fetch_repo_rates(self) -> List[RepoRate]:
        if self.use_online:
            try:
                online = self._fetch_repo_online()
                if online:
                    return online
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("[fixed_income] online repo failed (%s); using stub", exc)
        return [
            RepoRate(code=c, name=n, rate=r, date=date.today().isoformat())
            for c, n, r in STUB_REPO_RATES
        ]

    def _fetch_repo_online(self) -> List[RepoRate]:
        # akshare historical repo interface; tolerant of shape changes.
        import akshare as ak

        try:
            df = ak.bond_buy_back_hist_em(symbol="GC001")
        except Exception:
            df = None
        if df is None or df.empty:
            return []
        rates: List[RepoRate] = []
        date_col = next((c for c in df.columns if "日期" in str(c)), None)
        rate_col = next((c for c in df.columns if "收盘" in str(c) or "利率" in str(c)), None)
        if rate_col is None:
            return []
        last = df.iloc[-1]
        d = str(last.get(date_col)) if date_col else None
        try:
            rates.append(RepoRate(code="GC001", name="上交所隔夜回购",
                                  rate=float(last[rate_col]), date=d))
        except (TypeError, ValueError):
            pass
        return rates
