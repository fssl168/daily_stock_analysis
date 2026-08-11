# -*- coding: utf-8 -*-
"""Unit tests for backtest engine."""

import math
import unittest
from dataclasses import dataclass
from datetime import date, timedelta

from src.core.backtest_engine import BacktestEngine, EvaluationConfig


@dataclass
class Bar:
    date: date
    high: float
    low: float
    close: float


class BacktestEngineTestCase(unittest.TestCase):
    def _bars(self, start: date, closes, highs=None, lows=None):
        highs = highs or closes
        lows = lows or closes
        bars = []
        for i, c in enumerate(closes):
            bars.append(Bar(date=start + timedelta(days=i + 1), high=highs[i], low=lows[i], close=c))
        return bars

    def test_buy_win_when_up(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [102, 104, 105], highs=[103, 105, 106], lows=[101, 103, 104])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=95,
            take_profit=110,
            config=cfg,
        )
        self.assertEqual(res["eval_status"], "completed")
        self.assertEqual(res["outcome"], "win")
        self.assertTrue(res["direction_correct"])  # up

    def test_sell_win_when_down_cash(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [98, 97, 96], highs=[99, 98, 97], lows=[97, 96, 95])
        res = BacktestEngine.evaluate_single(
            operation_advice="卖出",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=95,
            take_profit=110,
            config=cfg,
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["outcome"], "win")
        self.assertEqual(res["simulated_return_pct"], 0.0)
        self.assertEqual(res["first_hit"], "not_applicable")

    def test_wait_maps_to_cash_and_flat_direction(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        # Stock drops ~5%: AI said wait (neutral), stock moved significantly → loss
        bars = self._bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="观望",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=95,
            take_profit=110,
            config=cfg,
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["direction_expected"], "flat")
        self.assertEqual(res["outcome"], "loss")

    def test_bearish_like_phrases_match_keyword_substring(self):
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("建议买入"),
            "long",
        )
        self.assertEqual(
            BacktestEngine.infer_direction_expected("继续持有"),
            "not_down",
        )
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("建议持有"),
            "long",
        )
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("建议洗盘观察"),
            "long",
        )

    def test_range_bound_watch_is_treated_as_hold_long_path(self):
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("震荡观望"),
            "long",
        )
        self.assertEqual(
            BacktestEngine.infer_direction_expected("Range-bound watch"),
            "not_down",
        )
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("Range-bound watch"),
            "long",
        )

    def test_shakeout_watch_is_treated_as_hold_long_path(self):
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("洗盘观察"),
            "long",
        )
        self.assertEqual(
            BacktestEngine.infer_direction_expected("Shakeout watch"),
            "not_down",
        )
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("Hold and watch"),
            "long",
        )

    def test_hold_win_when_flat(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [100.5, 100.2, 101], highs=[101, 101, 101], lows=[99.8, 99.9, 100])
        res = BacktestEngine.evaluate_single(
            operation_advice="持有",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["outcome"], "win")

    def test_hold_win_when_up(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [102, 103, 104], highs=[103, 104, 105], lows=[101, 102, 103])
        res = BacktestEngine.evaluate_single(
            operation_advice="持有",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["outcome"], "win")

    def test_decision_signal_helper_classifies_structured_up_not_down_and_not_up(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0, engine_version="decision-signal-v1")

        up = BacktestEngine.evaluate_decision_signal(
            direction_expected="up",
            anchor_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=self._bars(date(2024, 1, 1), [101, 102, 103]),
            config=cfg,
        )
        not_down = BacktestEngine.evaluate_decision_signal(
            direction_expected="not_down",
            anchor_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=self._bars(date(2024, 1, 1), [99.5, 99, 99]),
            config=cfg,
        )
        not_up = BacktestEngine.evaluate_decision_signal(
            direction_expected="not_up",
            anchor_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=self._bars(date(2024, 1, 1), [100.5, 101, 101.5]),
            config=cfg,
        )
        not_up_miss = BacktestEngine.evaluate_decision_signal(
            direction_expected="not_up",
            anchor_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=self._bars(date(2024, 1, 1), [101, 102, 103]),
            config=cfg,
        )

        self.assertEqual(up["outcome"], "hit")
        self.assertEqual(not_down["outcome"], "neutral")
        self.assertEqual(not_up["outcome"], "hit")
        self.assertEqual(not_up_miss["outcome"], "miss")

    def test_decision_signal_helper_rejects_non_finite_prices(self):
        cfg = EvaluationConfig(eval_window_days=1, neutral_band_pct=2.0, engine_version="decision-signal-v1")

        bad_start = BacktestEngine.evaluate_decision_signal(
            direction_expected="up",
            anchor_date=date(2024, 1, 1),
            start_price=math.nan,
            forward_bars=self._bars(date(2024, 1, 1), [103]),
            config=cfg,
        )
        bad_end = BacktestEngine.evaluate_decision_signal(
            direction_expected="up",
            anchor_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=[Bar(date=date(2024, 1, 2), high=101, low=99, close=math.nan)],
            config=cfg,
        )
        bad_bounds = BacktestEngine.evaluate_decision_signal(
            direction_expected="up",
            anchor_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=[Bar(date=date(2024, 1, 2), high=math.inf, low=-math.inf, close=103)],
            config=cfg,
        )

        self.assertEqual(bad_start["eval_status"], "unable")
        self.assertEqual(bad_start["unable_reason"], "invalid_anchor_price")
        self.assertEqual(bad_end["eval_status"], "unable")
        self.assertEqual(bad_end["unable_reason"], "invalid_end_close")
        self.assertEqual(bad_bounds["eval_status"], "completed")
        self.assertEqual(bad_bounds["stock_return_pct"], 3.0)
        self.assertIsNone(bad_bounds["max_high"])
        self.assertIsNone(bad_bounds["min_low"])

    def test_decision_signal_helper_does_not_change_evaluate_single_hold_behavior(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        res = BacktestEngine.evaluate_single(
            operation_advice="持有",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=self._bars(date(2024, 1, 1), [100.2, 100.4, 100.6]),
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )

        self.assertEqual(res["direction_expected"], "not_down")
        self.assertEqual(res["outcome"], "win")

    def test_stop_loss_hit_first(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [99, 98, 97], highs=[101, 100, 99], lows=[94, 97, 96])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=95,
            take_profit=110,
            config=cfg,
        )
        self.assertTrue(res["hit_stop_loss"])
        self.assertEqual(res["first_hit"], "stop_loss")
        self.assertEqual(res["simulated_exit_reason"], "stop_loss")

    def test_take_profit_hit_first(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [105, 106, 107], highs=[111, 107, 108], lows=[103, 105, 106])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=95,
            take_profit=110,
            config=cfg,
        )
        self.assertTrue(res["hit_take_profit"])
        self.assertEqual(res["first_hit"], "take_profit")
        self.assertEqual(res["simulated_exit_reason"], "take_profit")

    def test_ambiguous_same_day(self):
        cfg = EvaluationConfig(eval_window_days=2, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [100, 100], highs=[111, 100], lows=[94, 99])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=95,
            take_profit=110,
            config=cfg,
        )
        self.assertEqual(res["first_hit"], "ambiguous")
        self.assertEqual(res["simulated_exit_reason"], "ambiguous_stop_loss")

    def test_buy_loss_when_down(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=93,
            take_profit=110,
            config=cfg,
        )
        self.assertEqual(res["eval_status"], "completed")
        self.assertEqual(res["outcome"], "loss")
        self.assertFalse(res["direction_correct"])

    def test_hold_loss_when_down(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [98, 96, 95], highs=[99, 97, 96], lows=[97, 95, 94])
        res = BacktestEngine.evaluate_single(
            operation_advice="持有",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["direction_expected"], "not_down")
        self.assertEqual(res["outcome"], "loss")
        self.assertFalse(res["direction_correct"])

    def test_sell_loss_when_up(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [102, 104, 106], highs=[103, 105, 107], lows=[101, 103, 105])
        res = BacktestEngine.evaluate_single(
            operation_advice="卖出",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["direction_expected"], "down")
        self.assertEqual(res["outcome"], "loss")
        self.assertFalse(res["direction_correct"])

    def test_neutral_outcome(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [100.5, 100.2, 100.8], highs=[101, 101, 101], lows=[100, 100, 100])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["direction_expected"], "up")
        self.assertEqual(res["outcome"], "neutral")
        self.assertIsNone(res["direction_correct"])

    def test_direction_correct_false_buy_down(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [97, 95, 94], highs=[98, 96, 95], lows=[96, 94, 93])
        res = BacktestEngine.evaluate_single(
            operation_advice="buy",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["direction_expected"], "up")
        self.assertEqual(res["outcome"], "loss")
        self.assertFalse(res["direction_correct"])

    def test_insufficient_data(self):
        cfg = EvaluationConfig(eval_window_days=5, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [100, 101])
        res = BacktestEngine.evaluate_single(
            operation_advice="买入",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["eval_status"], "insufficient_data")

    def test_unrecognized_advice_defaults_to_cash(self):
        cfg = EvaluationConfig(eval_window_days=3, neutral_band_pct=2.0)
        bars = self._bars(date(2024, 1, 1), [102, 104, 105], highs=[103, 105, 106], lows=[101, 103, 104])
        res = BacktestEngine.evaluate_single(
            operation_advice="some gibberish text",
            analysis_date=date(2024, 1, 1),
            start_price=100,
            forward_bars=bars,
            stop_loss=None,
            take_profit=None,
            config=cfg,
        )
        self.assertEqual(res["position_recommendation"], "cash")
        self.assertEqual(res["direction_expected"], "flat")

    def test_none_empty_advice_defaults_to_cash(self):
        for advice in [None, "", "   "]:
            pos = BacktestEngine.infer_position_recommendation(advice)
            direction = BacktestEngine.infer_direction_expected(advice)
            self.assertEqual(pos, "cash", f"Expected cash for advice={advice!r}")
            self.assertEqual(direction, "flat", f"Expected flat for advice={advice!r}")

    def test_negated_sell_not_classified_bearish(self):
        # "do not sell" negates "sell" — should NOT be direction=down
        self.assertNotEqual(BacktestEngine.infer_direction_expected("do not sell"), "down")

    def test_chinese_negated_sell_not_bearish(self):
        # "不要卖出" = "don't sell" — should NOT be direction=down
        self.assertNotEqual(BacktestEngine.infer_direction_expected("不要卖出"), "down")

    def test_conditional_support_phrase_not_negating_hold(self):
        # "不跌破支撑继续持有" means conditional support hold, not explicit negation of hold.
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("不跌破支撑继续持有"),
            "long",
        )
        self.assertEqual(
            BacktestEngine.infer_direction_expected("不跌破支撑继续持有"),
            "not_down",
        )

    def test_wait_then_buy_classified_as_cash(self):
        # "wait" matches first in priority order → cash
        pos = BacktestEngine.infer_position_recommendation("wait for a dip then buy")
        self.assertEqual(pos, "cash")

    def test_wait_phrase_before_bullish_phrases_stays_wait(self):
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("先观望再买入"),
            "cash",
        )
        self.assertEqual(
            BacktestEngine.infer_direction_expected("先观望再买入"),
            "flat",
        )
        self.assertEqual(
            BacktestEngine.infer_position_recommendation("观望后买入"),
            "cash",
        )
        self.assertEqual(
            BacktestEngine.infer_direction_expected("观望后买入"),
            "flat",
        )


