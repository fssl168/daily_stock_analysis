# -*- coding: utf-8 -*-
"""P0-2: 修复账户 3 演示数据的假深亏 (seed 成本价虚高).

规则: 对 avg_cost 与 last_price 偏差超过 30% 的持仓, 将 avg_cost 对齐为
现价的 97% (小幅浮盈), stop_loss = avg_cost * 0.93 (SL < 现价, 合理止损).
修复前备份变更记录输出.
"""
import logging
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

DB = Path(__file__).resolve().parent.parent / "data" / "stock_analysis.db"
DEVIATION_THRESHOLD = 0.15   # 成本与现价偏差阈值 (seed 演示价失真检测)
TARGET_PROFIT = 0.97         # 修复后成本 = 现价 * 0.97 (约 +3% 浮盈)
SL_RATIO = 0.93              # 止损 = 成本 * 0.93

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

rows = cur.execute(
    "SELECT id, account_id, code, name, avg_cost, last_price, stop_loss "
    "FROM paper_positions WHERE quantity > 0"
).fetchall()

fixed = []
for r in rows:
    avg = float(r["avg_cost"] or 0.0)
    last = float(r["last_price"] or 0.0)
    if avg <= 0 or last <= 0:
        continue
    deviation = abs(avg - last) / last
    if deviation <= DEVIATION_THRESHOLD:
        continue  # 正常持仓不动
    new_avg = round(last * TARGET_PROFIT, 4)
    new_sl = round(new_avg * SL_RATIO, 4)
    cur.execute(
        "UPDATE paper_positions SET avg_cost=?, stop_loss=? WHERE id=?",
        (new_avg, new_sl, r["id"]),
    )
    fixed.append({
        "code": r["code"], "name": r["name"], "account_id": r["account_id"],
        "old_avg": avg, "last": last, "deviation_pct": round(deviation * 100, 1),
        "new_avg": new_avg, "new_sl": new_sl,
    })

con.commit()
con.close()

print(f"修复 {len(fixed)} 个持仓:")
for f in fixed:
    print(
        f"  acct{f['account_id']} {f['code']} {f['name']}: "
        f"成本 {f['old_avg']:.2f} -> {f['new_avg']:.2f} "
        f"(偏差 {f['deviation_pct']}%) SL={f['new_sl']:.2f}"
    )
if not fixed:
    print("  无超阈值持仓 (全部正常)")
