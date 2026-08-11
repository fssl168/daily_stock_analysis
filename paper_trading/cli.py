# -*- coding: utf-8 -*-
"""Paper Trading CLI — 实时量化交易命令行接口。

This module defines the argparse subcommand tree for ``python main.py paper-trading``
and routes each command to the corresponding paper_trading module.
All commands reuse the existing paper_trading library — no new business logic.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI colours for terminal output
# ---------------------------------------------------------------------------

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BLUE = "\033[94m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_COLORS = dict(
    up=_GREEN, down=_RED, buy=_RED, sell=_GREEN,
    bid=_GREEN, ask=_RED, warn=_YELLOW, info=_CYAN,
    header=_BOLD, sub=_BLUE, reset=_RESET,
)


def color(label: str, text: Any) -> str:
    return f"{_COLORS.get(label, '')}{text}{_RESET}"


def _table(headers, rows, aligns=None):
    """Print an aligned table to stdout."""
    cols = []
    for i, h in enumerate(headers):
        a = (aligns or [])[i] if aligns and i < len(aligns) else "l"
        w = max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
        fmt = f"{{:<{w}}}" if a in ("l",) else f"{{:>{w}}}"
        cols.append((fmt, w))
    sep = "  "
    print(_BOLD + sep.join(c[0].format(h) for c, h in zip(cols, headers)) + _RESET)
    for row in rows:
        print(sep.join(c[0].format(str(row[i]) if i < len(row) else "-") for i, c in enumerate(cols)))

# ---------------------------------------------------------------------------
# Module-scoped entry point
# ---------------------------------------------------------------------------

def add_subparser(subparsers):
    """Register the ``paper-trading`` subcommand group on the root parser."""
    pt = subparsers.add_parser(
        "paper-trading",
        help="Paper Trading — 实时量化交易 CLI",
        description="实时量化交易命令行接口：账户管理、策略评估、回测、实时监听、风险监控、订单执行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _sub = pt.add_subparsers(dest="pt_command", title="子命令", metavar="<command>")

    _register_account(_sub)
    _register_strategy(_sub)
    _register_backtest(_sub)
    _register_listen(_sub)
    _register_order(_sub)
    _register_risk(_sub)
    _register_performance(_sub)
    _register_health(_sub)

    return pt


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

def _register_account(sub):
    acct = sub.add_parser("account", help="账户管理")
    a_sub = acct.add_subparsers(dest="pt_account_action", metavar="<action>")

    a_sub.add_parser("list", help="列出所有虚拟账户")
    a_sub.add_parser("create", help="创建虚拟账户").add_argument("--name", default="default")
    a_sub.add_parser("show", help="查看账户详情").add_argument("--account-id", type=int, required=True)
    a_sub.add_parser("positions", help="查看持仓").add_argument("--account-id", type=int, required=True)
    a_sub.add_parser("orders", help="查看委托").add_argument("--account-id", type=int, required=True)
    a_sub.add_parser("trades", help="查看成交").add_argument("--account-id", type=int, required=True)
    a_sub.add_parser("signals", help="查看信号").add_argument("--account-id", type=int, required=True)
    a_sub.add_parser("net-value", help="查看净值曲线").add_argument("--account-id", type=int, required=True)
    a_sub.add_parser("delete", help="删除账户").add_argument("--account-id", type=int, required=True)

    # --format flag for list-type commands
    for name in ("list", "show", "positions", "orders", "trades", "signals", "net-value"):
        p = a_sub._name_parser_map.get(name)
        if p is not None:
            p.add_argument("--format", choices=("table", "json"), default="table", help="输出格式")


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

def _register_strategy(sub):
    st = sub.add_parser("strategy", help="策略管理")
    s_sub = st.add_subparsers(dest="pt_strategy_action", metavar="<action>")

    s_sub.add_parser("list", help="列出所有策略")
    s_sub.add_parser("show", help="查看策略详情").add_argument("--name", required=True)
    s_sub.add_parser("scaffold", help="创建策略模板").add_argument("--name", required=True)
    eval_parser = s_sub.add_parser("evaluate", help="评估策略")
    eval_parser.add_argument("--name", required=True)
    eval_parser.add_argument("--code", required=True)
    s_sub.add_parser("import", help="导入策略").add_argument("--file", required=True)
    s_sub.add_parser("lifecycle", help="策略生命周期").add_argument("--account-id", type=int, required=True)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def _register_backtest(sub):
    bt = sub.add_parser("backtest", help="回测分析")
    b_sub = bt.add_subparsers(dest="pt_backtest_action", metavar="<action>")

    run_parser = b_sub.add_parser("run", help="运行回测")
    run_parser.add_argument("--strategy", required=True)
    run_parser.add_argument("--codes", required=True, help="逗号分隔股票代码")
    run_parser.add_argument("--start")
    run_parser.add_argument("--end")
    run_parser.add_argument("--capital", type=float, default=100_000.0)
    run_parser.add_argument("--benchmark", default="000300")

    b_sub.add_parser("list", help="列出回测历史")
    b_sub.add_parser("result", help="查看回测结果").add_argument("--result-id", type=int)

    walk = b_sub.add_parser("walk-forward", help="Walk-forward 优化")
    walk.add_argument("--strategy", required=True)
    walk.add_argument("--code", required=True)
    walk.add_argument("--train-days", type=int, default=504)
    walk.add_argument("--test-days", type=int, default=126)
    walk.add_argument("--step-days", type=int, default=63)

    gs = b_sub.add_parser("grid-search", help="参数网格搜索")
    gs.add_argument("--strategy", required=True)
    gs.add_argument("--code", required=True)
    gs.add_argument("--params", required=True, help="JSON 字符串, e.g. '{\"fast\": [5,10,20], \"slow\": [20,30,50]}'")

    b_sub.add_parser("compare", help="回测 vs 纸面对比").add_argument("--account-id", type=int, required=True)


# ---------------------------------------------------------------------------
# Listen
# ---------------------------------------------------------------------------

def _register_listen(sub):
    ls = sub.add_parser("listen", help="实时行情监听")
    l_sub = ls.add_subparsers(dest="pt_listen_action", metavar="<action>")

    start = l_sub.add_parser("start", help="启动监听")
    start.add_argument("--account-id", type=int, required=True)
    start.add_argument("--daemon", action="store_true", help="后台模式")
    start.add_argument("--interactive", action="store_true", help="交互 watch 模式")

    l_sub.add_parser("status", help="查看监听状态")
    l_sub.add_parser("stop", help="停止监听")
    l_sub.add_parser("restart", help="重启监听").add_argument("--account-id", type=int, required=True)


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

def _register_order(sub):
    od = sub.add_parser("order", help="订单管理")
    o_sub = od.add_subparsers(dest="pt_order_action", metavar="<action>")

    submit = o_sub.add_parser("submit", help="提交订单")
    submit.add_argument("--account-id", type=int, required=True)
    submit.add_argument("--side", required=True, choices=("buy", "sell"))
    submit.add_argument("--code", required=True)
    submit.add_argument("--price", type=float, required=True)
    submit.add_argument("--quantity", type=float, required=True)
    submit.add_argument("--order-type", default="market", choices=("market", "limit"))
    submit.add_argument("--reason", default="CLI manual signal")

    o_sub.add_parser("list", help="查看挂单").add_argument("--account-id", type=int, required=True)
    o_sub.add_parser("cancel", help="撤单").add_argument("--order-id", type=int, required=True)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

def _register_risk(sub):
    rk = sub.add_parser("risk", help="风控与监控")
    r_sub = rk.add_subparsers(dest="pt_risk_action", metavar="<action>")

    r_sub.add_parser("breaker", help="熔断状态").add_argument("--account-id", type=int, required=True)
    r_sub.add_parser("breaker-reset", help="重置熔断").add_argument("--account-id", type=int, required=True)
    r_sub.add_parser("var", help="VaR 报告").add_argument("--account-id", type=int, required=True)
    r_sub.add_parser("liquidity", help="流动性风险").add_argument("--account-id", type=int, required=True)
    r_sub.add_parser("anomaly", help="市场异常").add_argument("--account-id", type=int, required=True)
    r_sub.add_parser("extreme-market", help="极端行情").add_argument("--account-id", type=int, default=1)
    r_sub.add_parser("latency", help="延迟监控").add_argument("--account-id", type=int, required=True)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

def _register_performance(sub):
    pr = sub.add_parser("performance", help="性能分析")
    p_sub = pr.add_subparsers(dest="pt_performance_action", metavar="<action>")

    p_sub.add_parser("metrics", help=" 绩效指标").add_argument("--account-id", type=int, required=True)
    p_sub.add_parser("drawdown", help="回撤曲线").add_argument("--account-id", type=int, required=True)
    p_sub.add_parser("leaderboard", help="策略排名").add_argument("--account-id", type=int, required=True)
    p_sub.add_parser("drift", help="漂移检测").add_argument("--account-id", type=int, required=True)
    p_sub.add_parser("features", help="特征工程").add_argument("--account-id", type=int, required=True)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def _register_health(sub):
    p = sub.add_parser("health", help="系统健康检查").add_argument("--format", choices=("table", "json"), default="table")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(args) -> int:
    """Route the parsed CLI args to the matching command handler."""
    cmd = getattr(args, "pt_command", "")
    handlers: Dict[str, Callable] = {
        "account": _handle_account,
        "strategy": _handle_strategy,
        "backtest": _handle_backtest,
        "listen": _handle_listen,
        "order": _handle_order,
        "risk": _handle_risk,
        "performance": _handle_performance,
        "health": _handle_health,
    }
    handler = handlers.get(cmd)
    if handler is None:
        print(f"未知命令: {cmd}。使用 --help 查看子命令。", file=sys.stderr)
        return 1
    return handler(args)


# ---------------------------------------------------------------------------
# Account handlers
# ---------------------------------------------------------------------------

def _handle_account(args) -> int:
    from paper_trading.account import PaperAccountManager
    from paper_trading.order import OrderManager
    from paper_trading.position import PositionManager
    from src.storage import get_db

    db = get_db()
    acct_mgr = PaperAccountManager(db)
    action = getattr(args, "pt_account_action", "")
    out_fmt = getattr(args, "format", "table")

    if action == "list":
        accts = acct_mgr.list_accounts() if hasattr(acct_mgr, "list_accounts") else []
        if out_fmt == "json":
            import json
            print(json.dumps([a.to_dict() if hasattr(a, "to_dict") else {"id": getattr(a, "id")} for a in accts], indent=2))
        else:
            if not accts:
                print("暂无虚拟账户。")
                return 0
            snapshots = []
            for a in accts:
                try:
                    s = acct_mgr.snapshot(a.id)
                    snapshots.append(s)
                except Exception:
                    snapshots.append(None)
            _table(
                ["ID", "名称", "本金", "现金", "净值", "收益率%", "持仓数", "状态"],
                [
                    [a.id, s.name if s else a.name,
                     f"{s.initial_capital:,.2f}" if s else f"{getattr(a, 'initial_capital', a.cash):,.2f}",
                     f"{s.cash:,.2f}" if s else f"{getattr(a, 'cash', 0):,.2f}",
                     f"{s.total_assets:,.2f}" if s else "-",
                     f"{s.pnl_pct:+.2f}" if s else "-",
                     len(getattr(s, 'config', {}).get('positions', [])) if s else "-",
                     s.status if s else getattr(a, 'status', '-')]
                    for a, s in zip(accts, snapshots)
                ]
            )
        return 0

    if action == "create":
        name = getattr(args, "name", "default")
        capital = getattr(args, "initial_capital", 1000.0) if hasattr(args, "initial_capital") else 1000.0
        account = acct_mgr.get_or_create_account(name=name, initial_capital=capital)
        print(f"{color('up', '✓')} 账户创建成功: id={account.id} name={name} capital={capital:,.2f}")
        return 0

    aid = getattr(args, "account_id", None)
    if aid is None:
        print(f"{color('warn', '✗')} 缺少 --account-id", file=sys.stderr)
        return 1

    if action == "show":
        snap = acct_mgr.snapshot(aid)
        if out_fmt == "json":
            import json
            print(json.dumps(snap.to_dict(), indent=2))
        else:
            print(f"""{_BOLD}账户 #{aid}{_RESET}
  名称:    {snap.name}
  初始本金: {snap.initial_capital:,.2f}
  现金:     {snap.cash:,.2f}
  冻结:     {snap.frozen_cash:,.2f}
  市值:     {snap.total_market_value:,.2f}
  净值:     {snap.net_value:,.2f}
  收益率:   {color('up' if snap.return_pct >= 0 else 'down', f'{snap.return_pct:+.2f}%')}
  持仓数:   {snap.position_count}
  状态:     {snap.status}""")
        return 0

    if action == "positions":
        pmgr = PositionManager(db)
        positions = pmgr.list_positions(aid)
        if out_fmt == "json":
            import json
            print(json.dumps(positions, indent=2, default=str))
        else:
            if not positions:
                print("暂无持仓。")
                return 0
            _table(
                ["代码", "名称", "数量", "可用", "成本价", "现价", "浮动盈亏", "盈亏%"],
                [
                    [p.get("code"), p.get("name"), p.get("available_quantity"),
                     p.get("available_quantity"), f"{p.get('avg_cost', 0):.2f}",
                     f"{p.get('last_price', 0):.2f}",
                     color("up" if float(p.get("floating_pnl", 0)) >= 0 else "down",
                           f"{float(p.get('floating_pnl', 0)):+,.2f}"),
                     color("up" if float(p.get("floating_pnl_pct", 0)) >= 0 else "down",
                           f"{float(p.get('floating_pnl_pct', 0)):+.1f}%")]
                    for p in positions
                ]
            )
        return 0

    if action in ("orders", "trades", "signals"):
        omgr = OrderManager(db)
        if action == "orders":
            items = omgr.list_orders(aid) if hasattr(omgr, "list_orders") else omgr.get_orders(aid)
            headers = ["ID", "代码", "方向", "类型", "价格", "数量", "已成交", "状态", "时间"]
            rows = [
                [o.id, o.code, o.side, o.order_type, f"{o.price:.2f}", f"{o.quantity:.0f}",
                 f"{o.filled_quantity:.0f}", o.status, str(o.created_at)[:16]]
                for o in items
            ]
        elif action == "trades":
            items = list(omgr._list_trades(aid)) if hasattr(omgr, "_list_trades") else []
            headers = ["ID", "代码", "方向", "成交价", "数量", "金额", "手续费", "时间"]
            rows = [
                [t.id, t.code, t.side, f"{t.price:.2f}", f"{t.quantity:.0f}",
                 f"{t.amount:.2f}", f"{t.fee:.2f}", str(t.traded_at)[:16]]
                for t in items
            ]
        else:  # signals
            items = list(omgr._list_signals_memory(aid)) if hasattr(omgr, "_list_signals_memory") else omgr.list_signals(aid) if hasattr(omgr, "list_signals") else []
            headers = ["ID", "代码", "方向", "触发价", "策略", "状态", "AI确认", "时间"]
            rows = [
                [s.id, s.code, s.side, f"{s.trigger_price:.2f}", s.strategy_name,
                 s.status, "✓" if getattr(s, "agent_confirmed", None) else "-",
                 str(s.created_at)[:16]]
                for s in items
            ]
        if out_fmt == "json":
            import json
            print(json.dumps([dict(id=int(r[0])) if not isinstance(r, dict) else r for r in rows], indent=2, default=str))
        else:
            if not rows:
                print(f"暂无{action}记录。")
            else:
                _table(headers, rows)
        return 0

    if action == "net-value":
        from paper_trading.account import PaperAccountManager as PAM
        import pandas as pd
        # delegate to the existing net-value query
        print(f"{color('info', '→')} 净值曲线（调用 API 或本地数据）: account_id={aid}")
        return 0

    if action == "delete":
        print(f"{color('warn', '⚠')} 删除账户 {aid} 操作暂未实现。")
        return 0

    print(f"{color('warn', '?')} 未知账户操作: {action}")
    return 1


# ---------------------------------------------------------------------------
# Strategy handlers
# ---------------------------------------------------------------------------

def _handle_strategy(args) -> int:
    action = getattr(args, "pt_strategy_action", "")
    from pathlib import Path

    if action == "list":
        strat_dir = (
            Path(__file__).resolve().parent / "strategies" / "configs"
        )
        if not strat_dir.exists():
            print("策略目录不存在。")
            return 1
        yamls = sorted(strat_dir.glob("*.yaml"))
        for y in yamls:
            name = y.stem
            desc = ""
            try:
                import yaml
                data = yaml.safe_load(y.read_text(encoding="utf-8"))
                desc = data.get("display_name", data.get("description", "")) or ""
            except Exception:
                pass
            print(f"  {_BOLD}{name}{_RESET}  {desc}")
        print(f"\n共 {len(yamls)} 个策略。")
        return 0

    if action == "show":
        name = getattr(args, "name", "")
        path = Path(__file__).parent / "paper_trading" / "strategies" / "configs" / f"{name}.yaml"
        if not path.exists():
            print(f"策略 '{name}' 不存在。")
            return 1
        print(path.read_text(encoding="utf-8"))
        return 0

    if action == "evaluate":
        name = getattr(args, "name", "")
        code = getattr(args, "code", "")
        from paper_trading.strategies.engine.rule_engine import RuleEngine
        from paper_trading.strategies.engine.schema import load_strategy
        from data_provider import DataFetcherManager

        strategy = load_strategy(name)
        if strategy is None:
            print(f"无法加载策略: {name}")
            return 1
        fetcher = DataFetcherManager()
        df, _ = fetcher.get_daily_data(code, days=60)
        if df is None or df.empty:
            print(f"{code} 无日线数据。")
            return 1
        engine = RuleEngine()
        signal = engine.evaluate(strategy, df, code=code)
        side_color = "up" if signal.side == "buy" else "down" if signal.side == "sell" else "info"
        print(f"""{_BOLD}{name}{_RESET} → {code}
  方向:       {color(side_color, signal.side.upper())}
  触发价格:   {signal.trigger_price}
  建议数量:   {signal.suggested_quantity}
  规则名:     {signal.rule_name}
  理由:       {signal.reason}""")
        return 0

    if action == "scaffold":
        name = getattr(args, "name", "untitled")
        template = f"""name: {name}
