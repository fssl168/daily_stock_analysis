# -*- coding: utf-8 -*-
"""
===================================
行情数据质量 Pipeline (T11)
===================================

实时行情 / 日线数据的质量校验流水线：
- DataQualityPipeline：
  - validate_realtime(quote)：实时行情逐字段校验（价格合理性 + 时间戳新鲜度）
  - validate_daily(df, code)：日线 DataFrame 校验（价格合理性 + 无缺失）
  - 单项检查异常隔离，不影响其他检查
- CrossSourceValidator：多源价格交叉验证（>=2 源，价格偏差 < 2% 通过）

来源: docs/architecture/realtime_quant_system_design.md §3.1
规格: .claude/specs/quant-p2/dev-plan.md T11
纯 pandas/numpy 实现，无外部网络依赖。
实时 quote 兼容对象属性或 dict（统一走 _get helper）。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHANGE_PCT = 500.0      # 涨跌幅绝对值 > 500% 视为异常（防港币价标 A 股等）
DEFAULT_MAX_AGE_SECONDS = 60.0      # 行情时间戳最大新鲜度（秒）
DEFAULT_MAX_GAP_DAYS = 4            # 日线相邻日期最大日历间隔（跨周末/节假日容忍）
DEFAULT_MAX_DEVIATION = 0.02        # 多源价格最大偏差（2%）


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """兼容对象属性或 dict 的取值 helper。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class QualityReport:
    """单项 / 整体质量校验报告。"""

    code: str
    passed: bool
    checks: List[Dict[str, Any]]     # each check: {name, passed, detail}
    source: str
    timestamp: datetime


