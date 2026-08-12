# -*- coding: utf-8 -*-
"""pending-api §1/§3 数据源单元测试（TASK-004/TASK-003）.

覆盖：
- TickLatencyAggregator 的报表契约（空样本全零、阶段分位、清空）
- MarketListener._record_tick_latency 的四阶段归一化
- PaperTradingEventBus 的事件/告警消息形状 + 重放 + 注销
"""

from __future__ import annotations

from src.utils.latency_tracker import TickLatencyAggregator
from paper_trading.events import (
    PaperTradingEventBus,
    emit_risk_alert,
    emit_trade_event,
)


class TestTickLatencyAggregator:
    def test_no_samples_returns_zeros_and_empty_steps(self):
        agg = TickLatencyAggregator(window_size=10)
        report = agg.report()
        assert report["tick_total_ms"] == {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        assert report["steps"] == []

    def test_records_and_reports_phase_percentiles(self):
        agg = TickLatencyAggregator(window_size=100)
        for i in range(1, 4):
            agg.record(
                {
                    "operation": "tick_market",
                    "total_ms": float(i * 10),
                    "steps": {
                        "data_fetch": float(i),
                        "signal_calc": float(i * 2),
                        "risk_check": float(i * 3),
                        "order_execute": float(i * 4),
                    },
                }
            )
        report = agg.report()
        assert report["tick_total_ms"]["p50"] == 20.0
        names = [s["name"] for s in report["steps"]]
        assert names == ["data_fetch", "signal_calc", "risk_check", "order_execute"]
        assert report["steps"][0]["p50_ms"] == 2.0

    def test_clear_resets(self):
        agg = TickLatencyAggregator(window_size=10)
        agg.record({"total_ms": 1.0, "steps": {"data_fetch": 1.0}})
        agg.clear()
        assert agg.count() == 0
        assert agg.report()["steps"] == []


class TestListenerRecordNormalizesPhases:
    def test_record_maps_span_steps_to_four_phases(self):
        from paper_trading.market_listener import MarketListener

        agg = TickLatencyAggregator(window_size=10)
        # 用 object.__new__ 绕过重型初始化，仅测归一化逻辑。
        listener = object.__new__(MarketListener)
        listener._latency_tracker = agg
        listener._record_tick_latency(
            {
                "operation": "tick_market",
                "total_ms": 150.0,
                "steps": {
                    "data_fetch": 30.0,
                    "signal_calc": 45.0,
                    "risk_check": 15.0,
                    "tick_market.end": 60.0,
                },
                "trace_id": "t1",
            }
        )
        assert agg.count() == 1
        report = agg.report()
        assert report["tick_total_ms"]["p50"] == 150.0
        assert [s["name"] for s in report["steps"]] == [
            "data_fetch",
            "signal_calc",
            "risk_check",
            "order_execute",
        ]
        assert report["steps"][3]["p50_ms"] == 60.0

    def test_record_ignores_unknown_steps(self):
        from paper_trading.market_listener import MarketListener

        agg = TickLatencyAggregator(window_size=10)
        listener = object.__new__(MarketListener)
        listener._latency_tracker = agg
        listener._record_tick_latency(
            {
                "total_ms": 5.0,
                "steps": {"fetch_prices_done": 5.0, "tick_market.end": 0.0},
            }
        )
        report = agg.report()
        data_fetch = report["steps"][0]
        assert data_fetch["p50_ms"] == 0.0  # 未知 step 归零
        assert report["tick_total_ms"]["p50"] == 5.0


class TestPaperTradingEventBus:
    def setup_method(self):
        PaperTradingEventBus.reset_instance()

    def test_trade_event_shape(self):
        received = []
        bus = PaperTradingEventBus.instance()
        bus.subscribe(received.append)
        emit_trade_event(
            "signal_generated",
            code="600519",
            side="buy",
            price=1685.5,
            quantity=100,
            strategy_name="momentum_v2",
            reason="MACD金叉",
        )
        assert len(received) == 1
        msg = received[0]
        assert msg["eventType"] == "signal_generated"
        assert msg["code"] == "600519"
        assert msg["side"] == "buy"
        assert msg["price"] == 1685.5
        assert msg["quantity"] == 100
        assert msg["strategyName"] == "momentum_v2"
        assert msg["reason"] == "MACD金叉"
        assert msg["timestamp"]
        assert "alertType" not in msg

    def test_risk_alert_shape(self):
        received = []
        bus = PaperTradingEventBus.instance()
        bus.subscribe(received.append)
        emit_risk_alert(
            "var_breach",
            message="组合 VaR 超过阈值",
            detail="VaR 占资金: 5.20%",
            level="danger",
        )
        msg = received[0]
        assert msg["alertType"] == "var_breach"
        assert msg["message"] == "组合 VaR 超过阈值"
        assert msg["level"] == "danger"
        assert msg["detail"].startswith("VaR")
        assert "eventType" not in msg

    def test_replay_and_unsubscribe(self):
        bus = PaperTradingEventBus.instance()
        emit_trade_event("order_created", code="600000", order_id=7)
        assert len(bus.replay()) >= 1
        seen = []
        handler = bus.subscribe(seen.append)
        emit_trade_event("order_filled", code="600000", order_id=7)
        bus.unsubscribe(handler)
        emit_trade_event("order_canceled", code="600000", order_id=7)
        assert len(seen) == 1
        assert seen[0]["eventType"] == "order_filled"

    def test_clear_resets_bus(self):
        bus = PaperTradingEventBus.instance()
        emit_trade_event("order_created", code="600000")
        bus.clear()
        assert bus.replay() == []


class TestFrontendAlignedHelpers:
    """文档外补充接口（drift / strategies/performance / features）的辅助逻辑."""

    def test_drift_to_item_camelcase(self):
        from api.v1.endpoints.paper_trading import _drift_to_item
        from paper_trading.drift_detector import DriftReport

        report = DriftReport(
            strategy_name="mom",
            is_drifting=True,
            rolling_sharpe=[1.0, 0.5],
            sharpe_trend=-0.05,
            consecutive_losing_days=6,
            recommended_action="pause",
        )
        item = _drift_to_item(report)
        assert item.strategyName == "mom"
        assert item.isDrifting is True
        assert item.rollingSharpe == [1.0, 0.5]
        assert item.sharpeTrend == -0.05
        assert item.consecutiveLosingDays == 6
        assert item.recommendedAction == "pause"

    def test_strategy_status_mapping(self):
        from api.v1.endpoints.paper_trading import _strategy_status

        assert _strategy_status("x", "keep", 1.0) == "active"
        assert _strategy_status("x", "reduce_weight", 1.0) == "reduced"
        assert _strategy_status("x", "pause", 1.0) == "paused"
        assert _strategy_status("x", "retire", 1.0) == "retired"
        assert _strategy_status("x", "keep", 0.0) == "paused"

    def test_feature_rows_conversion(self):
        import pandas as pd

        from api.v1.endpoints.paper_trading import _feature_rows

        idx = pd.MultiIndex.from_tuples(
            [("600519", "2026-08-12"), ("000001", "2026-08-12")],
            names=["code", "date"],
        )
        df = pd.DataFrame(
            {
                "sma_crossover": [1, 0],
                "rsi": [55.5, float("nan")],
                "volume_spike": [0, 1],
                "ma_alignment": [1, 0],
                "bid_ask_imbalance": [0.2, -0.1],
            },
            index=idx,
        )
        rows = _feature_rows(df)
        assert len(rows) == 2
        r = rows[0]
        assert r["code"] == "600519"
        assert r["date"] == "2026-08-12"
        assert r["smaCrossover"] == 1.0
        assert r["rsi"] == 55.5
        assert rows[1]["rsi"] == 0.0  # NaN -> 0.0

    def test_compute_strategy_trade_metrics(self):
        from api.v1.endpoints.paper_trading import _compute_strategy_trade_metrics
        from paper_trading.account import PaperAccountManager
        from src.storage import DatabaseManager, PaperOrder, PaperTrade

        db = DatabaseManager(db_url="sqlite:///:memory:")
        acc = PaperAccountManager(db).get_or_create_account(
            name="unit_drift", initial_capital=10000
        )
        acc_id = acc.id
        with db.session_scope() as session:
            o1 = PaperOrder(
                account_id=acc_id, code="600000", side="buy", order_type="market",
                quantity=100, status="filled", filled_quantity=100,
                filled_price_avg=10.0, strategy_name="s1",
            )
            o2 = PaperOrder(
                account_id=acc_id, code="600000", side="sell", order_type="market",
                quantity=100, status="filled", filled_quantity=100,
                filled_price_avg=12.0, strategy_name="s1",
            )
            session.add_all([o1, o2])
            session.commit()
            session.refresh(o1)
            session.refresh(o2)
            t1 = PaperTrade(
                account_id=acc_id, order_id=o1.id, code="600000", side="buy",
                price=10.0, quantity=100, amount=1000, fee=0.0,
            )
            t2 = PaperTrade(
                account_id=acc_id, order_id=o2.id, code="600000", side="sell",
                price=12.0, quantity=100, amount=1200, fee=1.0,
            )
            session.add_all([t1, t2])
            session.commit()

        metrics = _compute_strategy_trade_metrics(acc_id, db)
        assert "s1" in metrics
        m = metrics["s1"]
        assert m["tradeCount"] == 2
        assert m["winRate"] == 1.0  # (12-10)*100 - 1 = 199 > 0
        assert m["maxDrawdownPct"] == 0.0