if __name__ == "__main__":
    unittest.main()

# ============================================================================
# T5: paper_trading.backtest (BacktestEngine + WalkforwardOptimizer)
# Appended to the existing file; the src.core.backtest_engine tests above are
# preserved unchanged. Aliased imports avoid rebinding names used up-file.
# ============================================================================

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from paper_trading.backtest import (
    BacktestConfig as PTBacktestConfig,
    BacktestEngine as PTBacktestEngine,
    BacktestResult as PTBacktestResult,
    DailySnapshot as PTDailySnapshot,
    WalkforwardConfig as PTWalkforwardConfig,
    WalkforwardOptimizer as PTWalkforwardOptimizer,
    WalkforwardResult as PTWalkforwardResult,
)
from paper_trading.fees import FeeModel
from paper_trading.strategies.engine.rule_engine import RuleEngine, Signal
from paper_trading.strategies.engine.schema import Rule, RuleStrategy


def _dates(n, start=date(2024, 1, 2)):
    return [start + timedelta(days=i) for i in range(n)]


def _df(closes, start=date(2024, 1, 2), pad=1.0):
    n = len(closes)
    return pd.DataFrame(
        {
            "open": [c - 0.5 for c in closes],
            "high": [c + pad for c in closes],
            "low": [c - pad for c in closes],
            "close": [float(c) for c in closes],
            "volume": [10000] * n,
        },
        index=pd.to_datetime(_dates(n, start)),
    )


