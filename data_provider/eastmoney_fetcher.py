# -*- coding: utf-8 -*-
"""Eastmoney direct realtime quote fetcher (东方财富, no API key required).

Realtime: https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}
Fields: f43 price, f44 high, f45 low, f46 open, f47 volume(lots), f48 amount,
        f57 code, f58 name, f60 pre_close, f169 change_amount, f170 change_pct.
Price fields are scaled x100 (分); f170 is pct x100.

The push2his kline endpoint is not reachable from this network, so daily
data returns an empty frame (allow_empty_daily_data=True) and callers fall
back to other fetchers — this source's value is fast realtime quotes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover
    cffi_requests = None
    _HAS_CURL_CFFI = False

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    is_bse_code,
    normalize_stock_code,
)
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 8

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170"


class EastmoneyFetcher(BaseFetcher):
    """Eastmoney realtime quote fetcher (realtime only)."""

    name = "EastmoneyFetcher"
    priority = 0
    allow_empty_daily_data = True

    _QUOTE_ENDPOINT = "https://push2.eastmoney.com/api/qt/stock/get"

    # ------------------------------------------------------------------
    # Daily K-line: unsupported (network-blocked endpoint) -> empty frame
    # ------------------------------------------------------------------

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        # push2his kline endpoint is unreachable on this network; signal the
        # manager to fall through to the next fetcher.
        raise DataFetchError("EastmoneyFetcher kline endpoint unavailable")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df

    # ------------------------------------------------------------------
    # Realtime quote
    # ------------------------------------------------------------------

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        secid = _to_em_secid(stock_code)
        if not secid:
            return None
        try:
            params = {"secid": secid, "fields": _FIELDS}
            # 东财对 urllib3 的 TLS 指纹拒连 (RemoteDisconnected)，curl_cffi
            # 模拟浏览器指纹可正常访问；不可用时回退 requests。
            if _HAS_CURL_CFFI:
                resp = cffi_requests.get(
                    self._QUOTE_ENDPOINT, params=params, headers=_HEADERS,
                    timeout=_HTTP_TIMEOUT_SECONDS, impersonate="chrome",
                )
            else:
                resp = requests.get(
                    self._QUOTE_ENDPOINT, params=params, headers=_HEADERS,
                    timeout=_HTTP_TIMEOUT_SECONDS,
                )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.debug("EastmoneyFetcher realtime failed for %s: %s", stock_code, exc)
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        # 港美股价格字段精度为厘(×1000)，A股为分(×100)
        divisor = 1000.0 if secid.startswith(("116.", "105.")) else 100.0
        return _build_quote(data, stock_code, divisor=divisor)


def _to_em_secid(stock_code: str) -> str:
    code = normalize_stock_code(stock_code)
    if not code:
        return ""
    # 港股: HK00700 -> 116.00700
    if code[:2].lower() == "hk" and len(code) == 7 and code[2:].isdigit():
        return f"116.{code[2:]}"
    # 美股: AAPL -> 105.AAPL
    if code.isalpha() and 1 <= len(code) <= 5:
        return f"105.{code.upper()}"
    if not code.isdigit() or len(code) != 6:
        return ""
    if is_bse_code(code):
        # 北交所（43/83/87/920 号段）：东财 push2 与深市共用 0 市场段。
        return f"0.{code}"
    if code.startswith(("6", "5", "9")):
        return f"1.{code}"  # 上交所（含 900xxx 沪B）
    return f"0.{code}"      # 深交所


def _scale(value: Any, divisor: float = 100.0) -> Optional[float]:
    """东财价格字段为整数精度 (A股分 ×100 / 港美股厘 ×1000), 无效值返回 None.

    前提：push2 的 f43/f44/f45/f46/f60 等价格字段以整数精度返回
    （A股以「分」、港美股以「厘」为单位）。若某接口改为返回带小数的价格，
    需要先识别再决定是否缩放。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v is None or v <= 0 or v > 1e9:
        return None
    return v / divisor


def _build_quote(data: dict[str, Any], stock_code: str, divisor: float = 100.0) -> Optional[UnifiedRealtimeQuote]:
    price = _scale(data.get("f43"), divisor)
    pre_close = _scale(data.get("f60"), divisor)
    open_price = _scale(data.get("f46"), divisor)
    high = _scale(data.get("f44"), divisor)
    low = _scale(data.get("f45"), divisor)
    if price is None:
        return None

    change_amount = None
    change_pct = None
    if pre_close:
        change_amount = price - pre_close
        change_pct = change_amount / pre_close * 100.0

    try:
        volume = int(float(data.get("f47") or 0)) * 100  # 手 -> 股
    except (TypeError, ValueError):
        volume = None
    try:
        amount = float(data.get("f48") or 0) or None
    except (TypeError, ValueError):
        amount = None

    return UnifiedRealtimeQuote(
        code=normalize_stock_code(stock_code),
        name=str(data.get("f58") or ""),
        source=RealtimeSource.EASTMONEY,
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