display_name: {name.replace('_', ' ').title()}
description: 自定义策略
timeframes: [1d]
indicators:
  - name: ma5
    spec: MA({5})
  - name: ma20
    spec: MA({20})
entry_rules:
  - left: ma5
    op: cross_up
    right: ma20
exit_rules:
  - left: ma5
    op: cross_down
    right: ma20
"""
        path = Path(__file__).parent / "paper_trading" / "strategies" / "configs" / f"{name}.yaml"
        path.write_text(template, encoding="utf-8")
        print(f"{color('up', '✓')} 策略模板创建: {path}")
        return 0

    if action == "import":
        filepath = Path(getattr(args, "file", ""))
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            return 1
        dest = Path(__file__).parent / "paper_trading" / "strategies" / "configs" / filepath.name
        dest.write_text(filepath.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"{color('up', '✓')} 策略导入完成: {dest}")
        return 0

    if action == "lifecycle":
        from paper_trading.strategy_lifecycle import StrategyLifecycle
        lc = StrategyLifecycle()
        strategies = lc.list_strategies()
        if not strategies:
            print("暂无策略生命周期数据。")
            return 0
        for name, state in strategies.items():
            print(f"  {_BOLD}{name}{_RESET} → {state}")
        return 0

    print(f"{color('warn', '?')} 未知策略操作: {action}")
    return 1


# ---------------------------------------------------------------------------
# Backtest / Listen / Order / Risk / Performance / Health stubs
# (Phase 2-4 implementation — see plan)
# ---------------------------------------------------------------------------

def _handle_backtest(args) -> int:
    action = getattr(args, "pt_backtest_action", "")
    if action == "run":
        from pathlib import Path
        from paper_trading.backtest.engine import BacktestEngine, BacktestConfig
        from paper_trading.strategies.engine.schema import load_strategy
        from data_provider import DataFetcherManager

        strategy = load_strategy(
            Path(__file__).resolve().parent / "strategies" / "configs" / f"{getattr(args, 'strategy', '')}.yaml"
        )
        if strategy is None:
            print(f"无法加载策略: {getattr(args, 'strategy', '')}")
            return 1
        codes = [c.strip() for c in getattr(args, "codes", "").split(",") if c.strip()]
        config = BacktestConfig(
            initial_cash=getattr(args, "capital", 100_000.0),
            start_date=date.fromisoformat(getattr(args, "start", "2023-01-01")) if getattr(args, "start", None) else None,
            end_date=date.fromisoformat(getattr(args, "end", "")) if getattr(args, "end", None) else None,
            benchmark_code=getattr(args, "benchmark", "000300"),
        )
        fetcher = DataFetcherManager()
        daily_data = {}
        for c in codes:
            try:
                df, src = fetcher.get_daily_data(c, days=9999)
                if df is not None and not df.empty:
                    daily_data[c] = df
            except Exception as exc:
                print(f"{color('warn', '⚠')} {c}: 数据获取失败 — {exc}")
        if not daily_data:
            print("无可用日线数据（所有代码数据源均失败）。")
            return 1
        engine = BacktestEngine(config)
        result = engine.run(codes, [strategy], daily_data)
        # Terminal formatted output
        is_up = result.total_return >= 0
        msg = f"""
{_BOLD}{'='*60}{_RESET}
  {_BOLD}回测完成{_RESET} — {getattr(args, 'strategy')} × {getattr(args, 'codes')}

  总收益率:      {color('up' if is_up else 'down', f'{result.total_return:+.2%}')}
  年化收益率:    {f'{result.annual_return:+.2%}'}
  Sharpe 比率:   {f'{result.sharpe_ratio:.2f}'}
  Calmar 比率:   {f'{result.calmar_ratio:.2f}'}
  最大回撤:      {color('down', f'{result.max_drawdown:.2%}')}
  胜率:          {f'{result.win_rate:.1%}'}
  盈亏比:        {f'{result.profit_loss_ratio:.2f}'}
  超额收益:      {f'{result.excess_return:+.2%}'}
  交易笔数:      {len(result.trades)}
{_BOLD}{'='*60}{_RESET}
"""
        print(msg)
        return 0
    if action == "list":
        from src.storage import get_db
        from paper_trading.backtest.engine import BacktestEngine
        from paper_trading.account import PaperAccountManager
        print("回测历史查询将在后端接入 BacktestEngine 持久化存储后可用。")
        return 0
    if action == "result":
        rid = getattr(args, "result_id", 0)
        if rid <= 0:
            print(f"请指定有效的 --result-id。")
            return 1
        from src.storage import DatabaseManager
        db = DatabaseManager()
        with db.session_scope() as session:
            from sqlalchemy import select
            from src.storage import PaperNetValue, PaperTrade
            rows = session.execute(select(PaperNetValue).where(PaperNetValue.account_id == 42).limit(5)).all()
            if rows:
                _table(["日期", "净值"], [[r[0].date, f"{r[0].net_value:.4f}"] for r in rows])
            else:
                print(f"回测结果 #{rid} 暂无数据。")
        return 0
    if action == "walk-forward":
        from pathlib import Path
        from paper_trading.backtest.walkforward import WalkforwardOptimizer, WalkforwardConfig
        from paper_trading.strategies.engine.schema import load_strategy
        strategy = load_strategy(
            Path(__file__).resolve().parent / "strategies" / "configs" / f"{getattr(args, 'strategy', '')}.yaml"
        )
        code = getattr(args, "code", "")
        from data_provider import DataFetcherManager
        fetcher = DataFetcherManager()
        try:
            df, _ = fetcher.get_daily_data(code, days=9999)
        except Exception:
            print(f"{code} 数据不可用。")
            return 1
        cfg = WalkforwardConfig(
            train_window_days=getattr(args, "train_days", 504),
            test_window_days=getattr(args, "test_days", 126),
            step_days=getattr(args, "step_days", 63),
        )
        opt = WalkforwardOptimizer()
        result = opt.run(strategy, df, cfg)
        print(f"{_BOLD}Walk-forward 结果{_RESET}")
        print(f"  窗口数:   {len(result.windows)}")
        if hasattr(result, "out_of_sample_sharpe"):
            print(f"  样本外 Sharpe: {result.out_of_sample_sharpe:.2f}")
        print(f"  最优参数: {result.best_params}")
        return 0
    if action == "grid-search":
        import json as _json
        from paper_trading.backtest.engine import BacktestEngine, BacktestConfig
        from paper_trading.strategies.engine.schema import load_strategy
        import itertools
        strategy = load_strategy(
            Path(__file__).resolve().parent / "strategies" / "configs" / f"{getattr(args, 'strategy', '')}.yaml"
        )
        code = getattr(args, "code", "")
        params_str = getattr(args, "params", "{}")
        param_grid = _json.loads(params_str)
        from data_provider import DataFetcherManager
        fetcher = DataFetcherManager()
        try:
            df, _ = fetcher.get_daily_data(code, days=9999)
        except Exception:
            print(f"{code} 数据不可用。")
            return 1
        keys = list(param_grid.keys())
        values = [param_grid[k] for k in keys]
        best_sharpe = -999
        best_combo = None
        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            config = BacktestConfig(initial_cash=100_000)
            engine = BacktestEngine(config)
            result = engine.run([code], [strategy], {code: df})
            if result.sharpe_ratio > best_sharpe:
                best_sharpe = result.sharpe_ratio
                best_combo = (params, result)
        if best_combo:
            params, res = best_combo
            print(f"{_BOLD}网格搜索最优{_RESET}")
            print(f"  参数:  {params}")
            print(f"  Sharpe: {res.sharpe_ratio:.2f}")
            print(f"  总收益: {res.total_return:+.2%}")
        return 0
    if action == "compare":
        aid = getattr(args, "account_id", 1)
        from paper_trading.backtest_adapter import backtest_from_paper_account
        print(f"回测 vs 纸面对比 (account_id={aid}) 需指定策略和日期范围。")
        return 0
    return 1


def _handle_listen(args) -> int:
    action = getattr(args, "pt_listen_action", "")
    if action == "start":
        return _start_listener(args)
    if action == "status":
        from src.config import get_config
        from paper_trading.market_listener import build_default_listener
        print(f"{color('info', '→')} 监听状态: 需在运行中的监听器实例上查询。")
        return 0
    if action == "stop":
        from pathlib import Path
        import signal as _sig, os as _os
        pid_file = Path("/tmp/paper_trading_listener.pid")
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            print(f"{color('info', '→')} 发送停止信号到 PID {pid}…")
            try:
                _os.kill(pid, _sig.SIGTERM)
                pid_file.unlink()
                print(f"{color('up', '✓')} 已发送停止信号。")
            except ProcessLookupError:
                print(f"{color('warn', '!')} PID {pid} 不存在，清理 pid 文件。")
                pid_file.unlink()
            except PermissionError:
                print(f"{color('warn', '✗')} 无权限操作 PID {pid}。")
        else:
            print(f"{color('warn', '!')} 未找到运行中的监听器（PID 文件不存在）。")
        return 0
    if action == "restart":
        from pathlib import Path
        pid_file = Path("/tmp/paper_trading_listener.pid")
        if pid_file.exists():
            try:
                import signal, os
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
                pid_file.unlink()
            except Exception:
                pass
        return _start_listener(args, "restart")
    return 1


def _start_listener(args, mode="start") -> int:
    from pathlib import Path
    from paper_trading.market_listener import build_default_listener
    from src.config import get_config
    aid = getattr(args, "account_id", 1)
    cfg = get_config()
    listener = build_default_listener(cfg, account_id=aid)
    listener.start()
    print(f"{color('up', '✓')} 实时监听已启动 (account_id={aid}, mode={mode})。")
    if getattr(args, "daemon", False):
        import os as _os_d
        from pathlib import Path
        pid_file = Path("/tmp/paper_trading_listener.pid")
        pid_file.write_text(str(_os_d.getpid()))
        print(f"  PID: {_os_d.getpid()} (写入 {pid_file})")
        print(f"  PID: {os.getpid()} (写入 {pid_file})")
        print(f"  使用 'python main.py paper-trading listen stop' 来停止。")
    if getattr(args, "interactive", False):
        print(f"{_BOLD}交互模式{_RESET}（输入 help 查看命令）：")
        import threading
        _stop_flag = threading.Event()
        def _interact():
            cmds = {
                "status": lambda: print(f"  running={listener.is_running()}"),
                "pause": lambda: print("  ⚠ 暂停功能需要在策略级别实现。"),
                "breaker": lambda: _handle_risk(type("A", (), {"pt_risk_action": "breaker", "account_id": aid})),
                "latency": lambda: _handle_risk(type("A", (), {"pt_risk_action": "latency", "account_id": aid})),
                "positions": lambda: _handle_account(type("A", (), {"pt_account_action": "positions", "account_id": aid, "format": "table"})),
                "help": lambda: print("  命令: status, pause, breaker, latency, positions, help, quit"),
            }
            while not _stop_flag.is_set():
                try:
                    line = input("> ").strip()
                    if line == "quit":
                        _stop_flag.set()
                        break
                    if line in cmds:
                        cmds[line]()
                    else:
                        print(f"  未知命令: {line}")
                except (EOFError, KeyboardInterrupt):
                    _stop_flag.set()
        threading.Thread(target=_interact, daemon=True).start()
    try:
        while True:
            if not listener.is_running():
                print("监听器已退出。")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n{color('warn', '!')} 收到中断信号，正在关闭…")
        listener.stop()
        print("监听器已关闭。")
    return 0


def _handle_order(args) -> int:
    action = getattr(args, "pt_order_action", "")
    if action == "submit":
        from src.storage import get_db
        from paper_trading.account import PaperAccountManager
        from paper_trading.trading_engine import TradingEngine, OrderType
        from paper_trading.strategies.engine.rule_engine import Signal as _Sig

        aid = getattr(args, "account_id", 1)
        engine = TradingEngine(db_manager=get_db(), account_manager=PaperAccountManager(get_db()))
        signal = _Sig(
            side=getattr(args, "side", "buy"),
            code=getattr(args, "code", "").upper(),
            trigger_price=getattr(args, "price", 0.0),
            suggested_quantity=getattr(args, "quantity", 0.0),
            strategy_name="cli_manual",
            rule_name="manual_signal",
            reason=getattr(args, "reason", "CLI manual signal"),
        )
        ot = OrderType.MARKET if getattr(args, "order_type", "market") == "market" else OrderType.LIMIT
        result = engine.submit_signal(aid, signal, order_type=ot)
        status_color = "up" if result.status in ("executed", "filled") else "down" if result.status == "rejected" else "info"
        print(f"""{_BOLD}订单提交{_RESET}
  信号ID:   {result.signal_id}
  订单ID:   {result.order_id}
  状态:     {color(status_color, result.status)}
  成交价:   {result.fill_price or '-'}
  成交量:   {result.fill_quantity or '-'}
  手续费:   {result.fee or '-'}
  原因:     {result.reason}""")
        return 0
    if action == "list":
        from paper_trading.order import OrderManager
        from src.storage import get_db
        omgr = OrderManager(get_db())
        aid = getattr(args, "account_id", 1)
        orders = list(omgr.get_orders(aid)) if hasattr(omgr, "get_orders") else []
        if not orders:
            print("暂无挂单。")
        else:
            _table(
                ["ID", "代码", "方向", "价格", "数量", "已成交", "状态"],
                [[o.id, o.code, o.side, f"{o.price:.2f}", f"{o.quantity:.0f}",
                  f"{o.filled_quantity:.0f}", o.status] for o in orders]
            )
        return 0
    if action == "cancel":
        oid = getattr(args, "order_id", 0)
        from paper_trading.order import OrderManager
        from src.storage import get_db
        try:
            omgr = OrderManager(get_db())
            result = omgr.cancel_order(oid, reason="CLI cancel")
            cancel_status = getattr(result, "status", "canceled") if result else "canceled"
            print(f"{color('up', '✓')} 订单 #{oid} 已撤单。状态: {cancel_status}")
        except Exception as exc:
            print(f"{color('warn', '✗')} 撤单失败: {exc}")
        return 0
    return 1


def _handle_risk(args) -> int:
    action = getattr(args, "pt_risk_action", "")
    aid = getattr(args, "account_id", 1)

    if action == "breaker":
        from paper_trading.circuit_breaker import CircuitBreaker, BreakerConfig
        cb = CircuitBreaker(BreakerConfig(), aid)
        print(f"熔断状态: {cb.state.level.value}")
        return 0
    if action == "breaker-reset":
        try:
            from paper_trading.circuit_breaker import BreakerConfig, CircuitBreaker
            cb = CircuitBreaker(BreakerConfig(), aid)
            cb.reset_daily()
            print(f"{color('up', '✓')} 熔断已重置 (account_id={aid})。当前级别: {cb.state.level.value}")
        except Exception as exc:
            print(f"{color('warn', '⚠')} 熔断重置失败: {exc}")
        return 0
    if action in ("var", "liquidity", "anomaly"):
        try:
            from paper_trading.risk_daemon import RiskDaemon
            from paper_trading.account import PaperAccountManager
            from src.storage import get_db
            acct_mgr = PaperAccountManager(get_db())
            account = acct_mgr.snapshot(aid)
            positions = acct_mgr.list_accounts(limit=1)  # fallback
            rd = RiskDaemon(aid)
            # Build a minimal MarketAnomalyDetector-style check
            if action == "var":
                from paper_trading.risk_daemon import VaRMonitor, VaRResult
                varm = VaRMonitor()
                print(f"VaR monitor 已初始化 (account_id={aid})。需要持仓数据 + 价格快照才能给出具体 VaR 数值。")
            elif action == "liquidity":
                from paper_trading.risk_daemon import LiquidityMonitor, LiquidityRisk
                liqm = LiquidityMonitor()
                print(f"流动性监控已初始化 (account_id={aid})。需要持仓数据 + 换手率/买卖价差数据才能给出具体风险指标。")
            elif action == "anomaly":
                from paper_trading.risk_daemon import MarketAnomalyDetector
                anom = MarketAnomalyDetector()
                print(f"市场异常检测器已初始化 (account_id={aid})。需要指数日线数据才能判断波动率尖峰。")
        except Exception as exc:
            print(f"{color('warn', '⚠')} {action} 模块加载失败: {exc}")
        return 0
    if action == "extreme-market":
        try:
            from paper_trading.extreme_market import ExtremeMarketDetector
            em = ExtremeMarketDetector()
            print(f"极端行情检测器已初始化。当前需要指数日线数据（如沪深300）才能做出判断。")
        except Exception as exc:
            print(f"{color('warn', '⚠')} 极端行情检测器加载失败: {exc}")
        return 0
    if action == "latency":
        try:
            from src.utils.latency_tracker import LatencyTracker
            lt = LatencyTracker()
            report = lt.report()
            if not report:
                print("暂无延迟数据。实时监听器启动后会自动记录每 tick 耗时。")
            else:
                _table(
                    ["操作", "p50 ms", "p95 ms", "p99 ms"],
                    [[r.get("operation", ""), r.get("p50", "-"), r.get("p95", "-"), r.get("p99", "-")]
                     for r in report]
                )
        except Exception as exc:
            print(f"{color('warn', '⚠')} 延迟数据加载失败: {exc}")
        return 0
    return 1


def _handle_performance(args) -> int:
    action = getattr(args, "pt_performance_action", "")
    aid = getattr(args, "account_id", 1)

    if action == "metrics":
        try:
            from paper_trading.performance import PerformanceAnalyzer
            ana = PerformanceAnalyzer()
            metrics = ana.calculate(aid)
            is_up = metrics.total_return_pct >= 0
            print(f"""
{_BOLD}{'='*60}{_RESET}
  {_BOLD}绩效指标{_RESET} — account_id={aid}

  总收益率:       {color('up' if is_up else 'down', f'{metrics.total_return_pct:+.2f}%')}
  年化收益率:     {f'{metrics.annualized_return_pct:+.2f}%'}
  Sharpe 比率:    {f'{metrics.sharpe_ratio:.2f}' if metrics.sharpe_ratio is not None else '-'}
  Calmar 比率:    {f'{metrics.calmar_ratio:.2f}' if metrics.calmar_ratio is not None else '-'}
  最大回撤:       {color('down', f'{metrics.max_drawdown_pct:.2f}%')}
  胜率:           {f'{metrics.win_rate:.1%}'}
  盈亏比:         {f'{metrics.profit_factor:.2f}' if metrics.profit_factor is not None else '-'}
  平均盈利:       {f'{metrics.avg_win:+,.2f}'}
  平均亏损:       {f'{metrics.avg_loss:+,.2f}'}
  交易笔数:       {metrics.trade_count} (胜{metrics.win_count} / 负{metrics.loss_count})
{_BOLD}{'='*60}{_RESET}
""")
        except Exception as exc:
            print(f"{color('warn', '⚠')} 绩效指标计算失败 (account_id={aid}): {exc}")
        return 0

    if action == "drawdown":
        try:
            from paper_trading.performance import PerformanceAnalyzer
            ana = PerformanceAnalyzer()
            metrics = ana.calculate(aid)
            print(f"最大回撤: {color('down', f'{metrics.max_drawdown_pct:.2f}%')}")
            if metrics.max_drawdown_start_date:
                print(f"  开始: {metrics.max_drawdown_start_date}")
            if metrics.max_drawdown_end_date:
                print(f"  结束: {metrics.max_drawdown_end_date}")
        except Exception as exc:
            print(f"{color('warn', '⚠')} 回撤数据计算失败: {exc}")
        return 0

    if action == "leaderboard":
        try:
            from paper_trading.strategy_lifecycle import StrategyLifecycle
            from paper_trading.signal_fusion import SignalFusionEngine, FusionMethod
            lc = StrategyLifecycle()
            strategies = lc.list_strategies()
            if not strategies:
                print("暂无策略数据。")
            else:
                _table(
                    ["策略", "状态", "审批数"],
                    [[name, state, str(lc.get_approval_history(name))] for name, state in strategies.items()]
                )
        except Exception as exc:
            print(f"{color('warn', '⚠')} 策略排行榜加载失败: {exc}")
        return 0

    if action == "drift":
        try:
            from paper_trading.drift_detector import DriftDetector
            dd = DriftDetector()
            print("漂移检测器已初始化。需要策略日 PnL 数据（实时监听器自动记录）才能做出判断。")
        except Exception as exc:
            print(f"{color('warn', '⚠')} 漂移检测器加载失败: {exc}")
        return 0

    if action == "features":
        try:
            from paper_trading.features.pipeline import FeaturePipeline, FeatureConfig
            fp = FeaturePipeline([])
            print(f"特征工程管线已初始化。已注册特征: {fp.registry.registered_names()}")
        except Exception as exc:
            print(f"{color('warn', '⚠')} 特征工程加载失败: {exc}")
        return 0

    return 1


def _handle_health(args) -> int:
    fmt = getattr(args, "format", "table")
    if fmt == "json":
        import json
        results = {}
        try:
            from src.utils.exchange_clock import ExchangeClock
            results["ntp_synced"] = ExchangeClock.is_synced()
        except Exception:
            results["ntp_synced"] = "unavailable"
        try:
            from src.services.health_check import check_system_resources, check_task_queue
            sys_health = check_system_resources()
            results["system_resources"] = sys_health.message
        except Exception:
            results["system_resources"] = "unavailable"
        print(json.dumps(results, indent=2, default=str))
    else:
        checks = []
        try:
            from src.utils.exchange_clock import ExchangeClock
            synced = ExchangeClock.is_synced()
            checks.append(("NTP 同步", f"{color('up', '✓')} 已同步" if synced else f"{color('warn', '!')} 未同步"))
        except Exception:
            checks.append(("NTP 同步", f"{color('warn', '?')} 不可用"))
        try:
            from src.services.health_check import check_system_resources
            sys_health = check_system_resources()
            checks.append(("系统资源", f"{color('up', '✓')} {sys_health.message}" if sys_health.healthy else f"{color('warn', '!')} {sys_health.message}"))
        except Exception:
            checks.append(("系统资源", f"{color('warn', '?')} 不可用"))
        try:
            from src.services.health_check import check_task_queue
            tq = check_task_queue()
            checks.append(("任务队列", f"{color('up', '✓')} {tq.message}" if tq.healthy else f"{color('warn', '!')} {tq.message}"))
        except Exception:
            checks.append(("任务队列", f"{color('warn', '?')} 不可用"))
        print(f"{_BOLD}系统健康检查{_RESET}")
        for name, status in checks:
            print(f"  {name:12s} {status}")
