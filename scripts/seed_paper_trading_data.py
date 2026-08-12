#!/usr/bin/env python
"""Seed paper trading demo data directly into the database.

Creates accounts, positions, orders, trades, and publishes EventBus events.
Safe to re-run: cleans up existing paper accounts first.

Usage:
    .venv\\Scripts\\python.exe scripts\\seed_paper_trading_data.py
"""

from __future__ import annotations

import sys
import os
import json
import random
from datetime import datetime, timedelta, timezone

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage import (
    DatabaseManager,
    Account,
    PaperPosition,
    PaperOrder,
    PaperTrade,
)
from src.services.bootstrap_event_bus import bootstrap_event_bus
from src.services.event_bus import SystemEvent, SystemEventType, EventSeverity


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hours_ago(h: float) -> datetime:
    return utc_now() - timedelta(hours=h)


def days_ago(d: float) -> datetime:
    return utc_now() - timedelta(days=d)


# ── Stock universe ──────────────────────────────────────────────────────────

STOCKS_CN = [
    ("600519", "贵州茅台", 1685.00),
    ("000858", "五粮液",   148.50),
    ("601318", "中国平安",   48.20),
    ("000001", "平安银行",   11.80),
    ("300750", "宁德时代",  215.30),
    ("601012", "隆基绿能",   23.45),
]

STOCKS_HK = [
    ("00700", "腾讯控股",  385.60),
    ("09988", "阿里巴巴",   82.30),
    ("03690", "美团",      112.50),
]

STOCKS_US = [
    ("AAPL",  "Apple",     195.80),
    ("NVDA",  "NVIDIA",    875.40),
    ("TSLA",  "Tesla",     248.50),
]


def _seed_account(
    session,
    name: str,
    market: str,
    cash: float,
    stocks: list[tuple[str, str, float]],
) -> int:
    """Create one account with positions + orders + trades. Returns account id."""
    # Check if account already exists
    existing = session.query(Account).filter_by(name=name).first()
    if existing:
        # Delete related data
        session.query(PaperTrade).filter_by(account_id=existing.id).delete()
        session.query(PaperOrder).filter_by(account_id=existing.id).delete()
        session.query(PaperPosition).filter_by(account_id=existing.id).delete()
        session.query(Account).filter_by(id=existing.id).delete()
        session.flush()

    acct = Account(
        name=name,
        account_type="paper",
        market=market,
        base_currency="CNY" if market == "cn" else ("HKD" if market == "hk" else "USD"),
        cash=cash,
        frozen_cash=0.0,
        status="active",
        is_active=True,
        initial_capital=cash + sum(s[2] * 200 for s in stocks),
    )
    session.add(acct)
    session.flush()
    aid = acct.id

    # ── Positions ──
    for code, sname, price in stocks:
        qty = random.choice([100, 200, 300, 500])
        avg_cost = round(price * random.uniform(0.92, 1.05), 2)
        pos = PaperPosition(
            account_id=aid,
            code=code,
            name=sname,
            quantity=float(qty),
            available_quantity=float(qty),
            avg_cost=avg_cost,
            last_price=price,
            stop_loss=round(avg_cost * 0.90, 2),
            take_profit=round(avg_cost * 1.15, 2),
        )
        session.add(pos)

    session.flush()

    # ── Orders (mix of filled, pending, cancelled) ──
    for code, sname, price in stocks:
        # One filled buy order
        buy_order = PaperOrder(
            account_id=aid,
            code=code,
            name=sname,
            side="buy",
            order_type="limit",
            price=round(price * 0.98, 2),
            quantity=200.0,
            filled_quantity=200.0,
            filled_price_avg=round(price * 0.98, 2),
            status="filled",
            strategy_name="MA_Cross",
            reason=f"MA金叉信号买入 {sname}",
            created_at=days_ago(3),
            updated_at=days_ago(3),
            filled_at=days_ago(3),
            version=1,
        )
        session.add(buy_order)
        session.flush()

        # Trade for the filled order
        trade = PaperTrade(
            account_id=aid,
            order_id=buy_order.id,
            code=code,
            name=sname,
            side="buy",
            price=round(price * 0.98, 2),
            quantity=200.0,
            amount=round(price * 0.98 * 200, 2),
            fee=round(price * 0.98 * 200 * 0.0003, 2),
            traded_at=days_ago(3),
        )
        session.add(trade)

        # One pending sell limit order
        pending_sell = PaperOrder(
            account_id=aid,
            code=code,
            name=sname,
            side="sell",
            order_type="limit",
            price=round(price * 1.08, 2),
            quantity=100.0,
            filled_quantity=0.0,
            filled_price_avg=0.0,
            status="pending",
            strategy_name="MA_Cross",
            reason=f"止盈挂单 {sname}",
            created_at=hours_ago(2),
            updated_at=hours_ago(2),
            version=0,
        )
        session.add(pending_sell)

        # One cancelled order
        cancelled = PaperOrder(
            account_id=aid,
            code=code,
            name=sname,
            side="buy",
            order_type="limit",
            price=round(price * 0.92, 2),
            quantity=100.0,
            filled_quantity=0.0,
            filled_price_avg=0.0,
            status="canceled",
            cancel_reason="价格偏离过大，撤单重挂",
            strategy_name="Mean_Reversion",
            reason=f"均值回归信号 {sname}",
            created_at=days_ago(1),
            updated_at=days_ago(1),
            version=0,
        )
        session.add(cancelled)

    session.flush()
    return aid