def _strategy(name="t5", entry=None, exit_=None, lot_size=100):
    return RuleStrategy(
        name=name,
        display_name=name,
        description="test strategy",
        entry_rules=[entry] if entry else [],
        exit_rules=[exit_] if exit_ else [],
        params={"lot_size": lot_size},
    )


def _zero_fee_config(**overrides):
    base = dict(
        slippage_bps=0.0,
        commission_bps=0.0,
        stamp_duty_bps=0.0,
        min_commission=0.0,
    )
    base.update(overrides)
    return PTBacktestConfig(**base)


# ---------------------------------------------------------------------------
# Basic run
# ---------------------------------------------------------------------------


class TestBacktestBasicRun:
    def test_run_produces_snapshots_and_trades(self):
        strat = _strategy(
            entry=Rule("close", ">", "100"), exit_=Rule("close", "<", "100")
        )
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(
            ["000001"], [strat], {"000001": _df([99, 101, 102, 103, 99])}
        )
        assert len(result.snapshots) == 5
        snap = result.snapshots[0]
        assert isinstance(snap, PTDailySnapshot)
        assert snap.date == date(2024, 1, 2)
        assert snap.cash >= 0
        assert snap.total_assets > 0
        assert isinstance(snap.positions, dict)
        assert snap.daily_return == 0.0
        assert snap.cumulative_return == pytest.approx(0.0)
        assert snap.benchmark_return == 0.0

        executed = [t for t in result.trades if t["status"] == "executed"]
        assert executed, "expected at least one executed fill"
        assert {t["side"] for t in executed} == {"buy", "sell"}
        # Trade dict is aligned with TradeResult.to_dict() shape.
        for t in executed:
            for key in ("side", "code", "quantity", "price", "fee", "status"):
                assert key in t
            assert t["fill_price"] == t["price"]
            assert t["fill_quantity"] == t["quantity"]

    def test_run_single_strategy_broadcast_to_multiple_codes(self):
        strat = _strategy(
            entry=Rule("close", ">", "100"), exit_=Rule("close", "<", "100")
        )
        engine = PTBacktestEngine(_zero_fee_config())
        data = {
            "000001": _df([99, 101, 100, 99]),
            "000002": _df([98, 99, 97, 96]),
        }
        result = engine.run(["000001", "000002"], [strat], data)
        assert len(result.snapshots) == 4
        assert any(t["code"] == "000001" and t["status"] == "executed" for t in result.trades)

    def test_run_one_strategy_per_code(self):
        buy = _strategy("buyer", entry=Rule("close", ">", "100"), exit_=Rule("close", "<", "100"))
        sell = _strategy("loser", entry=Rule("close", ">", "10"), exit_=Rule("close", "<", "10"))
        engine = PTBacktestEngine(_zero_fee_config())
        data = {
            "A": _df([99, 101, 100, 99]),
            "B": _df([9, 11, 12, 9]),
        }
        result = engine.run(["A", "B"], [buy, sell], data)
        assert len(result.snapshots) == 4
        codes = {t["code"] for t in result.trades if t["status"] == "executed"}
        assert codes == {"A", "B"}

    def test_strategies_length_mismatch_raises(self):
        engine = PTBacktestEngine(_zero_fee_config())
        with pytest.raises(ValueError):
            engine.run(["A", "B", "C"], [_strategy(), _strategy()], {"A": _df([1, 2]), "B": _df([1, 2]), "C": _df([1, 2])})

    def test_market_value_position_cap_creates_rejected_buy(self):
        strat = _strategy(entry=Rule("close", ">", "100"))
        engine = PTBacktestEngine(_zero_fee_config(max_position_pct=0.05))
        result = engine.run(["A"], [strat], {"A": _df([99, 101, 102, 103])})
        reasons = {t["reason"] for t in result.trades}
        assert "insufficient_cash" in reasons


