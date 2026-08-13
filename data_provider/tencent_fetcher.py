# -*- coding: utf-8 -*-
"""Tencent direct daily K-line fetcher for A-share fallback routing."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests

try:
    import exchange_calendars as xcals
except ImportError:  # pragma: no cover - dependency is present in supported installs
    xcals = None

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS, normalize_stock_code, is_bse_code
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote

logger = logging.getLogger(__name__)

_MAX_KLINE_BARS = 800


class TencentFetcher(BaseFetcher):
    """Fetch qfq daily K-line data from Tencent's direct quote endpoint."""

    name = "TencentFetcher"
    priority = 0
    allow_empty_daily_data = True

    _KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _HTTP_TIMEOUT_SECONDS = 8

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        code = normalize_stock_code(stock_code)
        symbol = _to_tencent_symbol(code)
        if not symbol:
            raise DataFetchError(f"TencentFetcher unsupported stock code: {stock_code}")

        lookback = _estimate_lookback_days(start_date=start_date, end_date=end_date)
        explicit_start = _format_tencent_date(start_date)
        explicit_end = _format_tencent_date(end_date)
        explicit_window = (
            f"{explicit_start},{explicit_end}"
            if explicit_start and explicit_end
            else ","
        )
        response = requests.get(
            self._KLINE_ENDPOINT,
            params={"param": f"{symbol},day,{explicit_window},{lookback},qfq"},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"},
            timeout=self._HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        rows = _extract_kline_rows(payload, symbol=symbol)
        if not rows:
            logger.info("TencentFetcher empty daily history for %s", stock_code)
            return _empty_daily_frame()

        df = pd.DataFrame(rows)
        first_returned_date = _first_returned_date(df)
        if first_returned_date and _is_capped_history_incomplete(
            first_returned_date=first_returned_date,
            start_date=start_date,
            lookback=lookback,
            returned_rows=len(rows),
        ):
            logger.info(
                "TencentFetcher incomplete capped daily history for %s: first_date=%s requested_start=%s",
                stock_code,
                first_returned_date,
                start_date,
            )
            return _empty_daily_frame()

        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        if df.empty:
            logger.info(
                "TencentFetcher daily history outside requested range for %s: %s~%s",
                stock_code,
                start_date,
                end_date,
            )
            return _empty_daily_frame()
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if "pct_chg" not in normalized.columns:
            normalized["pct_chg"] = normalized["close"].pct_change().fillna(0.0) * 100
        normalized = normalized[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]]
        return normalized

    # ------------------------------------------------------------------
    # Realtime quote (A-share / HK / US)
    # ------------------------------------------------------------------

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        symbol = _to_tencent_symbol(stock_code)
        if not symbol:
            return None
        try:
            resp = requests.get(
                f"https://qt.gtimg.cn/q={symbol}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                timeout=self._HTTP_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.debug("TencentFetcher realtime failed for %s: %s", stock_code, exc)
            return None
        return _parse_tencent_realtime(resp.content, stock_code, symbol)


def _to_tencent_symbol(stock_code: str) -> str:
    code = normalize_stock_code(stock_code)
    if not code:
        return ""
    # 港股: hk00700 (hk + 5 位数字, normalize 后可能为大写 HK00700)
    if code[:2].lower() == "hk" and len(code) == 7 and code[2:].isdigit():
        return f"hk{code[2:]}"
    # 美股: AAPL / NVDA / TSLA (字母代码)
    if code.isalpha() and 1 <= len(code) <= 5:
        return f"us{code.upper()}"
    if not code.isdigit() or len(code) != 6:
        return ""
    if is_bse_code(code):
        return f"bj{code}"
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _estimate_lookback_days(*, start_date: str, end_date: str) -> int:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        calendar_days = max(1, (end - start).days + 1)
    except ValueError:
        calendar_days = 90
    # Trading days are sparse over calendar days; add margin for holidays/suspensions.
    return max(30, min(_MAX_KLINE_BARS, int(calendar_days * 1.8) + 20))


def _empty_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_COLUMNS)


