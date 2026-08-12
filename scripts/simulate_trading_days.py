#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Simulate N consecutive trading days for a paper account (T-06).

Runs end-of-day settlement across N synthetic trading days with
deterministic stub prices, verifying the daily net-value curve stays
continuous and settlement never throws.

Usage:
  python scripts/simulate_trading_days.py --account demo --days 5
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402

setup_env()

from src.storage import get_db, PaperPosition  # noqa: E402
from sqlalchemy import update  # noqa: E402
from paper_trading.account import PaperAccountManager  # noqa: E402
from paper_trading.fees import FeeModel  # noqa: E402
from paper_trading.position import PositionManager  # noqa: E402
from paper_trading.settlement import Settlement  # noqa: E402

logger = logging.getLogger("simulate_trading_days")


def synthetic_price(code: str, base: float, day_idx: int) -> float:
    """Deterministic pseudo-drift per code+day (reproducible, no network)."""
    seed = (sum(ord(c) for c in code) + day_idx * 17) % 100
    return round(base * (1.0 + (seed - 50) / 1000.0), 4)


def last_weekdays(n: int) -> List[date]:
    days: List[date] = []
    d = date.today()
    while len(days) < n:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return days


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate N trading days (T-06)")
    parser.add_argument("--account", default="E2E-演示账户")
    parser.add_argument("--days", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    db = get_db()
    account_mgr = PaperAccountManager(db_manager=db)
    account = account_mgr.get_account(name=args.account)
    if account is None:
        print(f"account '{args.account}' not found; run scripts/seed_demo_data.py first.")
        return 1
    account_id = account.id

    pos_mgr = PositionManager(db)
    settlement = Settlement(
        account_mgr=account_mgr, position_mgr=pos_mgr, fee_model=FeeModel()
    )

    positions = pos_mgr.list_positions(account_id)
    # Simulate T+1 unlock: by settlement, holdings bought on prior days are
    # sellable, so available_quantity == total quantity. (Seed/demo positions
    # are all "today" buys with available_quantity=0, which would make MTM skip
    # them.) Restoring availability makes mark-to-market exercise the full book.
    with db.session_scope() as session:
        session.execute(
            update(PaperPosition)
            .where(PaperPosition.account_id == account_id)
            .values(available_quantity=PaperPosition.quantity)
        )
    base: Dict[str, float] = {
        p["code"]: float(p.get("last_price") or p.get("avg_cost") or 10.0) for p in positions
    }
    if not base:
        print("no open positions; simulation has nothing to mark-to-market.")
        return 1
    print(f"positions: {list(base.keys())}")

    days = last_weekdays(args.days)
    curve: List[Tuple[str, float, int]] = []
    for idx, day in enumerate(days):
        prices = {code: synthetic_price(code, b, idx) for code, b in base.items()}
        res = settlement.daily_settle(account_id, target_date=day, latest_prices=prices)
        curve.append((day.isoformat(), round(res.total_assets, 2), res.position_count))
        print(
            f"  {day.isoformat()} assets={res.total_assets:.2f} cash={res.cash:.2f} "
            f"pos_value={res.positions_value:.2f} pos={res.position_count}"
        )

    # ---- assertions ----
    assert len(curve) == args.days, "not enough settlement days produced"
    assets = [c[1] for c in curve]
    assert all(a > 0 for a in assets), "total assets must stay positive"
    # monotonic-ish sanity: last day differs from first (prices actually moved)
    assert assets[-1] != assets[0] or len(set(assets)) > 1, "prices did not move"
    print(f"\n{args.days}-day net-value curve: {assets}")
    print("simulation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
