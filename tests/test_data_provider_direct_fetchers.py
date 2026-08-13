# -*- coding: utf-8 -*-
"""Offline tests for the direct Sina / Eastmoney fetchers (no network).

Covers Sina K-line JSONP + realtime GBK parsing, Eastmoney field scaling and
secid/symbol mapping (including BSE 92xxxx), and the MultiSourceDataFetcher
realtime-quote *object* acceptance regression — the root cause of
"all data sources unavailable".
"""

import unittest

from data_provider.eastmoney_fetcher import (
    _build_quote,
    _scale,
    _to_em_secid,
)
from data_provider.sina_fetcher import (
    _parse_kline_jsonp,
    _parse_realtime,
    _to_sina_symbol,
)
from src.data_fetcher import MultiSourceDataFetcher


class _StubPriceQuote:
    """Minimal realtime-quote stand-in exposing a ``price`` attribute."""

    def __init__(self, price):
        self.price = price
        self.code = "600519"


class _QuoteAdapter:
    def __init__(self, quote):
        self.quote = quote

    def get_realtime_quote(self, code):
        return self.quote


class TestSinaSymbolMapping(unittest.TestCase):
    def test_sh_sz_bj_prefixes(self):
        self.assertEqual(_to_sina_symbol("600519"), "sh600519")
        self.assertEqual(_to_sina_symbol("600519.SH"), "sh600519")
        self.assertEqual(_to_sina_symbol("300750"), "sz300750")
        self.assertEqual(_to_sina_symbol("000001.SZ"), "sz000001")
        self.assertEqual(_to_sina_symbol("920748.BJ"), "bj920748")

    def test_unsupported_markets_skipped(self):
        self.assertEqual(_to_sina_symbol("00700.HK"), "")
        self.assertEqual(_to_sina_symbol("AAPL"), "")
        self.assertEqual(_to_sina_symbol(""), "")


class TestSinaKlineParsing(unittest.TestCase):
    def test_parse_kline_jsonp(self):
        txt = (
            'var _=([{"day":"2026-08-12","open":"11.26","high":"11.29",'
            '"low":"11.20","close":"11.25","volume":"63295026"},'
            '{"day":"2026-08-11","open":"11.31","high":"11.40",'
            '"low":"11.24","close":"11.26","volume":"66336053"}]);'
        )
        rows = _parse_kline_jsonp(txt)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date"], "2026-08-12")
        self.assertEqual(rows[0]["close"], "11.25")

    def test_parse_kline_malformed(self):
        self.assertEqual(_parse_kline_jsonp("not json"), [])
        self.assertEqual(_parse_kline_jsonp(""), [])


class TestSinaRealtimeParsing(unittest.TestCase):
    def test_parse_realtime_gbk(self):
        body = (
            'var hq_str_sh600519="贵州茅台,1680.000,1675.000,1682.000,'
            '1690.000,1670.000,1682.000,1690.000,1234567,2000000000,'
            '1,200000,贵州茅台,28.000,1685.000,11.000,2026-08-13,10:00:00,00";'
        )
        q = _parse_realtime(body.encode("gbk"), "600519", "sh600519")
        self.assertIsNotNone(q)
        self.assertEqual(q.price, 1682.0)
        self.assertEqual(q.pre_close, 1675.0)
        self.assertEqual(q.volume, 1234567)
        self.assertAlmostEqual(q.change_pct, 0.42, places=2)

    def test_parse_realtime_empty(self):
        self.assertIsNone(_parse_realtime(b'var hq_str_x="";', "600519", "sh600519"))


class TestEastmoneySecidMapping(unittest.TestCase):
    def test_market_mapping(self):
        self.assertEqual(_to_em_secid("600519"), "1.600519")
        self.assertEqual(_to_em_secid("600519.SH"), "1.600519")
        self.assertEqual(_to_em_secid("300750"), "0.300750")
        self.assertEqual(_to_em_secid("000001.SZ"), "0.000001")
        # 沪B 900xxx 仍归上交所
        self.assertEqual(_to_em_secid("900901"), "1.900901")

    def test_bse_codes_use_market_zero(self):
        # 北交所新号段 92xxxx 与历史 43/83/87 号段
        self.assertEqual(_to_em_secid("920748.BJ"), "0.920748")
        self.assertEqual(_to_em_secid("430047"), "0.430047")
        self.assertEqual(_to_em_secid("830799"), "0.830799")

    def test_unsupported_markets_skipped(self):
        self.assertEqual(_to_em_secid("00700.HK"), "")
        self.assertEqual(_to_em_secid("AAPL"), "")


class TestEastmoneyScale(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(_scale(168200), 1682.0)
        self.assertEqual(_scale("168200"), 1682.0)

    def test_scale_invalid(self):
        self.assertIsNone(_scale(0))
        self.assertIsNone(_scale(-1))
        self.assertIsNone(_scale("abc"))


class TestEastmoneyBuildQuote(unittest.TestCase):
    def test_build_quote(self):
        data = {
            "f43": 168200, "f44": 169000, "f45": 167000, "f46": 167500,
            "f47": 12345, "f48": 2000000000, "f57": "600519",
            "f58": "贵州茅台", "f60": 167500, "f169": 700, "f170": 0.42,
        }
        q = _build_quote(data, "600519")
        self.assertIsNotNone(q)
        self.assertEqual(q.price, 1682.0)
        self.assertEqual(q.pre_close, 1675.0)
        self.assertEqual(q.volume, 1234500)  # 手 -> 股
        self.assertEqual(q.amount, 2000000000.0)

    def test_build_quote_missing_price(self):
        self.assertIsNone(_build_quote({"f43": -1}, "600519"))


class TestMultiSourceRealtimeRootCause(unittest.TestCase):
    """Regression: get_realtime_quote must accept object quotes (root cause)."""

    def test_accepts_object_quote(self):
        fetcher = MultiSourceDataFetcher(source_priority=["fake"])
        fetcher._get_source_adapter = lambda name: _QuoteAdapter(_StubPriceQuote(10.5))
        quote = fetcher.get_realtime_quote("600519")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.price, 10.5)

    def test_accepts_dict_quote(self):
        fetcher = MultiSourceDataFetcher(source_priority=["fake"])
        fetcher._get_source_adapter = lambda name: _QuoteAdapter(
            {"code": "600519", "price": 12.34}
        )
        quote = fetcher.get_realtime_quote("600519")
        self.assertIsNotNone(quote)
        self.assertEqual(quote["price"], 12.34)

    def test_skips_zero_price_and_falls_through(self):
        fetcher = MultiSourceDataFetcher(source_priority=["bad", "good"])
        adapters = {
            "bad": _QuoteAdapter(_StubPriceQuote(0)),
            "good": _QuoteAdapter(_StubPriceQuote(8.8)),
        }
        fetcher._get_source_adapter = lambda name: adapters[name]
        quote = fetcher.get_realtime_quote("600519")
        self.assertEqual(quote.price, 8.8)


if __name__ == "__main__":
    unittest.main()
