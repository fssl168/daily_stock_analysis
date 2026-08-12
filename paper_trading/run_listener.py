# -*- coding: utf-8 -*-
"""Standalone MarketListener launcher for paper trading (account 2).

Runs in foreground — use as a background process or via start_paper_trading_listener.bat.
"""
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
    listener = build_default_listener(cfg, account_id=account_id)
    log.info("Starting MarketListener account_id=%s", account_id)
    log.info("  watched=%d codes, strategies=%d", len(listener.config.watched_codes), len(listener.strategies))
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


if __name__ == "__main__":
    main()
