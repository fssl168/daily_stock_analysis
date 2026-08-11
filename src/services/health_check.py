# -*- coding: utf-8 -*-
"""
系统健康检查与告警（HealthCheckDaemon）。

独立 daemon 线程周期执行全部注册检查项：

- 连续失败次数达到 ``alert_threshold``（默认 3）后才触发 ``on_alert("CRITICAL", msg)``
- 检查项恢复（healthy=True）时重置该组件的连续失败计数
- 单个检查项抛异常时仅记录日志，不影响其他检查项

模块级便捷检查函数（供调用方注册）：

- ``check_system_resources()``: 内存/CPU/磁盘占用超阈值告警（psutil 缺失时降级为 healthy）
- ``check_task_queue()``: 任务队列 pending 积压告警（队列未初始化 → healthy）
- ``check_ntp_sync()``: 复用 ``ExchangeClock.is_synced()``（只读引用）

来源: docs/architecture/realtime_quant_system_design.md §2.4
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# psutil 必须在 try/except 内导入：未安装时降级为 healthy，守护进程不崩溃
try:
    import psutil
except ImportError:  # 未安装 psutil 的环境降级处理
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# 告警阈值（与架构文档 §2.4 配置项默认值一致）
MEMORY_WARN_PCT = 85.0  # 内存占用告警阈值
CPU_WARN_PCT = 90.0  # CPU 占用告警阈值
DISK_WARN_PCT = 90.0  # 磁盘占用告警阈值
TASK_QUEUE_MAX_PENDING = 20  # 任务队列 pending 积压告警阈值
ALERT_THRESHOLD = 3  # 连续失败告警阈值


@dataclass
class HealthStatus:
    """单项健康检查结果。"""

    component: str
    healthy: bool
    message: str
    last_checked: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)


class HealthCheckDaemon:
    """独立线程：每 N 秒执行全套健康检查，推送告警。"""

    def __init__(
        self,
        on_alert: Callable[[str, str], None],
        check_interval: float = 30.0,
    ):
        self._checks: List[Callable[[], HealthStatus]] = []
        self._on_alert = on_alert
        self._interval = check_interval
        self._past_failures: Dict[str, int] = {}  # component -> 连续失败次数
        self._alert_threshold = ALERT_THRESHOLD
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register(self, check_fn: Callable[[], HealthStatus]) -> None:
        """注册检查项。"""
        self._checks.append(check_fn)

    def start(self) -> None:
        """启动守护线程（已运行时重复调用为 no-op）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="health-daemon"
        )
        self._thread.start()

    def stop(self) -> None:
        """停止守护线程并等待退出。"""
        self._shutdown.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def _loop(self) -> None:
        """守护线程主循环：每 interval 执行全部检查。"""
        while not self._shutdown.is_set():
            self._run_checks()
            self._shutdown.wait(timeout=self._interval)

    def _run_checks(self) -> List[HealthStatus]:
        """执行全部注册检查，返回本轮状态列表。

        检查项抛异常时记录日志并跳过，不影响其他检查项。
        每轮检查完成后发布 HEALTH_CHECK_COMPLETED 事件到 SystemEventBus
        （全主动观察：L4 元认知订阅健康趋势，第一版不触发自动修复）。
        """
        statuses: List[HealthStatus] = []
        for check in tuple(self._checks):
            try:
                status = check()
            except Exception as exc:
                logger.exception("Health check raised: %s", exc)
                continue
            statuses.append(status)
            if status.healthy:
                self._past_failures[status.component] = 0
            else:
                self._past_failures[status.component] = (
                    self._past_failures.get(status.component, 0) + 1
                )
                if self._past_failures[status.component] >= self._alert_threshold:
                    self._on_alert("CRITICAL", f"[{status.component}] {status.message}")

        # 发布健康检查聚合事件（观察型；失败仅记录，不影响健康检查本身）
        try:
            self._publish_health_event(statuses)
        except Exception as exc:
            logger.debug("publish health event failed (observe-only): %s", exc)

        return statuses

    def _publish_health_event(self, statuses: List[HealthStatus]) -> None:
        """发布 HEALTH_CHECK_COMPLETED 事件（尽力而为）。"""
        from dataclasses import asdict
        from src.services.event_bus import EventSeverity, SystemEvent, SystemEventType
        from src.services.event_bus import SystemEventBus

        bus = SystemEventBus.instance()
        payload_statuses = []
        for s in statuses:
            d = asdict(s)
            d["last_checked"] = s.last_checked.isoformat()
            payload_statuses.append(d)

        unhealthy = [s.component for s in statuses if not s.healthy]
        severity = EventSeverity.CRITICAL if len(unhealthy) >= self._alert_threshold else (
            EventSeverity.WARNING if unhealthy else EventSeverity.INFO
        )

        bus.publish(SystemEvent(
            event_id=f"health_{int(__import__('time').time() * 1000)}",
            event_type=SystemEventType.HEALTH_CHECK_COMPLETED,
            severity=severity,
            source="health_check_daemon",
            payload={
                "component_statuses": payload_statuses,
                "unhealthy_components": unhealthy,
                "unhealthy_count": len(unhealthy),
                "alert_threshold": self._alert_threshold,
            },
        ))


