#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate reproducible demo data for the paper-trading system (T-03).

Creates (or resets) a demo paper account and seeds:
  - live trading data (orders / positions / trades / signals) via TradingEngine
  - agent artifacts (reflections / battle plan / PM decisions) as fallback rows
  - net-value history curve
  - backtest summaries + analysis-history reports (Backtest / History pages)

Usage:
  python scripts/seed_demo_data.py --account demo --capital 1000000
  python scripts/seed_demo_data.py --account demo --reset
  python scripts/seed_demo_data.py --help

The script is idempotent: with --reset it fully deletes an existing demo
account (and all its paper data) before rebuilding. It does not start any
server and does not require network or LLM credentials.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402
from src.storage import (  # noqa: E402
    Account,
    AnalysisHistory,
    BacktestResult,
    BacktestSummary,
    DatabaseManager,
    PaperBattlePlan,
    PaperDecision,
    PaperNetValue,
    PaperReflection,
    get_db,
)
from sqlalchemy import delete, select  # noqa: E402

from paper_trading.account import PaperAccountManager  # noqa: E402
from paper_trading.fees import FeeModel  # noqa: E402
from paper_trading.order import OrderManager, OrderType  # noqa: E402
from paper_trading.position import PositionManager  # noqa: E402
from paper_trading.risk import RiskChecker, RiskConfig  # noqa: E402
from paper_trading.strategies import Signal  # noqa: E402
from paper_trading.trading_engine import TradingEngine  # noqa: E402

logger = logging.getLogger("seed_demo_data")

OVERALL_SENTINEL = "__overall__"


# ---------------------------------------------------------------------------
# Trading data (orders/positions/trades/signals)
# ---------------------------------------------------------------------------


def _make_engine(db: DatabaseManager) -> TradingEngine:
    fee_model = FeeModel()
    pos_mgr = PositionManager(db)
    order_mgr = OrderManager(db)
    account_mgr = PaperAccountManager(db_manager=db)
    risk = RiskChecker(
        db_manager=db,
        account_manager=account_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
        config=RiskConfig(max_daily_loss_pct=0.05),
    )
    return TradingEngine(
        db_manager=db,
        account_manager=account_mgr,
        order_manager=order_mgr,
        position_manager=pos_mgr,
        fee_model=fee_model,
        risk_checker=risk,
        enable_auto_sltp=False,
        quote_cache=None,  # T-02: no live cache in seed -> falls back to reference price
    )


def _signal(code: str, name: str, side: str, price: float, qty: float, strategy: str) -> Signal:
    return Signal(
        side=side, code=code, name=name, strategy_name=strategy,
        rule_name="seed_demo", trigger_price=price,
        suggested_quantity=qty, reason="seed_demo_data",
    )


def seed_trading_data(db: DatabaseManager, account_id: int) -> int:
    """Place deterministic orders through the engine. Returns trade count."""
    engine = _make_engine(db)
    orders = [
        ("600519", "贵州茅台", "buy", 100, "market", 1680.0, "momentum_v2"),
        ("600519", "贵州茅台", "buy", 200, "market", 1685.0, "momentum_v2"),
        ("300750", "宁德时代", "buy", 300, "market", 190.0, "sma_crossover"),
        ("000001", "平安银行", "buy", 500, "market", 45.0, "rsi_breakout"),
        ("600519", "贵州茅台", "sell", 100, "market", 1700.0, "take_profit"),
    ]
    for code, name, side, qty, otype, price, strategy in orders:
        engine.submit_signal(
            account_id=account_id,
            signal=_signal(code, name, side, price, qty, strategy),
            order_type=OrderType.MARKET,
            limit_price=price,
            quantity_override=qty,
        )
    # limit order (stays pending)
    engine.submit_signal(
        account_id=account_id,
        signal=_signal("600519", "贵州茅台", "buy", 1650.0, 50, "mean_reversion"),
        order_type=OrderType.LIMIT,
        limit_price=1650.0,
        quantity_override=50,
    )
    # batch orders (market + limit)
    order_mgr = engine.order_mgr
    from paper_trading.order import OrderRequest, OrderSide

    created = order_mgr.create_batch_orders(
        account_id,
        [
            OrderRequest(account_id=account_id, code="600519", side=OrderSide.BUY,
                         quantity=50, order_type=OrderType.MARKET, price=1688.0,
                         name="贵州茅台", strategy_name="batch_demo", reason="seed_demo_data"),
            OrderRequest(account_id=account_id, code="hk00700", side=OrderSide.BUY,
                         quantity=100, order_type=OrderType.MARKET, price=320.0,
                         name="腾讯控股", strategy_name="batch_demo", reason="seed_demo_data"),
            OrderRequest(account_id=account_id, code="300750", side=OrderSide.BUY,
                         quantity=100, order_type=OrderType.LIMIT, price=185.0,
                         name="宁德时代", strategy_name="batch_demo", reason="seed_demo_data"),
        ],
    )
    for order in created:
        if order.order_type == OrderType.MARKET.value:
            order_dict = order_mgr._order_to_dict(order)
            engine._execute_triggered_market_order(order_dict, fill_price=float(order.price or 0.0))
    # conditional stop-loss order
    order_mgr.create_conditional_order(
        account_id=account_id, code="600519", side=OrderSide.SELL,
        quantity=50, order_type=OrderType.STOP_LOSS, trigger_price=1600.0,
        price=1595.0, name="贵州茅台", strategy_name="sltp_demo", reason="seed_demo_data",
    )
    with db.session_scope() as session:
        return _count_trades(session, account_id)