def _publish_events(account_ids: dict[str, int]) -> None:
    """Publish demo events to the EventBus for the observability page."""
    bus = bootstrap_event_bus()

    events = [
        SystemEvent(
            event_id="seed-evt-001",
            event_type=SystemEventType.SYSTEM_STARTUP,
            severity=EventSeverity.INFO,
            source="paper_trading",
            payload={
                "message": "纸面交易账户已初始化",
                "accounts": list(account_ids.keys()),
                "total_capital": 1_000_000,
            },
            timestamp=hours_ago(4),
        ),
        SystemEvent(
            event_id="seed-evt-002",
            event_type=SystemEventType.PIPELINE_STARTED,
            severity=EventSeverity.INFO,
            source="paper_trading",
            payload={
                "message": "MA金叉策略触发买入信号",
                "strategy": "MA_Cross",
                "stocks": ["600519", "00700", "AAPL"],
                "action": "buy",
            },
            timestamp=days_ago(3),
        ),
        SystemEvent(
            event_id="seed-evt-003",
            event_type=SystemEventType.OUTCOME_DEVIATION,
            severity=EventSeverity.WARNING,
            source="risk_reviewer",
            payload={
                "message": "账户波动率超过阈值 (vol=0.32, threshold=0.25)",
                "account_id": account_ids.get("A股量化"),
                "metric": "volatility",
                "value": 0.32,
                "threshold": 0.25,
            },
            timestamp=hours_ago(6),
        ),
        SystemEvent(
            event_id="seed-evt-004",
            event_type=SystemEventType.HEALTH_CHECK_COMPLETED,
            severity=EventSeverity.INFO,
            source="bootstrap",
            payload={
                "message": "数据源预热完成 (9 sources)",
                "sources": 9,
                "primary": "TushareFetcher",
            },
            timestamp=hours_ago(8),
        ),
        SystemEvent(
            event_id="seed-evt-005",
            event_type=SystemEventType.BIAS_DETECTED,
            severity=EventSeverity.WARNING,
            source="meta_cognitive",
            payload={
                "message": "L4元认知检测到策略回归: MA_Cross 胜率从 62% 降至 48%",
                "observer": "MetaCognitiveObserver",
                "regression": "win_rate_drop",
                "before": 0.62,
                "after": 0.48,
            },
            timestamp=hours_ago(2),
        ),
        SystemEvent(
            event_id="seed-evt-006",
            event_type=SystemEventType.SERVICE_ERROR,
            severity=EventSeverity.ERROR,
            source="paper_trading",
            payload={
                "message": "订单被拒: 600519 买入数量不足1手",
                "code": "600519",
                "side": "buy",
                "quantity": 50,
                "reject_reason": "min_lot_violation",
            },
            timestamp=hours_ago(1),
        ),
    ]

    for evt in events:
        bus.publish(evt)

    # Flush to disk immediately
    bus.flush_to_disk()
    print(f"  Published {len(events)} events to EventBus")


def main() -> None:
    print("=" * 60)
    print("  Paper Trading Demo Data Seeder")
    print("=" * 60)

    db = DatabaseManager()

    print("\n[1/3] Seeding accounts, positions, orders, trades...")

    account_ids: dict[str, int] = {}

    with db.session_scope() as session:
        aid = _seed_account(session, "A股量化", "cn", 500_000.0, STOCKS_CN)
        account_ids["A股量化"] = aid
        print(f"  ✓ Account 'A股量化' (id={aid}, market=cn, 6 stocks)")

        aid = _seed_account(session, "港股通", "hk", 300_000.0, STOCKS_HK)
        account_ids["港股通"] = aid
        print(f"  ✓ Account '港股通' (id={aid}, market=hk, 3 stocks)")

        aid = _seed_account(session, "美股科技", "us", 200_000.0, STOCKS_US)
        account_ids["美股科技"] = aid
        print(f"  ✓ Account '美股科技' (id={aid}, market=us, 3 stocks)")

    print(f"\n  Total: {len(account_ids)} accounts")

    # Count records
    with db.session_scope() as session:
        positions = session.query(PaperPosition).count()
        orders = session.query(PaperOrder).count()
        trades = session.query(PaperTrade).count()
        print(f"  Positions: {positions}")
        print(f"  Orders: {orders}")
        print(f"  Trades: {trades}")

    print("\n[2/3] Publishing EventBus events...")
    _publish_events(account_ids)

    print("\n[3/3] Done!")
    print("\nDemo data ready. Open http://localhost:5173/ to test.")


if __name__ == "__main__":
    main()
