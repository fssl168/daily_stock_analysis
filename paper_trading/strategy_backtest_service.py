# -*- coding: utf-8 -*-
"""策略级回测服务：批量回测 → 落库 → 融合权重持久化 → 每周重算。

统一封装：
1. run_strategy_backtests(): 跑全部模板策略 × 自选股，结果落 strategy_backtest_results
2. refresh_fusion_weights(): 回测后 SoftMax 权重 → 写入 DB（供重启恢复）
3. load_fusion_weights(): 从 DB 加载最新权重（listener 启动时调用）
4. weekly_backtest_job(): 每周重算任务（注册到 runtime_scheduler）

Usage:
    from paper_trading.strategy_backtest_service import (
        run_strategy_backtests, refresh_fusion_weights,
        load_fusion_weights, weekly_backtest_job,
    )
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 参与批量回测的策略模板
DEFAULT_STRATEGY_NAMES = [
    "golden_cross", "rsi_reversal", "boll_breakout", "macd_momentum",
]


def _load_strategies() -> List[Any]:
    from paper_trading.strategies.engine.templates import TEMPLATES, get_template

    return [get_template(name) for name in DEFAULT_STRATEGY_NAMES if name in TEMPLATES]


def _load_daily_data(codes: List[str]) -> Dict[str, Any]:
    """拉取日线并转换为回测引擎期望的日期索引格式。"""
    import pandas as pd
    from data_provider import DataFetcherManager

    fetcher = DataFetcherManager()
    daily = {}
    for c in codes:
        try:
            df, src = fetcher.get_daily_data(c, days=9999)
            if df is not None and not df.empty:
                if "date" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
                    df = df.set_index(pd.to_datetime(df["date"])).sort_index()
                daily[c] = df
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily data fetch failed for %s: %s", c, exc)
    return daily


def run_strategy_backtests(
    *,
    codes: Optional[List[str]] = None,
    initial_cash: float = 1_000_000.0,
    start_date: Optional[date] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """运行全部模板策略 × 自选股回测，落库并返回结果。

    Returns:
        {"results": {strategy: metrics}, "ranked": [...], "weights": {...}}
    """
    from src.config import get_config, setup_env

    setup_env()
    cfg = get_config()
    stock_codes = list(codes or getattr(cfg, "stock_list", []))
    start = start_date or date(2024, 1, 1)

    from paper_trading.backtest.engine import BacktestEngine, BacktestConfig

    strategies = _load_strategies()
    if not strategies:
        return {"results": {}, "ranked": [], "weights": {}}

    daily = _load_daily_data(stock_codes)
    if not daily:
        logger.warning("no daily data; backtest aborted")
        return {"results": {}, "ranked": [], "weights": {}}

    results: Dict[str, Dict[str, float]] = {}
    for strategy in strategies:
        try:
            config = BacktestConfig(
                initial_cash=initial_cash,
                start_date=start,
                benchmark_code="000300",
            )
            engine = BacktestEngine(config)
            result = engine.run(stock_codes, [strategy], daily)
            results[strategy.name] = {
                "sharpe_ratio": float(result.sharpe_ratio or 0.0),
                "total_return_pct": float((result.total_return or 0.0) * 100),
                "annual_return_pct": float(getattr(result, "annual_return", 0.0) or 0.0) * 100,
                "win_rate_pct": float((result.win_rate or 0.0) * 100),
                "max_drawdown_pct": float((result.max_drawdown or 0.0) * 100),
                "profit_loss_ratio": float(getattr(result, "profit_loss_ratio", 0.0) or 0.0),
                "excess_return_pct": float(getattr(result, "excess_return", 0.0) or 0.0) * 100,
                "trade_count": len(result.trades),
                "codes_count": len(daily),
            }
            logger.info(
                "backtest %s: sharpe=%.2f ret=%.2f%% trades=%d",
                strategy.name, result.sharpe_ratio, (result.total_return or 0) * 100,
                len(result.trades),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("backtest failed for %s: %s", strategy.name, exc)

    # SoftMax 权重（仅统计有交易的策略）
    weights = _compute_softmax_weights(results)

    if persist:
        _persist_results(results, weights)

    ranked = sorted(results.items(), key=lambda kv: kv[1]["sharpe_ratio"], reverse=True)
    return {"results": results, "ranked": ranked, "weights": weights}


def _compute_softmax_weights(results: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """仅对有交易的策略按 Sharpe SoftMax 归一化。"""
    import math

    valid = {n: r for n, r in results.items() if r.get("trade_count", 0) > 0}
    if not valid:
        return {}
    values = [r["sharpe_ratio"] for r in valid.values()]
    max_v = max(values)
    exps = [math.exp(v - max_v) for v in values]
    total = sum(exps)
    return {n: e / total for n, e in zip(valid.keys(), exps)}


def _persist_results(
    results: Dict[str, Dict[str, float]],
    weights: Dict[str, float],
) -> None:
    """回测结果 + 权重写入 strategy_backtest_results 表。"""
    from src.storage import DatabaseManager, StrategyBacktestResult

    db = DatabaseManager.get_instance()
    batch = date.today()
    stored = 0
    with db.get_session() as session:
        from sqlalchemy import select

        for name, r in results.items():
            try:
                existing = session.execute(
                    select(StrategyBacktestResult).where(
                        StrategyBacktestResult.strategy_name == name,
                        StrategyBacktestResult.batch_date == batch,
                        StrategyBacktestResult.eval_window_days == 250,
                        StrategyBacktestResult.engine_version == "v1",
                    )
                ).scalar_one_or_none()
                fields = dict(r)
                fields["fusion_weight"] = weights.get(name)
                fields["computed_at"] = datetime.now()
                fields["diagnostics_json"] = json.dumps(
                    {"codes_count": r.get("codes_count", 0)}, ensure_ascii=False
                )
                if existing is not None:
                    for k, v in fields.items():
                        setattr(existing, k, v)
                else:
                    session.add(StrategyBacktestResult(
                        strategy_name=name,
                        batch_date=batch,
                        eval_window_days=250,
                        engine_version="v1",
                        **fields,
                    ))
                stored += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("persist %s failed: %s", name, exc)
        session.commit()
    logger.info("strategy backtest results persisted: %s rows (batch=%s)", stored, batch)


def refresh_fusion_weights(
    *,
    batch_date: Optional[date] = None,
    persist: bool = True,
) -> Dict[str, float]:
    """重算并持久化融合权重（从最新回测批次读取 Sharpe → SoftMax）。

    Returns:
        {strategy_name: fusion_weight}
    """
    from src.storage import DatabaseManager, StrategyBacktestResult

    db = DatabaseManager.get_instance()
    batch = batch_date or date.today()
    with db.get_session() as session:
        from sqlalchemy import select

        rows = session.execute(
            select(StrategyBacktestResult).where(
                StrategyBacktestResult.batch_date == batch,
                StrategyBacktestResult.trade_count > 0,
            )
        ).scalars().all()
        sharpe_map = {r.strategy_name: float(r.sharpe_ratio or 0.0) for r in rows}
    if not sharpe_map:
        return {}
    weights = _compute_softmax_weights(
        {n: {"sharpe_ratio": s, "trade_count": 1} for n, s in sharpe_map.items()}
    )
    if persist:
        # 回填权重到该批次
        with db.get_session() as session:
            for name, w in weights.items():
                row = session.execute(
                    select(StrategyBacktestResult).where(
                        StrategyBacktestResult.strategy_name == name,
                        StrategyBacktestResult.batch_date == batch,
                    )
                ).scalar_one_or_none()
                if row is not None:
                    row.fusion_weight = w
            session.commit()
        logger.info("fusion weights refreshed (batch=%s): %s", batch, weights)
    return weights


def load_fusion_weights(batch_date: Optional[date] = None) -> Dict[str, float]:
    """从 DB 加载最新融合权重（listener 启动时调用）。

    返回空 dict 时调用方应保留默认权重（各策略 1.0）。
    """
    from src.storage import DatabaseManager, StrategyBacktestResult

    db = DatabaseManager.get_instance()
    try:
        with db.get_session() as session:
            from sqlalchemy import select, func

            if batch_date is None:
                latest = session.execute(
                    select(func.max(StrategyBacktestResult.batch_date))
                    .where(StrategyBacktestResult.fusion_weight.is_not(None))
                ).scalar()
                if latest is None:
                    return {}
                batch_date = latest
            rows = session.execute(
                select(StrategyBacktestResult).where(
                    StrategyBacktestResult.batch_date == batch_date,
                    StrategyBacktestResult.fusion_weight.is_not(None),
                )
            ).scalars().all()
            return {r.strategy_name: float(r.fusion_weight) for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_fusion_weights failed: %s", exc)
        return {}


def weekly_backtest_job() -> Dict[str, Any]:
    """每周重算任务：跑全量回测 → 落库 → 权重持久化。

    供 runtime_scheduler 注册（建议每周一次，如周日）。
    """
    logger.info("weekly strategy backtest job started")
    try:
        outcome = run_strategy_backtests(persist=True)
        weights = outcome.get("weights", {})
        logger.info("weekly backtest done: %s strategies, weights=%s",
                    len(outcome.get("results", {})), weights)
        return {"status": "ok", "strategies": len(outcome.get("results", {})), "weights": weights}
    except Exception as exc:  # noqa: BLE001
        logger.exception("weekly backtest job failed: %s", exc)
        return {"status": "error", "error": str(exc)}
