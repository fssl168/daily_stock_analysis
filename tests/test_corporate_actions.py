# -*- coding: utf-8 -*-
"""Unit tests for T13 CorporateEventCalendar (data_provider/corporate_actions.py)."""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest

from data_provider.corporate_actions import CorporateEvent, CorporateEventCalendar


def make_event(
    code: str = "600519",
    event_date: object = date(2024, 5, 10),
    event_type: str = "dividend",
    details: dict | None = None,
) -> CorporateEvent:
    return CorporateEvent(
        code=code,
        event_date=event_date,
        event_type=event_type,
        details=details if details is not None else {},
    )


def make_daily_df(close_values, dates=None):
    """构造日线 DataFrame（open/high/low/close），index 为 DatetimeIndex."""
    if dates is None:
        dates = pd.date_range("2024-05-06", periods=len(close_values), freq="D")
    return pd.DataFrame(
        {
            "open": [c * 0.99 for c in close_values],
            "high": [c * 1.01 for c in close_values],
            "low": [c * 0.98 for c in close_values],
            "close": list(close_values),
        },
        index=pd.DatetimeIndex(dates),
    )


class TestCorporateEvent:
    def test_fields_stored(self):
        e = CorporateEvent(
            code="600519",
            event_date=date(2024, 5, 10),
            event_type="dividend",
            details={"dividend_per_share": 0.5},
        )
        assert e.code == "600519"
        assert e.event_date == date(2024, 5, 10)
        assert e.event_type == "dividend"
        assert e.details == {"dividend_per_share": 0.5}

    def test_details_defaults_to_empty_dict(self):
        e = CorporateEvent(code="600519", event_date=date(2024, 5, 10), event_type="split")
        assert e.details == {}


