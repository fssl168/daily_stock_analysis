# -*- coding: utf-8 -*-
"""实盘前置: 日终对账 (P2-2 §8.2).

两种模式:
  --mode paper    (默认) 本地自洽对账: 每账户持仓市值+现金 ≈ 总资产, 输出持仓/资金基线
  --mode broker   券商对账: EastMoneyBroker 持仓/资金 vs 本地 paper 持仓/资金
                  (需 Windows + 已登录桌面客户端; 未连接时明确提示)

对账维度: code/quantity/available_quantity/avg_cost (±0.1% 容差) / cash。
差异非 0 时输出结构化 diff 并以退出码 1 返回 (供调度告警)。

用法:
  python scripts/reconcile_positions.py --mode paper
  python scripts/reconcile_positions.py --mode broker --account-id 3
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("reconcile_positions")

from src.config import setup_env, get_config
setup_env()
cfg = get_config()

from src.storage import get_db
from paper_trading.account import PaperAccountManager
from paper_trading.position import PositionManager

PRICE_TOLERANCE = 0.001  # ±0.1%


def _local_positions(position_mgr, account_id: int) -> dict:
    """本地持仓 → {code: {quantity, available_quantity, avg_cost, last_price}}."""
    out = {}
    for p in position_mgr.list_positions(account_id):
        out[str(p.get("code"))] = {
            "quantity": float(p.get("quantity", 0) or 0),
            "available_quantity": float(p.get("available_quantity", 0) or 0),
            "avg_cost": float(p.get("avg_cost", 0) or 0),
            "last_price": float(p.get("last_price", 0) or 0),
        }
    return out


def _fmt_diff(diff: list) -> str:
    if not diff:
        return "  无差异 ✅"
    lines = ["  差异:"]
    for d in diff:
        lines.append(f"    {d}")
    return "\n".join(lines)


def reconcile_paper(acct_mgr, position_mgr) -> int:
    """模式 1: 本地自洽对账 (持仓市值+现金 vs 总资产 + 持仓/资金基线)."""
    accounts = acct_mgr.list_accounts(status="active")
    print(f"=== 本地自洽对账: {len(accounts)} 个 active 账户 ===")
    total_diff = 0
    for acc in accounts:
        snap = acct_mgr.snapshot(acc.id)
        total = float(getattr(snap, "total_assets", 0) or 0)
        cash = float(getattr(snap, "cash", 0) or 0)
        pos_mv = 0.0
        pos_lines = []
        for code, info in _local_positions(position_mgr, acc.id).items():
            mv = info["quantity"] * info["last_price"]  # 现价口径, 与 total_assets 一致
            pos_mv += mv
            pos_lines.append(
                f"    {code}: qty={info['quantity']:.0f} avail={info['available_quantity']:.0f} "
                f"avg={info['avg_cost']:.4f} last={info['last_price']:.4f} mv≈{mv:.2f}"
            )
        mismatch = abs(pos_mv + cash - total)
        ok = mismatch < max(1.0, total * 0.001)  # 千分之一容差
        print(f"账户 {acc.id}: 总资产={total:.2f} 现金={cash:.2f} 持仓市值≈{pos_mv:.2f} "
              f"({'+' if ok else 'MISMATCH '}{mismatch:.2f})")
        if pos_lines:
            print("\n".join(pos_lines))
        if not ok:
            total_diff += 1
    print(_fmt_diff([] if total_diff == 0 else [f"{total_diff} 个账户自洽性异常"]))
    return 1 if total_diff else 0


def reconcile_broker(acct_mgr, position_mgr, account_id: int) -> int:
    """模式 2: 券商 vs 本地对账 (需 Windows 已登录桌面客户端)."""
    from paper_trading.broker.eastmoney_broker import EastMoneyBroker

    broker = EastMoneyBroker()
    if not broker.is_connected():
        print("✗ 券商未连接 (需 Windows + 已登录东方财富桌面客户端, 凭据见 .env BROKER_EASTMONEY_*)")
        print("  方案 §8.2 要求 sandbox 环境执行; 本地 paper 模式请用 --mode paper")
        return 2

    broker_positions = {p["code"]: p for p in broker.query_positions()}
    broker_acct = broker.query_account()
    local_positions = _local_positions(position_mgr, account_id)

    print(f"=== 券商 vs 本地对账 (account {account_id}) ===")
    diff: list[str] = []
    all_codes = sorted(set(broker_positions) | set(local_positions))
    for code in all_codes:
        b = broker_positions.get(code)
        l = local_positions.get(code)
        if b is None:
            diff.append(f"{code}: 本地有但券商无 (qty={l['quantity']:.0f})")
            continue
        if l is None:
            diff.append(f"{code}: 券商有但本地无 (qty={b['quantity']:.0f})")
            continue
        for field, label in (("quantity", "数量"), ("available_quantity", "可用"),
                             ("avg_cost", "成本")):
            bv, lv = float(b.get(field, 0) or 0), float(l[field])
            if field == "avg_cost":
                if abs(bv - lv) > max(0.01, abs(lv) * PRICE_TOLERANCE):
                    diff.append(f"{code} {label}: 券商={bv:.4f} 本地={lv:.4f}")
            elif abs(bv - lv) > 0.5:
                diff.append(f"{code} {label}: 券商={bv:.0f} 本地={lv:.0f}")

    b_cash = float(broker_acct.get("available_cash", 0) or 0)
    l_cash = float(getattr(acct_mgr.snapshot(account_id), "cash", 0) or 0)
    if abs(b_cash - l_cash) > max(1.0, abs(l_cash) * PRICE_TOLERANCE):
        diff.append(f"现金: 券商={b_cash:.2f} 本地={l_cash:.2f}")

    print(_fmt_diff(diff))
    return 1 if diff else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["paper", "broker"], default="paper")
    parser.add_argument("--account-id", type=int, default=2)
    args = parser.parse_args()

    db = get_db()
    acct_mgr = PaperAccountManager(db_manager=db)
    position_mgr = PositionManager(db)

    if args.mode == "broker":
        return reconcile_broker(acct_mgr, position_mgr, args.account_id)
    return reconcile_paper(acct_mgr, position_mgr)


if __name__ == "__main__":
    sys.exit(main())
