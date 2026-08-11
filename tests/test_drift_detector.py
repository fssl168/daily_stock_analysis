# -*- coding: utf-8 -*-
"""Tests for paper_trading/drift_detector.py (T4)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.drift_detector import ANNUALIZATION_FACTOR, SHARPE_WINDOW, DriftDetector, DriftReport


def _reference_rolling_sharpe(pnl, window=SHARPE_WINDOW):
    """独立参考实现：与模块内滑动年化 Sharpe 定义一致。"""
    arr = np.asarray(pnl, dtype=float)
    out = []
    for i in range(window, len(arr) + 1):
        w = arr[i - window : i]
        mean = float(np.mean(w))
        std = float(np.std(w))
        out.append((mean / std) * np.sqrt(ANNUALIZATION_FACTOR) if std > 0 else 0.0)
    return out


class TestInsufficientData:
    def test_below_min_trades_returns_keep(self):
        det = DriftDetector()
        for _ in range(19):
            det.record_daily_pnl("s1", 0.01)
        report = det.check("s1")
        assert report.strategy_name == "s1"
        assert report.is_drifting is False
        assert report.rolling_sharpe == []
        assert report.sharpe_trend == 0.0
        assert report.consecutive_losing_days == 0
        assert report.recommended_action == "keep"

    def test_unknown_strategy_returns_keep(self):
        det = DriftDetector()
        report = det.check("never_recorded")
        assert report.is_drifting is False
        assert report.recommended_action == "keep"

    def test_custom_min_trades(self):
        det = DriftDetector(min_trades=5)
        for _ in range(4):
            det.record_daily_pnl("s", 0.01)
        assert det.check("s").recommended_action == "keep"

    def test_sufficient_but_no_full_sharpe_window_returns_keep(self):
        # min_trades < SHARPE_WINDOW：样本够 min_trades，但凑不满一个 20 日窗口。
        det = DriftDetector(min_trades=5)
        for _ in range(10):
            det.record_daily_pnl("s", 0.01)
        report = det.check("s")
        assert report.is_drifting is False
        assert report.rolling_sharpe == []
        assert report.recommended_action == "keep"


class TestRollingSharpe:
    def test_matches_reference_implementation(self):
        det = DriftDetector()
        pnl = np.linspace(0.02, 0.001, 60)
        for p in pnl:
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        assert report.rolling_sharpe == pytest.approx(_reference_rolling_sharpe(pnl))

    def test_single_window_length(self):
        # 恰好 min_trades=20 条 → 只产生 1 个滑动 Sharpe 点。
        det = DriftDetector()
        for p in np.linspace(0.01, -0.01, 20):
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        assert len(report.rolling_sharpe) == 1

    def test_zero_std_returns_zero_sharpe(self):
        det = DriftDetector()
        for _ in range(40):
            det.record_daily_pnl("s", 0.5)
        report = det.check("s")
        assert len(report.rolling_sharpe) == 21  # 40 - 20 + 1
        assert report.rolling_sharpe == [0.0] * 21
        assert report.recommended_action == "keep"


class TestSharpeTrend:
    def test_trend_matches_polyfit_slope(self):
        det = DriftDetector()
        pnl = np.linspace(0.02, 0.001, 60)
        for p in pnl:
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        expected = float(np.polyfit(np.arange(len(report.rolling_sharpe)), report.rolling_sharpe, 1)[0])
        assert report.sharpe_trend == pytest.approx(round(expected, 4))

    def test_single_rolling_window_trend_is_zero(self):
        det = DriftDetector()
        for p in np.linspace(0.01, -0.01, 20):
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        assert len(report.rolling_sharpe) == 1
        assert report.sharpe_trend == 0.0


class TestConsecutiveLosingDays:
    def test_counts_trailing_losses_only(self):
        det = DriftDetector()
        pnl = [0.01] * 10 + [-0.005] * 5 + [0.01] * 2 + [-0.005] * 3
        for p in pnl:
            det.record_daily_pnl("s", p)
        report = det.check("s")
        assert report.consecutive_losing_days == 3
        assert report.recommended_action == "keep"

    def test_no_losses_returns_zero(self):
        det = DriftDetector()
        for _ in range(30):
            det.record_daily_pnl("s", 0.01)
        assert det.check("s").consecutive_losing_days == 0


class TestActions:
    def test_reduce_weight(self):
        det = DriftDetector()
        pnl = np.linspace(0.02, 0.001, 60)
        for p in pnl:
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        assert report.is_drifting is True
        assert len(report.rolling_sharpe) >= 30
        assert report.sharpe_trend < -0.01
        assert report.rolling_sharpe[-1] > 0  # 不满足 pause
        assert report.consecutive_losing_days < 15
        assert report.recommended_action == "reduce_weight"

    def test_pause(self):
        det = DriftDetector()
        pnl = list(np.linspace(0.02, -0.06, 55)) + [0.005] * 5
        for p in pnl:
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        assert report.is_drifting is True
        assert report.rolling_sharpe[-1] <= 0.0
        assert report.sharpe_trend < -0.02
        assert report.consecutive_losing_days < 15  # 尾部正收益打断连续亏损
        assert report.recommended_action == "pause"

    def test_retire(self):
        det = DriftDetector()
        pnl = np.linspace(0.01, -0.01, 40)
        for p in pnl:
            det.record_daily_pnl("s", float(p))
        report = det.check("s")
        assert report.is_drifting is True
        assert report.consecutive_losing_days >= 15
        # retire 优先级最高：即使同时满足 pause 条件，也返回 retire。
        assert report.recommended_action == "retire"


class TestStrategyIsolation:
    def test_multiple_strategies_isolated_state(self):
        det = DriftDetector()
        # 策略 A：持续走弱 → reduce_weight
        for p in np.linspace(0.02, 0.001, 60):
            det.record_daily_pnl("weak", float(p))
        # 策略 B：平稳正收益 → keep
        for _ in range(30):
            det.record_daily_pnl("stable", 0.01)
        # 策略 C：样本不足 → keep
        det.record_daily_pnl("new", 0.01)

        rep_weak = det.check("weak")
        rep_stable = det.check("stable")
        rep_new = det.check("new")

        assert rep_weak.recommended_action == "reduce_weight"
        assert rep_stable.recommended_action == "keep"
        assert rep_new.recommended_action == "keep"
        # 各策略数据互不污染。
        assert len(rep_stable.rolling_sharpe) == 11  # 30 - 20 + 1
        assert rep_stable.consecutive_losing_days == 0
        assert rep_weak.consecutive_losing_days == 0

    def test_window_trims_old_records(self):
        det = DriftDetector(window_days=60)
        for p in np.linspace(0.02, 0.001, 80):
            det.record_daily_pnl("s", float(p))
        assert len(det._daily_pnl["s"]) == 60  # 滚动窗口只保留最近 60 条
        report = det.check("s")
        assert len(report.rolling_sharpe) == 41  # 60 - 20 + 1


class TestDriftReport:
    def test_report_defaults(self):
        rep = DriftReport(strategy_name="s")
        assert rep.is_drifting is False
        assert rep.rolling_sharpe == []
        assert rep.sharpe_trend == 0.0
        assert rep.consecutive_losing_days == 0
        assert rep.recommended_action == "keep"