def _count_trades(session, account_id: int) -> int:
    from src.storage import PaperTrade

    return len(session.execute(
        select(PaperTrade.id).where(PaperTrade.account_id == account_id)
    ).scalars().all())


# ---------------------------------------------------------------------------
# Agent artifacts (fallback rows: reflections / battle plan / PM decisions)
# ---------------------------------------------------------------------------


def seed_agent_artifacts(db: DatabaseManager, account_id: int) -> None:
    now = datetime.now()
    with db.session_scope() as session:
        session.execute(select(PaperReflection.id).where(PaperReflection.account_id == account_id))
        session.add(PaperReflection(
            account_id=account_id, scope="trade", subject="买入 600519 复盘",
            summary="日内按动量策略买入贵州茅台 100 股，成交均价 1680。",
            takeaway="趋势跟随有效但需控制单票仓位，追高时预留回落空间。",
            lessons_json=json.dumps(["趋势策略在放量突破日表现稳定", "单票仓位应控制在 15% 以内"], ensure_ascii=False),
            tags="追高,趋势,仓位控制", mood="good", code="600519",
            elapsed_seconds=2.5, used_fallback=True,
            created_at=now - timedelta(days=1),
        ))
        session.add(PaperReflection(
            account_id=account_id, scope="daily", subject="日度复盘",
            summary="当日完成多笔买入与限价挂单，持仓分散到多板块。",
            takeaway="多策略并行时注意相关性，避免同板块高 beta 品种集中。",
            lessons_json=json.dumps(["分散到不同板块", "限价单需关注成交概率"], ensure_ascii=False),
            tags="复盘,风控,净值", mood="neutral",
            elapsed_seconds=3.0, used_fallback=True,
            created_at=now,
        ))
        holdings = [
            {"code": "600519", "name": "贵州茅台", "current_price": 1680.0,
             "stop_loss": 1580.0, "take_profit_1": 1850.0, "take_profit_2": 1950.0},
            {"code": "300750", "name": "宁德时代", "current_price": 190.0,
             "stop_loss": 172.0, "take_profit_1": 215.0, "take_profit_2": 235.0},
            {"code": "000001", "name": "平安银行", "current_price": 45.0,
             "stop_loss": 41.5, "take_profit_1": 49.0, "take_profit_2": 52.0},
        ]
        candidates = [
            {"code": "601318", "name": "中国平安", "auction_condition": "高开>1% 且量比>1.2",
             "intraday_trigger": "突破 55.0 放量", "position_ratio": 0.10,
             "stop_loss": 51.5, "take_profit_1": 58.0, "take_profit_2": 62.0, "technical_score": 72},
            {"code": "510300", "name": "沪深300ETF", "auction_condition": "低开回补缺口",
             "intraday_trigger": "站上 4.0", "position_ratio": 0.08,
             "stop_loss": 3.85, "take_profit_1": 4.15, "take_profit_2": 4.3, "technical_score": 68},
        ]
        session.add(PaperBattlePlan(
            account_id=account_id, date=now.date(),
            holdings_plans_json=json.dumps(holdings, ensure_ascii=False),
            candidates_json=json.dumps(candidates, ensure_ascii=False),
            market_review="大盘温和放量，消费与新能源轮动，金融板块低估值修复延续。",
            sentiment_score=62, main_theme="消费+新能源+金融修复",
            used_fallback=True, created_at=now,
        ))
        session.add(PaperDecision(
            account_id=account_id, action="buy", code="600519", name="贵州茅台",
            params_json=json.dumps({"entry_price": 1680.0, "quantity": 100, "stop_loss": 1580.0, "take_profit": 1850.0}, ensure_ascii=False),
            reason="动量突破+资金流入，符合买入条件", confidence=0.82,
            source="pm_agent", status="executed", created_at=now - timedelta(days=1),
        ))
        session.add(PaperDecision(
            account_id=account_id, action="buy", code="300750", name="宁德时代",
            params_json=json.dumps({"entry_price": 190.0, "quantity": 300, "stop_loss": 172.0, "take_profit": 215.0}, ensure_ascii=False),
            reason="板块景气度回升，回踩确认", confidence=0.76,
            source="pm_agent", status="executed", created_at=now - timedelta(days=1),
        ))
        session.add(PaperDecision(
            account_id=account_id, action="hold", code=None, name=None,
            params_json=json.dumps({"reason": "no_new_signal"}, ensure_ascii=False),
            reason="市场无新增信号，保持现有仓位观望", confidence=0.60,
            source="pm_agent", status="skipped", created_at=now,
        ))