# ---------------------------------------------------------------------------
# Slippage / fees / limit up-down / range
# ---------------------------------------------------------------------------


class TestBacktestFills:
    def test_slippage_applied_to_buy_and_sell(self):
        strat = _strategy(
            entry=Rule("close", ">", "99"), exit_=Rule("close", "<", "99.5")
        )
        engine = PTBacktestEngine(PTBacktestConfig(slippage_bps=5.0))
        result = engine.run(["A"], [strat], {"A": _df([98, 100, 100, 99])})
        executed = [t for t in result.trades if t["status"] == "executed"]
        buys = [t for t in executed if t["side"] == "buy"]
        sells = [t for t in executed if t["side"] == "sell"]
        assert buys and sells
        # Buy pays more than the trigger price; sell receives less.
        assert buys[0]["price"] == pytest.approx(100 * (1 + 0.0005), abs=1e-6)
        assert sells[0]["price"] == pytest.approx(99 * (1 - 0.0005), abs=1e-6)

    def test_slippage_disabled_when_zero(self):
        strat = _strategy(entry=Rule("close", ">", "99"))
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [strat], {"A": _df([98, 100, 100])})
        buys = [t for t in result.trades if t["status"] == "executed" and t["side"] == "buy"]
        assert buys[0]["price"] == pytest.approx(100.0)

    def test_fee_model_built_from_config_and_fees_applied(self):
        config = PTBacktestConfig(
            initial_cash=3000.0,
            max_position_pct=0.5,
            commission_bps=2.5,
            stamp_duty_bps=10.0,
            min_commission=5.0,
            slippage_bps=5.0,
            lot_size=100,
            enable_limit_up_down=False,
        )
        engine = PTBacktestEngine(config)
        expected_model = FeeModel(
            commission_rate=0.00025,
            commission_min=5.0,
            stamp_duty_rate=0.001,
            transfer_fee_rate=0.0,
            slippage_bps=5.0,
        )
        assert engine.fee_model == expected_model

        strat = _strategy(
            entry=Rule("close", ">", "5"), exit_=Rule("close", "<", "9.5")
        )
        result = engine.run(["A"], [strat], {"A": _df([4, 10, 9.9, 9.4])})
        executed = [t for t in result.trades if t["status"] == "executed"]
        buys = [t for t in executed if t["side"] == "buy"]
        sells = [t for t in executed if t["side"] == "sell"]
        assert len(buys) == 1 and len(sells) == 1
        buy = buys[0]
        assert buy["quantity"] == 100.0
        # Commission 1000.5 * 0.00025 = 0.25 < min 5 -> min commission applies.
        assert buy["fee"] == pytest.approx(5.0, abs=1e-6)
        assert buy["fee"] == pytest.approx(
            expected_model.compute_fee("buy", buy["price"], buy["quantity"]), abs=1e-6
        )
        sell = sells[0]
        assert sell["fee"] == pytest.approx(
            expected_model.compute_fee("sell", sell["price"], sell["quantity"]), abs=1e-6
        )
        # Sell-side fee includes stamp duty -> strictly larger than buy fee.
        assert sell["fee"] > buy["fee"]

    def test_limit_up_blocks_buy(self):
        strat = _strategy(entry=Rule("close", ">", "105"))
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [strat], {"A": _df([100, 110])})
        assert len(result.trades) == 1
        assert result.trades[0]["status"] == "rejected"
        assert result.trades[0]["reason"] == "limit_up"
        assert result.trades[0]["side"] == "buy"
        assert all(t["status"] != "executed" for t in result.trades)

    def test_limit_down_blocks_sell(self):
        strat = _strategy(
            entry=Rule("close", ">", "101"), exit_=Rule("close", "<", "101")
        )
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [strat], {"A": _df([100, 102, 91.8])})
        reasons = [t["reason"] for t in result.trades]
        assert "limit_down" in reasons
        sells = [t for t in result.trades if t["side"] == "sell"]
        assert sells and sells[-1]["status"] == "rejected"
        # Position was NOT closed -> still marked to market on the last snapshot.
        assert result.snapshots[-1].positions.get("A", 0.0) > 0

    def test_limit_up_down_disabled_allows_fill(self):
        strat = _strategy(
            entry=Rule("close", ">", "105"), exit_=Rule("close", "<", "101")
        )
        engine = PTBacktestEngine(_zero_fee_config(enable_limit_up_down=False))
        result = engine.run(["A"], [strat], {"A": _df([100, 110, 100])})
        executed = [t for t in result.trades if t["status"] == "executed"]
        assert any(t["side"] == "buy" for t in executed)
        assert any(t["side"] == "sell" for t in executed)

    def test_out_of_range_fill_rejected(self):
        strat = _strategy(entry=Rule("close", ">", "5"))
        engine = PTBacktestEngine(
            PTBacktestConfig(slippage_bps=5.0, enable_limit_up_down=False)
        )
        df = _df([4, 10])
        df.loc[df.index[1], "high"] = 9.9  # fill 10.005 exceeds the bar high
        result = engine.run(["A"], [strat], {"A": df})
        assert len(result.trades) == 1
        assert result.trades[0]["reason"] == "out_of_range"
        assert result.trades[0]["status"] == "rejected"

    def test_sell_without_position_not_recorded(self):
        strat = _strategy(exit_=Rule("close", "<", "100"))
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [strat], {"A": _df([99, 98, 97])})
        assert result.trades == []


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------


