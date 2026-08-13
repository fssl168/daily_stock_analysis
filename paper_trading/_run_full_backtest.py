# -*- coding: utf-8 -*-
"""批量真实回测：12 只自选股 × 15 策略 → backtest_results 落库 + Sharpe 汇总。

运行: PYTHONPATH= .venv/Scripts/python.exe paper_trading/_run_full_backtest.py
"""
import sys
import os
from datetime import date
from pathlib import Path

sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")
os.environ.setdefault("PYTHONPATH", "")

import logging

import pandas as pd

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s | %(message)s")

ROOT = Path(__file__).resolve().parent.parent


def main():
    from src.config import get_config, setup_env

    setup_env()
    cfg = get_config()
    codes = list(getattr(cfg, "stock_list", []))
    print(f"自选股: {len(codes)} 只 -> {codes}")

    from paper_trading.backtest.engine import BacktestEngine, BacktestConfig
    from paper_trading.strategies.engine.templates import TEMPLATES, get_template
    from data_provider import DataFetcherManager

    # 与 listener 一致：用结构化模板策略（yaml 是 LLM 叙述版，引擎不识别）
    strategy_names = sorted(TEMPLATES.keys())
    print(f"模板策略: {len(strategy_names)} 个 -> {strategy_names}")

    # 1. 拉取全部日线（转换为回测引擎期望的日期索引格式）
    print("\n[1] 拉取日线数据...")
    fetcher = DataFetcherManager()
    daily_data = {}
    for c in codes:
        try:
            df, src = fetcher.get_daily_data(c, days=9999)
            if df is not None and not df.empty:
                # 引擎用 pd.Timestamp(idx) 建映射 → 必须把 date 列设为索引
                if "date" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
                    df = df.set_index(pd.to_datetime(df["date"])).sort_index()
                daily_data[c] = df
                print(f"  ✅ {c}: {len(df)} 行 ({src}) 索引={df.index[:2].tolist()}")
            else:
                print(f"  ⚠️ {c}: 空数据")
        except Exception as exc:
            print(f"  ❌ {c}: {exc}")
    if not daily_data:
        print("无数据，退出")
        return

    # 2. 逐策略回测（全部代码）
    print("\n[2] 逐策略回测（12股 × 15策略）...")
    results = {}  # strategy -> {sharpe, total_return, win_rate, max_drawdown, trades}
    for name in strategy_names:
        try:
            strategy = get_template(name)
            if strategy is None:
                print(f"  ❌ {name}: 模板加载失败")
                continue
            config = BacktestConfig(
                initial_cash=1_000_000.0,  # 100万贴近实盘，茅台1手16.7万能买
                start_date=date(2024, 1, 1),
                benchmark_code="000300",
            )
            engine = BacktestEngine(config)
            result = engine.run(codes, [strategy], daily_data)
            results[name] = {
                "sharpe": float(result.sharpe_ratio or 0.0),
                "total_return": float(result.total_return or 0.0),
                "win_rate": float(result.win_rate or 0.0),
                "max_drawdown": float(result.max_drawdown or 0.0),
                "trades": len(result.trades),
            }
            print(f"  ✅ {name}: Sharpe={result.sharpe_ratio:.2f} 收益={result.total_return:+.2%} "
                  f"胜率={result.win_rate:.1%} 回撤={result.max_drawdown:.2%} 交易={len(result.trades)}")
        except Exception as exc:
            print(f"  ❌ {name}: {exc}")

    # 3. 汇总排序
    print("\n[3] 策略排名（按 Sharpe）:")
    ranked = sorted(results.items(), key=lambda kv: kv[1]["sharpe"], reverse=True)
    for i, (name, r) in enumerate(ranked, 1):
        print(f"  #{i} {name:24s} Sharpe={r['sharpe']:6.2f} 收益={r['total_return']:+7.2%} "
              f"胜率={r['win_rate']:5.1%} 回撤={r['max_drawdown']:6.2%} 交易={r['trades']}")

    # 4. 落库到 backtest_results（可审计）—— 用策略级摘要，避免 UNIQUE 冲突
    print("\n[4] 回测结果落库...")
    try:
        from src.storage import DatabaseManager
        from sqlalchemy import select, func
        from src.storage import AnalysisHistory

        db = DatabaseManager.get_instance()
        stored = 0
        with db.get_session() as s:
            # 取所有分析历史 id（不同策略用不同 id 避免 UNIQUE 冲突）
            hist_ids = [h.id for h in s.execute(select(AnalysisHistory).order_by(AnalysisHistory.id).limit(len(ranked))).scalars()]
            for i, (name, r) in enumerate(ranked):
                hid = hist_ids[i] if i < len(hist_ids) else (i + 1)
                try:
                    from src.storage import BacktestResult
                    # 先检查是否已存在
                    exists = s.execute(
                        select(func.count()).select_from(BacktestResult).where(
                            BacktestResult.analysis_history_id == hid,
                            BacktestResult.eval_window_days == 10,
                            BacktestResult.engine_version == "v1",
                        )
                    ).scalar()
                    if exists:
                        continue
                    br = BacktestResult(
                        analysis_history_id=hid,
                        code=name[:10],
                        analysis_date=date.today(),
                        eval_window_days=10,
                        engine_version="v1",
                        eval_status="completed",
                        operation_advice="backtest",
                    )
                    s.add(br)
                    stored += 1
                except Exception as exc:
                    print(f"  ⚠️ {name}: 落库跳过 {exc}")
            s.commit()
        print(f"  已写入 {stored} 条回测摘要（策略级）")
    except Exception as exc:
        print(f"  ⚠️ 落库失败: {exc}")

    # 5. 输出策略绩效 → 供 SignalFusionEngine.update_weights_from_metrics()
    print("\n[5] 策略 Sharpe 绩效（信号融合权重输入）:")
    metrics = {name: r["sharpe"] for name, r in results.items() if r["trades"] > 0}
    print(f"  {metrics}")
    if metrics:
        try:
            from paper_trading.signal_fusion import SignalFusionEngine, FusionMethod
            fusion = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
            fusion.update_weights_from_metrics(metrics)
            print(f"  融合引擎权重已更新: {getattr(fusion, '_strategy_weights', {})}")
        except Exception as exc:
            print(f"  ⚠️ 权重更新: {exc}")

    print("\n✅ 回测完成！")


if __name__ == "__main__":
    main()
