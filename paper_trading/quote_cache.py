# -*- coding: utf-8 -*-
"""SharedQuoteCache — 双通道行情缓存（P2 / T12）.

PollChannel 与 WebSocketChannel 共享的最新价缓存：所有读写通过 RLock 保证线程安全，
读取时按 received_at 判定过期，get / get_all / __len__ 只暴露未过期条目。

实现依据: docs/architecture/realtime_quant_system_design.md §2.1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Dict, Optional


@dataclass
class CachedQuote:
    """缓存的单条行情快照."""

    price: float
    volume: float
    change_pct: float
    high: float
    low: float
    open: float
    pre_close: float
    timestamp: datetime  # 行情时间戳（不是本地接收时间！）
    source: str  # "poll_efinance" / "ws_tickflow" / "ws_longbridge"
    received_at: datetime = field(default_factory=datetime.now)


class SharedQuoteCache:
    """线程安全的最新价缓存 — PollChannel 和 WebSocketChannel 共享."""

    def __init__(self, max_age_seconds: float = 5.0):
        self._quotes: Dict[str, CachedQuote] = {}
        self._lock = RLock()
        self._max_age = max_age_seconds

    def _is_fresh(self, quote: CachedQuote, now: Optional[datetime] = None) -> bool:
        """received_at 距今不超过 max_age 视为新鲜."""
        now = now or datetime.now()
        return (now - quote.received_at).total_seconds() <= self._max_age

    def update(self, code: str, quote: CachedQuote) -> None:
        with self._lock:
            self._quotes[code] = quote

    def get(self, code: str) -> Optional[CachedQuote]:
        with self._lock:
            q = self._quotes.get(code)
            if q is None:
                return None
            if not self._is_fresh(q):
                return None  # 过期
            return q

    def get_all(self) -> Dict[str, CachedQuote]:
        """返回所有未过期条目（MarketListener._fetch_latest_prices 替代品）."""
        with self._lock:
            now = datetime.now()
            return {
                code: q
                for code, q in self._quotes.items()
                if self._is_fresh(q, now)
            }

    def remove(self, code: str) -> None:
        """移除单条（不存在时静默忽略）."""
        with self._lock:
            self._quotes.pop(code, None)

    def clear(self) -> None:
        with self._lock:
            self._quotes.clear()

    def is_fresh(self, code: str) -> bool:
        """code 存在且未过期返回 True，否则 False."""
        with self._lock:
            q = self._quotes.get(code)
            return q is not None and self._is_fresh(q)

    def __len__(self) -> int:
        """当前有效（未过期）条目数."""
        with self._lock:
            return sum(1 for q in self._quotes.values() if self._is_fresh(q))