class TestBacktestMetrics:
    def _mixed_result(self):
        # Buy on cross_up above 95, sell on cross_down below 99.
        strat = _strategy(
            entry=Rule("close", "cross_up", "95"),
            exit_=Rule("close", "cross_down", "99"),
        )
        closes = [94, 96, 100, 101, 98, 94, 98, 101, 104, 99, 97]
        engine = PTBacktestEngine(_zero_fee_config(max_position_pct=0.20))
        return engine.run(["A"], [strat], {"A": _df(closes)})

    def test_total_return_maxdd_winrate(self):
        result = self._mixed_result()
        assert result.total_return == pytest.approx(0.002, abs=1e-9)
        assert result.max_drawdown == pytest.approx(1 - 100200 / 101600, abs=1e-9)
        assert result.max_drawdown_duration == 2
        assert result.win_rate == pytest.approx(0.5, abs=1e-9)
        assert result.profit_loss_ratio == pytest.approx(2.0, abs=1e-9)
        assert result.avg_hold_days == pytest.approx(3.5, abs=1e-9)

    def test_sharpe_annualized_sqrt242(self):
        result = self._mixed_result()
        daily = np.array([s.daily_return for s in result.snapshots], dtype=float)
        expected = float(np.mean(daily)) / float(np.std(daily, ddof=1)) * math.sqrt(242)
        assert result.sharpe_ratio == pytest.approx(expected, abs=1e-9)
        assert result.sharpe_ratio > 0

    def test_annual_and_calmar(self):
        result = self._mixed_result()
        n = len(result.snapshots)
        expected_annual = (1.002) ** (242 / n) - 1.0
        assert result.annual_return == pytest.approx(expected_annual, abs=1e-9)
        assert result.calmar_ratio == pytest.approx(
            expected_annual / (1 - 100200 / 101600), abs=1e-9
        )

    def test_no_trades_returns_zero_metrics(self):
        strat = _strategy(entry=Rule("close", ">", "1000"))
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [strat], {"A": _df([10, 11, 12])})
        assert result.trades == []
        assert result.total_return == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.max_drawdown == 0.0
        assert result.win_rate == 0.0
        assert result.profit_loss_ratio == 0.0
        assert result.avg_hold_days == 0.0
        assert result.calmar_ratio == 0.0
        assert result.benchmark_return == 0.0
        assert result.excess_return == 0.0


