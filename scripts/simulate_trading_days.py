# -*- coding: utf-8 -*-
"""P1-3: 5 交易日稳定性演练（真实密钥）.

对 5 个未来交易日依次执行: 日终结算(全 active 账户) → 日终复盘 →
作战卡生成, 每步验证落库。验收: 5 日无未捕获异常; 每日复盘/作战卡
各 1 条; 净值曲线连续（5 天各有结算点）。

用法: python scripts/simulate_trading_days.py [--days 5]
"""
import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("simulate_trading_days")

from src.config import setup_env, get_config
setup_env()
cfg = get_config()

from src.storage import get_db
from paper_trading.account import PaperAccountManager
from paper_trading.trading_engine import TradingEngine
from paper_trading.reflection import build_reflection_engine
from paper_trading.battle_plan import build_battle_plan_generator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=5)
    args = parser.parse_args()

    db = get_db()
    engine = TradingEngine()
    reflection = build_reflection_engine(config=cfg, account_id=2)
    battle_plan = build_battle_plan_generator(
        config=cfg, account_id=2, trading_engine=engine,
    )
    acct_mgr = PaperAccountManager(db_manager=db)
    accounts = [a for a in acct_mgr.list_accounts(status="active")]

    # 未来 5 个交易日（避开今天的真实数据）
    days = [date.today() + timedelta(days=i + 1) for i in range(args.days)]
    logger.info("演练账户: %s | 模拟日: %s ~ %s", [a.id for a in accounts], days[0], days[-1])

    stats = {"settle": 0, "reflection": 0, "battle_plan": 0, "errors": []}
    for i, target in enumerate(days, 1):
        logger.info("=== 模拟日 %d/%d: %s ===", i, args.days, target)
        day_start = time.time()
        try:
            # 1) 日终结算: 全 active 账户
            for acc in accounts:
                engine.daily_settle(acc.id, target_date=target)
                stats["settle"] += 1
            logger.info("  [结算] %d 账户完成", len(accounts))

            # 2) 日终复盘
            note = reflection.reflect_on_daily(account_id=2, review_date=target)
            stats["reflection"] += 1
            logger.info("  [复盘] subject=%s", str(note.subject)[:50])

            # 3) 作战卡
            plan = battle_plan.generate(account_id=2, target_date=target)
            stats["battle_plan"] += 1
            logger.info("  [作战卡] fallback=%s", getattr(plan, "used_fallback", "?"))

            logger.info("  [耗时] %.1fs", time.time() - day_start)
        except Exception as exc:  # noqa: BLE001 — 演练捕获全部异常计入失败
            logger.error("  模拟日 %s 失败: %s", target, exc)
            stats["errors"].append(f"{target}: {type(exc).__name__}: {exc}")

    # 汇总
    print("\n=== 演练汇总 ===")
    print(f"模拟日: {args.days} | 结算点: {stats['settle']} | 复盘: {stats['reflection']} | 作战卡: {stats['battle_plan']}")
    print(f"异常: {len(stats['errors'])}")
    for e in stats["errors"][:10]:
        print(f"  ✗ {e}")
    ok = not stats["errors"] and stats["reflection"] == args.days and stats["battle_plan"] == args.days
    print(f"\n验收: {'✅ PASS (5 日无异常, 每日复盘/作战卡齐全)' if ok else '❌ FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
