# -*- coding: utf-8 -*-
"""Unit tests for T12 SharedQuoteCache (paper_trading/quote_cache.py)."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.quote_cache import CachedQuote, SharedQuoteCache


def make_quote(price: float = 10.0, received_at: datetime | None = None, **overrides) -> CachedQuote:
    """构造测试用行情快照；不传 received_at 时使用默认值（当前时间）."""
    fields = dict(
        price=price,
        volume=1000.0,
        change_pct=1.5,
        high=11.0,
        low=9.5,
        open=9.8,
        pre_close=9.85,
        timestamp=datetime(2026, 8, 10, 10, 0, 0),
        source="ws_tickflow",
    )
    fields.update(overrides)
    if received_at is not None:
        fields["received_at"] = received_at
    return CachedQuote(**fields)


class TestCachedQuote:
    def test_received_at_defaults_to_now(self):
        before = datetime.now()
        q = make_quote()
        after = datetime.now()
        assert before <= q.received_at <= after

    def test_fields_stored(self):
        q = make_quote(
            price=12.5,
            volume=200.0,
            change_pct=-1.2,
            high=13.0,
            low=11.8,
            open=12.0,
            pre_close=12.65,
            timestamp=datetime(2026, 8, 10, 10, 30, 0),
            source="poll_efinance",
        )
        assert q.price == 12.5
        assert q.volume == 200.0
        assert q.change_pct == -1.2
        assert q.high == 13.0
        assert q.low == 11.8
        assert q.open == 12.0
        assert q.pre_close == 12.65
        assert q.timestamp == datetime(2026, 8, 10, 10, 30, 0)
        assert q.source == "poll_efinance"


class TestSharedQuoteCache:
    def test_update_get_roundtrip(self):
        cache = SharedQuoteCache()
        quote = make_quote(price=15.0)
        cache.update("600519", quote)
        assert cache.get("600519") is quote

    def test_update_overwrites_existing(self):
        cache = SharedQuoteCache()
        cache.update("code1", make_quote(price=1.0))
        cache.update("code1", make_quote(price=2.0))
        assert cache.get("code1").price == 2.0

    def test_get_missing_returns_none(self):
        cache = SharedQuoteCache()
        assert cache.get("never_written") is None

    def test_get_expired_returns_none(self):
        cache = SharedQuoteCache(max_age_seconds=5.0)
        cache.update("code1", make_quote(received_at=datetime.now() - timedelta(seconds=5.1)))
        assert cache.get("code1") is None

    def test_get_boundary_exactly_max_age_is_fresh(self):
        cache = SharedQuoteCache(max_age_seconds=5.0)
        cache.update("code1", make_quote(received_at=datetime.now() - timedelta(seconds=5.0)))
        assert cache.get("code1") is not None

    def test_get_all_returns_only_fresh(self):
        cache = SharedQuoteCache(max_age_seconds=5.0)
        cache.update("fresh1", make_quote(price=1.0))
        cache.update("fresh2", make_quote(price=2.0))
        cache.update(
            "stale",
            make_quote(price=3.0, received_at=datetime.now() - timedelta(seconds=10.0)),
        )
        result = cache.get_all()
        assert set(result.keys()) == {"fresh1", "fresh2"}
        assert result["fresh1"].price == 1.0
        assert result["fresh2"].price == 2.0

    def test_get_all_empty(self):
        cache = SharedQuoteCache()
        assert cache.get_all() == {}

    def test_is_fresh(self):
        cache = SharedQuoteCache(max_age_seconds=5.0)
        assert cache.is_fresh("missing") is False
        cache.update("fresh", make_quote())
        assert cache.is_fresh("fresh") is True
        cache.update("stale", make_quote(received_at=datetime.now() - timedelta(seconds=6.0)))
        assert cache.is_fresh("stale") is False

    def test_remove(self):
        cache = SharedQuoteCache()
        cache.update("code1", make_quote())
        cache.update("code2", make_quote())
        cache.remove("code1")
        assert cache.get("code1") is None
        assert cache.get("code2") is not None
        assert len(cache) == 1

    def test_remove_missing_no_error(self):
        cache = SharedQuoteCache()
        cache.remove("never_written")  # 不应抛异常

    def test_clear(self):
        cache = SharedQuoteCache()
        cache.update("code1", make_quote())
        cache.update("code2", make_quote())
        cache.clear()
        assert cache.get_all() == {}
        assert len(cache) == 0
        assert cache.get("code1") is None

    def test_len_counts_only_fresh(self):
        cache = SharedQuoteCache(max_age_seconds=5.0)
        assert len(cache) == 0
        cache.update("fresh1", make_quote())
        cache.update("fresh2", make_quote())
        cache.update("stale", make_quote(received_at=datetime.now() - timedelta(seconds=10.0)))
        assert len(cache) == 2

    def test_concurrent_writes_distinct_codes(self):
        cache = SharedQuoteCache()
        total = 32

        def write(i: int) -> None:
            cache.update(f"code_{i}", make_quote(price=float(i)))

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, range(total)))

        assert len(cache) == total
        for i in range(total):
            assert cache.get(f"code_{i}").price == float(i)

    def test_concurrent_writes_same_code(self):
        cache = SharedQuoteCache()
        values = list(range(64))

        def write(i: int) -> None:
            cache.update("shared", make_quote(price=float(i)))

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(write, values))

        q = cache.get("shared")
        assert q is not None
        assert q.price in {float(v) for v in values}
        assert len(cache) == 1
