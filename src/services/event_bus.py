# -*- coding: utf-8 -*-
"""
系统事件总线（SystemEventBus）—— L3/L4 架构自修复的双向反馈中枢。

核心职责：
- 统一的系统事件发布/订阅机制
- L3 模块（降级/回滚/重启）发布操作级事件
- L4 MetaCognitiveEngine 订阅并处理系统事件作为认知输入
- 事件持久化到环形日志，支持回放和审计

设计原则：
- 面向接口订阅，发布方与订阅方解耦
- 单例模式，全局唯一实例
- 线程安全
- 事件不可变（frozen dataclass）

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 1
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


# ===================================================================
# 系统事件类型
# ===================================================================


class SystemEventType(str, Enum):
    """系统事件类型枚举。"""

    # L3-1 模块重启事件
    MODULE_RESTARTED = "module_restarted"
    MODULE_RESTART_FAILED = "module_restart_failed"
    MODULE_HEALTH_CHANGED = "module_health_changed"

    # L3-2 配置回滚事件
    CONFIG_SNAPSHOT_CREATED = "config_snapshot_created"
    CONFIG_ROLLBACK_EXECUTED = "config_rollback_executed"
    CONFIG_REGRESSION_DETECTED = "config_regression_detected"

    # L3-3 优雅降级事件
    DEGRADATION_TRANSITION = "degradation_transition"
    CAPABILITY_DISABLED = "capability_disabled"
    CAPABILITY_RESTORED = "capability_restored"

    # L4 元认知事件
    REFLECTION_COMPLETED = "reflection_completed"
    BIAS_DETECTED = "bias_detected"
    CIRCULARITY_DETECTED = "circularity_detected"
    OUTCOME_DEVIATION = "outcome_deviation"

    # 系统级事件
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    HEALTH_CHECK_COMPLETED = "health_check_completed"

    # ---- L1 基础设施事件（2026-08-12 集成实施方案新增） ----
    DATA_SOURCE_FALLBACK = "data_source_fallback"
    DATA_FETCH_FAILED = "data_fetch_failed"
    DATA_QUALITY_ALERT = "data_quality_alert"
    CIRCUIT_OPEN = "circuit_open"
    CIRCUIT_CLOSED = "circuit_closed"
    CONFIG_CHANGED = "config_changed"
    CLOCK_DEGRADED = "clock_degraded"
    LATENCY_SUMMARY = "latency_summary"
    LLM_BACKEND_SWITCHED = "llm_backend_switched"
    LLM_USAGE = "llm_usage"
    STORAGE_ERROR = "storage_error"

    # ---- L2 业务执行事件（2026-08-12 集成实施方案新增） ----
    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"
    MARKET_REVIEW_COMPLETED = "market_review_completed"
    BACKTEST_STARTED = "backtest_started"
    BACKTEST_COMPLETED = "backtest_completed"
    AGENT_TOOL_CALL = "agent_tool_call"
    AGENT_TOOL_RESULT = "agent_tool_result"
    AGENT_LOOP_DETECTED = "agent_loop_detected"
    NO_TRADE_DECISION = "no_trade_decision"
    NOTIFICATION_SENT = "notification_sent"
    NOTIFICATION_FAILED = "notification_failed"
    SERVICE_ERROR = "service_error"


class EventSeverity(str, Enum):
    """事件严重程度。"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ===================================================================
# 系统事件数据结构
# ===================================================================


