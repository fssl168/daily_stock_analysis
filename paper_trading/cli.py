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
    s_sub.add_parser("evaluate", help="评估策略") \
        .add_argument("--name", required=True); \
    s_sub.add_parser("evaluate", help="评估策略").add_argument("--code", required=True)
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
    r_sub.add_parser("extreme-market", help="极端行情").add_argument("--account-id", type=int, required=True)
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
        print("回测列表功能将在 Phase 2 实现。")
        return 0
    if action == "result":
        print(f"回测结果 #{(getattr(args, 'result_id', 0))} 功能将在 Phase 2 实现。")
        return 0
    if action in ("walk-forward", "grid-search", "compare"):
        print(f"'{action}' 功能将在 Phase 2 实现。")
        return 0
    return 1


def _handle_listen(args) -> int:
    action = getattr(args, "pt_listen_action", "")
    if action == "start":
        from paper_trading.market_listener import build_default_listener
        from src.config import get_config

        aid = getattr(args, "account_id", 1)
        cfg = get_config()
        listener = build_default_listener(cfg, account_id=aid)
        listener.start()
        print(f"{color('up', '✓')} 实时监听已启动 (account_id={aid})。")
        if getattr(args, "interactive", False):
            print("交互模式将在 Phase 2 实现。")
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
    if action == "status":
        print("监听状态查询将在 Phase 2 完善。")
        return 0
    if action in ("stop", "restart"):
        print(f"'{action}' 功能将在 Phase 2 完善（需 PID 文件管理）。")
        return 0
    return 1


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
        print(f"撤单 #{getattr(args, 'order_id', 0)} 功能将在 Phase 2 完善。")
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
        print(f"熔断重置 (account_id={aid}) 功能将在 Phase 3 完善。")
        return 0
    if action in ("var", "liquidity", "anomaly"):
        print(f"'{action}' 功能将在 Phase 3 完善。")
        return 0
    if action == "extreme-market":
        print("极端行情检测将在 Phase 3 完善。")
        return 0
    if action == "latency":
        print("延迟监控将在 Phase 3 完善。")
        return 0
    return 1


def _handle_performance(args) -> int:
    action = getattr(args, "pt_performance_action", "")
    if action == "metrics":
        print(f"绩效指标 (account_id={getattr(args, 'account_id', 1)}) 将在 Phase 3 完善。")
        return 0
    if action in ("drawdown", "leaderboard", "drift", "features"):
        print(f"'{action}' 功能将在 Phase 3 完善。")
        return 0
    return 1


def _handle_health(args) -> int:
    print(f"健康检查 ({getattr(args, 'format', 'table')}) 将在 Phase 3 完善。")
    return 0
