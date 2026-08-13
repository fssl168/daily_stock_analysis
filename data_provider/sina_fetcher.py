# -*- coding: utf-8 -*-
"""Sina Finance direct fetcher (新浪财经): daily K-line + realtime quotes.

Free, no API key required. Covers A-shares (and index/ETF via symbol mapping).

K-line:   https://quotes.sina.cn/cn/api/jsonp_v2.php/...getKLineData?symbol=sh600519&scale=240
Realtime: https://hq.sinajs.cn/list=sh600519 (requires Referer: finance.sina.com.cn)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    normalize_stock_code,
    is_bse_code,
)
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 8
_MAX_KLINE_BARS = 320

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}


class SinaFetcher(BaseFetcher):
    """Sina Finance daily K-line + realtime quote fetcher."""

    name = "SinaFetcher"
    priority = 0
    allow_empty_daily_data = True

    _KLINE_ENDPOINT = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
    _QUOTE_ENDPOINT = "https://hq.sinajs.cn/list="

    # ------------------------------------------------------------------
    # Daily K-line (BaseFetcher contract)
    # ------------------------------------------------------------------

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        symbol = _to_sina_symbol(stock_code)
        if not symbol:
            raise DataFetchError(f"SinaFetcher unsupported stock code: {stock_code}")

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            datalen = max(30, min(_MAX_KLINE_BARS, int((end - start).days * 1.5) + 20))
        except ValueError:
            datalen = 90

        try:
            resp = requests.get(
                self._KLINE_ENDPOINT,
                params={"symbol": symbol, "scale": "240", "ma": "no", "datalen": datalen},
                headers=_HEADERS,
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except Exception as exc:
            raise DataFetchError(f"SinaFetcher kline request failed: {exc}") from exc

        rows = _parse_kline_jsonp(resp.text)
        if not rows:
            logger.info("SinaFetcher empty daily history for %s", stock_code)
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date"])  # 解析失败/空日期行丢弃，避免字符串比较异常
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if "amount" not in normalized.columns:
            normalized["amount"] = None
        if "pct_chg" not in normalized.columns:
            normalized["pct_chg"] = normalized["close"].pct_change().fillna(0.0) * 100
        cols = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
        return normalized[[c for c in cols if c in normalized.columns]]

    # ------------------------------------------------------------------
    # Realtime quote
    # ------------------------------------------------------------------

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        symbol = _to_sina_symbol(stock_code)
        if not symbol:
            return None
        try:
            resp = requests.get(
                self._QUOTE_ENDPOINT + symbol,
                headers=_HEADERS,
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("SinaFetcher realtime request failed for %s: %s", stock_code, exc)
            return None
        return _parse_realtime(resp.content, stock_code, symbol)


def _to_sina_symbol(stock_code: str) -> str:
    code = normalize_stock_code(stock_code)
    if not code:
        return ""
    # 港股: HK00700 -> hk00700 (新浪港股接口)
    if code[:2].lower() == "hk" and len(code) == 7 and code[2:].isdigit():
        return f"hk{code[2:]}"
    if not code.isdigit() or len(code) != 6:
        return ""
    if is_bse_code(code):
        return f"bj{code}"
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _parse_kline_jsonp(text: str) -> list[dict[str, Any]]:
    """Parse `var _=([{...},{...}]);` JSONP payload."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "date": str(item.get("day", "")),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": _to_number(item.get("volume")),
            }
        )
    return rows


def _to_number(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _parse_realtime(content: bytes, stock_code: str, symbol: str) -> Optional[UnifiedRealtimeQuote]:
    """Parse ``var hq_str_xxx="...";`` (GBK).

    A-share: name, open, pre_close, price, high, low, ..., volume(股), amount(元)
    HK:      code_en, name, open, pre_close, high, low, price, change_amount, change_pct
    """
    try:
        text = content.decode("gbk", errors="replace")
    except Exception:
        text = content.decode("utf-8", errors="replace")
    m = re.search(r'"([^"]*)"', text)
    if not m:
        return None
    fields = m.group(1).split(",")
    is_hk = symbol.startswith("hk")

    if is_hk:
        # 港股: [0]=代码 [1]=名称 [2]=开盘 [3]=昨收 [4]=最高 [5]=最低 [6]=现价
        #       [7]=涨跌额 [8]=涨跌幅%
        if len(fields) < 9:
            return None
        try:
            price = float(fields[6]) if fields[6] else None
            pre_close = float(fields[3]) if fields[3] else None
            open_price = float(fields[2]) if fields[2] else None
            high = float(fields[4]) if fields[4] else None
            low = float(fields[5]) if fields[5] else None
        except (ValueError, IndexError):
            return None
        name = fields[1]
        change_amount = None
        change_pct = None
        try:
            if fields[8]:
                change_pct = float(fields[8])
        except (ValueError, IndexError):
            pass
        if price is not None and pre_close:
            change_amount = price - pre_close
            if change_pct is None:
                change_pct = change_amount / pre_close * 100.0
        volume = amount = None
    else:
        # A股
        if len(fields) < 10:
            return None
        try:
            price = float(fields[3]) if fields[3] else None
            pre_close = float(fields[2]) if fields[2] else None
            open_price = float(fields[1]) if fields[1] else None
            high = float(fields[4]) if fields[4] else None
            low = float(fields[5]) if fields[5] else None
            volume = int(float(fields[8])) if fields[8] else None
            amount = float(fields[9]) if fields[9] else None
        except (ValueError, IndexError):
            return None
        name = fields[0]
        change_amount = None
        change_pct = None
        if price is not None and pre_close:
            change_amount = price - pre_close
            change_pct = change_amount / pre_close * 100.0

    return UnifiedRealtimeQuote(
        code=normalize_stock_code(stock_code),
        name=name,
        source=RealtimeSource.SINA,
        price=price,
        open_price=open_price,
        high=high,
        low=low,
        pre_close=pre_close,
        change_amount=change_amount,
        change_pct=change_pct,
        volume=volume,
        amount=amount,
        fetched_at=datetime.now().isoformat(),
    )