def check_system_resources() -> HealthStatus:
    """内存 >85% / CPU >90% / 磁盘 >90% 时告警；psutil 缺失时降级为 healthy。"""
    if psutil is None:
        return HealthStatus(
            component="system_resources",
            healthy=True,
            message="psutil not installed; degraded to healthy",
            metadata={"psutil_available": False},
        )
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=1)
    disk = psutil.disk_usage("/")
    issues = []
    if mem.percent > MEMORY_WARN_PCT:
        issues.append(f"memory={mem.percent}%")
    if cpu > CPU_WARN_PCT:
        issues.append(f"cpu={cpu}%")
    if disk.percent > DISK_WARN_PCT:
        issues.append(f"disk={disk.percent}%")
    return HealthStatus(
        component="system_resources",
        healthy=len(issues) == 0,
        message="; ".join(issues) if issues else "OK",
        metadata={
            "memory_pct": mem.percent,
            "cpu_pct": cpu,
            "disk_pct": disk.percent,
        },
    )


def check_task_queue() -> HealthStatus:
    """任务队列 pending 积压 >20 时告警；队列未初始化 → healthy。"""
    try:
        from src.services.task_queue import get_task_queue

        queue = get_task_queue()
    except Exception:
        logger.warning("Task queue unavailable; degraded to healthy")
        return HealthStatus(
            component="task_queue",
            healthy=True,
            message="not initialized",
            metadata={"pending": 0},
        )
    if queue is None:
        return HealthStatus(
            component="task_queue",
            healthy=True,
            message="not initialized",
            metadata={"pending": 0},
        )
    try:
        pending = len(queue.list_pending_tasks())
        stats = queue.get_task_stats()
    except Exception:
        logger.warning("Task queue inspection failed; degraded to healthy")
        return HealthStatus(
            component="task_queue",
            healthy=True,
            message="queue unavailable",
            metadata={"pending": 0},
        )
    healthy = pending <= TASK_QUEUE_MAX_PENDING
    return HealthStatus(
        component="task_queue",
        healthy=healthy,
        message=f"pending={pending}",
        metadata={"pending": pending, "stats": stats},
    )


def check_ntp_sync() -> HealthStatus:
    """复用 ExchangeClock.is_synced()（只读引用）判断 NTP 同步状态。"""
    from src.utils.exchange_clock import ExchangeClock

    synced = ExchangeClock.is_synced()
    return HealthStatus(
        component="ntp",
        healthy=synced,
        message="synced" if synced else "NOT SYNCHRONIZED",
        metadata={"offset_ms": getattr(ExchangeClock, "_offset_ms", None)},
    )


# ---------------------------------------------------------------------------
# Runtime-object health checks (registered from main.py via lambda closures)
# ---------------------------------------------------------------------------


def check_listener_alive(listener: Any) -> HealthStatus:
    """Check whether the MarketListener daemon thread is still running.

    Caller should pass the listener via a lambda at registration time,
    e.g. ``daemon.register(lambda: check_listener_alive(listener))``.
    """
    is_running = getattr(listener, "is_alive", lambda: False)()
    last_tick = getattr(listener, "_last_settle_date", None)
    return HealthStatus(
        component="market_listener",
        healthy=bool(is_running),
        message="running" if is_running else "DEAD",
        metadata={"last_settle": str(last_tick) if last_tick else "N/A"},
    )


def check_data_source_health(fetcher: Any) -> HealthStatus:
    """Check whether any data source has a failure rate above 30%.

    Uses ``_daily_source_health`` on the fetcher (if available).
    Caller should pass the fetcher via a lambda closure.
    """
    health_dict = getattr(fetcher, "_daily_source_health", {})
    failures: Dict[str, float] = {}
    for name, (s, f) in health_dict.items():
        total = s + f
        if total > 0 and f / total > 0.30:
            failures[name] = round(f / total, 3)
    return HealthStatus(
        component="data_sources",
        healthy=len(failures) == 0,
        message=f"failures: {failures}" if failures else "all healthy",
        metadata={"source_failure_rates": failures},
    )


def check_broker_connection(broker: Any) -> HealthStatus:
    """Check whether the broker backend is reachable.

    Caller should pass the broker via a lambda closure,
    e.g. ``daemon.register(lambda: check_broker_connection(broker))``.
    """
    connected = getattr(broker, "is_connected", lambda: False)()
    return HealthStatus(
        component="broker",
        healthy=bool(connected),
        message="connected" if connected else "DISCONNECTED",
    )