# ---------------------------------------------------------------------------
# Empty data / benchmark
# ---------------------------------------------------------------------------


class TestBacktestEdgeCases:
    def test_empty_daily_data(self):
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [_strategy()], {})
        assert result.snapshots == []
        assert result.trades == []
        assert result.total_return == 0.0

    def test_empty_dataframe_for_code(self):
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [_strategy()], {"A": pd.DataFrame()})
        assert result.snapshots == []
        assert result.trades == []

    def test_missing_close_column_skipped(self):
        engine = PTBacktestEngine(_zero_fee_config())
        df = pd.DataFrame({"open": [1, 2]}, index=pd.to_datetime(_dates(2)))
        result = engine.run(["A"], [_strategy()], {"A": df})
        assert result.snapshots == []

    def test_dates_outside_config_range(self):
        cfg = _zero_fee_config(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        engine = PTBacktestEngine(cfg)
        result = engine.run(["A"], [_strategy()], {"A": _df([1, 2, 3], start=date(2024, 1, 2))})
        assert result.snapshots == []

    def test_no_codes(self):
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run([], [_strategy()], {"A": _df([1, 2])})
        assert result.snapshots == []

    def test_benchmark_comparison(self):
        strat = _strategy(entry=Rule("close", ">", "1000"))  # never trades
        engine = PTBacktestEngine(_zero_fee_config())
        bm = _df([100, 110, 121])
        result = engine.run(["A"], [strat], {"A": _df([90, 95, 99])}, benchmark_df=bm)
        assert result.benchmark_return == pytest.approx(0.21, abs=1e-9)
        assert [s.benchmark_return for s in result.snapshots] == pytest.approx(
            [0.0, 0.10, 0.21], abs=1e-9
        )
        assert result.excess_return == pytest.approx(-0.21, abs=1e-9)

    def test_benchmark_none_returns_zero(self):
        strat = _strategy(entry=Rule("close", ">", "1000"))
        engine = PTBacktestEngine(_zero_fee_config())
        result = engine.run(["A"], [strat], {"A": _df([90, 95, 99])})
        assert result.benchmark_return == 0.0
        assert result.excess_return == 0.0

    def test_benchmark_missing_close_column(self):
        strat = _strategy(entry=Rule("close", ">", "1000"))
        engine = PTBacktestEngine(_zero_fee_config())
        bm = pd.DataFrame({"open": [1, 2, 3]}, index=pd.to_datetime(_dates(3)))
        result = engine.run(["A"], [strat], {"A": _df([90, 95, 99])}, benchmark_df=bm)
        assert result.benchmark_return == 0.0


# ---------------------------------------------------------------------------
# No lookahead + dependency injection
# ---------------------------------------------------------------------------


class RecordingRuleEngine(RuleEngine):
    """Records the history length fed to each evaluation (lookahead check)."""

    def __init__(self):
        super().__init__()
        self.history_lengths = []
        self.evaluate_calls = 0

    def evaluate(self, strategy, df, code, name=None):
        self.evaluate_calls += 1
        self.history_lengths.append(len(df))
        return Signal(
            side="none", code=code, name=name, strategy_name=strategy.name,
            rule_name=None, trigger_price=0.0, suggested_quantity=None, reason="none",
        )


class FakeRuleEngine:
    """Injected rule engine that always emits a buy signal."""

    def __init__(self, price=10.0, qty=100.0):
        self.price = price
        self.qty = qty
        self.calls = 0

    def evaluate(self, strategy, df, code, name=None):
        self.calls += 1
        return Signal(
            side="buy", code=code, name=name, strategy_name=strategy.name,
            rule_name="fake", trigger_price=self.price, suggested_quantity=self.qty,
            reason="fake signal",
        )


class FakeFeeModel:
    """Injected fee model: no slippage, flat 1 CNY fee per fill."""

    def apply_slippage(self, price, side):
        return price

    def compute_fee(self, side, price, quantity):
        return 1.0

    def estimate_buy_cost(self, price, quantity):
        return price * quantity + 1.0


class TestNoLookaheadAndDI:
    def test_each_bar_evaluation_only_sees_history(self):
        recorder = RecordingRuleEngine()
        engine = PTBacktestEngine(_zero_fee_config(), rule_engine=recorder)
        df = _df([90, 91, 92, 93, 94])
        engine.run(["A"], [_strategy()], {"A": df})
        # One evaluation per bar, each seeing only rows up to that bar.
        assert recorder.history_lengths == [1, 2, 3, 4, 5]

    def test_ensure_no_lookahead_helper(self):
        df = _df([1, 2, 3, 4, 5])
        truncated = PTBacktestEngine._ensure_no_lookahead(df, 2)
        assert len(truncated) == 3
        assert list(truncated["close"]) == [1.0, 2.0, 3.0]

    def test_injected_rule_engine_and_fee_model_used(self):
        fake_engine = FakeRuleEngine(price=10.0, qty=100.0)
        fake_fees = FakeFeeModel()
        engine = PTBacktestEngine(
            _zero_fee_config(max_position_pct=0.3),
            rule_engine=fake_engine,
            fee_model=fake_fees,
        )
        result = engine.run(["A"], [_strategy()], {"A": _df([9, 10, 10, 10])})
        assert fake_engine.calls == 4
        executed = [t for t in result.trades if t["status"] == "executed" and t["side"] == "buy"]
        assert executed
        assert executed[0]["price"] == 10.0
        assert executed[0]["fee"] == 1.0
        # budget = 30000 -> floor(30000/10/100)*100 = 3000; 3001 > budget so
        # reduce by one lot -> 2900 shares @ 10 + 1 fee = 29001 <= 30000.
        assert executed[0]["quantity"] == 2900.0

    def test_injected_rule_engine_buy_only_fills_executed(self):
        fake_engine = FakeRuleEngine()
        engine = PTBacktestEngine(
            _zero_fee_config(enable_limit_up_down=False), rule_engine=fake_engine
        )
        result = engine.run(["A"], [_strategy()], {"A": _df([9, 10, 10, 10])})
        assert result.trades
        executed = [t for t in result.trades if t["status"] == "executed"]
        rejected = [t for t in result.trades if t["status"] == "rejected"]
        assert executed and all(t["side"] == "buy" for t in executed)
        assert rejected and all(t["reason"] == "insufficient_cash" for t in rejected)


# ---------------------------------------------------------------------------
# Walk-forward optimizer
# ---------------------------------------------------------------------------


def _fake_result(sharpe=0.5, total_return=0.1, max_dd=0.05):
    return PTBacktestResult(
        config=PTBacktestConfig(),
        snapshots=[],
        trades=[],
        total_return=total_return,
        annual_return=0.0,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        max_drawdown_duration=0,
        win_rate=0.5,
        profit_loss_ratio=1.0,
        avg_hold_days=1.0,
        calmar_ratio=0.0,
        benchmark_return=0.0,
        excess_return=0.0,
    )


class FakeEngine:
    """Injected engine stub for the walk-forward optimizer DI test."""

    def __init__(self, result=None):
        self.result = result or _fake_result()
        self.calls = 0

    def run(self, codes, strategies, daily_data, benchmark_df=None):
        self.calls += 1
        return self.result


class TestWalkforward:
    def _strategy(self):
        return _strategy(
            entry=Rule("close", ">", "100"), exit_=Rule("close", "<", "100")
        )

    def test_basic_run(self):
        optimizer = PTWalkforwardOptimizer()
        config = PTWalkforwardConfig(
            train_window_days=3,
            test_window_days=2,
            step_days=2,
            param_grid={"lot_size": [100, 200]},
        )
        closes = [99, 101, 102, 100, 98, 101, 103, 99]
        result = optimizer.run(self._strategy(), _df(closes), config)
        assert isinstance(result, PTWalkforwardResult)
        assert len(result.windows) == 2
        for w in result.windows:
            assert w.best_params["lot_size"] in (100, 200)
            assert w.test_start > w.train_end
            assert isinstance(w.sharpe, float)
            assert isinstance(w.total_return, float)
        assert result.out_of_sample_sharpe == pytest.approx(
            float(np.mean([w.sharpe for w in result.windows])), abs=1e-12
        )
        assert result.best_params
        assert result.param_stability["lot_size"] == pytest.approx(1.0, abs=1e-9)

    def test_no_param_grid_uses_strategy_params(self):
        optimizer = PTWalkforwardOptimizer()
        config = PTWalkforwardConfig(train_window_days=3, test_window_days=2, step_days=2)
        closes = [99, 101, 102, 100, 98, 101, 103, 99]
        result = optimizer.run(self._strategy(), _df(closes), config)
        assert result.windows
        assert result.windows[0].best_params == {"lot_size": 100}

    def test_empty_data(self):
        optimizer = PTWalkforwardOptimizer()
        config = PTWalkforwardConfig(train_window_days=3, test_window_days=2, step_days=2)
        result = optimizer.run(self._strategy(), pd.DataFrame(), config)
        assert result.windows == []
        assert result.out_of_sample_sharpe == 0.0
        assert result.best_params == {}

    def test_data_too_short(self):
        optimizer = PTWalkforwardOptimizer()
        config = PTWalkforwardConfig(train_window_days=5, test_window_days=3, step_days=1)
        result = optimizer.run(self._strategy(), _df([1, 2, 3, 4]), config)
        assert result.windows == []

    def test_dependency_injection_mock_engine(self):
        fake = FakeEngine()
        optimizer = PTWalkforwardOptimizer(engine=fake)
        config = PTWalkforwardConfig(
            train_window_days=3,
            test_window_days=2,
            step_days=2,
            param_grid={"lot_size": [100, 200]},
        )
        result = optimizer.run(self._strategy(), _df([99, 101, 102, 100, 98, 101, 103, 99]), config)
        assert fake.calls > 0
        assert len(result.windows) == 2
        assert result.out_of_sample_sharpe == pytest.approx(0.5, abs=1e-12)
        assert result.out_of_sample_return == pytest.approx(0.1, abs=1e-12)
