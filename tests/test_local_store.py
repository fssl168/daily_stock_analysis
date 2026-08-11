# -*- coding: utf-8 -*-
"""Tests for data_provider/local_store.py (T9)

Covers: upsert 插入/覆盖（同 code+date）、str/date 索引兼容、get 返回
DatetimeIndex、空区间返回 None、needs_update 无记录/新鲜/过期、close。

所有用例使用 pytest 的 tmp_path fixture 创建临时 DB，绝不触碰项目 data/。
"""

import sys
import sqlite3
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

# 现有仓库约定：导入 data_provider 前 stub 可选依赖，避免环境差异导致测试无法运行
if "fake_useragent" not in sys.modules:
    sys.modules["fake_useragent"] = MagicMock()

import pandas as pd
import pytest

from data_provider.local_store import LocalMarketStore, StoreConfig

COLUMNS = ["open", "high", "low", "close", "volume", "amount", "source"]


def _make_df(index, closes=None):
    """构造日线 DataFrame，index 可为日期字符串/date/Timestamp 列表。"""
    index = list(index)
    n = len(index)
    closes = closes if closes is not None else [10.5] * n
    return pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [12.0] * n,
            "low": [9.0] * n,
            "close": closes,
            "volume": [1000.0] * n,
            "amount": [10000.0] * n,
        },
        index=index,
    )


def _expected_index(dates):
    """get() 返回的 DatetimeIndex，名称沿用 trade_date。"""
    return pd.DatetimeIndex(dates, name="trade_date")


def _rewrite_fetched_at(store, code, fetched_at):
    """直接改写 fetched_at，用于构造“过期记录”场景。"""
    store._conn.execute(
        "UPDATE daily_kline SET fetched_at = ? WHERE code = ?", (fetched_at, code)
    )
    store._conn.commit()


@pytest.fixture
def store(tmp_path):
    """每个用例独立的临时 DB，不污染 data/。"""
    s = LocalMarketStore(
        StoreConfig(db_path=str(tmp_path / "market_data.db"), stale_threshold_hours=24)
    )
    yield s
    s.close()


class TestUpsert:
    def test_upsert_inserts_rows(self, store):
        store.upsert("600519", _make_df(["2026-08-03", "2026-08-04", "2026-08-05"]), "test")
        result = store.get("600519", date(2026, 8, 3), date(2026, 8, 5))
        assert result is not None
        assert len(result) == 3
        pd.testing.assert_index_equal(
            result.index, _expected_index(["2026-08-03", "2026-08-04", "2026-08-05"])
        )

    def test_upsert_replaces_existing_same_code_date(self, store):
        store.upsert("600519", _make_df(["2026-08-04"], closes=[10.0]), "test")
        store.upsert("600519", _make_df(["2026-08-04"], closes=[99.0]), "test")
        result = store.get("600519", date(2026, 8, 4), date(2026, 8, 4))
        assert result is not None
        assert len(result) == 1
        assert float(result.iloc[0]["close"]) == 99.0

    def test_upsert_accepts_date_index(self, store):
        store.upsert("000001", _make_df([date(2026, 8, 3)]), "test")
        result = store.get("000001", date(2026, 8, 3), date(2026, 8, 3))
        assert result is not None
        assert len(result) == 1
        pd.testing.assert_index_equal(result.index, _expected_index(["2026-08-03"]))

    def test_upsert_accepts_str_index(self, store):
        store.upsert("000002", _make_df(["2026-08-03", "2026-08-04"]), "test")
        result = store.get("000002", date(2026, 8, 3), date(2026, 8, 4))
        assert result is not None
        assert len(result) == 2
        pd.testing.assert_index_equal(
            result.index, _expected_index(["2026-08-03", "2026-08-04"])
        )


class TestGet:
    def test_get_returns_dataframe_with_datetimeindex(self, store):
        store.upsert("600519", _make_df(["2026-08-03", "2026-08-04"]), "test")
        result = store.get("600519", date(2026, 8, 3), date(2026, 8, 4))
        assert result is not None
        assert isinstance(result.index, pd.DatetimeIndex)
        assert list(result.columns) == COLUMNS
        assert float(result.iloc[0]["open"]) == 10.0

    def test_get_empty_range_returns_none(self, store):
        store.upsert("600519", _make_df(["2026-08-04"]), "test")
        # 区间内无数据
        assert store.get("600519", date(2026, 1, 1), date(2026, 1, 31)) is None
        # 无记录 code
        assert store.get("000001", date(2026, 8, 3), date(2026, 8, 5)) is None

    def test_get_respects_date_range_boundary(self, store):
        store.upsert("600519", _make_df(["2026-08-03", "2026-08-04", "2026-08-05"]), "test")
        result = store.get("600519", date(2026, 8, 4), date(2026, 8, 5))
        assert result is not None
        assert len(result) == 2
        pd.testing.assert_index_equal(
            result.index, _expected_index(["2026-08-04", "2026-08-05"])
        )


class TestNeedsUpdate:
    def test_no_record_true(self, store):
        assert store.needs_update("600519") is True

    def test_fresh_record_false(self, store):
        store.upsert("600519", _make_df(["2026-08-04"]), "test")
        assert store.needs_update("600519") is False

    def test_stale_record_true(self, store):
        store.upsert("600519", _make_df(["2026-08-04"]), "test")
        stale_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        _rewrite_fetched_at(store, "600519", stale_ts)
        assert store.needs_update("600519") is True


class TestClose:
    def test_close(self, store):
        store.close()
        with pytest.raises(sqlite3.ProgrammingError):
            store._conn.execute("SELECT 1 FROM daily_kline")
