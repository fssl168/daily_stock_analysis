# -*- coding: utf-8 -*-
"""
全链路延迟监控（LatencyTracker）。

来源: docs/architecture/realtime_quant_system_design.md §3.4
- ``SpanEvent``: 追踪链上的一个时间点
- ``LatencySpan``: 一次完整操作的时间跨度追踪（mark/finish 打点）
- ``LatencyTracker``: 全局延迟追踪 — 滑动窗口 + p50/p95/p99 分位统计

无外部依赖；``threading.Lock`` 保证线程安全。
"""

import statistics
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class SpanEvent:
    """追踪链上的一个时间点。"""

    span_name: str
    timestamp: datetime = field(default_factory=lambda: datetime.now())
    metadata: Dict[str, Any] = field(default_factory=dict)


class LatencySpan:
    """一次完整操作的时间跨度追踪。

    ``__init__`` 自动记录 ``{operation}.start`` 起始点；
    ``mark`` 记录中间阶段；``finish`` 记录结束点并输出各阶段耗时与总耗时。
    """

    def __init__(self, operation: str, trace_id: str):
        self.operation = operation
        self.trace_id = trace_id
        self.events: List[SpanEvent] = []
        self.start()

    def start(self) -> None:
        """记录起始时间点。"""
        self.events.append(SpanEvent(span_name=f"{self.operation}.start"))

    def mark(self, step: str, **metadata: Any) -> None:
        """记录一个阶段时间点，可附带任意元数据。"""
        self.events.append(SpanEvent(span_name=step, metadata=metadata))

    def finish(self) -> Dict[str, Any]:
        """结束追踪并输出结果。

        Returns:
            Dict: {trace_id, operation, total_ms, steps: {step_name: ms}}。
                各阶段耗时按相邻事件时间差计算，单位毫秒，保留两位小数。
        """
        self.events.append(SpanEvent(span_name=f"{self.operation}.end"))
        total_ms = (
            self.events[-1].timestamp - self.events[0].timestamp
        ).total_seconds() * 1000

        steps: Dict[str, float] = {}
        for i in range(len(self.events) - 1):
            step_name = self.events[i + 1].span_name
            step_ms = (
                self.events[i + 1].timestamp - self.events[i].timestamp
            ).total_seconds() * 1000
            steps[step_name] = round(step_ms, 2)

        return {
            "trace_id": self.trace_id,
            "operation": self.operation,
            "total_ms": round(total_ms, 2),
            "steps": steps,
        }


def _compute_percentiles(values: List[float]):
    """计算 p50/p95/p99。

    - 空样本: 返回 (None, None, None)
    - 单样本: 三个分位均为该值（``statistics.quantiles`` 需至少两个样本）
    - 多样本: 用 ``statistics.quantiles``（n=4/20/100 取 p50/p95/p99）
    """
    n = len(values)
    if n == 0:
        return None, None, None
    if n == 1:
        v = float(values[0])
        return v, v, v
    p50 = statistics.quantiles(values, n=4)[1]
    p95 = statistics.quantiles(values, n=20)[18]
    p99 = statistics.quantiles(values, n=100)[98]
    return p50, p95, p99


class LatencyTracker:
    """全局延迟追踪 — 滑动窗口统计。

    记录每个 span 结果，并按 operation 维护滑动窗口内的 p50/p95/p99 与样本数。
    ``threading.Lock`` 保护窗口与统计状态，支持多线程并发打点。
    """

    def __init__(self, window_size: int = 1000):
        self._spans: deque = deque(maxlen=window_size)
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record(self, span_result: Dict[str, Any]) -> None:
        """记录一次 span 结果并更新分位统计。

        窗口驱逐会影响所有 operation 的样本集合，因此记录后对当前窗口内
        出现过的所有 operation 重算统计，避免被驱逐样本的旧统计残留。
        """
        with self._lock:
            self._spans.append(span_result)
            self._recompute_all_stats()

    def _recompute_all_stats(self) -> None:
        """从当前滑动窗口重算所有 operation 的分位统计。"""
        operations = {
            str(s.get("operation")) for s in self._spans if s.get("operation")
        }
        for operation in operations:
            self._update_stats(operation)

    def _update_stats(self, operation: str) -> None:
        """从当前滑动窗口内该 operation 的样本重新计算分位统计。"""
        samples = [
            float(s["total_ms"]) for s in self._spans if s.get("operation") == operation
        ]
        p50, p95, p99 = _compute_percentiles(samples)
        self._stats[operation] = {
            "operation": operation,
            "p50": p50,
            "p95": p95,
            "p99": p99,
            "count": len(samples),
        }

    def get_p95(self, operation: str) -> Optional[float]:
        """返回指定 operation 的 p95 分位（毫秒）；无记录时返回 None。"""
        with self._lock:
            return self._stats.get(operation, {}).get("p95")

    def report(self) -> List[Dict[str, Any]]:
        """返回各 operation 的统计摘要列表（浅拷贝，避免外部篡改内部状态）。"""
        with self._lock:
            return [dict(v) for v in self._stats.values()]
