# -*- coding: utf-8 -*-
"""
统一时钟源（ExchangeClock）。

提供交易所时区映射与 NTP 时钟同步，作为全项目唯一的时间来源：

- ``EXCHANGE_TIMEZONES``: cn/hk=UTC+8, us=UTC-4(EDT), jp/kr=UTC+9
- ``ExchangeClock.now()``: 返回指定交易所的当前时间（带时区）
- ``ExchangeClock.sync()``: NTP 同步（ntplib），失败返回 False 不抛异常
- ``ExchangeClock.is_synced()``: 距上次同步 < 3600s 视为已同步
- ``ExchangeClock._offset_ms``: 记录 NTP 偏差（local - NTP，单位毫秒）
- 单例模式（classmethod + ``_instance``）

优先级: NTP → 交易所 API 时间 → 系统时间（降级）。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import ntplib
except ImportError:  # pragma: no cover - 离线环境未安装 ntplib 时降级
    ntplib = None  # type: ignore[assignment]

# 交易所时区映射（固定偏移，与架构文档 §1.3 一致）
EXCHANGE_TIMEZONES = {
    "cn": timezone(timedelta(hours=8)),  # 上交所/深交所
    "hk": timezone(timedelta(hours=8)),  # 港交所
    "us": timezone(timedelta(hours=-4)),  # 美东夏令时 (EDT)
    "jp": timezone(timedelta(hours=9)),  # 东京
    "kr": timezone(timedelta(hours=9)),  # 首尔
}

# NTP 同步判定阈值（秒）：距上次同步 < 3600s 视为已同步
SYNC_EXPIRY_SECONDS = 3600


class ExchangeClock:
    """
    统一时钟源。

    优先级: NTP → 交易所 API 时间 → 系统时间（降级）。
    所有模块的时间获取必须通过此类，避免各模块使用不同时间源导致时间轴乱序。

    Attributes:
        _instance: 单例实例。
        _offset_ms: NTP 偏差（本地时间 - NTP 时间），单位毫秒。
        _last_sync: 最近一次 NTP 同步时间（UTC，带时区）。
    """

    _instance: Optional["ExchangeClock"] = None
    _offset_ms: float = 0.0  # NTP 偏差 (local - NTP)
    _last_sync: Optional[datetime] = None

    def __new__(cls) -> "ExchangeClock":
        """单例：全局只保留一个实例。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def now(cls, market: str = "cn") -> datetime:
        """返回指定交易所的当前时间（带时区）。

        Args:
            market: 交易所标识，取值见 ``EXCHANGE_TIMEZONES``；
                未知 market 回退到 cn（UTC+8）。

        Returns:
            datetime: 带时区的交易所当前时间。
        """
        utc = datetime.now(timezone.utc)
        tz = EXCHANGE_TIMEZONES.get(market, EXCHANGE_TIMEZONES["cn"])
        return utc.astimezone(tz)

    @classmethod
    def sync(cls) -> bool:
        """NTP 同步 — 启动时和每 60 分钟执行。

        所有 NTP 调用均被 try/except 包裹：ntplib 未安装或网络失败时
        返回 False，不抛异常，保证离线可运行。

        Returns:
            bool: 同步成功返回 True，失败返回 False。
        """
        if ntplib is None:
            return False
        try:
            client = ntplib.NTPClient()
            response = client.request("pool.ntp.org", version=3, timeout=3)
            cls._offset_ms = response.offset * 1000  # 秒 → 毫秒
            cls._last_sync = datetime.now(timezone.utc)
            return True
        except Exception:
            return False

    @classmethod
    def is_synced(cls) -> bool:
        """NTP 同步状态 — 用于健康检查。

        Returns:
            bool: 距上次同步 < 3600 秒视为已同步；从未同步返回 False。
        """
        if cls._last_sync is None:
            return False
        elapsed = datetime.now(timezone.utc) - cls._last_sync
        return elapsed.total_seconds() < SYNC_EXPIRY_SECONDS
