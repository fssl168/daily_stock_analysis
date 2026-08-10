# -*- coding: utf-8 -*-
"""AI Signal Worker — 独立线程异步产生 AI 分析信号（T20）。

在独立线程中按 cron 调度触发 AI 分析（LLM ReAct 循环），
产生的信号通过已有的 AIAnalysisSignalQueue 推送给 MarketListener 消费，
不再阻塞规则引擎的秒级 tick 循环。

来源: docs/architecture/realtime_quant_system_design.md §5.3
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class AISignalWorker:
    """独立线程：按 cron 调度触发 AI 分析，产出信号写入队列。

    与 MarketListener 并行运行，不阻塞规则引擎的 tick 循环。
    """

    def __init__(
        self,
        analysis_fn: Callable[[], List],
        signal_queue: Optional[object] = None,
        schedule_interval_seconds: float = 3600.0,
    ):
        """
        Args:
            analysis_fn: 无参可调用对象，返回 AI 分析信号列表。
            signal_queue: AIAnalysisSignalQueue 实例（可选，支持 push 方法）。
            schedule_interval_seconds: AI 分析间隔（默认 1 小时）。
        """
        self._analysis_fn = analysis_fn
        self._signal_queue = signal_queue
        self._interval = float(schedule_interval_seconds)
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """启动守护线程（已运行时重复调用为 no-op）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="ai-signal-worker",
        )
        self._thread.start()
        logger.info(
            "[AISignalWorker] started: interval=%.0fs", self._interval,
        )

    def stop(self) -> None:
        """停止守护线程并等待退出。"""
        self._shutdown.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        logger.info("[AISignalWorker] stopped")

    def _loop(self) -> None:
        """主循环：定时触发分析，异常不中断循环。"""
        while not self._shutdown.is_set():
            try:
                signals = self._analysis_fn()
                if signals and self._signal_queue is not None:
                    for s in signals:
                        self._signal_queue.push(s)
                    logger.info(
                        "[AISignalWorker] produced %d signals", len(signals),
                    )
            except Exception:
                logger.exception("[AISignalWorker] analysis cycle failed")
            self._shutdown.wait(timeout=self._interval)
        logger.info("[AISignalWorker] loop exited")
