# -*- coding: utf-8 -*-
"""
Tests for src/utils/latency_tracker.py
Covers: SpanEvent 默认值、LatencySpan mark/finish 打点正确性、Tracker
record/get_p95/report、p50/p95/p99 分位正确性、空 tracker、单样本边界、
滑动窗口淘汰、线程安全冒烟测试。
"""

import threading
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.utils.latency_tracker import LatencySpan, LatencyTracker, SpanEvent

BASE = datetime(2026, 8, 10, 4, 0, 0, 0)


class FakeDateTime:
    """模块级 datetime 的替身：now() 返回可编程的递增时间点（毫秒步进）。"""

    _current = BASE
    _step = timedelta(milliseconds=10)

    @classmethod
    def reset(cls, start=BASE, step_ms=10):
        cls._current = start
        cls._step = timedelta(milliseconds=step_ms)

    @classmethod
    def now(cls):
        current = cls._current
        cls._current = cls._current + cls._step
        return current


@pytest.fixture(autouse=True)
def _fake_clock():
    """每个用例前重置假时钟并替换模块内 datetime。"""
    FakeDateTime.reset()
    with patch("src.utils.latency_tracker.datetime", FakeDateTime):
        yield
    FakeDateTime.reset()


def _span_result(operation, total_ms, trace_id="t-1"):
    """构造一次 finish() 输出形状的 span 结果。"""
    return {"trace_id": trace_id, "operation": operation, "total_ms": float(total_ms), "steps": {}}


class TestSpanEvent:
    def test_defaults(self):
        ev = SpanEvent(span_name="fetch_prices_start")
        assert ev.span_name == "fetch_prices_start"
        assert isinstance(ev.timestamp, datetime)
        assert ev.metadata == {}

    def test_metadata_passed(self):
        ev = SpanEvent(span_name="match_orders_done", metadata={"matched": 2})
        assert ev.metadata == {"matched": 2}


class TestLatencySpan:
    def test_init_creates_start_event(self):
        span = LatencySpan("tick_market", "trace-1")
        assert len(span.events) == 1
        assert span.events[0].span_name == "tick_market.start"
        assert span.operation == "tick_market"
        assert span.trace_id == "trace-1"

    def test_mark_appends_event_with_metadata(self):
        span = LatencySpan("tick_market", "trace-1")
        span.mark("fetch_prices_done", codes=3)
        assert span.events[1].span_name == "fetch_prices_done"
        assert span.events[1].metadata == {"codes": 3}

    def test_finish_output_steps_and_total_ms(self):
        span = LatencySpan("tick_market", "trace-1")
        span.mark("fetch_prices_done", codes=3)
        span.mark("match_orders_done", matched=2)
        result = span.finish()

        # 假时钟步进 10ms：start=0ms, fetch=10ms, match=20ms, end=30ms
        assert result == {
            "trace_id": "trace-1",
            "operation": "tick_market",
            "total_ms": 30.0,
            "steps": {
                "fetch_prices_done": 10.0,
                "match_orders_done": 10.0,
                "tick_market.end": 10.0,
            },
        }
        # finish 追加了结束事件
        assert span.events[-1].span_name == "tick_market.end"

    def test_finish_without_marks(self):
        span = LatencySpan("ping", "trace-2")
        result = span.finish()
        assert result["total_ms"] == 10.0
        assert result["steps"] == {"ping.end": 10.0}

    def test_finish_called_explicit_start(self):
        span = LatencySpan("op", "trace-3")
        span.mark("mid")
        span.start()  # 显式再次调用 start 也只是一次时间点记录
        result = span.finish()
        # start=0ms, mid=10ms, start=20ms, end=30ms
        assert result["total_ms"] == 30.0
        assert result["steps"] == {"mid": 10.0, "op.start": 10.0, "op.end": 10.0}