def _first_returned_date(df: pd.DataFrame) -> Optional[str]:
    if "date" not in df.columns or df.empty:
        return None
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.min().strftime("%Y-%m-%d")


def _is_capped_history_incomplete(
    *,
    first_returned_date: str,
    start_date: str,
    lookback: int,
    returned_rows: int,
) -> bool:
    hit_cap = lookback >= _MAX_KLINE_BARS and returned_rows >= _MAX_KLINE_BARS
    if not hit_cap:
        return False
    try:
        first = datetime.strptime(first_returned_date, "%Y-%m-%d")
        requested_start = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return False
    return first > _first_trading_date_on_or_after(requested_start)


def _first_trading_date_on_or_after(start_date: datetime) -> datetime:
    if xcals is not None:
        try:
            cal = xcals.get_calendar("XSHG")
            session = cal.date_to_session(start_date.date(), direction="next")
            return datetime.combine(session.date(), datetime.min.time())
        except Exception:
            pass

    current = start_date
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


def _format_tencent_date(date_text: str) -> Optional[str]:
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_tencent_realtime(content: bytes, stock_code: str, symbol: str) -> Optional[UnifiedRealtimeQuote]:
    """Parse Tencent realtime payload.

    A-share (sh/sz/bj): comma-separated
      name, open, pre_close, price, high, low, ..., volume(股), amount(元), ...
    HK/US (~-separated, e.g. ``v_hk00700="100~TENCENT~00700~price~pre_close~open~vol...``):
      [1]=name [2]=code [3]=price [4]=pre_close [5]=open [6]=volume [..]
      trailing fields carry change_amount / change_pct.
    """
    try:
        text = content.decode("gbk", errors="replace")
    except Exception:
        text = content.decode("utf-8", errors="replace")
    m = re.search(r'="([^"]*)"', text)
    if not m or "none_match" in text:
        return None
    raw = m.group(1)

    if symbol.startswith(("sh", "sz", "bj")):
        # A-share comma format
        f = raw.split(",")
        if len(f) < 10:
            return None
        try:
            price = float(f[3]) if f[3] else None
            pre_close = float(f[2]) if f[2] else None
            open_price = float(f[1]) if f[1] else None
            high = float(f[4]) if f[4] else None
            low = float(f[5]) if f[5] else None
            volume = int(float(f[8])) if f[8] else None
            amount = float(f[9]) if f[9] else None
        except (ValueError, IndexError):
            return None
        name = f[0]
    else:
        # HK/US tilde format
        f = raw.split("~")
        if len(f) < 7:
            return None
        try:
            price = float(f[3]) if f[3] else None
            pre_close = float(f[4]) if f[4] else None
            open_price = float(f[5]) if f[5] else None
            volume = int(float(f[6])) if f[6] else None
        except (ValueError, IndexError):
            return None
        high = low = None
        amount = None
        name = f[1]
        # HK/US 涨跌幅从价格差计算（尾部字段结构不稳定）:
        # change_pct 由下方 price/pre_close 计算得出。

    change_amount = None
    change_pct = None
    if price is not None and pre_close:
        change_amount = price - pre_close
        change_pct = change_amount / pre_close * 100.0

    return UnifiedRealtimeQuote(
        code=normalize_stock_code(stock_code),
        name=name,
        source=RealtimeSource.TENCENT,
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


def _lots_to_shares(volume: Any) -> Any:
    try:
        return float(volume) * 100
    except (TypeError, ValueError):
        return volume


def _extract_kline_rows(payload: dict[str, Any], *, symbol: str) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    item = data.get(symbol) if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return []
    rows = item.get("qfqday") or item.get("day") or []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        amount: Optional[Any] = row[6] if len(row) > 6 else None
        result.append(
            {
                "date": str(row[0]),
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": _lots_to_shares(row[5]),
                "amount": amount,
            }
        )
    return result
