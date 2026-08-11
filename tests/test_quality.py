# -*- coding: utf-8 -*-
"""
T11 DataQualityPipeline 单元测试。

运行: python -m pytest --cov=data_provider.quality tests/test_quality.py --cov-report=term-missing
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from data_provider.quality import CrossSourceValidator, DataQualityPipeline, QualityReport


def _find(report, name):
    for check in report.checks:
        if check["name"] == name:
            return check
    raise AssertionError(f"check {name!r} not found in {report.checks}")


def _quote(code="600519", price=100.0, change_pct=1.0, timestamp=None, source="src_a"):
    q = {"code": code, "price": price, "source": source}
    if change_pct is not None:
        q["change_pct"] = change_pct
    if timestamp is not None:
        q["timestamp"] = timestamp
    return q


def _daily_df(dates, close=None, date_col="date"):
    if close is None:
        close = [100.0 + i for i in range(len(dates))]
    return pd.DataFrame({date_col: pd.to_datetime(dates), "close": close})


@pytest.fixture
def pipeline():
    return DataQualityPipeline()


# ---------------- price sanity（实时 quote） ----------------

def test_price_sanity_negative_price_fails(pipeline):
    report = pipeline.validate_realtime(_quote(price=-1.0))
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_zero_price_fails(pipeline):
    report = pipeline.validate_realtime(_quote(price=0.0))
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_huge_change_fails(pipeline):
    report = pipeline.validate_realtime(_quote(price=100.0, change_pct=600.0))
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_huge_negative_change_fails(pipeline):
    report = pipeline.validate_realtime(_quote(price=100.0, change_pct=-600.0))
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_missing_price_fails(pipeline):
    report = pipeline.validate_realtime({"code": "600519", "change_pct": 1.0, "source": "a"})
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_nan_price_fails(pipeline):
    report = pipeline.validate_realtime(_quote(price=float("nan")))
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_missing_change_pct_passes(pipeline):
    report = pipeline.validate_realtime({"code": "600519", "price": 100.0, "source": "a"})
    assert _find(report, "price_sanity")["passed"] is True


def test_price_sanity_valid_quote_passes(pipeline):
    report = pipeline.validate_realtime(_quote(price=100.0, change_pct=1.5))
    assert _find(report, "price_sanity")["passed"] is True


# ---------------- price sanity（日线 df） ----------------

def test_daily_price_sanity_valid(pipeline):
    df = _daily_df(["2026-08-03", "2026-08-04", "2026-08-05"], close=[100.0, 101.0, 102.0])
    assert _find(pipeline.validate_daily(df, "600519"), "price_sanity")["passed"] is True


def test_daily_price_sanity_negative_close_fails(pipeline):
    df = _daily_df(["2026-08-03", "2026-08-04", "2026-08-05"], close=[10.0, -5.0, 12.0])
    assert _find(pipeline.validate_daily(df, "600519"), "price_sanity")["passed"] is False


def test_daily_price_sanity_nan_close_fails(pipeline):
    df = _daily_df(["2026-08-03", "2026-08-04", "2026-08-05"], close=[10.0, np.nan, 12.0])
    assert _find(pipeline.validate_daily(df, "600519"), "price_sanity")["passed"] is False


def test_daily_price_sanity_huge_change_fails(pipeline):
    df = _daily_df(["2026-08-03", "2026-08-04"], close=[100.0, 700.0])
    assert _find(pipeline.validate_daily(df, "600519"), "price_sanity")["passed"] is False


def test_daily_price_sanity_missing_close_fails(pipeline):
    df = pd.DataFrame({"open": [1.0, 2.0]})
    assert _find(pipeline.validate_daily(df, "600519"), "price_sanity")["passed"] is False


def test_daily_price_sanity_empty_df_skipped(pipeline):
    df = pd.DataFrame(columns=["date", "close"])
    assert _find(pipeline.validate_daily(df, "600519"), "price_sanity")["passed"] is True


# ---------------- timestamp freshness ----------------

def test_timestamp_fresh_fresh(pipeline):
    ts = datetime.now() - timedelta(seconds=1)
    report = pipeline.validate_realtime(_quote(timestamp=ts))
    assert _find(report, "timestamp_freshness")["passed"] is True


def test_timestamp_fresh_stale(pipeline):
    ts = datetime.now() - timedelta(seconds=120)
    report = pipeline.validate_realtime(_quote(timestamp=ts))
    assert _find(report, "timestamp_freshness")["passed"] is False


def test_timestamp_missing_attr_skipped(pipeline):
    report = pipeline.validate_realtime({"code": "600519", "price": 100.0, "source": "a"})
    check = _find(report, "timestamp_freshness")
    assert check["passed"] is True
    assert "skip" in check["detail"].lower()


def test_timestamp_naive_vs_aware(pipeline):
    naive_fresh = datetime.now() - timedelta(seconds=1)
    assert _find(pipeline.validate_realtime(_quote(timestamp=naive_fresh)), "timestamp_freshness")["passed"] is True
    aware_fresh = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert _find(pipeline.validate_realtime(_quote(timestamp=aware_fresh)), "timestamp_freshness")["passed"] is True
    aware_stale = datetime.now(timezone.utc) - timedelta(seconds=120)
    assert _find(pipeline.validate_realtime(_quote(timestamp=aware_stale)), "timestamp_freshness")["passed"] is False
    # 非 UTC 时区的 aware timestamp 也应正确换算
    tz8 = timezone(timedelta(hours=8))
    aware_tz8 = (datetime.now(timezone.utc) - timedelta(seconds=1)).astimezone(tz8)
    assert _find(pipeline.validate_realtime(_quote(timestamp=aware_tz8)), "timestamp_freshness")["passed"] is True


def test_timestamp_pandas_and_numpy(pipeline):
    ts_pd = pd.Timestamp(datetime.now(timezone.utc) - timedelta(seconds=1))
    assert _find(pipeline.validate_realtime(_quote(timestamp=ts_pd)), "timestamp_freshness")["passed"] is True
    ts_np = np.datetime64(datetime.now() - timedelta(seconds=1))
    assert _find(pipeline.validate_realtime(_quote(timestamp=ts_np)), "timestamp_freshness")["passed"] is True


def test_timestamp_iso_string_parsed(pipeline):
    naive = (datetime.now() - timedelta(seconds=1)).isoformat()
    assert _find(pipeline.validate_realtime(_quote(timestamp=naive)), "timestamp_freshness")["passed"] is True
    aware = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    assert _find(pipeline.validate_realtime(_quote(timestamp=aware)), "timestamp_freshness")["passed"] is True


def test_timestamp_unparseable_fails(pipeline):
    report = pipeline.validate_realtime(_quote(timestamp="not-a-date"))
    assert _find(report, "timestamp_freshness")["passed"] is False


# ---------------- no gaps ----------------

def test_no_gaps_complete_series(pipeline):
    dates = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    assert _find(pipeline.validate_daily(_daily_df(dates), "600519"), "no_gaps")["passed"] is True


def test_no_gaps_weekend_gap_ok(pipeline):
    # 周五 -> 周一（3 个日历日，0 个交易日缺失）
    dates = ["2026-08-07", "2026-08-10"]
    assert _find(pipeline.validate_daily(_daily_df(dates), "600519"), "no_gaps")["passed"] is True


def test_no_gaps_holiday_gap_ok(pipeline):
    # 周四 -> 周一（4 个日历日，仅 1 个交易日缺失，容忍）
    dates = ["2026-08-06", "2026-08-10"]
    assert _find(pipeline.validate_daily(_daily_df(dates), "600519"), "no_gaps")["passed"] is True


def test_no_gaps_two_plus_missing_trading_days_fails(pipeline):
    # 周一 -> 周五：日历间隔 4 天 <= 4，但中间缺失 3 个交易日
    dates = ["2026-08-03", "2026-08-07"]
    assert _find(pipeline.validate_daily(_daily_df(dates), "600519"), "no_gaps")["passed"] is False


def test_no_gaps_large_calendar_gap_fails(pipeline):
    # 周五 -> 周三：日历间隔 5 天 > 4
    dates = ["2026-08-07", "2026-08-12"]
    assert _find(pipeline.validate_daily(_daily_df(dates), "600519"), "no_gaps")["passed"] is False


def test_no_gaps_less_than_two_rows_skip(pipeline):
    df = _daily_df(["2026-08-10"])
    check = _find(pipeline.validate_daily(df, "600519"), "no_gaps")
    assert check["passed"] is True
    assert "skip" in check["detail"].lower()


def test_no_gaps_empty_df_skip(pipeline):
    df = pd.DataFrame(columns=["date", "close"])
    check = _find(pipeline.validate_daily(df, "600519"), "no_gaps")
    assert check["passed"] is True


def test_no_gaps_datetime_index(pipeline):
    df = pd.DataFrame(
        {"close": [1.0, 2.0, 3.0]},
        index=pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"]),
    )
    assert _find(pipeline.validate_daily(df, "600519"), "no_gaps")["passed"] is True


def test_no_gaps_datetime_column(pipeline):
    df = _daily_df(["2026-08-03", "2026-08-04", "2026-08-05"], date_col="datetime")
    assert _find(pipeline.validate_daily(df, "600519"), "no_gaps")["passed"] is True


def test_no_gaps_missing_date_fails(pipeline):
    df = pd.DataFrame({"close": [1.0, 2.0]})
    assert _find(pipeline.validate_daily(df, "600519"), "no_gaps")["passed"] is False


def test_no_gaps_invalid_date_value_fails(pipeline):
    df = pd.DataFrame({"date": ["2026-08-03", "garbage"], "close": [1.0, 2.0]})
    assert _find(pipeline.validate_daily(df, "600519"), "no_gaps")["passed"] is False


# ---------------- exception isolation ----------------

def test_realtime_check_exception_isolation(pipeline, monkeypatch):
    def boom(quote):
        raise ValueError("boom")

    monkeypatch.setattr(pipeline, "_check_timestamp_freshness", boom)
    report = pipeline.validate_realtime(_quote(timestamp=datetime.now()))
    checks = {c["name"]: c for c in report.checks}
    assert checks["timestamp_freshness"]["passed"] is False
    assert "boom" in checks["timestamp_freshness"]["detail"]
    assert checks["price_sanity"]["passed"] is True
    assert report.passed is False


def test_daily_check_exception_isolation(pipeline, monkeypatch):
    def boom(df):
        raise RuntimeError("daily boom")

    monkeypatch.setattr(pipeline, "_check_no_gaps", boom)
    df = _daily_df(["2026-08-03", "2026-08-04"])
    report = pipeline.validate_daily(df, "600519")
    checks = {c["name"]: c for c in report.checks}
    assert checks["no_gaps"]["passed"] is False
    assert "daily boom" in checks["no_gaps"]["detail"]
    assert checks["price_sanity"]["passed"] is True


# ---------------- cross-source validator ----------------

def test_cross_source_two_sources_pass():
    validator = CrossSourceValidator()
    quotes = [{"price": 100.0, "source": "a"}, {"price": 100.5, "source": "b"}]
    report = validator.validate("600519", quotes)
    assert report.passed is True
    assert report.checks[0]["passed"] is True


def test_cross_source_three_sources_pass():
    validator = CrossSourceValidator()
    quotes = [
        {"price": 100.0, "source": "a"},
        {"price": 100.2, "source": "b"},
        {"price": 99.9, "source": "c"},
    ]
    assert validator.validate("600519", quotes).passed is True


def test_cross_source_deviation_over_two_percent_fails():
    validator = CrossSourceValidator()
    quotes = [{"price": 100.0, "source": "a"}, {"price": 105.0, "source": "b"}]
    report = validator.validate("600519", quotes)
    assert report.passed is False
    assert report.checks[0]["passed"] is False
    assert "deviation" in report.checks[0]["detail"].lower()


def test_cross_source_less_than_two_sources_fails():
    validator = CrossSourceValidator()
    report = validator.validate("600519", [{"price": 100.0, "source": "a"}])
    assert report.passed is False
    assert "only 1 source" in report.checks[0]["detail"]


def test_cross_source_less_than_two_valid_prices_fails():
    validator = CrossSourceValidator()
    quotes = [
        {"price": 100.0, "source": "a"},
        {"price": 0.0, "source": "b"},
        {"price": None, "source": "c"},
        {"price": "not-a-number", "source": "d"},
    ]
    report = validator.validate("600519", quotes)
    assert report.passed is False
    assert "not enough valid prices" in report.checks[0]["detail"]


def test_cross_source_object_quotes():
    class Quote:
        def __init__(self, price, source):
            self.price = price
            self.source = source

    validator = CrossSourceValidator()
    report = validator.validate("AAPL", [Quote(100.0, "a"), Quote(100.3, "b")])
    assert report.passed is True


# ---------------- report shape ----------------

def test_quality_report_shape(pipeline):
    report = pipeline.validate_realtime(_quote())
    assert isinstance(report, QualityReport)
    assert report.code == "600519"
    assert report.source == "src_a"
    assert isinstance(report.timestamp, datetime)
    assert all(set(c) == {"name", "passed", "detail"} for c in report.checks)
    assert report.passed is True


def test_validate_daily_report_shape(pipeline):
    df = _daily_df(["2026-08-03", "2026-08-04"])
    report = pipeline.validate_daily(df, "600519")
    assert isinstance(report, QualityReport)
    assert report.code == "600519"
    assert report.passed is True
    assert {c["name"] for c in report.checks} == {"price_sanity", "no_gaps"}

# ---------------- defensive branches ----------------

def test_price_sanity_invalid_price_string_fails(pipeline):
    report = pipeline.validate_realtime(_quote(price="not-a-number"))
    assert _find(report, "price_sanity")["passed"] is False


def test_price_sanity_invalid_change_pct_skipped(pipeline):
    report = pipeline.validate_realtime(_quote(price=100.0, change_pct="not-a-number"))
    assert _find(report, "price_sanity")["passed"] is True


def test_price_sanity_nan_change_pct_skipped(pipeline):
    report = pipeline.validate_realtime(_quote(price=100.0, change_pct=float("nan")))
    assert _find(report, "price_sanity")["passed"] is True


def test_timestamp_unsupported_type_fails(pipeline):
    report = pipeline.validate_realtime(_quote(timestamp=123456))
    assert _find(report, "timestamp_freshness")["passed"] is False