class TestLatencyTracker:
    def test_record_get_p95_and_report(self):
        tracker = LatencyTracker(window_size=1000)
        for total in [10, 20, 30, 40]:
            tracker.record(_span_result("fetch", total))
        assert tracker.get_p95("fetch") == pytest.approx(47.5)
        report = tracker.report()
        assert len(report) == 1
        entry = report[0]
        assert entry["operation"] == "fetch"
        assert entry["count"] == 4
        assert entry["p50"] == pytest.approx(25.0)
        assert entry["p95"] == pytest.approx(47.5)
        assert entry["p99"] == pytest.approx(49.5)

    def test_report_isolates_operations(self):
        tracker = LatencyTracker()
        tracker.record(_span_result("fetch", 10))
        tracker.record(_span_result("match", 20))
        tracker.record(_span_result("match", 30))
        report = {entry["operation"]: entry for entry in tracker.report()}
        assert set(report.keys()) == {"fetch", "match"}
        assert report["fetch"]["count"] == 1
        assert report["fetch"]["p95"] == pytest.approx(10.0)
        assert report["match"]["count"] == 2
        assert report["match"]["p95"] == pytest.approx(38.5)

    def test_get_p95_unknown_operation_returns_none(self):
        tracker = LatencyTracker()
        tracker.record(_span_result("fetch", 10))
        assert tracker.get_p95("unknown") is None

    def test_empty_tracker(self):
        tracker = LatencyTracker()
        assert tracker.get_p95("anything") is None
        assert tracker.report() == []

    def test_single_sample_edge(self):
        tracker = LatencyTracker()
        tracker.record(_span_result("fetch", 5.0))
        assert tracker.get_p95("fetch") == 5.0
        entry = tracker.report()[0]
        assert entry["p50"] == 5.0
        assert entry["p95"] == 5.0
        assert entry["p99"] == 5.0
        assert entry["count"] == 1

    def test_quantile_correctness_1_to_100(self):
        tracker = LatencyTracker()
        for total in range(1, 101):
            tracker.record(_span_result("op", total))
        entry = tracker.report()[0]
        assert entry["count"] == 100
        assert entry["p50"] == pytest.approx(50.5)
        assert entry["p95"] == pytest.approx(95.95)
        assert entry["p99"] == pytest.approx(99.99)

    def test_quantile_correctness_small_set(self):
        tracker = LatencyTracker()
        for total in [1, 2, 3, 4]:
            tracker.record(_span_result("op", total))
        entry = tracker.report()[0]
        assert entry["p50"] == pytest.approx(2.5)
        assert entry["p95"] == pytest.approx(4.75)
        assert entry["p99"] == pytest.approx(4.95)

    def test_window_sliding_keeps_most_recent(self):
        tracker = LatencyTracker(window_size=3)
        for total in [1, 2, 3, 4]:
            tracker.record(_span_result("op", total))
        entry = tracker.report()[0]
        assert entry["count"] == 3
        assert entry["p50"] == pytest.approx(3.0)  # 窗口内样本 [2,3,4]

    def test_window_eviction_recomputes_stats(self):
        tracker = LatencyTracker(window_size=2)
        tracker.record(_span_result("op", 1))
        tracker.record(_span_result("op", 2))
        tracker.record(_span_result("other", 5))
        # 窗口内 [op:2, other:5]，op 的旧样本已被淘汰
        assert tracker.get_p95("op") == pytest.approx(2.0)
        report = {entry["operation"]: entry for entry in tracker.report()}
        assert report["op"]["count"] == 1
        assert report["other"]["count"] == 1

    def test_report_returns_copies(self):
        tracker = LatencyTracker()
        tracker.record(_span_result("op", 10))
        first = tracker.report()[0]
        first["count"] = 999
        first["p95"] = 0.0
        assert tracker.report()[0]["count"] == 1
        assert tracker.report()[0]["p95"] == pytest.approx(10.0)

    def test_record_repeated_updates_stats(self):
        tracker = LatencyTracker()
        for total in [100, 200]:
            tracker.record(_span_result("op", total))
        assert tracker.report()[0]["count"] == 2
        assert tracker.get_p95("op") == pytest.approx(285.0)

    def test_thread_safety_smoke(self):
        tracker = LatencyTracker(window_size=10000)
        n_threads = 8
        per_thread = 50
        errors = []

        def worker(worker_id):
            try:
                op = f"op-{worker_id % 2}"
                for i in range(per_thread):
                    tracker.record(_span_result(op, float(i), trace_id=f"t-{worker_id}-{i}"))
            except Exception as exc:  # pragma: no cover - 仅冒烟收集
                errors.append(exc)

        def reader():
            try:
                for _ in range(200):
                    tracker.get_p95("op-0")
                    tracker.report()
            except Exception as exc:  # pragma: no cover - 仅冒烟收集
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        threads += [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        total = sum(entry["count"] for entry in tracker.report())
        assert total == n_threads * per_thread

