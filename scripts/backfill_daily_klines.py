# -*- coding: utf-8 -*-
"""Backfill daily klines from 2026-07-31 for all watched codes.

遵循数据源铁律:最多 2 条并发 + 批间随机冷却(1-3s)防 IP 拉黑。
用法: DSA_VENV_LIB / hermes venv python scripts/backfill_daily_klines.py
"""
import sys
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, r"D:\leanpython\daily_stock_analysis")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")

START = "2026-07-31"
END = "2026-08-12"
CODES = ["600519", "000410", "000410.SZ", "000523.SZ", "600038.SH",
         "600114.SH", "600133.SH", "600511.SH"]


def backfill_one(code: str) -> str:
    from src.config import setup_env
    setup_env()
    from data_provider import DataFetcherManager  # noqa: E402
    from src.storage import get_db  # noqa: E402

    try:
        fm = DataFetcherManager()
        df, source = fm.get_daily_data(code, start_date=START, end_date=END)
        if df is None or df.empty:
            return f"{code}: EMPTY from {source}"
        db = get_db()
        n = db.save_daily_data(df, code, source)
        return f"{code}: saved {n} rows from {source}"
    except Exception as exc:
        return f"{code}: FAIL {type(exc).__name__}: {str(exc)[:120]}"


def main():
    log.info("Backfilling %d codes from %s to %s", len(CODES), START, END)
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(backfill_one, c): c for c in CODES}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = f"{code}: FATAL {exc}"
            log.info(res)
            results.append(res)
            # 随机冷却 1-3s
            cool = random.uniform(1.0, 3.0)
            log.info("cooling %.1fs before next batch...", cool)
            time.sleep(cool)
    log.info("=== DONE ===")
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
