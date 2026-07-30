# -*- coding: utf-8 -*-
"""Smoke tests for paper-trading notification channel filtering (P2-D)."""

from unittest.mock import MagicMock

import pytest

from paper_trading.notification_integration import PaperTradingNotifier, PushResult


class _DummyConfig:
    """Minimal config stand-in."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_parse_notification_channels_accepts_comma_string():
    notifier = PaperTradingNotifier(config=_DummyConfig())
    assert notifier._parse_notification_channels("feishu,wechat,email") == [
        "feishu",
        "wechat",
        "email",
    ]


def test_parse_notification_channels_ignores_whitespace_and_empty():
    notifier = PaperTradingNotifier(config=_DummyConfig())
    assert notifier._parse_notification_channels("  feishu , , DINGTALK  ") == [
        "feishu",
        "dingtalk",
    ]


def test_parse_notification_channels_returns_none_when_empty():
    notifier = PaperTradingNotifier(config=_DummyConfig())
    assert notifier._parse_notification_channels("") is None
    assert notifier._parse_notification_channels(None) is None
    assert notifier._parse_notification_channels([]) is None


def test_send_via_notification_service_without_filter_uses_unified_send():
    config = _DummyConfig(paper_trading_use_notification_service=True)
    notifier = PaperTradingNotifier(config=config)

    service = MagicMock()
    service.send.return_value = True

    results = notifier._send_via_notification_service(
        service, "Header", "body", "daily_summary"
    )

    service.send.assert_called_once_with("Header\n\nbody")
    assert len(results) == 1
    assert results[0].channel == "notification_service"
    assert results[0].success is True


def test_send_via_notification_service_with_filter_calls_only_requested_channels():
    config = _DummyConfig(
        paper_trading_use_notification_service=True,
        paper_trading_notification_channels="feishu,wechat",
    )
    notifier = PaperTradingNotifier(config=config)

    service = MagicMock()
    service.get_available_channels.return_value = [
        MagicMock(value="feishu"),
        MagicMock(value="wechat"),
        MagicMock(value="email"),
    ]
    service.send_to_feishu.return_value = True
    service.send_to_wechat.return_value = False

    results = notifier._send_via_notification_service(
        service, "Header", "body", "reflection"
    )

    service.send_to_feishu.assert_called_once_with("Header\n\nbody")
    service.send_to_wechat.assert_called_once_with("Header\n\nbody")
    assert not service.send_to_email.called
    assert {r.channel: r.success for r in results} == {
        "feishu": True,
        "wechat": False,
    }


def test_send_via_notification_service_warns_when_no_requested_channel_available():
    config = _DummyConfig(
        paper_trading_use_notification_service=True,
        paper_trading_notification_channels="telegram",
    )
    notifier = PaperTradingNotifier(config=config)

    service = MagicMock()
    service.get_available_channels.return_value = [MagicMock(value="email")]

    results = notifier._send_via_notification_service(
        service, "Header", "body", "battle_plan"
    )

    assert len(results) == 1
    assert results[0].channel == "notification_service"
    assert results[0].success is False
    assert "no requested channels available" in results[0].error


def test_dispatch_returns_skipped_when_no_channel_configured():
    config = _DummyConfig(
        paper_trading_use_notification_service=False,
        paper_trading_lark_webhook_url=None,
        paper_trading_dingtalk_webhook_url=None,
    )
    notifier = PaperTradingNotifier(config=config)

    results = notifier._dispatch("Header", "body", "daily_summary")

    assert len(results) == 1
    assert results[0].channel == "skipped"
    assert results[0].success is False
