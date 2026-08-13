# -*- coding: utf-8 -*-
"""端到端验证：AI 分析信号队列链路（AISignalWorker push → MarketListener pop）。

模拟 analyzer 分析完成后 push 信号 → 队列 → listener._consume_ai_signals 消费 → submit_signal。

运行: PYTHONPATH= .venv/Scripts/python.exe paper_trading/_e2e_ai_signal_queue.py
"""
import os
import sys

sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")
os.environ.setdefault("PYTHONPATH", "")

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(message)s")
log = logging.getLogger("e2e_ai_signal")

from src.paper_trading_signal_queue import init_signal_queue, AIAnalysisSignal, get_signal_queue
from src.config import get_config, setup_env


def main():
    setup_env()
    cfg = get_config()
    print("=" * 66)
    print("端到端验证: AI 分析信号队列链路")
    print("=" * 66)

    # 前置条件检查
    print(f"\n[1] 配置检查:")
    print(f"    paper_trading_enabled        = {getattr(cfg, 'paper_trading_enabled', '?')}")
    print(f"    enable_ai_signal_source      = {getattr(cfg, 'paper_trading_enable_ai_signal_source', '?')}")
    print(f"    min_confidence               = {getattr(cfg, 'paper_trading_ai_signal_min_confidence', '?')}")
    print(f"    watched_codes (stock_list)   = {len(getattr(cfg, 'stock_list', []))} 只")

    # 初始化队列（main.py 启动时做的事）
    print(f"\n[2] 初始化信号队列:")
    q = init_signal_queue(maxsize=1000)
    print(f"    队列已初始化: maxsize={q._queue.maxsize}, 当前={q.size()} 条")

    # 模拟 analyzer push（构造符合 watched_codes 的买入信号）
    watched = list(getattr(cfg, "stock_list", []))
    code = watched[0] if watched else "600519"
    print(f"\n[3] 模拟 analyzer push 信号: {code} buy")
    sig = AIAnalysisSignal(
        code=code,
        side="buy",
        name="测试AI信号",
        trigger_price=1.0,  # 会被 live price 覆盖
        suggested_quantity=100,
        reason="E2E验证 AI 信号队列链路",
        strategy_name="ai_decision_hook",
        confidence=0.95,
    )
    ok = q.push(sig)
    print(f"    push 结果: {ok}, 队列现有 {q.size()} 条")
    assert ok, "push 失败"

    # 模拟 listener._consume_ai_signals（从队列消费并 submit）
    print(f"\n[4] 模拟 listener 消费:")
    from paper_trading.market_listener import MarketListener, MarketListenerConfig
    from paper_trading.trading_engine import TradingEngine

    # 构建真实 listener（账户3）
    from paper_trading.market_listener import build_default_listener
    listener = build_default_listener(cfg, account_id=3)
    print(f"    listener 就绪: watched={len(listener.config.watched_codes)}, strategies={len(listener.strategies)}")

    # 拉一次真实行情作为 latest_prices（复用 listener 的行情获取路径）
    prices = {}
    try:
        prices = listener._fetch_latest_prices(watched[:5])
    except Exception as exc:
        print(f"    ⚠️ 行情获取异常: {exc}")
    if not prices:
        print("    ⚠️ 行情获取失败，用测试价格")
        prices = {code: 10.0}
    print(f"    行情: {len(prices)} 只 -> { {k: round(v,2) for k,v in list(prices.items())[:3]} }")

    # 直接调用消费函数（绕过 tick 循环，聚焦验证队列消费逻辑）
    consumed = listener._consume_ai_signals(prices)
    remaining = q.size()
    print(f"    消费完成: 队列剩余 {remaining} 条")

    # 验证结果
    from src.storage import DatabaseManager
    from sqlalchemy import select, desc
    from src.storage import PaperSignal

    db = DatabaseManager.get_instance()
    with db.get_session() as s:
        rows = s.execute(
            select(PaperSignal)
            .where(PaperSignal.strategy_name.in_(["ai_decision_hook", "ai_analysis_signal"]))
            .order_by(desc(PaperSignal.id)).limit(3)
        ).scalars().all()
        print(f"\n[5] paper_signals 中的 AI 信号记录 ({len(rows)} 条):")
        for r in rows:
            print(f"    id={r.id} {r.code} {r.side} status={r.status} strategy={r.strategy_name}")
            print(f"      reason: {(r.reason or '')[:100]}")

    print("\n✅ 端到端链路验证完成" if remaining == 0 else "\n⚠️ 队列还有残留")


if __name__ == "__main__":
    main()
