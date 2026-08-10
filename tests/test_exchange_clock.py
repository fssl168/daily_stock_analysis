# -*- coding: utf-8 -*-
"""
Tests for src/utils/exchange_clock.py
Covers: 交易所时区映射、now() 正确性、NTP 同步失败路径、is_synced 过期、
单例行为、负偏差。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import src.utils.exchange_clock as exchange_clock
from src.utils.exchange_clock import ExchangeClock

UTC = timezone.utc

# 固定 UTC 时间点，用于确定性断言（2026-08-10 04:00:00 UTC）
FIXED_UTC = datetime(2026, 8, 10, 4, 0, 0, tzinfo=UTC)


class FakeDateTime:
    """模块级 datetime 的替身：now() 返回固定 UTC 时间点。"""

    @staticmethod
    def now(tz=None):
        if tz is None:
            return FIXED_UTC.replace(tzinfo=None)
        return FIXED_UTC.astimezone(tz)


class FakeNtplibResponse:
    """伪造的 NTP 响应，只暴露 offset 字段。"""

    def __init__(self, offset):
        self.offset = offset


class FakeNtplib:
    """伪造的 ntplib 模块：可配置 offset 或抛异常。"""

    def __init__(self, offset=0.0, exc=None):
        self._offset = offset
        self._exc = exc

    def NTPClient(self):
        return self

    def request(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return FakeNtplibResponse(self._offset)


@pytest.fixture(autouse=True)
def _reset_clock_state():
    """每个用例前重置单例与同步状态，避免用例间相互污染。"""
    ExchangeClock._instance = None
    ExchangeClock._offset_ms = 0.0
    ExchangeClock._last_sync = None
    yield
    ExchangeClock._instance = None
    ExchangeClock._offset_ms = 0.0
    ExchangeClock._last_sync = None


def _patch_utc_now():
    """用固定 UTC 时间点替换模块内的 datetime.now。"""
    return patch("src.utils.exchange_clock.datetime", FakeDateTime)


class TestTimezoneMapping:
    def test_mapping_offsets_all_markets(self):
        expected = {
            "cn": timedelta(hours=8),
            "hk": timedelta(hours=8),
            "us": timedelta(hours=-4),
            "jp": timedelta(hours=9),
            "kr": timedelta(hours=9),
        }
        assert set(exchange_clock.EXCHANGE_TIMEZONES.keys()) == set(expected.keys())
        for market, offset in expected.items():
            tz = exchange_clock.EXCHANGE_TIMEZONES[market]
            assert tz.utcoffset(FIXED_UTC) == offset

    def test_now_unknown_market_falls_back_to_cn(self):
        with _patch_utc_now():
            result = ExchangeClock.now("unknown_market")
            assert result.utcoffset() == timedelta(hours=8)


class TestNow:
    def test_now_returns_timezone_aware(self):
        result = ExchangeClock.now("cn")
        assert result.tzinfo is not None
        assert result.utcoffset() is not None

    def test_now_cn(self):
        with _patch_utc_now():
            result = ExchangeClock.now("cn")
            assert result == FIXED_UTC.astimezone(exchange_clock.EXCHANGE_TIMEZONES["cn"])
            assert result.isoformat() == "2026-08-10T12:00:00+08:00"

    def test_now_hk(self):
        with _patch_utc_now():
            result = ExchangeClock.now("hk")
            assert result == FIXED_UTC.astimezone(exchange_clock.EXCHANGE_TIMEZONES["hk"])
            assert result.isoformat() == "2026-08-10T12:00:00+08:00"

    def test_now_us_edt(self):
        with _patch_utc_now():
            result = ExchangeClock.now("us")
            assert result == FIXED_UTC.astimezone(exchange_clock.EXCHANGE_TIMEZONES["us"])
            assert result.isoformat() == "2026-08-10T00:00:00-04:00"

    def test_now_jp_and_kr(self):
        with _patch_utc_now():
            jp = ExchangeClock.now("jp")
            kr = ExchangeClock.now("kr")
            assert jp.isoformat() == "2026-08-10T13:00:00+09:00"
            assert kr.isoformat() == "2026-08-10T13:00:00+09:00"

    def test_now_default_market_is_cn(self):
        with _patch_utc_now():
            assert ExchangeClock.now() == ExchangeClock.now("cn")


class TestSync:
    def test_sync_success_sets_offset_and_last_sync(self):
        fake = FakeNtplib(offset=0.25)
        with patch("src.utils.exchange_clock.ntplib", fake), _patch_utc_now():
            assert ExchangeClock.sync() is True
            assert ExchangeClock._offset_ms == 250.0
            assert ExchangeClock._last_sync == FIXED_UTC
            assert ExchangeClock.is_synced() is True

    def test_sync_negative_offset(self):
        fake = FakeNtplib(offset=-0.5)
        with patch("src.utils.exchange_clock.ntplib", fake), _patch_utc_now():
            assert ExchangeClock.sync() is True
        assert ExchangeClock._offset_ms == -500.0

    def test_sync_zero_offset(self):
        fake = FakeNtplib(offset=0.0)
        with patch("src.utils.exchange_clock.ntplib", fake), _patch_utc_now():
            assert ExchangeClock.sync() is True
        assert ExchangeClock._offset_ms == 0.0

    def test_sync_failure_returns_false_no_raise(self):
        fake = FakeNtplib(exc=OSError("network unreachable"))
        with patch("src.utils.exchange_clock.ntplib", fake):
            # 不应抛出异常
            assert ExchangeClock.sync() is False
        assert ExchangeClock._offset_ms == 0.0
        assert ExchangeClock._last_sync is None

    def test_sync_failure_preserves_previous_state(self):
        fake_ok = FakeNtplib(offset=0.1)
        with patch("src.utils.exchange_clock.ntplib", fake_ok), _patch_utc_now():
            assert ExchangeClock.sync() is True
        fake_bad = FakeNtplib(exc=TimeoutError("ntp timeout"))
        with patch("src.utils.exchange_clock.ntplib", fake_bad):
            assert ExchangeClock.sync() is False
        # 失败不覆盖已有同步状态
        assert ExchangeClock._offset_ms == 100.0
        assert ExchangeClock._last_sync == FIXED_UTC

    def test_sync_when_ntplib_missing(self):
        with patch("src.utils.exchange_clock.ntplib", None):
            assert ExchangeClock.sync() is False
        assert ExchangeClock._last_sync is None

    def test_sync_any_exception_type(self):
        fake = FakeNtplib(exc=ValueError("bad response"))
        with patch("src.utils.exchange_clock.ntplib", fake):
            assert ExchangeClock.sync() is False


class TestIsSynced:
    def test_never_synced_returns_false(self):
        assert ExchangeClock.is_synced() is False

    def test_synced_right_after_sync(self):
        fake = FakeNtplib(offset=0.0)
        with patch("src.utils.exchange_clock.ntplib", fake), _patch_utc_now():
            ExchangeClock.sync()
            assert ExchangeClock.is_synced() is True

    def test_synced_just_before_expiry(self):
        ExchangeClock._last_sync = FIXED_UTC - timedelta(seconds=3599)
        with _patch_utc_now():
            assert ExchangeClock.is_synced() is True

    def test_expired_at_exactly_3600_seconds(self):
        ExchangeClock._last_sync = FIXED_UTC - timedelta(seconds=3600)
        with _patch_utc_now():
            assert ExchangeClock.is_synced() is False

    def test_expired_after_3600_seconds(self):
        ExchangeClock._last_sync = FIXED_UTC - timedelta(seconds=3601)
        with _patch_utc_now():
            assert ExchangeClock.is_synced() is False

    def test_expired_after_many_hours(self):
        # 超过 24h 也要判定为过期（验证使用 total_seconds 而非 timedelta.seconds）
        ExchangeClock._last_sync = FIXED_UTC - timedelta(seconds=2 * 3600)
        with _patch_utc_now():
            assert ExchangeClock.is_synced() is False


class TestSingleton:
    def test_instances_are_the_same(self):
        assert ExchangeClock() is ExchangeClock()

    def test_instance_equals_class_instance_attr(self):
        instance = ExchangeClock()
        assert ExchangeClock._instance is instance

    def test_singleton_state_shared_via_class(self):
        instance = ExchangeClock()
        fake = FakeNtplib(offset=-1.5)
        with patch("src.utils.exchange_clock.ntplib", fake), _patch_utc_now():
            assert instance.sync() is True
        # 类属性与实例共享同一份状态
        assert ExchangeClock._offset_ms == -1500.0
        assert instance._offset_ms == -1500.0