class DataQualityPipeline:
    """行情质量校验流水线。"""

    def __init__(
        self,
        max_change_pct: float = DEFAULT_MAX_CHANGE_PCT,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
    ):
        self.max_change_pct = max_change_pct
        self.max_age_seconds = max_age_seconds
        self.max_gap_days = max_gap_days

    # ---------------- 对外入口 ----------------

    def validate_realtime(self, quote) -> QualityReport:
        """对实时行情逐字段校验。"""
        results = [
            self._safe_check("price_sanity", self._check_price_sanity, quote),
            self._safe_check("timestamp_freshness", self._check_timestamp_freshness, quote),
        ]
        return QualityReport(
            code=str(_get(quote, "code", "") or ""),
            passed=all(r["passed"] for r in results),
            checks=results,
            source=str(_get(quote, "source", "") or ""),
            timestamp=datetime.now(),
        )

    def validate_daily(self, df: pd.DataFrame, code: str) -> QualityReport:
        """对日线 DataFrame 校验。"""
        results = [
            self._safe_check("price_sanity", self._check_price_sanity, df),
            self._safe_check("no_gaps", self._check_no_gaps, df),
        ]
        return QualityReport(
            code=code,
            passed=all(r["passed"] for r in results),
            checks=results,
            source="",
            timestamp=datetime.now(),
        )

    # ---------------- 单项检查 ----------------

    def _safe_check(self, name: str, fn, *args) -> Dict[str, Any]:
        """单项检查异常隔离：异常 -> 该项 failed（含 detail），不影响其他检查。"""
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 - 隔离所有异常
            logger.warning("Quality check %s raised: %s", name, exc)
            return {"name": name, "passed": False, "detail": f"check error: {exc}"}

    def _check_price_sanity(self, quote_or_df) -> Dict[str, Any]:
        """价格合理性：价格 > 0；涨跌幅绝对值 < max_change_pct。"""
        if isinstance(quote_or_df, pd.DataFrame):
            return self._price_sanity_df(quote_or_df)
        return self._price_sanity_quote(quote_or_df)

    def _price_sanity_quote(self, quote) -> Dict[str, Any]:
        name = "price_sanity"
        price = _get(quote, "price", None)
        if price is None:
            return {"name": name, "passed": False, "detail": "price missing"}
        try:
            price = float(price)
        except (TypeError, ValueError):
            return {"name": name, "passed": False, "detail": "price invalid"}
        if not np.isfinite(price):
            return {"name": name, "passed": False, "detail": "price invalid (NaN)"}
        if price <= 0:
            return {"name": name, "passed": False, "detail": f"price={price} <= 0"}
        change_pct = _get(quote, "change_pct", None)
        if change_pct is None:
            return {"name": name, "passed": True, "detail": "price ok, change_pct missing skipped"}
        try:
            change_pct = float(change_pct)
        except (TypeError, ValueError):
            return {"name": name, "passed": True, "detail": "price ok, change_pct invalid skipped"}
        if not np.isfinite(change_pct):
            return {"name": name, "passed": True, "detail": "price ok, change_pct invalid skipped"}
        if abs(change_pct) >= self.max_change_pct:
            return {
                "name": name,
                "passed": False,
                "detail": f"change_pct={change_pct}% out of range (<{self.max_change_pct}%)",
            }
        return {"name": name, "passed": True, "detail": f"price={price}, change_pct={change_pct}%"}

    def _price_sanity_df(self, df: pd.DataFrame) -> Dict[str, Any]:
        name = "price_sanity"
        if df is None or df.empty:
            return {"name": name, "passed": True, "detail": "empty df, skipped"}
        if "close" not in df.columns:
            return {"name": name, "passed": False, "detail": "no close column"}
        close = pd.to_numeric(df["close"], errors="coerce")
        if close.isna().any():
            return {"name": name, "passed": False, "detail": "close contains NaN/invalid values"}
        if not (close > 0).all():
            return {"name": name, "passed": False, "detail": f"close <= 0 found (min={close.min()})"}
        pct_change = close.pct_change().abs().dropna()
        max_pct = float(pct_change.max()) * 100.0 if not pct_change.empty else 0.0
        if max_pct >= self.max_change_pct:
            return {
                "name": name,
                "passed": False,
                "detail": f"max abs change={max_pct:.2f}% >= {self.max_change_pct}%",
            }
        return {"name": name, "passed": True, "detail": f"close>0, max abs change={max_pct:.2f}%"}

    def _check_timestamp_freshness(self, quote) -> Dict[str, Any]:
        """行情时间戳距今 < max_age_seconds 为新鲜（接受带 tz 或 naive datetime）。"""
        name = "timestamp_freshness"
        ts = _get(quote, "timestamp", None)
        if ts is None:
            return {"name": name, "passed": True, "detail": "no timestamp attribute, skipped"}
        ts = self._coerce_datetime(ts)
        if ts is None:
            return {"name": name, "passed": False, "detail": "timestamp parse error"}
        if ts.tzinfo is None:
            age = (datetime.now() - ts).total_seconds()
        else:
            age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        passed = age < self.max_age_seconds
        return {
            "name": name,
            "passed": passed,
            "detail": f"age={age:.1f}s (max={self.max_age_seconds:.1f}s)",
        }

    @staticmethod
    def _coerce_datetime(value) -> Any:
        """把 datetime / pandas / numpy / ISO 字符串统一转成可比较的 datetime。"""
        if isinstance(value, datetime):
            return value
        if isinstance(value, np.datetime64):
            return pd.Timestamp(value).to_pydatetime()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _check_no_gaps(self, df: pd.DataFrame) -> Dict[str, Any]:
        """日线无缺失：日期间隔 <= max_gap_days；无 2+ 交易日连续缺失；少于 2 行跳过。"""
        name = "no_gaps"
        if df is None or df.empty:
            return {"name": name, "passed": True, "detail": "empty df, skipped"}
        dates = self._extract_dates(df)
        if dates is None:
            return {"name": name, "passed": False, "detail": "no date index or date/datetime column"}
        dates = pd.DatetimeIndex(dates)
        if dates.isna().any():
            return {"name": name, "passed": False, "detail": "date column contains invalid values"}
        if len(dates) < 2:
            return {"name": name, "passed": True, "detail": "less than 2 rows, skipped"}
        unique = pd.DatetimeIndex(sorted(set(dates)))
        diffs = unique.to_series().diff().dt.days.dropna().astype(int)
        max_gap = int(diffs.max())
        if max_gap > self.max_gap_days:
            return {
                "name": name,
                "passed": False,
                "detail": f"max calendar gap={max_gap}d > {self.max_gap_days}d",
            }
        bad_gaps = []
        for prev, nxt in zip(unique[:-1], unique[1:]):
            # 用工作日近似交易日：统计 (prev, nxt) 之间严格位于区间内的工作日数
            missing_trading = int(np.busday_count(prev.date() + timedelta(days=1), nxt.date()))
            if missing_trading >= 2:
                bad_gaps.append((str(prev.date()), str(nxt.date()), missing_trading))
        if bad_gaps:
            return {
                "name": name,
                "passed": False,
                "detail": f"{len(bad_gaps)} gap(s) with >=2 missing trading days, first={bad_gaps[0]}",
            }
        return {"name": name, "passed": True, "detail": f"max gap={max_gap}d, no missing trading days"}

    @staticmethod
    def _extract_dates(df: pd.DataFrame):
        """从 DatetimeIndex 或 date/datetime 列提取日期序列。"""
        if isinstance(df.index, pd.DatetimeIndex):
            return df.index
        for col in ("date", "datetime"):
            if col in df.columns:
                return pd.to_datetime(df[col], errors="coerce")
        return None


class CrossSourceValidator:
    """多源交叉验证：需要 >=2 源；价格偏差 < max_deviation 通过，否则 failed。"""

    def __init__(self, max_deviation: float = DEFAULT_MAX_DEVIATION):
        self.max_deviation = max_deviation

    def validate(self, code: str, quotes: List) -> QualityReport:
        if len(quotes) < 2:
            return QualityReport(
                code=code,
                passed=False,
                checks=[{"name": "cross_validation", "passed": False, "detail": "only 1 source"}],
                source="cross_source",
                timestamp=datetime.now(),
            )
        prices: List[float] = []
        sources: List[str] = []
        for q in quotes:
            price = _get(q, "price", None)
            if price is not None:
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    price = None
            if price is not None and np.isfinite(price) and price > 0:
                prices.append(price)
                sources.append(str(_get(q, "source", "") or ""))
        if len(prices) < 2:
            return QualityReport(
                code=code,
                passed=False,
                checks=[{"name": "cross_validation", "passed": False, "detail": "not enough valid prices"}],
                source="cross_source",
                timestamp=datetime.now(),
            )
        mean_price = float(np.mean(prices))
        max_deviation = max(abs(p - mean_price) / mean_price for p in prices)
        passed = max_deviation < self.max_deviation
        return QualityReport(
            code=code,
            passed=passed,
            checks=[{
                "name": "cross_validation",
                "passed": passed,
                "detail": f"max_deviation={max_deviation:.2%}, sources={sources}",
            }],
            source="cross_source",
            timestamp=datetime.now(),
        )
