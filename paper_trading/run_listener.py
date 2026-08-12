# -*- coding: utf-8 -*-
"""Standalone MarketListener launcher for paper trading (account 2).

Runs in foreground — use as a background process or via start_paper_trading_listener.bat.
"""
import os
import sys
import logging

sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("listener_launcher")

from src.config import get_config, setup_env  # noqa: E402
from paper_trading.market_listener import build_default_listener  # noqa: E402


def main():
    setup_env()
    cfg = get_config()
    account_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2

    # 共享行情缓存：WebSocket push feed 与 MarketListener 共用，push 优先、轮询兜底。
    from paper_trading.quote_cache import SharedQuoteCache

    quote_cache = SharedQuoteCache()
    listener = build_default_listener(
        cfg, account_id=account_id, quote_cache=quote_cache,
    )
    log.info("Starting MarketListener account_id=%s", account_id)
    log.info("  watched=%d codes, strategies=%d", len(listener.config.watched_codes), len(listener.strategies))

    # 方案 3：可选 WebSocket 行情 push feed。配置 PAPER_TRADING_WS_QUOTE_URL
    # （如 Longbridge `wss://openapi-quote.longbridge.cn/v2`）后启动；未配置则维持轮询。
    ws_url = os.getenv("PAPER_TRADING_WS_QUOTE_URL", "").strip()
    feed = None
    if ws_url:
        from paper_trading.ws_quote_feed import WsQuoteFeed

        feed = WsQuoteFeed(
            quote_cache,
            watched_codes=list(listener.config.watched_codes),
            url=ws_url,
        )
        feed.start()
        log.info("WebSocket quote feed started: %s", ws_url)
    else:
        log.info("未配置 PAPER_TRADING_WS_QUOTE_URL，行情走轮询")

    listener.start()
    log.info("Listener started (tick=%ss). Ctrl+C to stop.", listener.config.tick_interval_seconds)
    try:
        import time
        while True:
            time.sleep(60)
            if not listener.is_running():
                log.error("Listener thread died! Restarting...")
                listener.start()
    except KeyboardInterrupt:
        log.info("Stopping listener...")
        listener.stop()
        if feed is not None:
            feed.stop()


if __name__ == "__main__":
    main()
