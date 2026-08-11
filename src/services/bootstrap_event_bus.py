# -*- coding: utf-8 -*-
"""
系统事件总线装配层（bootstrap_event_bus）—— L1/L2/L3/L4 全主动观察的启动入口。

核心职责（第一版 · 全主动观察）：
- 初始化 SystemEventBus 全局单例
- 注册各层"只读观察者"订阅：
  - L4 MetaCognitiveEngine 订阅全部事件 → 转化为系统观察 / 内省
  - L3 订阅配置回归事件（第一版仅记录，不自动回滚）
  - L2 订阅反思结论（第一版仅记录，不调整策略）
- 提供进程生命周期钩子（publish SYSTEM_STARTUP / SYSTEM_SHUTDOWN）
- 事件持久化到环形日志 + 磁盘（flush_to_disk），支持回放与审计

设计原则：
- 全部订阅者为"观察型"：只读消费、落盘、报告，不产生副作用
- 所有修复/回滚/降级动作保持 dry_run（本文件不触发任何实际干预）
- 订阅者异常由 EventBus 内置隔离，不影响生产路径
- 幂等：重复调用 bootstrap_event_bus() 不会重复装配

来源: docs/L1_L4_INTEGRATION_IMPLEMENTATION_PLAN.md Phase 1b
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.event_bus import EventSeverity, SystemEvent, SystemEventBus, SystemEventType

logger = logging.getLogger(__name__)

# 事件落盘路径（相对项目根目录）
DEFAULT_LOG_PATH = Path("data") / "event_bus_log.jsonl"

# 模块级观察者引用（由 bootstrap_event_bus() 填充，供 get_event_bus_stats 读取）
_META_OBSERVER: Optional["MetaCognitiveObserver"] = None
_L3_CONFIG_OBSERVER: Optional["L3ConfigObserver"] = None
_BOOTSTRAPPED = False


# ===================================================================
# L4 观察者：把系统事件转化为元认知观察
# ===================================================================


class MetaCognitiveObserver:
    """L4 元认知观察者。

    订阅全部系统事件，转发给 MetaCognitiveEngine.on_system_event()，
    并维护一份独立的只读事件统计（用于诊断面板 / 审计）。
    """

    def __init__(self, auto_reflect: bool = False) -> None:
        # 延迟导入避免循环依赖（meta_cognitive 依赖 event_bus）
        from src.services.meta_cognitive import MetaCognitiveEngine

        self._engine: Optional[MetaCognitiveEngine] = MetaCognitiveEngine(auto_reflect=auto_reflect)
        self._event_counts: Dict[str, int] = {}
        self._last_seen: Dict[str, str] = {}  # event_type -> iso timestamp

    def handle(self, event: SystemEvent) -> None:
        """EventBus 回调入口（subscribe_all）。只读，不抛异常。"""
        try:
            et = (
                event.event_type.value
                if isinstance(event.event_type, SystemEventType)
                else str(event.event_type)
            )
            self._event_counts[et] = self._event_counts.get(et, 0) + 1
            self._last_seen[et] = event.timestamp.isoformat()

            if self._engine is not None:
                self._engine.on_system_event(event)
        except Exception:
            logger.exception("MetaCognitiveObserver failed to handle event %s", event)

    def engine(self) -> Optional["Any"]:
        """暴露绑定的 MetaCognitiveEngine（供触发内省 / 生成报告）。"""
        return self._engine

    def stats(self) -> Dict[str, Any]:
        """返回观察者统计（供诊断面板 / 审计）。"""
        return {
            "total_events_observed": sum(self._event_counts.values()),
            "event_counts": dict(sorted(self._event_counts.items())),
            "last_seen": self._last_seen,
        }


# ===================================================================
# L3 观察者：配置回归观察（第一版仅记录）
# ===================================================================


class L3ConfigObserver:
    """L3 配置回归观察者。

    订阅 CONFIG_REGRESSION_DETECTED，记录回归信号到日志。
    第一版不触发任何自动回滚（ConfigAutoRollback.execute_rollback 保持人工触发）。
    """

    def __init__(self) -> None:
        self._regression_events: List[Dict[str, Any]] = []

    def on_config_regression(self, event: SystemEvent) -> None:
        try:
            self._regression_events.append({
                "timestamp": event.timestamp.isoformat(),
                "snapshot_id": event.payload.get("snapshot_id", ""),
                "signals": event.payload.get("regression_signals", []),
                "severity": (
                    event.severity.value
                    if isinstance(event.severity, EventSeverity)
                    else str(event.severity)
                ),
            })
            logger.warning(
                "L3 observed config regression (observe-only): %s",
                event.payload,
            )
        except Exception:
            logger.exception("L3ConfigObserver failed")

    def stats(self) -> Dict[str, Any]:
        return {"regression_events": len(self._regression_events)}


# ===================================================================
# 装配函数
# ===================================================================


def bootstrap_event_bus(log_path: Optional[Path] = None) -> SystemEventBus:
    """初始化系统事件总线并注册各层观察者。幂等。

    Args:
        log_path: 事件落盘路径。默认 data/event_bus_log.jsonl。

    Returns:
        初始化完成的 SystemEventBus 单例。
    """
    global _META_OBSERVER, _L3_CONFIG_OBSERVER, _BOOTSTRAPPED

    bus = SystemEventBus.instance(log_path=log_path or DEFAULT_LOG_PATH)

    if _BOOTSTRAPPED:
        logger.info("EventBus already bootstrapped; returning existing singleton")
        return bus

    # L4 元认知观察者（订阅全部事件）
    try:
        observer = MetaCognitiveObserver(auto_reflect=False)
        bus.subscribe_all(observer.handle)
        _META_OBSERVER = observer
        logger.info("L4 MetaCognitiveObserver attached (subscribe_all)")
    except Exception:
        logger.exception("Failed to attach L4 MetaCognitiveObserver; EventBus still usable")

    # L3 配置回归观察者（订阅 CONFIG_REGRESSION_DETECTED）
    try:
        l3_config = L3ConfigObserver()
        bus.subscribe(SystemEventType.CONFIG_REGRESSION_DETECTED, l3_config.on_config_regression)
        _L3_CONFIG_OBSERVER = l3_config
        logger.info("L3 ConfigObserver attached (CONFIG_REGRESSION_DETECTED)")
    except Exception:
        logger.exception("Failed to attach L3 ConfigObserver; EventBus still usable")

    _BOOTSTRAPPED = True
    logger.info("EventBus bootstrapped: L4 observer + L3 config observer registered")
    return bus


def publish_system_lifecycle(bus: SystemEventBus, phase: str, reason: str = "") -> None:
    """发布进程生命周期事件（SYSTEM_STARTUP / SYSTEM_SHUTDOWN）。

    Args:
        bus: SystemEventBus 实例。
        phase: "startup" 或 "shutdown"。
        reason: 附加说明（如 "main" / "api_server"）。
    """
    event_type = (
        SystemEventType.SYSTEM_STARTUP if phase == "startup" else SystemEventType.SYSTEM_SHUTDOWN
    )
    bus.publish(SystemEvent(
        event_id=f"{phase}_{reason}_{int(time.time() * 1000)}",
        event_type=event_type,
        severity=EventSeverity.INFO,
        source="bootstrap_event_bus",
        payload={"phase": phase, "reason": reason},
    ))


def get_event_bus_stats(bus: SystemEventBus) -> Dict[str, Any]:
    """汇总 EventBus + 各层观察者的只读统计。"""
    stats: Dict[str, Any] = {}
    try:
        stats["bus"] = bus.stats()
    except Exception:
        stats["bus"] = {}

    if _META_OBSERVER is not None:
        try:
            stats["l4_meta_observer"] = _META_OBSERVER.stats()
        except Exception:
            stats["l4_meta_observer"] = {}

    if _L3_CONFIG_OBSERVER is not None:
        try:
            stats["l3_config_observer"] = _L3_CONFIG_OBSERVER.stats()
        except Exception:
            stats["l3_config_observer"] = {}

    return stats


def get_meta_cognitive_engine() -> Optional["Any"]:
    """获取已装配的 MetaCognitiveEngine（供 L4 内省报告生成调用）。"""
    if _META_OBSERVER is not None:
        return _META_OBSERVER.engine()
    return None
