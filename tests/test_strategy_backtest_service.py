# -*- coding: utf-8 -*-
"""策略回测服务 + 融合权重持久化测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper_trading.signal_fusion import SignalFusionEngine, FusionMethod
from paper_trading.strategy_backtest_service import (
    _compute_softmax_weights,
    load_fusion_weights,
)


class TestSoftmaxWeights:
    def test_softmax_normalizes(self):
        # 2 个策略：0.5 和 1.0 → SoftMax 后大的权重更高且和为 1
        weights = _compute_softmax_weights({
            "a": {"sharpe_ratio": 0.5, "trade_count": 10},
            "b": {"sharpe_ratio": 1.5, "trade_count": 10},
        })
        assert len(weights) == 2
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert weights["b"] > weights["a"]

    def test_zero_trade_strategies_excluded(self):
        weights = _compute_softmax_weights({
            "a": {"sharpe_ratio": 0.9, "trade_count": 10},
            "b": {"sharpe_ratio": 0.9, "trade_count": 0},  # 无交易 → 排除
        })
        assert "a" in weights
        assert "b" not in weights

    def test_empty_input(self):
        assert _compute_softmax_weights({}) == {}
        assert _compute_softmax_weights({"a": {"sharpe_ratio": 1.0, "trade_count": 0}}) == {}


class TestFusionWeightsPersistence:
    def test_set_and_get_weights(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        engine.set_weights({"macd_momentum": 0.36, "rsi_reversal": 0.36})
        assert engine.get_weights() == {"macd_momentum": 0.36, "rsi_reversal": 0.36}

    def test_update_weights_from_metrics_then_get(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        engine.update_weights_from_metrics({"a": 0.9, "b": 0.5})
        w = engine.get_weights()
        assert len(w) == 2
        assert w["a"] > w["b"]

    def test_empty_metrics_clears(self):
        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        engine.update_weights_from_metrics({"a": 0.9})
        engine.update_weights_from_metrics({})
        assert engine.get_weights() == {}

    def test_db_persistence_roundtrip(self, monkeypatch):
        """DB 持久化往返：save → load 一致（用真实 DB 的临时批次）。"""
        from datetime import date
        from unittest.mock import MagicMock

        # 用真实 DB 但独立批次日期（避免污染生产数据）
        import paper_trading.signal_fusion as sf
        from src.storage import DatabaseManager, StrategyBacktestResult

        db = DatabaseManager.get_instance()
        batch = date(2020, 1, 1)  # 远过去的批次，隔离测试
        try:
            # 清理该批次
            with db.get_session() as s:
                from sqlalchemy import delete
                s.execute(delete(StrategyBacktestResult).where(
                    StrategyBacktestResult.batch_date == batch))
                s.commit()

            engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
            engine.update_weights_from_metrics({"golden_cross": 0.2, "rsi_reversal": 1.0})
            saved = engine.save_weights_to_db(batch_date=batch)
            assert len(saved) == 2

            loaded = load_fusion_weights(batch_date=batch)
            assert set(loaded.keys()) == set(saved.keys())
            for k in saved:
                assert abs(loaded[k] - saved[k]) < 1e-6

            # 幂等：重复保存不报错（UNIQUE 命中则更新）
            engine.save_weights_to_db(batch_date=batch)
        finally:
            # 清理测试批次
            with db.get_session() as s:
                from sqlalchemy import delete
                s.execute(delete(StrategyBacktestResult).where(
                    StrategyBacktestResult.batch_date == batch))
                s.commit()


class TestFusionEngineWeightsInFuse:
    def test_weighted_vote_uses_persisted_weights(self):
        """融合时真实使用持久化权重（高权重策略主导）。"""
        from paper_trading.strategies import Signal

        engine = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
        engine.set_weights({"strong": 0.9, "weak": 0.1})

        signals = [
            Signal(side="buy", code="600519", name="x", strategy_name="strong",
                   rule_name="r", trigger_price=100.0, suggested_quantity=100, reason=""),
            Signal(side="sell", code="600519", name="x", strategy_name="weak",
                   rule_name="r", trigger_price=100.0, suggested_quantity=100, reason=""),
        ]
        fused = engine.fuse("600519", signals)
        assert fused is not None
        assert fused.side == "buy"  # 强策略(0.9) vs 弱策略(0.1) → buy 主导