# ---------------------------------------------------------------------------
# Net-value history curve
# ---------------------------------------------------------------------------


def seed_net_value_history(db: DatabaseManager, account_id: int, days: int = 30) -> int:
    now = datetime.now()
    rows: List[PaperNetValue] = []
    d = now - timedelta(days=days)
    day_seq: List[date] = []
    while d.date() < now.date():
        if d.weekday() < 5:
            day_seq.append(d.date())
        d += timedelta(days=1)
    base = 1000000.0
    for i, day in enumerate(day_seq):
        progress = i / max(len(day_seq) - 1, 1)
        v = 1.0 + 0.06 * math.sin(progress * math.pi) - 0.003 * progress
        total = base * v
        cash = total * (1 - 0.35)
        mv = total - cash
        rows.append(PaperNetValue(
            account_id=account_id, date=day, total_assets=round(total, 2),
            cash=round(cash, 2), market_value=round(mv, 2),
            net_value=round(v, 6), return_pct=round((v - 1) * 100, 4),
        ))
    with db.session_scope() as session:
        for row in rows:
            session.add(row)
    return len(rows)


# ---------------------------------------------------------------------------
# Backtest summaries + analysis-history reports
# ---------------------------------------------------------------------------


def _build_raw_result(code: str, name: str, sentiment: int, trend: str, advice: str,
                      action: str, action_label: str, price: float, chg: float, summary: str) -> Dict[str, Any]:
    return {
        "code": code, "name": name, "sentiment_score": sentiment,
        "trend_prediction": trend, "operation_advice": advice,
        "decision_type": "buy" if action in ("buy", "add") else ("sell" if action in ("sell", "reduce") else "hold"),
        "confidence_level": "高" if sentiment >= 65 else "中", "report_language": "zh",
        "action": action, "action_label": action_label,
        "dashboard": {"core_conclusion": {"one_sentence": summary,
            "position_advice": {"summary": advice, "action": action, "action_label": action_label, "position": "30%"}}},
        "trend_analysis": f"{name} 近期处于上升趋势，量价配合良好。",
        "short_term_outlook": "短线或延续当前走势。", "medium_term_outlook": "中期关注板块轮动。",
        "technical_analysis": "MACD 多头排列，均线呈多头形态。",
        "ma_analysis": "5/20/60 日均线多头排列。", "volume_analysis": "近期温和放量，主力净流入。",
        "pattern_analysis": "日线收于平台上方。", "fundamental_analysis": "基本面稳健。",
        "sector_position": "板块景气度回升。", "company_highlights": "核心产品竞争力强。",
        "news_summary": "近期发布业绩快报，营收与利润双增。",
        "market_sentiment": "市场情绪偏暖。", "hot_topics": "业绩预增、政策利好。",
        "analysis_summary": summary, "key_points": "趋势向好；量价配合；板块催化。",
        "risk_warning": f"若跌破止损建议减仓。", "buy_reason": "技术突破+基本面+板块催化。",
        "market_snapshot": {"price": price, "change_pct": chg, "name": name, "code": code},
        "search_performed": True, "success": True,
        "current_price": price, "change_pct": chg,
        "model_used": "openai/agnes-2.5-flash",
    }


def _bt_code(c: str) -> str:
    return "00700" if c == "hk00700" else c


