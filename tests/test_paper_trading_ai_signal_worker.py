# -*- coding: utf-8 -*-
"""T-12 tests: AISignalWorker threads AI analysis into the signal queue,
which the MarketListener consumes (via _consume_ai_signals)."""

from __future__ import annotations

import time

from paper_trading.ai_signal_worker import AISignalWorker
from src.paper_trading_signal_queue import AIAnalysisSignal, init_signal_queue


def _mk_signal(code: str = "600519", side: str = "buy") -> AIAnalysisSignal:
    return AIAnalysisSignal(
        code=code, side=side, name="测试", trigger_price=100.0,
        suggested_quantity=100, reason="t12", strategy_name="ai_signal_worker",
        confidence=0.9,
    )


def _drain(q) -> list:
    items = q.pop_all()
    return list(items)


def test_worker_pushes_signals_to_queue():
    q = init_signal_queue(maxsize=100)
    _drain(q)  # 清空历史信号

    worker = AISignalWorker(
        analysis_fn=lambda: [_mk_signal()],
        signal_queue=q,
        schedule_interval_seconds=0.05,
    )
    worker.start()
    time.sleep(0.2)  # 至少一轮分析循环
    worker.stop()

    items = _drain(q)
    assert len(items) >= 1
    assert items[0].code == "600519"
    assert items[0].side == "buy"


def test_worker_skips_no_signals():
    q = init_signal_queue(maxsize=100)
    _drain(q)

    worker = AISignalWorker(
        analysis_fn=lambda: [],
        signal_queue=q,
        schedule_interval_seconds=0.05,
    )
    worker.start()
    time.sleep(0.15)
    worker.stop()

    items = _drain(q)
    assert len(items) == 0


def test_worker_survives_analysis_error():
    q = init_signal_queue(maxsize=100)
    _drain(q)

    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM unavailable")
        return [_mk_signal()]

    worker = AISignalWorker(
        analysis_fn=_flaky,
        signal_queue=q,
        schedule_interval_seconds=0.05,
    )
    worker.start()
    time.sleep(0.2)  # 让循环经历"失败 → 成功"两轮
    worker.stop()

    items = _drain(q)
    assert len(items) >= 1  # 第二轮的信号被 push


def test_worker_start_stop_idempotent():
    q = init_signal_queue(maxsize=100)
    worker = AISignalWorker(analysis_fn=lambda: [], signal_queue=q,
                            schedule_interval_seconds=0.05)
    worker.start()
    worker.start()  # 重复启动 no-op
    assert worker._thread is not None and worker._thread.is_alive()
    worker.stop()
    assert not (worker._thread is not None and worker._thread.is_alive())
