"""Tests for MarketListener._check_dynamic_sltp (P1-A trailing stop)."""
import types
from datetime import datetime
from unittest.mock import MagicMock, patch
from paper_trading.market_listener import MarketListener, MarketListenerConfig


def _build_listener(enable_dynamic_sltp=True, threshold_pct=20.0):
    engine = MagicMock()
    engine.position_mgr = MagicMock()
    config = MarketListenerConfig(
        account_id=1,
        enable_dynamic_sltp=enable_dynamic_sltp,
        sltp_dynamic_threshold_pct=threshold_pct,
    )
    listener = MarketListener.__new__(MarketListener)
    listener.engine = engine
    listener.config = config
    return listener, engine


def _make_position(code, avg_cost, stop_loss):
    return types.SimpleNamespace(code=code, avg_cost=avg_cost, stop_loss=stop_loss)


def test_dynamic_sltp_disabled_returns_early():
    listener, engine = _build_listener(enable_dynamic_sltp=False)
    listener._check_dynamic_sltp("cn", {"600519": 25.0})
    engine.position_mgr.list_positions.assert_not_called()


def test_dynamic_sltp_raises_sl_when_profit_exceeds_threshold():
    listener, engine = _build_listener(threshold_pct=20.0)
    # avg_cost=20, latest=25 -> profit_ratio=25% > 20% threshold
    pos = _make_position("600519", avg_cost=20.0, stop_loss=18.0)
    engine.position_mgr.list_positions.return_value = [pos]

    fake_result = types.SimpleNamespace(stop_loss=21.0)
    with patch("paper_trading.sltp_calculator.build_sltp_calculator") as mock_builder:
        mock_calc = MagicMock()
        mock_calc.calculate.return_value = fake_result
        mock_builder.return_value = mock_calc
        listener._check_dynamic_sltp("cn", {"600519": 25.0})

    engine.position_mgr.update_stop_loss_take_profit.assert_called_once()
    call_kwargs = engine.position_mgr.update_stop_loss_take_profit.call_args
    assert call_kwargs.kwargs["code"] == "600519"
    assert call_kwargs.kwargs["stop_loss"] == 21.0


def test_dynamic_sltp_does_not_lower_sl():
    listener, engine = _build_listener(threshold_pct=20.0)
    # new_sl (17.0) < current_sl (18.0) -> should NOT update
    pos = _make_position("600519", avg_cost=20.0, stop_loss=18.0)
    engine.position_mgr.list_positions.return_value = [pos]

    fake_result = types.SimpleNamespace(stop_loss=17.0)
    with patch("paper_trading.sltp_calculator.build_sltp_calculator") as mock_builder:
        mock_calc = MagicMock()
        mock_calc.calculate.return_value = fake_result
        mock_builder.return_value = mock_calc
        listener._check_dynamic_sltp("cn", {"600519": 25.0})

    engine.position_mgr.update_stop_loss_take_profit.assert_not_called()


def test_dynamic_sltp_skips_when_profit_below_threshold():
    listener, engine = _build_listener(threshold_pct=20.0)
    # avg_cost=20, latest=21 -> profit_ratio=5% < 20% threshold
    pos = _make_position("600519", avg_cost=20.0, stop_loss=18.0)
    engine.position_mgr.list_positions.return_value = [pos]

    with patch("paper_trading.sltp_calculator.build_sltp_calculator") as mock_builder:
        mock_calc = MagicMock()
        mock_builder.return_value = mock_calc
        listener._check_dynamic_sltp("cn", {"600519": 21.0})

    mock_calc.calculate.assert_not_called()
    engine.position_mgr.update_stop_loss_take_profit.assert_not_called()


def test_dynamic_sltp_skips_position_without_stop_loss():
    listener, engine = _build_listener(threshold_pct=20.0)
    pos = _make_position("600519", avg_cost=20.0, stop_loss=None)
    engine.position_mgr.list_positions.return_value = [pos]

    with patch("paper_trading.sltp_calculator.build_sltp_calculator"):
        listener._check_dynamic_sltp("cn", {"600519": 25.0})

    engine.position_mgr.update_stop_loss_take_profit.assert_not_called()
