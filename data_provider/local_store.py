# -*- coding: utf-8 -*-
"""LocalMarketStore — 行情数据持久化仓库（T9）.

来源: docs/architecture/realtime_quant_system_design.md §3.2
规格: .claude/specs/quant-p1/dev-plan.md T9

基于 SQLite 标准库的本地日线行情仓库：
- 建表 daily_kline，主键 (code, trade_date)，逐行 INSERT OR REPLACE 幂等写入
- sqlite3 + check_same_thread=False + threading.Lock，保证跨线程安全
- 按日期区间读取，返回 DatetimeIndex 的 DataFrame；无数据返回 None
- 通过 fetched_at 与 stale_threshold_hours 判断数据是否需要更新
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd

__all__ = ["StoreConfig", "LocalMarketStore"]


@dataclass
class StoreConfig:
    """本地仓库配置。"""

    db_path: str = "data/market_data.db"
    stale_threshold_hours: int = 24
    max_incremental_days: int = 5       # T-019: incremental pull window
    full_refresh_interval_days: int = 30  # T-019: full refresh cadence


class LocalMarketStore:
    """本地 SQLite 日线行情仓库。"""

    def __init__(self, config: StoreConfig):
        self.config = config
        self._conn = sqlite3.connect(config.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        """建表（幂等）。T-019: ensure adjust_factor column and index exist."""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_kline (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL,
                    adjust_factor REAL DEFAULT 1.0,
                    source TEXT,
                    fetched_at TEXT,
                    PRIMARY KEY (code, trade_date)
                )
            """)
            # Ensure adjust_factor column exists for tables created before T-019.
            try:
                self._conn.execute("ALTER TABLE daily_kline ADD COLUMN adjust_factor REAL DEFAULT 1.0")
            except Exception:
                pass  # column already exists
            # Ensure index for fast code+date lookups.
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kline_code_date
                ON daily_kline(code, trade_date)
            """)
            self._conn.commit()

    @staticmethod
    def _to_date_str(idx) -> str:
        """把 index 日期归一化为 YYYY-MM-DD（兼容 Timestamp/str/date）。"""
        if hasattr(idx, "strftime"):
            return idx.strftime("%Y-%m-%d")
        return str(idx)

    def upsert(self, code: str, df: pd.DataFrame, source: str):
        """逐行 INSERT OR REPLACE 写入日线数据（df index 为日期）。"""
        now = datetime.now().isoformat()
        with self._lock:
            for idx, row in df.iterrows():
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_kline
                    (code, trade_date, open, high, low, close, volume, amount, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        self._to_date_str(idx),
                        float(row.get("open", 0)),
                        float(row.get("high", 0)),
                        float(row.get("low", 0)),
                        float(row.get("close", 0)),
                        float(row.get("volume", 0)),
                        float(row.get("amount", 0)),
                        source,
                        now,
                    ),
                )
            self._conn.commit()

    def get(self, code: str, start: date, end: date) -> Optional[pd.DataFrame]:
        """按日期区间读取日线，返回 DatetimeIndex 的 DataFrame；无数据返回 None。"""
        query = """
            SELECT trade_date, open, high, low, close, volume, amount, source
            FROM daily_kline
            WHERE code = ? AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date ASC
        """
        with self._lock:
            df = pd.read_sql_query(
                query, self._conn, params=(code, start.isoformat(), end.isoformat())
            )
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df.set_index("trade_date", inplace=True)
        return df

    def needs_update(self, code: str) -> bool:
        """无记录，或最新 fetched_at 超过 stale_threshold_hours 时返回 True。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_at FROM daily_kline WHERE code = ? ORDER BY trade_date DESC LIMIT 1",
                (code,),
            ).fetchone()
        if row is None:
            return True
        fetched_at = datetime.fromisoformat(row[0])
        age_hours = (datetime.now() - fetched_at).total_seconds() / 3600
        return age_hours > self.config.stale_threshold_hours

    def close(self):
        """关闭连接。"""
        with self._lock:
            self._conn.close()
