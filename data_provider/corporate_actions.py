# -*- coding: utf-8 -*-
"""CorporateEventCalendar — 企业事件日历（P2 / T13）.

企业分红 / 拆股 / 配股 / 退市 / 更名事件日历：内存管理 + 可选 JSON 本地缓存。
回测时加载事件，实盘用于调整持仓数据；`apply_dividend_adjustment` 提供分红事件
的前复权调整。

- `CorporateEvent`: code / event_date / event_type / details，
  event_date 兼容 date / datetime / str（内部统一归一化为 date）
- `CorporateEventCalendar`:
  - `add_event`: 按 code 分组，同 code+date+type 去重
  - `get_events`: 按日期升序返回
  - `save()` / `load()`: JSON 缓存读写；load 容错（文件缺失 / 损坏 → 空并记日志）
  - `apply_dividend_adjustment`: 分红事件前复权调整，返回新 DataFrame，不改原 df

实现依据: docs/architecture/realtime_quant_system_design.md §4.3
规格: .claude/specs/quant-p2/dev-plan.md T13
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DIVIDEND = "dividend"


def _normalize_date(value: Any) -> date:
    """将 date / datetime / str / pd.Timestamp 统一归一化为 date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return pd.Timestamp(value).date()
    raise TypeError(f"Unsupported event_date type: {type(value)!r}")


def _event_to_dict(event: "CorporateEvent") -> Dict[str, Any]:
    """CorporateEvent → JSON 可序列化 dict（event_date 用 ISO 字符串）."""
    return {
        "code": event.code,
        "event_date": event.event_date.isoformat(),
        "event_type": event.event_type,
        "details": dict(event.details),
    }


@dataclass
class CorporateEvent:
    """企业事件。event_date 接受 date/datetime/str，内部归一化为 date."""

    code: str
    event_date: date
    event_type: str  # dividend / split / rights_issue / delist / name_change
    details: Dict[str, Any] = field(default_factory=dict)


