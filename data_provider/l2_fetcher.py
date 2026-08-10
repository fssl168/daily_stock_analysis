# -*- coding: utf-8 -*-
"""Level-2 depth-of-market fetcher (P3 — competitive differentiation).

Provides ten-level bid/ask order-book snapshots and derived order-flow
signals (large-order detection, iceberg and spoofing heuristics).

Currently backed by tickflow WebSocket.  Falls back gracefully: when
no L2 provider is configured, all methods return ``None`` and the rest
of the system operates on L1 data only.

Source: ``docs/architecture/realtime_quant_system_design.md`` §4.1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from data_provider.base import BaseFetcher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Level2Quote:
    """Ten-level bid/ask order-book snapshot.

    Arrays are indexed from level 1 (best) to level 10 (deepest).
    If fewer than 10 levels are available the missing entries are 0.0 / 0.
    """

    code: str
    timestamp: datetime = field(default_factory=datetime.now)
    # Bids (buy orders): bid_prices[0] is the best bid.
    bid_prices: List[float] = field(default_factory=lambda: [0.0] * 10)
    bid_volumes: List[int] = field(default_factory=lambda: [0] * 10)
    # Asks (sell orders): ask_prices[0] is the best ask.
    ask_prices: List[float] = field(default_factory=lambda: [0.0] * 10)
    ask_volumes: List[int] = field(default_factory=lambda: [0] * 10)
    # Derived.
    bid_ask_imbalance: float = 0.0
    weighted_bid: float = 0.0
    weighted_ask: float = 0.0
    depth_weighted_spread: float = 0.0


@dataclass
class OrderFlowSignal:
    """Intraday order-flow intelligence derived from tick-by-tick data."""

    code: str
    timestamp: datetime = field(default_factory=datetime.now)
    large_buy_orders: int = 0
    large_sell_orders: int = 0
    net_flow: float = 0.0               # positive = net buying pressure
    iceberg_detected: bool = False
    spoofing_detected: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


class L2Fetcher(BaseFetcher):
    """Level-2 depth-of-market data source adapter.

    Inherits from ``BaseFetcher`` (``data_provider/base.py``) for
    consistency with the existing data-provider architecture.  When no
    L2 provider is configured, all methods return ``None`` transparently.
    """

    name: str = "l2_tickflow"
    priority: int = 50  # lower than L1 fetchers — optional enhancement

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(self, provider: str = "tickflow") -> None:
        super().__init__()
        self._provider = provider
        self._available = False
        self._last_quote: Dict[str, Level2Quote] = {}  # code → latest L2

    def is_available(self) -> bool:
        """L2 is only available when a provider is configured."""
        return self._available

    def set_available(self, available: bool = True) -> None:
        """Mark the L2 channel as connected (called by WS integration layer)."""
        self._available = available

    # ------------------------------------------------------------------
    # Fetch methods
    # ------------------------------------------------------------------

    def get_level2_quote(self, stock_code: str) -> Optional[Level2Quote]:
        """Return the most recent L2 snapshot for *stock_code*, or None."""
        code = str(stock_code).strip().upper()
        return self._last_quote.get(code)

    def get_level2_quotes_batch(self, stock_codes: List[str]) -> Dict[str, Level2Quote]:
        """Return the latest L2 snapshots for all requested codes."""
        codes = {str(c).strip().upper() for c in stock_codes}
        return {c: q for c, q in self._last_quote.items() if c in codes}

    # ------------------------------------------------------------------
    # Order-flow analysis
    # ------------------------------------------------------------------

    def get_order_flow(self, stock_code: str) -> Optional[OrderFlowSignal]:
        """Compute order-flow intelligence from tick-level data.

        Currently returns None — requires tick-level stream from WS
        provider.  Implemented as a forward-compatible placeholder.
        """
        return None

    # ------------------------------------------------------------------
    # Ingestion (called by WebSocket on_message handler)
    # ------------------------------------------------------------------

    def ingest_l2_quote(self, raw: Dict[str, Any]) -> Optional[Level2Quote]:
        """Parse a raw L2 push message and update the internal cache.

        Expected fields:
        - ``code`` (str)
        - ``timestamp`` (ISO str or epoch ms)
        - ``bids``: list of ``[price, volume]`` pairs (best first)
        - ``asks``: list of ``[price, volume]`` pairs (best first)

        Returns the parsed ``Level2Quote`` or None on parse failure.
        """
        try:
            code = str(raw.get("code", "")).strip().upper()
            if not code:
                return None

            # Timestamp.
            ts_raw = raw.get("timestamp")
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw / 1000 if ts_raw > 1e12 else ts_raw)
            elif isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw)
            else:
                ts = datetime.now()

            # Bids and asks — each is [[price, volume], ...]
            bids_raw = raw.get("bids", []) or []
            asks_raw = raw.get("asks", []) or []

            bid_prices: List[float] = []
            bid_volumes: List[int] = []
            for bp, bv in bids_raw[:10]:
                bid_prices.append(float(bp))
                bid_volumes.append(int(bv))

            ask_prices: List[float] = []
            ask_volumes: List[int] = []
            for ap, av in asks_raw[:10]:
                ask_prices.append(float(ap))
                ask_volumes.append(int(av))

            # Pad to 10 levels.
            while len(bid_prices) < 10:
                bid_prices.append(0.0)
                bid_volumes.append(0)
            while len(ask_prices) < 10:
                ask_prices.append(0.0)
                ask_volumes.append(0)

            # Derived metrics.
            total_bid_vol = sum(bid_volumes)
            total_ask_vol = sum(ask_volumes)
            total = total_bid_vol + total_ask_vol
            imbalance = (total_bid_vol - total_ask_vol) / total if total > 0 else 0.0

            vol_bid = sum(bid_prices[i] * bid_volumes[i] for i in range(10))
            vol_ask = sum(ask_prices[i] * ask_volumes[i] for i in range(10))
            weighted_bid = vol_bid / total_bid_vol if total_bid_vol > 0 else 0.0
            weighted_ask = vol_ask / total_ask_vol if total_ask_vol > 0 else 0.0
            spread = weighted_ask - weighted_bid

            quote = Level2Quote(
                code=code,
                timestamp=ts,
                bid_prices=bid_prices,
                bid_volumes=bid_volumes,
                ask_prices=ask_prices,
                ask_volumes=ask_volumes,
                bid_ask_imbalance=round(imbalance, 4),
                weighted_bid=round(weighted_bid, 4),
                weighted_ask=round(weighted_ask, 4),
                depth_weighted_spread=round(spread, 4),
            )
            self._last_quote[code] = quote
            return quote
        except Exception as exc:
            logger.debug("L2 quote ingestion failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # BaseFetcher stubs (L2 does not provide daily/minute data)
    # ------------------------------------------------------------------

    def get_realtime_quote(self, stock_code: str) -> None:
        """L2Fetcher does not serve L1 realtime quotes."""
        return None

    def get_daily_data(self, stock_code: str, **kwargs: Any) -> None:
        """L2Fetcher does not serve daily bars."""
        return None

    def get_minute_data(self, stock_code: str, **kwargs: Any) -> None:
        """L2Fetcher does not serve minute bars."""
        return None
