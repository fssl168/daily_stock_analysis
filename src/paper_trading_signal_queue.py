# -*- coding: utf-8 -*-
"""Thread-safe in-memory signal queue for AI analysis results to feed into paper trading.

This module provides a mechanism for the main analysis process (in main.py / analyzer.py)
to push trading signals directly to the MarketListener, enabling "analysis equals trade"
flow without manual intervention.

Architecture:
- AIAnalysisSignal: dataclass representing an AI-generated trading signal
- AIAnalysisSignalQueue: thread-safe bounded queue with drop-oldest-full-policy
  suitable for high-frequency signal generation scenarios.

The queue is initialized as a module-level singleton; callers use init_signal_queue()
to get the shared instance, which must be called once at application startup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from queue import Queue, Full, Empty
from threading import Lock, RLock
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class AIAnalysisSignal:
    """AI 分析产生的交易信号模型.

    Attributes:
        code: Stock code (e.g., "600519", "AAPL")
        side: "buy" or "sell" - the direction of the trade
        name: Stock name/description
        trigger_price: Price at which to execute (entry price for limit orders)
        suggested_quantity: Suggested number of shares (None means auto-calculate)
        reason: Human-readable explanation of why this signal was generated
        strategy_name: Source of the signal (e.g., "ai_analysis_signal")
        confidence: AI model's confidence score (0.0-1.0), used for filtering
        timestamp: When the signal was created
    """

    code: str
    side: str  # "buy" or "sell"
    name: str
    trigger_price: float
    suggested_quantity: Optional[float] = None
    reason: str = ""
    strategy_name: str = "ai_analysis_signal"
    confidence: float = 1.0  # AI confidence score (0-1)
    timestamp: datetime = field(default_factory=datetime.now)


class AIAnalysisSignalQueue:
    """线程安全的内存信号队列，供 main.py 的分析结果推送给 paper_trading listener.

    特点：
    - 有界队列（默认 maxsize=1000），满时丢弃旧条目（drop oldest first）
    - pop_all() 一次性消费所有待处理信号，避免单次循环开销
    - thread-safe，适合多线程并发写入/读取
    - 支持优雅关闭（close()）
    """

    def __init__(self, maxsize: int = 1000):
        self._queue: Queue[AIAnalysisSignal] = Queue(maxsize=maxsize)
        self._lock = RLock()
        self._stopped = False

    def push(self, signal: AIAnalysisSignal) -> bool:
        """
        推送一个信号到队列。

        Returns:
            True if pushed successfully, False if dropped due to full queue.
        """
        try:
            self._queue.put_nowait(signal)
            return True
        except Full:
            with self._lock:
                logger.warning(
                    "AI analysis signal queue full (max=%d), dropping oldest",
                    self._queue.maxsize,
                )
            # Drop one old item to make room for new one
            try:
                self._queue.get_nowait()
                # Try again after removing old item
                self._queue.put_nowait(signal)
                return True
            except Exception:
                # Queue was empty, still can't put
                return False

    def pop_all(self) -> List[AIAnalysisSignal]:
        """一次性拉取所有待处理信号，原子操作."""
        signals = []
        while not self._queue.empty():
            try:
                signals.append(self._queue.get_nowait())
            except Empty:
                break
        return signals

    def empty(self) -> bool:
        return self._queue.empty()

    def size(self) -> int:
        with self._lock:
            return self._queue.qsize()

    def close(self):
        """关闭队列，阻止新入队."""
        with self._lock:
            self._stopped = True

    def __bool__(self) -> bool:
        """Queue is considered "active" if not closed and not empty."""
        with self._lock:
            return not self._stopped and not self._queue.empty()


# Module-level singleton instance (_initialized at import, actual setup in init_signal_queue())
_signal_queue: Optional[AIAnalysisSignalQueue] = None


def init_signal_queue(maxsize: int = 1000) -> AIAnalysisSignalQueue:
    """初始化或获取全局的信号队列单例实例.

    此函数应在应用程序启动时调用一次（例如在 main.py 中）。多次调用会返回
    同一个实例，忽略传入的 maxsize 参数（除非实例尚未创建）.

    Returns:
        The global AIAnalysisSignalQueue instance.
    """
    global _signal_queue
    if _signal_queue is None:
        with RLock():
            # Double-check locking in case another thread just created it
            if _signal_queue is None:
                _signal_queue = AIAnalysisSignalQueue(maxsize=maxsize)
    assert _signal_queue is not None
    return _signal_queue


def get_signal_queue() -> Optional[AIAnalysisSignalQueue]:
    """获取当前已初始化的信号队列实例（未初始化时返回 None）.

    Returns:
        The global AIAnalysisSignalQueue instance, or None if not yet initialized.
    """
    return _signal_queue
