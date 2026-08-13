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
from paper_trading.market_listener import build_full_listener  # noqa: E402


def main():
    setup_env()
    cfg = get_config()
    account_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2

    # T-08: 完整生产装配（PM/复盘/作战卡/漂移/延迟/特征管线按 .env flag 注入，
    # 与 API start_listener 装配对齐，消除"配置启用但未接线"的漂移）。
    listener = build_full_listener(cfg, account_id=account_id)
    log.info("Starting MarketListener account_id=%s", account_id)
    log.info("  watched=%d codes, strategies=%d", len(listener.config.watched_codes), len(listener.strategies))
    log.info(
        "  wired: pm_agent=%s reflection=%s battle_plan=%s drift=%s latency=%s feature=%s",
        listener.pm_agent is not None,
        listener.reflection_engine is not None,
        listener.battle_plan_generator is not None,
        listener._drift_detector is not None,
        listener._latency_tracker is not None,
        listener._feature_pipeline is not None,
    )

    # 方案 3：可选 WebSocket 行情 push feed。共享 quote_cache 从 listener 取，
    # 与 MarketListener 共用，push 优先、轮询兜底。配置 PAPER_TRADING_WS_QUOTE_URL
    # （如 Longbridge `wss://openapi-quote.longbridge.cn/v2`）后启动；未配置则维持轮询。
    ws_url = os.getenv("PAPER_TRADING_WS_QUOTE_URL", "").strip()
    feed = None
    if ws_url:
        quote_cache = getattr(listener, "_quote_cache", None)
        if quote_cache is not None:
            from paper_trading.ws_quote_feed import WsQuoteFeed

            feed = WsQuoteFeed(
                quote_cache,
                watched_codes=list(listener.config.watched_codes),
                url=ws_url,
            )
            feed.start()
            log.info("WebSocket quote feed started: %s", ws_url)
        else:
            log.warning(
                "PAPER_TRADING_WS_QUOTE_URL configured but quote_cache unavailable; polling"
            )
    else:
        log.info("未配置 PAPER_TRADING_WS_QUOTE_URL，行情走轮询")

    # T-12: 独立 AI 信号 Worker（可选）。PAPER_TRADING_ENABLE_AI_SIGNAL_WORKER=true
    # 时周期触发 PM 分析，产出信号写入队列，由 listener._consume_ai_signals 消费。
    ai_worker = None
    if getattr(cfg, "paper_trading_enable_ai_signal_worker", False):
        try:
            from paper_trading.ai_signal_worker import AISignalWorker
            from src.paper_trading_signal_queue import AIAnalysisSignal, init_signal_queue

            q = init_signal_queue(maxsize=1000)
            pm_agent = getattr(listener, "pm_agent", None)
            interval = float(
                getattr(cfg, "paper_trading_ai_signal_worker_interval_seconds", 3600.0)
                or 3600.0
            )
            min_conf = float(
                getattr(cfg, "paper_trading_ai_signal_min_confidence", 0.7) or 0.7
            )

            def _ai_analysis() -> list:
                if pm_agent is None:
                    return []
                try:
                    decision = pm_agent.make_decision(
                        account_id=account_id,
                        extra_context={"trigger": "ai_signal_worker"},
                    )
                except Exception as exc:
                    log.warning("AISignalWorker make_decision failed: %s", exc)
                    return []
                if decision is None or getattr(decision, "used_fallback", True):
                    return []
                action = getattr(decision, "action", None)
                conf = float(getattr(decision, "confidence", 0) or 0)
                if action not in ("buy", "sell") or conf < min_conf:
                    return []
                return [
                    AIAnalysisSignal(
                        code=getattr(decision, "code", "") or "",
                        side=action,
                        name=getattr(decision, "name", None),
                        trigger_price=float(getattr(decision, "trigger_price", 0) or 0),
                        suggested_quantity=getattr(decision, "suggested_quantity", None),
                        reason=str(getattr(decision, "reason", "") or "ai_signal_worker"),
                        strategy_name="ai_signal_worker",
                        confidence=conf,
                    )
                ]

            ai_worker = AISignalWorker(
                analysis_fn=_ai_analysis,
                signal_queue=q,
                schedule_interval_seconds=interval,
            )
            ai_worker.start()
            log.info(
                "AISignalWorker started (interval=%.0fs, pm_agent=%s)",
                interval, pm_agent is not None,
            )
        except Exception as exc:  # noqa: BLE001 — worker 可用性降级，不影响 listener
            log.warning("AISignalWorker unavailable (skipped): %s", exc)

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
        if ai_worker is not None:
            ai_worker.stop()
        if feed is not None:
            feed.stop()


if __name__ == "__main__":
    main()
