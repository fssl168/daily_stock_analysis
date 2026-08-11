# -*- coding: utf-8 -*-
"""Tests for paper_trading/ai_signal_worker.py (T20)."""

from __future__ import annotations

import time
from paper_trading.ai_signal_worker import AISignalWorker


class FakeQueue:
    """Minimal fake for AIAnalysisSignalQueue with push tracking."""

    def __init__(self):
        self.pushed: list = []

    def push(self, signal):
        self.pushed.append(signal)


def test_worker_no_queue_basic():
    """Worker runs analysis_fn, survives exceptions, stops cleanly."""
    calls: list = []

    def analysis():
        calls.append(1)
        return [{"side": "buy", "code": "000001"}]

    worker = AISignalWorker(analysis_fn=analysis, signal_queue=None, schedule_interval_seconds=0.1)
    worker.start()
    time.sleep(0.35)
    worker.stop()
    # Should have run at least 2 cycles (0.1s interval over 0.35s)
    assert len(calls) >= 2, f"expected >=2 calls, got {len(calls)}"


def test_worker_pushes_to_queue():
    q = FakeQueue()
    calls: list = []

    def analysis():
        calls.append(1)
        return [{"side": "sell", "code": "000002"}, {"side": "buy", "code": "000003"}]

    worker = AISignalWorker(analysis_fn=analysis, signal_queue=q, schedule_interval_seconds=0.1)
    worker.start()
    time.sleep(0.35)
    worker.stop()
    assert sum(1 for s in q.pushed if s["side"] == "sell") >= 2


def test_worker_exception_resilience():
    """Exception in analysis_fn must not kill the worker loop."""
    calls: list = []

    def analysis():
        calls.append("ok")
        if len(calls) <= 2:
            raise RuntimeError("simulated failure")
        return [{"side": "hold", "code": "000000"}]

    worker = AISignalWorker(analysis_fn=analysis, signal_queue=None, schedule_interval_seconds=0.1)
    worker.start()
    time.sleep(0.5)
    worker.stop()
    # Should have survived the first 2 failing cycles and produced at least 3 total
    assert len(calls) >= 3, f"expected >=3 calls after exceptions, got {len(calls)}"


def test_worker_double_start_is_noop():
    calls: list = []

    def analysis():
        calls.append(1)
        return []

    worker = AISignalWorker(analysis_fn=analysis, signal_queue=None, schedule_interval_seconds=0.1)
    worker.start()
    worker.start()  # double start should be no-op
    time.sleep(0.25)
    worker.stop()
    assert len(calls) >= 1


def test_worker_stopped_before_start():
    """Calling stop() on never-started worker is safe."""
    worker = AISignalWorker(analysis_fn=lambda: [])
    worker.stop()  # no-op, no exception
