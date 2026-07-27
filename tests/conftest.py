# -*- coding: utf-8 -*-
"""Shared pytest fixtures for the paper-trading test suite."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import DatabaseManager


def _make_synthetic_daily_df(
    code: str = "000001",
    days: int = 90,
    base_price: float = 10.0,
) -> pd.DataFrame:
    """Build a deterministic daily-bar DataFrame for offline tests."""
    idx = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    n = len(idx)
    drift = [i * 0.02 for i in range(n)]
    wave = [((i % 10) - 5) * 0.05 for i in range(n)]
    close = [base_price + drift[i] + wave[i] for i in range(n)]
    high = [c + 0.15 for c in close]
    low = [c - 0.15 for c in close]
    opn = [base_price] + close[:-1]
    volume = [10000 + (i % 5) * 500 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": opn,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )
    df.index.name = "date"
    return df


class StubDataProvider:
    """Offline data provider returning synthetic daily bars.

    Mimics ``DataFetcherManager.get_daily_data(code, days=...)`` and also
    supports the ``(df, source)`` tuple shape used by some callers.
    """

    def __init__(self, return_tuple: bool = False):
        self.return_tuple = return_tuple
        self.calls: list[str] = []

    def get_daily_data(self, code: str, days: int = 120) -> object:
        self.calls.append(code)
        base = {"600519": 18.0, "000001": 12.0, "600036": 38.0}.get(code, 10.0)
        df = _make_synthetic_daily_df(code, days=90, base_price=base)
        if self.return_tuple:
            return (df, "stub")
        return df


@pytest.fixture(scope="function")
def temp_db():
    """Yield a per-test temporary SQLite DatabaseManager instance."""
    db_path = Path(tempfile.gettempdir()) / f"pytest_paper_{id(object())}.db"
    db_url = f"sqlite:///{db_path}"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=db_url)
    try:
        yield db
    finally:
        try:
            db_path.unlink(missing_ok=True)
        except OSError:
            pass
        DatabaseManager.reset_instance()


@pytest.fixture(scope="function")
def stub_data_provider():
    """Yield a fresh stub data provider."""
    yield StubDataProvider(return_tuple=True)
