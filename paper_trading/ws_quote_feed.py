# -*- coding: utf-8 -*-
"""WebSocket 行情推送 feed（方案 3）.

用 ``WebSocketChannel`` 订阅外部行情 WebSocket，行情消息到达时更新
``SharedQuoteCache``。MarketListener 的 ``_fetch_latest_prices`` 已优先读
缓存，因此只要 feed 在跑，主循环就不走轮询（push 优先）。

当前外部行情 WS 源：
- Longbridge（港股/美股）：``LONGBRIDGE_QUOTE_WS_URL``，需 App Key/Secret
- A 股（tickflow/akshare）暂不提供 WS push → 该市场保持轮询兜底

feed 是独立可插拔模块：没有配置 WS 源时不启动，MarketListener 维持轮询。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Iterable, Optional

from paper_trading.quote_cache import CachedQuote, SharedQuoteCache
from paper_trading.ws_channel import WebSocketChannel

logger = logging.getLogger(__name__)


class WsQuoteFeed:
    """WebSocket 行情推送 feed：订阅 codes，行情到达更新 quote_cache."""

    def __init__(
        self,
        quote_cache: SharedQuoteCache,
        watched_codes: Iterable[str],
        url: str,
        auth_token: Optional[str] = None,
        parse_message: Optional[callable] = None,
    ) -> None:
        self._cache = quote_cache
        self._url = url
        self._channel = WebSocketChannel(
            watched_codes=watched_codes,
            on_message=self._on_message,
        )
        self._auth_token = auth_token
        self._parse_message = parse_message or _default_parse_message
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """后台线程运行 WS 重连/消费循环（阻塞，适合独立线程）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._channel.run_forever,
            args=(self._url, self._auth_token),
            name="ws-quote-feed",
            daemon=True,
        )
        self._thread.start()
        logger.info("WsQuoteFeed started url=%s", self._url)

    def stop(self) -> None:
        if self._channel.connected or self._thread is not None:
            self._channel.stop()
            logger.info("WsQuoteFeed stopped")

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------

    def _on_message(self, message: Any) -> None:
        try:
            quote = self._parse_message(message)
        except Exception as exc:
            logger.debug("WsQuoteFeed parse message failed: %s", exc)
            return
        if quote is None:
            return
        self._cache.update(quote["code"], quote["cached"])


def _default_parse_message(message: Any) -> Optional[Dict[str, Any]]:
    """默认解析：接受 dict（含 code/price/volume/change_pct 等）或 JSON 字符串。

    兼容 Longbridge push 消息的常见字段（symbol/code, last_done/price 等）。
    """
    if isinstance(message, str):
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return None
    elif isinstance(message, dict):
        data = message
    else:
        return None

    code = (
        data.get("code")
        or data.get("symbol")
        or data.get("stock_code")
        or data.get("secu_code")
    )
    if not code:
        return None

    price = data.get("price") or data.get("last_done") or data.get("close")
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    from datetime import datetime

    cached = CachedQuote(
        price=price,
        volume=float(data.get("volume") or 0),
        change_pct=float(data.get("change_pct") or data.get("change") or 0),
        high=float(data.get("high") or 0),
        low=float(data.get("low") or 0),
        open=float(data.get("open") or 0),
        pre_close=float(data.get("pre_close") or data.get("prev_close") or 0),
        timestamp=datetime.now(),
        source=data.get("source") or "ws_quote",
    )
    return {"code": str(code), "cached": cached}