class CorporateEventCalendar:
    """企业事件日历 — 内存 + 可选 JSON 缓存."""

    def __init__(self, cache_path: Optional[str] = None):
        self._cache_path = cache_path
        self._events: Dict[str, List[CorporateEvent]] = {}
        if cache_path is not None:
            self.load()

    # ---- 事件管理 ----

    def add_event(self, event: CorporateEvent) -> None:
        """添加事件；同 code+date+type 去重."""
        normalized = CorporateEvent(
            code=event.code,
            event_date=_normalize_date(event.event_date),
            event_type=event.event_type,
            details=dict(event.details),
        )
        events = self._events.setdefault(normalized.code, [])
        for existing in events:
            if (
                existing.event_date == normalized.event_date
                and existing.event_type == normalized.event_type
            ):
                return  # 已存在同 code+date+type，去重
        events.append(normalized)

    def get_events(self, code: str) -> List[CorporateEvent]:
        """按日期升序返回该 code 的事件（返回副本，不影响内部存储）."""
        return sorted(self._events.get(code, []), key=lambda e: e.event_date)

    # ---- JSON 缓存 ----

    def save(self) -> None:
        """将事件写入 JSON 缓存（code → events list）；无缓存路径时 no-op."""
        if self._cache_path is None:
            return
        payload = {
            code: [_event_to_dict(e) for e in events]
            for code, events in self._events.items()
        }
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def load(self) -> None:
        """从 JSON 缓存读取事件；文件缺失 / JSON 损坏 → 空并记日志."""
        if self._cache_path is None:
            return
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            events: Dict[str, List[CorporateEvent]] = {}
            for code, items in raw.items():
                parsed = []
                for item in items:
                    parsed.append(
                        CorporateEvent(
                            code=item["code"],
                            event_date=_normalize_date(item["event_date"]),
                            event_type=item["event_type"],
                            details=dict(item.get("details") or {}),
                        )
                    )
                events[code] = parsed
            self._events = events
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning(
                "failed to load corporate events cache %s: %s", self._cache_path, exc
            )
            self._events = {}

    # ---- 前复权调整 ----

    def apply_dividend_adjustment(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """对日线做分红前复权调整，返回新 DataFrame，不修改原 df.

        遍历该 code 的分红事件（event_type == "dividend" 且 details 含
        dividend_per_share）：事件日之前的价格 × (close_before - dividend) /
        close_before。close_before 取事件日当日 close；若事件日不在 df 内则取
        事件日后第一个 bar 的 close。无事件 → 原样 copy 返回。
        """
        result = df.copy()
        if df.empty or "close" not in df.columns:
            return result
        events = [
            e
            for e in self._events.get(code, [])
            if e.event_type == _DIVIDEND and "dividend_per_share" in e.details
        ]
        if not events:
            return result
        price_cols = [c for c in ("open", "high", "low", "close") if c in result.columns]
        for event in events:
            dividend = event.details["dividend_per_share"]
            if pd.isna(dividend):
                continue
            event_ts = pd.Timestamp(event.event_date)
            close_before = self._find_close_before(df, event_ts)
            if close_before is None or pd.isna(close_before) or close_before == 0:
                continue
            factor = (close_before - dividend) / close_before
            mask = df.index < event_ts
            for col in price_cols:
                result.loc[mask, col] = result.loc[mask, col] * factor
        return result

    @staticmethod
    def _find_close_before(df: pd.DataFrame, event_ts: pd.Timestamp) -> Optional[float]:
        """事件日当日 close；事件日不在 df 内则取事件日后第一个 bar 的 close."""
        if event_ts in df.index:
            return df.loc[event_ts, "close"]
        after = df.index[df.index > event_ts]
        if len(after) == 0:
            return None
        return df.loc[after[0], "close"]

    # ---- T-018: 外部数据拉取 + 统一复权入口 ----

    def update(self, codes: List[str]) -> int:
        """Fetch dividend records from akshare for all codes and ingest events.

        Returns the total number of events added.
        """
        try:
            import akshare as ak
        except ImportError:
            logger.warning("akshare not installed; cannot update corporate events")
            return 0

        added = 0
        for code in codes:
            try:
                clean = str(code).strip().upper()
                # Remove market suffix for akshare CN query if present.
                symbol = clean.replace("SH", "").replace("SZ", "").replace("BJ", "")
                df = ak.stock_dividents_cninfo(symbol=symbol)
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    event_date_raw = row.get("除权除息日") or row.get("公告日期")
                    dps = float(row.get("每股派息", 0) or 0)
                    if not event_date_raw or dps <= 0:
                        continue
                    event_date = _normalize_date(event_date_raw)
                    self.add_event(CorporateEvent(
                        code=clean,
                        event_date=event_date,
                        event_type=_DIVIDEND,
                        details={"dividend_per_share": dps},
                    ))
                    added += 1
            except Exception as exc:
                logger.debug("Corporate action fetch failed for %s: %s", code, exc)
        if added:
            self.save()
        return added

    def apply_to_prices(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """Unified entry point: apply all corporate actions to price data.

        Currently supports dividend (前复权) and split adjustments.
        Returns a new DataFrame; does not modify the original.
        """
        result = df.copy()
        # Dividend adjustment.
        result = self.apply_dividend_adjustment(code, result)
        # Split adjustment (future).
        return result

    def apply_split_adjustment(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """Apply split/stock-split forward adjustment.

        Each split event with details['split_ratio'] > 0 adjusts historical
        prices before the event date.
        """
        result = df.copy()
        if df.empty or "close" not in df.columns:
            return result
        events = [
            e for e in self._events.get(code, [])
            if e.event_type == "split" and e.details.get("split_ratio", 0) > 0
        ]
        if not events:
            return result
        price_cols = [c for c in ("open", "high", "low", "close") if c in result.columns]
        for event in events:
            ratio = float(event.details["split_ratio"])
            event_ts = pd.Timestamp(event.event_date)
            mask = result.index < event_ts
            for col in price_cols:
                result.loc[mask, col] = result.loc[mask, col] * ratio
        return result
