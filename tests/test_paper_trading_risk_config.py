# -*- coding: utf-8 -*-
"""Tests for paper trading risk config alignment with main system (P1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config, get_config
from paper_trading.risk_config_adapter import create_risk_config_from_main


@pytest.fixture(autouse=True)
def reset_config(monkeypatch):
    """Reset the Config singleton before/after each test."""
    Config._instance = None
    yield
    Config._instance = None


class TestRiskConfigAlignment:
    """Test that RiskConfig values are correctly mapped from main config."""

    def test_concentration_limit_from_portfolio_alert(self, monkeypatch):
        """portfolio_risk_concentration_alert_pct 应正确映射到 max_pct_per_stock."""
        # Set up a config with concentration alert at 40%
        def mock_get_config():
            cfg = type('MockConfig', (), {
                'portfolio_risk_concentration_alert_pct': 40.0,  # 主系统设置 40%
                'portfolio_max_open_positions': 10,
                'portfolio_risk_max_cash_per_buy_pct': 60.0,
                'paper_trading_max_daily_loss_pct': 0.05,
                'paper_trading_risk_free_rate': 0.02,
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        risk_cfg = create_risk_config_from_main()
        # Concentration should be capped at 30% (min(40%, 30%))
        assert risk_cfg.max_pct_per_stock == 0.30, f"Expected 0.30, got {risk_cfg.max_pct_per_stock}"

    def test_default_concentration_when_not_set(self, monkeypatch):
        """当 portfolio_risk_concentration_alert_pct 未设置时，默认使用 0.30."""
        def mock_get_config_missing():
            cfg = type('MockConfig', (), {})()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config_missing)

        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_pct_per_stock == 0.30, f"Expected default 0.30, got {risk_cfg.max_pct_per_stock}"

    def test_max_open_positions_from_config(self, monkeypatch):
        """portfolio_max_open_positions 应正确传递给 max_open_positions."""
        def mock_get_config():
            cfg = type('MockConfig', (), {
                'portfolio_max_open_positions': 12,
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_open_positions == 12

    def test_default_max_open_positions(self, monkeypatch):
        """当未设置 portfolio_max_open_positions 时，默认值为 8."""
        def mock_get_config():
            cfg = type('MockConfig', (), {})()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_open_positions == 8

    def test_cash_pct_conversion(self, monkeypatch):
        """portfolio_risk_max_cash_per_buy_pct 应转换为比例形式并限制上限为 0.5."""
        # Case 1: value > 100% -> should cap at 0.5
        def mock_get_config_high():
            cfg = type('MockConfig', (), {
                'portfolio_risk_max_cash_per_buy_pct': 80.0,  # 80% -> should become 0.5 (capped)
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config_high)
        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_pct_cash_per_buy == 0.5

        # Case 2: value < 50% -> should use the value
        def mock_get_config_low():
            cfg = type('MockConfig', (), {
                'portfolio_risk_max_cash_per_buy_pct': 30.0,  # 30% -> 0.3
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config_low)
        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_pct_cash_per_buy == 0.30

    def test_default_cash_pct(self, monkeypatch):
        """当未设置 portfolio_risk_max_cash_per_buy_pct 时，默认为 0.5."""
        def mock_get_config():
            cfg = type('MockConfig', (), {})()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_pct_cash_per_buy == 0.5

    def test_daily_loss_preserved(self, monkeypatch):
        """paper_trading_max_daily_loss_pct 应直接传递."""
        def mock_get_config():
            cfg = type('MockConfig', (), {
                'paper_trading_max_daily_loss_pct': 0.10,  # 10%
            })()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_daily_loss_pct == 0.10

    def test_daily_loss_default(self, monkeypatch):
        """当未设置 paper_trading_max_daily_loss_pct 时，默认为 0.05."""
        def mock_get_config():
            cfg = type('MockConfig', (), {})()
            return cfg

        monkeypatch.setattr("src.config.get_config", mock_get_config)

        risk_cfg = create_risk_config_from_main()
        assert risk_cfg.max_daily_loss_pct == 0.05