def seed_backtest_reports(db: DatabaseManager, account_id: int) -> None:
    # Idempotency: remove only rows this script previously created.
    # AnalysisHistory/BacktestResult/BacktestSummary carry no account_id, so we
    # mark them: history rows via query_id prefix, summaries via diagnostics flag.
    with db.session_scope() as session:
        seed_hids = session.execute(
            select(AnalysisHistory.id).where(AnalysisHistory.query_id.like("seed-%"))
        ).scalars().all()
        if seed_hids:
            session.execute(
                delete(BacktestResult).where(BacktestResult.analysis_history_id.in_(seed_hids))
            )
            session.execute(delete(AnalysisHistory).where(AnalysisHistory.id.in_(seed_hids)))
        # Rebuild the demo backtest rollups. Remove rows for the codes this
        # script owns (overall sentinel + demo stocks). New rows carry a
        # seed_demo diagnostics flag for future precise cleanups.
        seed_codes = [OVERALL_SENTINEL, "600519", "300750", "000001", "00700", "AAPL", "601318"]
        session.execute(delete(BacktestSummary).where(BacktestSummary.code.in_(seed_codes)))

    records = [
        ("600519", "贵州茅台", 72, "看多", "买入", "buy", "买入", 1680.0, 1.25),
        ("300750", "宁德时代", 68, "看多", "买入", "buy", "买入", 190.0, 0.85),
        ("000001", "平安银行", 55, "震荡", "持有", "hold", "持有", 45.0, -0.40),
        ("hk00700", "腾讯控股", 74, "强烈看多", "买入", "buy", "买入", 320.0, 2.10),
        ("AAPL", "苹果", 70, "看多", "买入", "buy", "买入", 215.0, 1.60),
        ("601318", "中国平安", 42, "看空", "卖出", "sell", "卖出", 54.0, -1.10),
    ]
    outcomes = [
        (1650.0, 1712.0, 1735.0, 1638.0, 3.76, "up", True, "win", False, True, "take_profit", 4.2),
        (187.0, 194.5, 197.0, 185.5, 4.01, "up", True, "win", False, True, "take_profit", 4.5),
        (45.5, 44.2, 46.0, 43.8, -2.86, "down", True, "win", False, False, "neither", -2.1),
        (312.0, 328.5, 331.0, 310.0, 5.29, "up", True, "win", False, True, "take_profit", 5.6),
        (210.0, 217.5, 219.0, 209.0, 3.57, "up", True, "win", False, True, "take_profit", 4.0),
        (55.0, 53.2, 55.8, 52.5, -3.27, "down", False, "loss", True, False, "stop_loss", -3.4),
    ]
    now = datetime.now()
    history_ids: List[int] = []
    with db.session_scope() as session:
        for i, (code, name, sentiment, trend, advice, action, alabel, price, chg) in enumerate(records):
            days_ago = i + 1
            created = now - timedelta(days=days_ago)
            summary = f"{name} 综合评分 {sentiment}，趋势判断为{trend}，操作建议：{advice}。"
            rec = AnalysisHistory(
                query_id=f"seed-{_bt_code(code).lower()}-{created.date().isoformat()}",
                code=_bt_code(code), name=name, report_type="standard",
                sentiment_score=sentiment, operation_advice=advice, trend_prediction=trend,
                analysis_summary=summary,
                raw_result=json.dumps(_build_raw_result(_bt_code(code), name, sentiment, trend, advice,
                                                        action, alabel, price, chg, summary), ensure_ascii=False),
                news_content=json.dumps([{"title": f"{name} 发布业绩预增公告",
                                          "snippet": "公司预计净利润同比增长显著。",
                                          "url": "https://example.com/news/seed"}], ensure_ascii=False),
                context_snapshot=json.dumps({"enhanced_context": {"realtime": {
                    "current_price": price, "change_pct": chg, "volume_ratio": 1.2, "turnover_rate": 0.8}}}, ensure_ascii=False),
                ideal_buy=price * 0.99, secondary_buy=price * 0.96,
                stop_loss=price * 0.93, take_profit=price * 1.10,
                created_at=created,
            )
            session.add(rec)
            session.flush()
            history_ids.append(rec.id)

            (sp, ec, mh, ml, sret, dex, dcorr, outc, hit_sl, hit_tp, fhit, sim_ret) = outcomes[i]
            session.add(BacktestResult(
                analysis_history_id=rec.id, code=_bt_code(code), analysis_date=created.date(),
                eval_window_days=10, engine_version="v1", eval_status="completed",
                evaluated_at=created + timedelta(hours=1),
                operation_advice=advice, position_recommendation="long" if outc == "win" else "cash",
                start_price=sp, end_close=ec, max_high=mh, min_low=ml,
                stock_return_pct=sret, direction_expected=dex, direction_correct=dcorr, outcome=outc,
                stop_loss=price * 0.93, take_profit=price * 1.10,
                hit_stop_loss=hit_sl, hit_take_profit=hit_tp, first_hit=fhit,
                first_hit_date=(created + timedelta(days=2)).date(), first_hit_trading_days=3,
                simulated_entry_price=sp, simulated_exit_price=ec,
                simulated_exit_reason="stop_loss" if fhit == "stop_loss" else "take_profit",
                simulated_return_pct=sim_ret,
            ))

        def _summary(scope: str, code: str, win: int, loss: int, neutral: int,
                     long_c: int, cash_c: int, avg_sret: float, avg_sim: float,
                     sl_rate: float, tp_rate: float, days_avg: float) -> None:
            total = win + loss + neutral
            session.add(BacktestSummary(
                scope=scope, code=code, eval_window_days=10, engine_version="v1",
                computed_at=now, total_evaluations=total, completed_count=total,
                insufficient_count=0, long_count=long_c, cash_count=cash_c,
                win_count=win, loss_count=loss, neutral_count=neutral,
                direction_accuracy_pct=round(win / total * 100, 2) if total else 0.0,
                win_rate_pct=round(win / (win + loss) * 100, 2) if (win + loss) else 0.0,
                neutral_rate_pct=round(neutral / total * 100, 2) if total else 0.0,
                avg_stock_return_pct=avg_sret, avg_simulated_return_pct=avg_sim,
                stop_loss_trigger_rate=sl_rate, take_profit_trigger_rate=tp_rate,
                ambiguous_rate=0.0, avg_days_to_first_hit=days_avg,
                advice_breakdown_json=json.dumps({"buy": 4, "hold": 1, "sell": 1}, ensure_ascii=False),
                diagnostics_json=json.dumps({"seed_demo": True}),
            ))

        _summary("overall", OVERALL_SENTINEL, 4, 2, 0, 5, 1, 3.42, 3.13, 16.67, 66.67, 4.2)
        _summary("stock", "600519", 1, 1, 0, 2, 0, 3.76, 4.2, 0.0, 50.0, 3.0)
        _summary("stock", "300750", 1, 0, 0, 1, 0, 4.01, 4.5, 0.0, 100.0, 2.0)
        _summary("stock", "000001", 1, 0, 0, 1, 0, -2.86, -2.1, 0.0, 0.0, 10.0)
        _summary("stock", "00700", 1, 0, 0, 1, 0, 5.29, 5.6, 0.0, 100.0, 2.0)
        _summary("stock", "AAPL", 1, 0, 0, 1, 0, 3.57, 4.0, 0.0, 100.0, 2.0)
        _summary("stock", "601318", 0, 1, 0, 0, 1, -3.27, -3.4, 100.0, 0.0, 6.0)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    setup_env()
    parser = argparse.ArgumentParser(description="Seed demo paper-trading data")
    parser.add_argument("--account", default="E2E-演示账户", help="demo account name")
    parser.add_argument("--capital", type=float, default=1000000.0, help="initial capital")
    parser.add_argument("--days", type=int, default=30, help="net-value history days")
    parser.add_argument("--reset", action="store_true", help="delete existing account before seeding")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    db = get_db()
    account_mgr = PaperAccountManager(db_manager=db)
    existing = account_mgr.get_account(name=args.account)
    if existing is not None:
        if not args.reset:
            print(f"Account '{args.account}' already exists; use --reset to rebuild.")
            return 1
        account_mgr.delete_account(existing.id)
        print(f"Reset existing account '{args.account}' (id={existing.id})")

    account = account_mgr.get_or_create_account(name=args.account, initial_capital=args.capital)
    account_id = account.id

    trades = seed_trading_data(db, account_id)
    seed_agent_artifacts(db, account_id)
    nv = seed_net_value_history(db, account_id, args.days)
    seed_backtest_reports(db, account_id)

    print(f"Seeded demo data for account #{account_id} '{args.account}' (capital={args.capital:.0f}):")
    print(f"  trades={trades}, net_value_points={nv}")
    print("  artifacts: reflections=2, battle_plan=1, pm_decisions=3")
    print("  backtest: results=6, summaries=7, analysis_history=6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