@dataclass(frozen=True)
class SystemEvent:
    """不可变系统事件。

    发布方创建事件后不能修改，确保审计链准确性。
    """

    event_id: str                                   # 唯一事件 ID
    event_type: SystemEventType                     # 事件类型
    severity: EventSeverity                         # 严重程度
    source: str                                     # 发布来源，如 "graceful_degradation"
    timestamp: datetime = field(default_factory=datetime.now)
    payload: Dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None            # 关联 ID，用于追踪事件链
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于持久化）。"""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SystemEvent":
        """从字典反序列化。"""
        return cls(
            event_id=data["event_id"],
            event_type=SystemEventType(data["event_type"]),
            severity=EventSeverity(data["severity"]),
            source=data["source"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
            correlation_id=data.get("correlation_id"),
            tags=data.get("tags", []),
        )


# ===================================================================
# 事件总线
# ===================================================================


class SystemEventBus:
    """全局系统事件总线（单例）。

    用法:
        bus = SystemEventBus.instance()

        # 订阅
        @bus.on(SystemEventType.DEGRADATION_TRANSITION)
        def handle_degradation(event: SystemEvent) -> None:
            ...

        # 发布
        bus.publish(SystemEvent(
            event_id="...",
            event_type=SystemEventType.DEGRADATION_TRANSITION,
            severity=EventSeverity.WARNING,
            source="graceful_degradation",
            payload={"from": "normal", "to": "elevated"},
        ))
    """

    _instance: Optional["SystemEventBus"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        log_path: Optional[Path] = None,
        max_log_bytes: int = 10 * 1024 * 1024,
        max_log_files: int = 5,
    ) -> None:
        self._subscriptions: Dict[SystemEventType, List[Callable[[SystemEvent], None]]] = {}
        self._wildcard_subscriptions: List[Callable[[SystemEvent], None]] = []

        # 事件环形日志
        self._event_log: deque[SystemEvent] = deque(maxlen=1000)
        self._log_path = log_path
        self._lock = threading.RLock()

        # 日志轮转配置：单文件大小上限 + 保留归档文件数
        self._max_log_bytes = max_log_bytes
        self._max_log_files = max(max_log_files, 1)

        # 事件计数
        self._event_counter = 0

        # 最近事件的回调（L4 可以用这个获取事件流）
        self._recent_batch_callbacks: List[Callable[[List[SystemEvent]], None]] = []

    @classmethod
    def instance(
        cls,
        log_path: Optional[Path] = None,
        max_log_bytes: int = 10 * 1024 * 1024,
        max_log_files: int = 5,
    ) -> "SystemEventBus":
        """获取全局唯一实例。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(
                        log_path=log_path,
                        max_log_bytes=max_log_bytes,
                        max_log_files=max_log_files,
                    )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        with cls._instance_lock:
            cls._instance = None

    # ==================================================================
    # 发布
    # ==================================================================

    def publish(self, event: SystemEvent) -> None:
        """发布系统事件。

        事件会同步分发到所有匹配的订阅者。订阅者异常不会影响其他订阅者。
        """
        with self._lock:
            self._event_counter += 1
            self._event_log.append(event)

        # 分发到精确匹配的订阅者
        handlers = self._subscriptions.get(event.event_type, [])
        for handler in list(handlers):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Event handler %s failed for event %s",
                    handler.__name__, event.event_id,
                )

        # 分发到通配订阅者
        for handler in list(self._wildcard_subscriptions):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Wildcard handler %s failed for event %s",
                    handler.__name__, event.event_id,
                )

    def publish_batch(self, events: List[SystemEvent]) -> None:
        """批量发布事件（减少锁竞争）。"""
        for event in events:
            self.publish(event)

        # 触发批量回调
        for cb in list(self._recent_batch_callbacks):
            try:
                cb(events)
            except Exception:
                logger.exception("Batch callback %s failed", cb.__name__)

    # ==================================================================
    # 订阅
    # ==================================================================

    def subscribe(
        self, event_type: SystemEventType, handler: Callable[[SystemEvent], None]
    ) -> None:
        """订阅指定类型的事件。

        Args:
            event_type: 要订阅的事件类型。
            handler: 回调函数，接收 SystemEvent。
        """
        with self._lock:
            if event_type not in self._subscriptions:
                self._subscriptions[event_type] = []
            self._subscriptions[event_type].append(handler)

    def subscribe_all(self, handler: Callable[[SystemEvent], None]) -> None:
        """订阅所有类型的事件（通配订阅）。"""
        with self._lock:
            self._wildcard_subscriptions.append(handler)

    def on_batch(self, handler: Callable[[List[SystemEvent]], None]) -> None:
        """注册批量事件回调（用于 L4 定期同步事件流）。"""
        with self._lock:
            self._recent_batch_callbacks.append(handler)

    def unsubscribe(
        self, event_type: SystemEventType, handler: Callable[[SystemEvent], None]
    ) -> None:
        """取消订阅。"""
        with self._lock:
            if event_type in self._subscriptions:
                self._subscriptions[event_type] = [
                    h for h in self._subscriptions[event_type] if h is not handler
                ]

    def unsubscribe_all(self, handler: Callable[[SystemEvent], None]) -> None:
        """取消通配订阅（subscribe_all 的对称方法）。"""
        with self._lock:
            self._wildcard_subscriptions = [
                h for h in self._wildcard_subscriptions if h is not handler
            ]

    def on(self, event_type: SystemEventType):
        """装饰器形式的订阅。

        Usage:
            @bus.on(SystemEventType.DEGRADATION_TRANSITION)
            def handle(event): ...
        """
        def decorator(func: Callable[[SystemEvent], None]):
            self.subscribe(event_type, func)
            return func
        return decorator

    # ==================================================================
    # 查询
    # ==================================================================

    def get_recent_events(
        self,
        limit: int = 50,
        event_type: Optional[SystemEventType] = None,
        source: Optional[str] = None,
        min_severity: Optional[EventSeverity] = None,
    ) -> List[SystemEvent]:
        """获取最近的系统事件，支持筛选。

        Args:
            limit: 返回事件数量上限。
            event_type: 按事件类型筛选。None = 所有类型。
            source: 按来源筛选。None = 所有来源。
            min_severity: 按最低严重程度筛选。None = 所有级别。
        """
        with self._lock:
            events = list(self._event_log)

        result = []
        severity_order = {s: i for i, s in enumerate(EventSeverity)}
        for e in reversed(events):
            if event_type and e.event_type != event_type:
                continue
            if source and e.source != source:
                continue
            if min_severity and severity_order[e.severity] < severity_order[min_severity]:
                continue
            result.append(e)
            if len(result) >= limit:
                break

        return result

    def get_event_count(self) -> int:
        """获取已发布的事件总数。"""
        return self._event_counter

    def get_events_by_correlation(self, correlation_id: str) -> List[SystemEvent]:
        """获取同一关联链上的所有事件。"""
        with self._lock:
            return [e for e in self._event_log if e.correlation_id == correlation_id]

    # ==================================================================
    # 持久化
    # ==================================================================

    def flush_to_disk(self) -> Optional[Path]:
        """将事件日志持久化到磁盘（JSONL，每行一个事件）。

        轮转策略：写前检查当前日志文件大小，超过 ``max_log_bytes`` 时
        将旧文件重命名为带时间戳的归档（``*.jsonl.N``），再写入新文件；
        超过 ``max_log_files`` 的旧归档会被清理，防止磁盘无限堆积。
        """
        if not self._log_path:
            return None

        with self._lock:
            events_data = [e.to_dict() for e in self._event_log]

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed()
            self._prune_archives()
            # JSONL 追加写（每行一个事件；全新写入时清空旧内容）
            if not self._log_path.exists() or self._log_path.stat().st_size == 0:
                lines = "\n".join(
                    json.dumps(e, ensure_ascii=False) for e in events_data
                )
                self._log_path.write_text(
                    (lines + "\n") if lines else "",
                    encoding="utf-8",
                )
            else:
                with self._log_path.open("a", encoding="utf-8") as f:
                    for e in events_data:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
            return self._log_path
        except Exception:
            logger.exception("Failed to flush event log to %s", self._log_path)
            return None

    def _rotate_if_needed(self) -> None:
        """日志轮转：文件超过大小上限时归档。"""
        if not self._log_path or not self._log_path.exists():
            return
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return
        if size < self._max_log_bytes:
            return

        # 归档当前文件：event_bus_log.jsonl -> event_bus_log.jsonl.<ns>（纳秒精度避免同秒冲突）
        try:
            archive = self._log_path.with_name(
                f"{self._log_path.name}.{time.time_ns()}"
            )
            shutil.move(str(self._log_path), str(archive))
            logger.info(
                "Event log rotated: %s -> %s (size=%d bytes)",
                self._log_path.name,
                archive.name,
                size,
            )
        except Exception:
            logger.exception("Failed to rotate event log %s", self._log_path)

    def _prune_archives(self) -> None:
        """清理超过 ``max_log_files`` 的旧归档（按 mtime，保留最新 N 个）。"""
        if not self._log_path or not self._log_path.parent.exists():
            return
        try:
            archives = sorted(
                self._log_path.parent.glob(f"{self._log_path.name}.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in archives[self._max_log_files - 1:]:
                try:
                    old.unlink()
                    logger.info("Pruned old event log archive: %s", old.name)
                except OSError:
                    logger.warning("Failed to prune event log archive: %s", old.name)
        except Exception:
            logger.exception("Failed to prune event log archives")

    def load_from_disk(self) -> int:
        """从磁盘加载事件日志（兼容 JSONL 与旧版 JSON 数组）。"""
        if not self._log_path or not self._log_path.exists():
            return 0

        try:
            raw = self._log_path.read_text(encoding="utf-8").strip()
            if not raw:
                return 0
            # 新格式：JSONL（每行一个事件）
            if "\n" in raw:
                data = [
                    json.loads(line)
                    for line in raw.splitlines()
                    if line.strip()
                ]
            else:
                # 旧格式：整个文件是一个 JSON 数组
                data = json.loads(raw)
                if not isinstance(data, list):
                    return 0
            with self._lock:
                for item in data:
                    try:
                        event = SystemEvent.from_dict(item)
                        self._event_log.append(event)
                    except Exception:
                        logger.debug("Skipping malformed event entry in log")
            return len(data)
        except Exception:
            logger.exception("Failed to load event log from %s", self._log_path)
            return 0

    # ==================================================================
    # 统计
    # ==================================================================

    def stats(self) -> Dict[str, Any]:
        """获取事件总线统计。"""
        with self._lock:
            type_counts: Dict[str, int] = {}
            source_counts: Dict[str, int] = {}
            severity_counts: Dict[str, int] = {}

            for e in self._event_log:
                type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
                source_counts[e.source] = source_counts.get(e.source, 0) + 1
                severity_counts[e.severity.value] = severity_counts.get(e.severity.value, 0) + 1

            return {
                "total_events": self._event_counter,
                "logged_events": len(self._event_log),
                "subscription_count": sum(
                    len(h) for h in self._subscriptions.values()
                ) + len(self._wildcard_subscriptions),
                "event_types": len(self._subscriptions),
                "type_distribution": type_counts,
                "source_distribution": source_counts,
                "severity_distribution": severity_counts,
            }

    def reset(self) -> None:
        """重置总线（仅用于测试）。"""
        with self._lock:
            self._subscriptions.clear()
            self._wildcard_subscriptions.clear()
            self._recent_batch_callbacks.clear()
            self._event_log.clear()
            self._event_counter = 0


# ===================================================================
# L3 → EventBus 集成辅助函数
# ===================================================================


def publish_module_event(
    event_type: SystemEventType,
    severity: EventSeverity,
    module_name: str,
    extra: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> SystemEvent:
    """L3 模块用：发布模块级系统事件。

    Args:
        event_type: 事件类型。
        severity: 严重程度。
        module_name: 模块名称。
        extra: 额外 payload。
        correlation_id: 关联 ID。

    Returns:
        创建的 SystemEvent。
    """
    ts = int(time.time() * 1000)
    event = SystemEvent(
        event_id=f"sys_{ts}_{module_name}_{event_type.value}",
        event_type=event_type,
        severity=severity,
        source=module_name,
        payload=extra or {},
        correlation_id=correlation_id,
    )
    SystemEventBus.instance().publish(event)
    return event


def publish_degradation_event(
    from_level: str,
    to_level: str,
    capabilities_affected: List[str],
    trigger_signals: List[str],
) -> SystemEvent:
    """L3-3 用：发布降级事件。"""
    severity_map = {
        "elevated": EventSeverity.WARNING,
        "high": EventSeverity.ERROR,
        "critical": EventSeverity.CRITICAL,
    }
    ts = int(time.time() * 1000)
    event = SystemEvent(
        event_id=f"sys_{ts}_degradation_{from_level}_to_{to_level}",
        event_type=SystemEventType.DEGRADATION_TRANSITION,
        severity=severity_map.get(to_level, EventSeverity.INFO),
        source="graceful_degradation",
        payload={
            "from_level": from_level,
            "to_level": to_level,
            "capabilities_affected": capabilities_affected,
            "trigger_signals": trigger_signals,
        },
    )
    SystemEventBus.instance().publish(event)
    return event


def publish_rollback_event(
    snapshot_id: str,
    success: bool,
    restored_keys: List[str],
    error: str = "",
) -> SystemEvent:
    """L3-2 用：发布配置回滚事件。"""
    ts = int(time.time() * 1000)
    event = SystemEvent(
        event_id=f"sys_{ts}_rollback_{'ok' if success else 'fail'}",
        event_type=SystemEventType.CONFIG_ROLLBACK_EXECUTED,
        severity=EventSeverity.ERROR if not success else EventSeverity.WARNING,
        source="config_rollback",
        payload={
            "snapshot_id": snapshot_id,
            "success": success,
            "restored_keys": restored_keys,
            "error": error,
        },
    )
    SystemEventBus.instance().publish(event)
    return event


def publish_reflection_event(
    reflection_id: str,
    summary: str,
    improvement_hints: List[str],
) -> SystemEvent:
    """L4 用：发布反思完成事件。"""
    ts = int(time.time() * 1000)
    event = SystemEvent(
        event_id=f"sys_{ts}_reflection_{reflection_id[:8]}",
        event_type=SystemEventType.REFLECTION_COMPLETED,
        severity=EventSeverity.INFO,
        source="meta_cognitive",
        payload={
            "reflection_id": reflection_id,
            "summary": summary,
            "improvement_hints": improvement_hints,
        },
    )
    SystemEventBus.instance().publish(event)
    return event
