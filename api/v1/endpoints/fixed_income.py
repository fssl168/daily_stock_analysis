# -*- coding: utf-8 -*-
"""Fixed-income API endpoints (T-04)."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.v1.schemas.common import ErrorResponse
from src.permissions import require_login

from paper_trading.fixed_income import FixedIncomeService


class YieldCurvePointOut(BaseModel):
    tenor: str
    tenor_years: float
    yield_rate: float


class YieldCurveOut(BaseModel):
    name: str
    date: Optional[str] = None
    points: List[YieldCurvePointOut] = []
    source: str = "stub"
    used_fallback: bool = False


class BondDurationOut(BaseModel):
    coupon_rate: float
    years: float
    yield_rate: float
    bond_price: float
    macaulay_duration: float
    modified_duration: float
    convexity: float


class CreditSpreadOut(BaseModel):
    corporate_yield: float
    treasury_yield: float
    spread_bps: float
    spread_pct: float


class RepoRateOut(BaseModel):
    code: str
    name: str
    rate: float
    date: Optional[str] = None


router = APIRouter(dependencies=[Depends(require_login)])


def get_fi_service(request: Request) -> FixedIncomeService:
    """Lazy shared FixedIncomeService on app state."""
    service = getattr(request.app.state, "fixed_income_service", None)
    if service is None:
        service = FixedIncomeService()
        request.app.state.fixed_income_service = service
    return service


@router.get(
    "/curve",
    response_model=YieldCurveOut,
    responses={500: {"description": "Server error", "model": ErrorResponse}},
    summary="Get treasury yield curve",
)
def get_treasury_curve(
    curve_name: str = Query("中债国债收益率曲线", description="Curve name (中债国债/国开/AAA 等)"),
    service: FixedIncomeService = Depends(get_fi_service),
) -> YieldCurveOut:
    try:
        curve = service.get_treasury_curve(curve_name)
        return YieldCurveOut(
            name=curve.name,
            date=curve.date,
            points=[YieldCurvePointOut(**p.__dict__) for p in curve.points],
            source=curve.source,
            used_fallback=curve.used_fallback,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"fixed-income curve failed: {exc}")


@router.get(
    "/duration",
    response_model=BondDurationOut,
    responses={400: {"description": "Invalid params", "model": ErrorResponse}},
    summary="Compute bond duration / convexity",
)
def get_bond_duration(
    coupon: float = Query(5.0, ge=0, le=50, description="Annual coupon rate (%)"),
    years: float = Query(10.0, gt=0, le=100, description="Years to maturity"),
    yield_rate: float = Query(3.5, ge=0, le=100, description="Yield to maturity (%)"),
    service: FixedIncomeService = Depends(get_fi_service),
) -> BondDurationOut:
    result = service.get_bond_duration(coupon, years, yield_rate)
    return BondDurationOut(**result.__dict__)


@router.get(
    "/spread",
    response_model=CreditSpreadOut,
    responses={400: {"description": "Invalid params", "model": ErrorResponse}},
    summary="Compute credit spread (bps)",
)
def get_credit_spread(
    corporate: float = Query(..., ge=0, le=100, description="Corporate yield (%)"),
    treasury: float = Query(..., ge=0, le=100, description="Treasury yield (%)"),
    service: FixedIncomeService = Depends(get_fi_service),
) -> CreditSpreadOut:
    result = service.get_credit_spread(corporate, treasury)
    return CreditSpreadOut(**result.__dict__)


@router.get(
    "/repo",
    response_model=List[RepoRateOut],
    responses={500: {"description": "Server error", "model": ErrorResponse}},
    summary="Get money-market repo reference rates",
)
def get_repo_rates(
    service: FixedIncomeService = Depends(get_fi_service),
) -> List[RepoRateOut]:
    try:
        rates = service.get_repo_rates()
        return [RepoRateOut(**r.__dict__) for r in rates]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"fixed-income repo failed: {exc}")
