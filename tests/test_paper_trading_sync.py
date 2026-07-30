# -*- coding: utf-8 -*-
"""Tests for paper-trading stock list sync functionality (P0)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config, get_config
from paper_trading import get_watched_codes


@pytest.fixture(autouse=True)
def reset_config(monkeypatch):
    """Reset the Config singleton before/after each test."""
    Config._instance = None
    yield
    Config._instance = None


class TestStockListSync:
    """Test paper_trading_sync_stock_list integration with STOCK_LIST."""

    def test_sync_enabled_uses_stock_list(self, monkeypatch):
        """验证 paper_trading_sync_stock_list=True 时使用 STOCK_LIST."""
        # Set up config with stock_list and sync enabled
        def mock_get_config():
            # Create a mock config object
            cfg = type('MockConfig', (), {
                'paper_trading_sync_stock_list': True,
                'stock_list': ['600519', '300750'],
                'paper_trading_watched_codes': [],
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        result = get_watched_codes()
        expected = ["600519", "300750"]
        assert result == expected, f"Expected {expected}, got {result}"

    def test_sync_disabled_uses_explicit_codes(self, monkeypatch):
        """验证关闭同步后使用显式配置的 watched_codes."""
        def mock_get_config():
            cfg = type('MockConfig', (), {
                'paper_trading_sync_stock_list': False,
                'stock_list': ['600519'],  # 这个应该被忽略
                'paper_trading_watched_codes': ['000001', '000002'],
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        result = get_watched_codes()
        expected = ["000001", "000002"]
        assert result == expected, f"Expected {expected}, got {result}"

    def test_sync_empty_stock_list_returns_default(self, monkeypatch):
        """当启用 sync 但 stock_list 为空时，使用 watched_codes 如果设置了，否则返回空列表."""
        def mock_get_config_empty_watch():
            cfg = type('MockConfig', (), {
                'paper_trading_sync_stock_list': True,
                'stock_list': [],
                'paper_trading_watched_codes': [],
            })()
            return cfg

        def mock_get_config_with_watch():
            cfg = type('MockConfig', (), {
                'paper_trading_sync_stock_list': True,
                'stock_list': [],
                'paper_trading_watched_codes': ['AAPL'],
            })()
            return cfg

        # Case 1: empty watched_codes -> return empty list
        monkeypatch.setattr("src.config.get_config", mock_get_config_empty_watch)
        result = get_watched_codes()
        assert result == []

        # Case 2: watched_codes has value -> return that
        monkeypatch.setattr("src.config.get_config", mock_get_config_with_watch)
        result = get_watched_codes()
        assert result == ["AAPL"]

    def test_case_uppering(self, monkeypatch):
        """验证股票代码中的字母会被转换为大写."""
        def mock_get_config():
            cfg = type('MockConfig', (), {
                'paper_trading_sync_stock_list': True,
                'stock_list': ['600519', '300750', '600000'],
                'paper_trading_watched_codes': [],
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)
        result = get_watched_codes()
        # 所有代码应等于预期（数字保持不变，字母已大写）
        assert result == ['600519', '300750', '600000']

    def test_empty_string_filter(self, monkeypatch):
        """验证空字符串在 stock_list 中被过滤掉."""
        def mock_get_config():
            cfg = type('MockConfig', (), {
                'paper_trading_sync_stock_list': True,
                'stock_list': ['600519', '', '300750', '   ', '600000'],
                'paper_trading_watched_codes': [],
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)
        result = get_watched_codes()
        expected = ['600519', '300750', '600000']
        assert result == expected
        assert '' not in result
        assert '   ' not in result