class TestEventDateNormalization:
    def test_accepts_date(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date=date(2024, 5, 10)))
        assert cal.get_events("600519")[0].event_date == date(2024, 5, 10)

    def test_accepts_datetime(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date=datetime(2024, 5, 10, 15, 30, 0)))
        assert cal.get_events("600519")[0].event_date == date(2024, 5, 10)

    def test_accepts_str(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10"))
        assert cal.get_events("600519")[0].event_date == date(2024, 5, 10)

    def test_rejects_unsupported_type(self):
        cal = CorporateEventCalendar()
        with pytest.raises(TypeError):
            cal.add_event(make_event(event_date=20240510))


class TestAddEvent:
    def test_adds_event_grouped_by_code(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(code="600519"))
        cal.add_event(make_event(code="AAPL"))
        assert len(cal.get_events("600519")) == 1
        assert len(cal.get_events("AAPL")) == 1

    def test_dedup_same_code_date_type(self):
        cal = CorporateEventCalendar()
        e1 = make_event(code="600519", event_date="2024-05-10", event_type="dividend")
        e2 = make_event(code="600519", event_date="2024-05-10", event_type="dividend")
        cal.add_event(e1)
        cal.add_event(e2)
        events = cal.get_events("600519")
        assert len(events) == 1
        assert events[0].details == {}

    def test_same_date_different_type_kept(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", event_type="dividend"))
        cal.add_event(make_event(event_date="2024-05-10", event_type="split"))
        assert len(cal.get_events("600519")) == 2

    def test_same_type_different_date_kept(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", event_type="dividend"))
        cal.add_event(make_event(event_date="2024-06-01", event_type="dividend"))
        assert len(cal.get_events("600519")) == 2


class TestGetEvents:
    def test_returns_sorted_by_date(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-06-01"))
        cal.add_event(make_event(event_date="2024-01-15"))
        cal.add_event(make_event(event_date="2024-03-20"))
        dates = [e.event_date for e in cal.get_events("600519")]
        assert dates == [date(2024, 1, 15), date(2024, 3, 20), date(2024, 6, 1)]

    def test_unknown_code_returns_empty(self):
        cal = CorporateEventCalendar()
        assert cal.get_events("missing") == []

    def test_returns_copy_not_internal_list(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event())
        events = cal.get_events("600519")
        events.clear()
        assert len(cal.get_events("600519")) == 1


class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        cache_path = tmp_path / "events.json"
        cal = CorporateEventCalendar(cache_path=str(cache_path))
        cal.add_event(make_event(code="600519", event_date="2024-05-10", details={"dividend_per_share": 0.5}))
        cal.add_event(make_event(code="600519", event_date="2024-08-01", event_type="split", details={"split_ratio": 2.0}))
        cal.add_event(make_event(code="AAPL", event_date="2024-06-15", details={"dividend_per_share": 0.25}))
        cal.save()

        loaded = CorporateEventCalendar(cache_path=str(cache_path))
        ev_600519 = loaded.get_events("600519")
        assert len(ev_600519) == 2
        assert ev_600519[0].event_date == date(2024, 5, 10)
        assert ev_600519[0].details == {"dividend_per_share": 0.5}
        assert ev_600519[1].event_type == "split"
        ev_aapl = loaded.get_events("AAPL")
        assert len(ev_aapl) == 1
        assert ev_aapl[0].details == {"dividend_per_share": 0.25}

    def test_saved_file_structure(self, tmp_path):
        cache_path = tmp_path / "events.json"
        cal = CorporateEventCalendar(cache_path=str(cache_path))
        cal.add_event(make_event(code="600519", event_date="2024-05-10"))
        cal.save()
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        assert raw["600519"][0]["event_date"] == "2024-05-10"
        assert raw["600519"][0]["event_type"] == "dividend"
        assert raw["600519"][0]["details"] == {}

    def test_load_missing_file_is_empty(self, tmp_path):
        cal = CorporateEventCalendar(cache_path=str(tmp_path / "nope.json"))
        assert cal.get_events("600519") == []

    def test_load_corrupt_json_is_empty(self, tmp_path):
        cache_path = tmp_path / "events.json"
        cache_path.write_text("{not valid json", encoding="utf-8")
        cal = CorporateEventCalendar(cache_path=str(cache_path))
        assert cal.get_events("600519") == []

    def test_load_invalid_structure_is_empty(self, tmp_path):
        cache_path = tmp_path / "events.json"
        cache_path.write_text("[1, 2, 3]", encoding="utf-8")
        cal = CorporateEventCalendar(cache_path=str(cache_path))
        assert cal.get_events("600519") == []

    def test_load_invalid_item_is_empty(self, tmp_path):
        cache_path = tmp_path / "events.json"
        cache_path.write_text(json.dumps({"600519": [{"code": "600519"}]}), encoding="utf-8")
        cal = CorporateEventCalendar(cache_path=str(cache_path))
        assert cal.get_events("600519") == []

    def test_load_missing_details_key(self, tmp_path):
        cache_path = tmp_path / "events.json"
        cache_path.write_text(
            json.dumps(
                {"600519": [{"code": "600519", "event_date": "2024-05-10", "event_type": "dividend"}]}
            ),
            encoding="utf-8",
        )
        cal = CorporateEventCalendar(cache_path=str(cache_path))
        assert cal.get_events("600519")[0].details == {}

    def test_in_memory_mode_save_load_noop(self, tmp_path):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(code="600519"))
        cal.save()  # 无缓存路径 → no-op
        cal.load()  # 无缓存路径 → no-op
        assert len(cal.get_events("600519")) == 1


class TestApplyDividendAdjustment:
    def test_event_day_in_df(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 2.0}))
        closes = [10.0, 10.5, 11.0, 9.0, 9.5]  # 事件日 2024-05-10 当日 close = 11.0
        df = make_daily_df(closes, dates=pd.date_range("2024-05-08", periods=5, freq="D"))
        result = cal.apply_dividend_adjustment("600519", df)
        factor = (11.0 - 2.0) / 11.0
        # 事件日之前两行被调整
        assert result.loc["2024-05-08", "close"] == pytest.approx(10.0 * factor)
        assert result.loc["2024-05-09", "close"] == pytest.approx(10.5 * factor)
        # 事件日及之后不调整
        assert result.loc["2024-05-10", "close"] == 11.0
        assert result.loc["2024-05-11", "close"] == 9.0
        assert result.loc["2024-05-12", "close"] == 9.5

    def test_event_day_not_in_df_uses_next_bar_close(self):
        cal = CorporateEventCalendar()
        # 事件日 2024-05-10 不在 df（df 从 05-11 开始），取 05-11 的 close=9.0
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 1.0}))
        df = make_daily_df([9.0, 9.5, 10.0], dates=pd.date_range("2024-05-11", periods=3, freq="D"))
        result = cal.apply_dividend_adjustment("600519", df)
        # 05-11 是事件日后第一个 bar，其 close 被用作 close_before，但 05-11 不在事件日之前，不调整
        assert result.loc["2024-05-11", "close"] == 9.0
        assert result.loc["2024-05-12", "close"] == 9.5
        assert result.loc["2024-05-13", "close"] == 10.0

    def test_event_day_not_in_df_with_bars_before(self):
        cal = CorporateEventCalendar()
        # 事件日 2024-05-10 不在 df（df 05-06..05-09 + 05-13），取事件日后第一个 bar 05-13 close=8.0
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 2.0}))
        df = make_daily_df(
            [10.0, 10.2, 10.4, 10.6, 8.0],
            dates=pd.date_range("2024-05-06", periods=4, freq="D").append(pd.DatetimeIndex([pd.Timestamp("2024-05-13")])),
        )
        result = cal.apply_dividend_adjustment("600519", df)
        factor = (8.0 - 2.0) / 8.0
        for d in ["2024-05-06", "2024-05-07", "2024-05-08", "2024-05-09"]:
            assert result.loc[d, "close"] == pytest.approx(df.loc[d, "close"] * factor)
        assert result.loc["2024-05-13", "close"] == 8.0

    def test_event_after_last_bar_skipped(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-20", details={"dividend_per_share": 1.0}))
        df = make_daily_df([10.0, 10.5, 11.0], dates=pd.date_range("2024-05-06", periods=3, freq="D"))
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)

    def test_no_events_returns_copy(self):
        cal = CorporateEventCalendar()
        df = make_daily_df([10.0, 10.5, 11.0])
        result = cal.apply_dividend_adjustment("600519", df)
        assert result is not df
        pd.testing.assert_frame_equal(result, df)

    def test_original_df_unmodified(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 2.0}))
        closes = [10.0, 10.5, 11.0, 9.0, 9.5]
        df = make_daily_df(closes, dates=pd.date_range("2024-05-08", periods=5, freq="D"))
        original = df.copy()
        cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(df, original)

    def test_ignores_non_dividend_events(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_type="split", details={"split_ratio": 2.0}))
        df = make_daily_df([10.0, 10.5, 11.0])
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)

    def test_ignores_dividend_without_dividend_per_share(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_type="dividend", details={"ratio": 0.1}))
        df = make_daily_df([10.0, 10.5, 11.0])
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)

    def test_skips_nan_dividend(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": float("nan")}))
        df = make_daily_df([10.0, 10.5, 11.0, 9.0, 9.5], dates=pd.date_range("2024-05-08", periods=5, freq="D"))
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)

    def test_skips_nan_close_before(self):
        cal = CorporateEventCalendar()
        # 事件日 2024-05-10 当日 close 为 NaN → 跳过
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 1.0}))
        df = make_daily_df(
            [10.0, 10.5, float("nan"), 9.0, 9.5],
            dates=pd.date_range("2024-05-08", periods=5, freq="D"),
        )
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)

    def test_skips_zero_close_before(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 1.0}))
        df = make_daily_df([10.0, 10.5, 0.0, 9.0, 9.5], dates=pd.date_range("2024-05-08", periods=5, freq="D"))
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)

    def test_empty_df_returns_copy(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(details={"dividend_per_share": 1.0}))
        df = make_daily_df([])
        result = cal.apply_dividend_adjustment("600519", df)
        assert result is not df
        assert result.empty

    def test_df_without_close_returns_copy(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(details={"dividend_per_share": 1.0}))
        df = pd.DataFrame({"volume": [100, 200, 300]}, index=pd.date_range("2024-05-06", periods=3, freq="D"))
        result = cal.apply_dividend_adjustment("600519", df)
        assert result is not df
        pd.testing.assert_frame_equal(result, df)

    def test_multiple_events_accumulate(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(event_date="2024-05-10", details={"dividend_per_share": 2.0}))
        cal.add_event(make_event(event_date="2024-05-12", details={"dividend_per_share": 1.0}))
        df = make_daily_df(
            [10.0, 10.5, 11.0, 12.0, 13.0],
            dates=pd.date_range("2024-05-08", periods=5, freq="D"),
        )
        result = cal.apply_dividend_adjustment("600519", df)
        f1 = (11.0 - 2.0) / 11.0
        f2 = (13.0 - 1.0) / 13.0
        # 05-08 / 05-09 在两次事件之前 → 乘两个因子
        assert result.loc["2024-05-08", "close"] == pytest.approx(10.0 * f1 * f2)
        assert result.loc["2024-05-09", "close"] == pytest.approx(10.5 * f1 * f2)
        # 05-10 / 05-11 在第二次事件之前 → 只乘 f2
        assert result.loc["2024-05-10", "close"] == pytest.approx(11.0 * f2)
        assert result.loc["2024-05-11", "close"] == pytest.approx(12.0 * f2)
        # 05-12 为第二次事件当日 → 不调整
        assert result.loc["2024-05-12", "close"] == pytest.approx(13.0)

    def test_other_code_events_ignored(self):
        cal = CorporateEventCalendar()
        cal.add_event(make_event(code="AAPL", event_date="2024-05-10", details={"dividend_per_share": 2.0}))
        df = make_daily_df([10.0, 10.5, 11.0])
        result = cal.apply_dividend_adjustment("600519", df)
        pd.testing.assert_frame_equal(result, df)
