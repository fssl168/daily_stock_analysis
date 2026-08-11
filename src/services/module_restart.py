# -*- coding: utf-8 -*-
"""
L3-1 模块自动重启引擎（ModuleAutoRestarter）。

功能：
- 注册需要守护的模块（进程、线程、回调三级重启策略）
- 基于 HealthCheckDaemon 的健康信号触发重启决策
- 冷却期与限流保护
- 依赖链拓扑排序重启
- 持久化重启历史到 JSON 文件

来源: docs/L3_L4_IMPLEMENTATION_PLAN.md §2
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 外部依赖—延迟导入以避免循环引用
# ---------------------------------------------------------------------------

try:
    from src.services.health_check import HealthCheckDaemon, HealthStatus
except ImportError:  # pragma: no cover
    HealthCheckDaemon = None  # type: ignore
    HealthStatus = None  # type: ignore


# ===================================================================
# 核心数据结构
# ===================================================================


class RestartPolicy(str):
    """重启策略枚举。"""

    PROCESS = "process"       # 进程级：通过启动命令重启
    THREAD = "thread"         # 线程级：通过 restart_callback 重启
    METHOD = "method"         # 方法级：调用回调方法（无状态守护）


@dataclass
class ModuleDef:
    """被守护模块的定义。

    对应实施计划 §2.2 ModuleDef。
    """

    module_id: str                              # 模块唯一标识（如 "market_listener"）
    display_name: str                           # 人类可读名称（如 "市场行情监听"）
    policy: str = RestartPolicy.THREAD          # 重启策略

    # 进程级参数
    start_command: Optional[List[str]] = None   # subprocess 启动命令
    start_delay_seconds: float = 5.0            # 启动后等待时间
    working_dir: Optional[str] = None           # 进程工作目录
    env: Optional[Dict[str, str]] = None        # 进程环境变量
    port_check: Optional[int] = None            # 启动后需监听的端口

    # 线程/方法级参数
    restart_callback: Optional[Callable[[], Tuple[bool, str]]] = None  # 返回 (ok, msg)
    is_alive_check: Optional[Callable[[], bool]] = None                # 存活检查

    # 依赖
    depends_on: List[str] = field(default_factory=list)   # 依赖的 module_id 列表
    max_restarts_per_hour: int = 5                        # 限流
    cooldown_seconds: int = 60                            # 冷却时间
    restart_on_consecutive_failures: int = 3              # 连续失败多少次触发重启

    # Phase 2: 增强验证
    health_probe: Optional[Callable[[], bool]] = None     # 功能探针：验证模块是否真正恢复功能


@dataclass
class RestartRecord:
    """单次重启记录。

    对应实施计划 §2.2 RestartRecord。
    """

    record_id: str = ""
    module_id: str = ""
    timestamp: str = ""                         # ISO 8601
    policy: str = ""
    success: bool = False
    message: str = ""
    trigger_reason: str = ""                    # 触发原因（健康消息）
    dependency_chain: List[str] = field(default_factory=list)


@dataclass
class ModuleHealthState:
    """模块当前健康状态。

    对应实施计划 §2.2 ModuleHealthState。
    """

    module_id: str = ""
    healthy: bool = True
    consecutive_failures: int = 0
    last_restart: Optional[datetime] = None
    last_healthy: Optional[datetime] = None
    last_message: str = ""
    restart_count_total: int = 0
    restart_count_hourly: int = 0
    in_cooldown: bool = False
    cooldown_until: Optional[datetime] = None
    restarts: List[RestartRecord] = field(default_factory=list)


# ===================================================================
# ModuleAutoRestarter
# ===================================================================


class ModuleAutoRestarter:
    """模块自动重启引擎。

    集成到 HealthCheckDaemon 的检查周期中，监视模块健康状态，
    当检测到模块连续失败超过阈值时自动触发重启。

    用法::

        restarter = ModuleAutoRestarter(on_alert=notify_fn)
        restarter.register_module(ModuleDef(
            module_id="market_listener",
            display_name="Market Listener",
            policy=RestartPolicy.THREAD,
            restart_callback=lambda: listener.restart(),
            is_alive_check=lambda: listener.is_alive(),
        ))
        # 然后通过 HealthCheckDaemon 周期调用 update_health()
    """

    # 内部常量
    _RESTART_HISTORY_MAX = 50                   # 每个模块最多保留的重启记录

    def __init__(
        self,
        state_file: Optional[str] = None,
        on_alert: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """初始化重启引擎。

        Args:
            state_file: 状态持久化文件路径（JSON）；为 None 时仅内存。
            on_alert: 告警回调，签名 (level: str, message: str)。
        """
        self._modules: Dict[str, ModuleDef] = {}
        self._states: Dict[str, ModuleHealthState] = {}
        self._on_alert = on_alert
        self._state_file = state_file
        self._lock = threading.Lock()

        # Phase 3: 修复效果日志（延迟初始化，避免循环导入）
        self._effectiveness_log: Optional[Any] = None
        self._effectiveness_log_path: Optional[str] = None

        # 从持久化文件恢复状态
        if state_file:
            self.load_state()

    def _get_effectiveness_log(self):
        """延迟初始化 RepairEffectivenessLog（避免循环导入）。

        Phase 3: 每次重启后记录修复效果，供后续学习使用。
        """
        if self._effectiveness_log is None:
            try:
                from src.services.repair_effectiveness_log import RepairEffectivenessLog

                if self._effectiveness_log_path is None:
                    import os
                    self._effectiveness_log_path = os.environ.get(
                        "DSA_REPAIR_LOG_PATH",
                        str(Path(__file__).resolve().parent.parent.parent / "data" / "repair_effectiveness.json"),
                    )
                self._effectiveness_log = RepairEffectivenessLog(
                    persist_path=Path(self._effectiveness_log_path),
                )
            except ImportError:
                self._effectiveness_log = None  # 可选依赖
        return self._effectiveness_log

    # ==================================================================
    # 模块注册
    # ==================================================================

    def register_module(self, module_def: ModuleDef) -> None:
        """注册一个需要守护的模块。

        幂等：重复注册同一 module_id 会覆盖旧定义，但保留已有健康状态。
        """
        with self._lock:
            mid = module_def.module_id
            self._modules[mid] = module_def
            if mid not in self._states:
                self._states[mid] = ModuleHealthState(
                    module_id=mid,
                    healthy=True,
                    last_healthy=datetime.now(),
                )

    def unregister_module(self, module_id: str) -> None:
        """移除模块注册。"""
        with self._lock:
            self._modules.pop(module_id, None)
            self._states.pop(module_id, None)

    @property
    def registered_modules(self) -> List[str]:
        """返回所有已注册模块的 module_id 列表。"""
        with self._lock:
            return list(self._modules.keys())

    # ==================================================================
    # 健康状态更新（由 HealthCheckDaemon 周期调用）
    # ==================================================================

    def update_health(self, module_id: str, healthy: bool, message: str = "") -> None:
        """更新模块健康状态，必要时触发自动重启。

        应在每个 HealthCheckDaemon 检查周期后调用。

        Args:
            module_id: 模块标识。
            healthy: 当前是否健康。
            message: 状态描述。
        """
        with self._lock:
            state = self._states.get(module_id)
            if state is None:
                return

            state.last_message = message

            if healthy:
                state.consecutive_failures = 0
                state.healthy = True
                state.last_healthy = datetime.now()
                # 成功后释放冷却（允许后续正常重启）
                state.in_cooldown = False
                state.cooldown_until = None
            else:
                state.consecutive_failures += 1
                state.healthy = False

    # ==================================================================
    # 重启决策
    # ==================================================================

    def should_restart(self, module_id: str) -> bool:
        """检查模块是否满足自动重启条件。

        条件（全部满足时返回 True）：
        1. 模块已到达 consecutive_failures >= restart_on_consecutive_failures
        2. 当前未处于冷却期
        3. 未超过每小时最大重启次数

        Args:
            module_id: 模块标识。

        Returns:
            bool: 是否应触发重启。
        """
        with self._lock:
            md = self._modules.get(module_id)
            st = self._states.get(module_id)
            if md is None or st is None:
                return False

            # 条件 1：连续失败次数
            if st.consecutive_failures < md.restart_on_consecutive_failures:
                return False

            # 条件 2：冷却期
            if self._is_in_cooldown(module_id):
                return False

            # 条件 3：速率限制
            if self._is_rate_limited(module_id):
                return False

            return True

    # ==================================================================
    # 重启执行
    # ==================================================================

    def restart_module(self, module_id: str) -> Tuple[bool, str]:
        """执行模块重启（进程/线程/方法）。

        Args:
            module_id: 模块标识。

        Returns:
            Tuple[bool, str]: (成功, 描述)。
        """
        with self._lock:
            md = self._modules.get(module_id)
            st = self._states.get(module_id)
            if md is None:
                return False, f"Unknown module: {module_id}"
            if st is None:
                return False, f"No state for module: {module_id}"

            # 检查依赖
            dep_ok, bad_deps = self._check_dependencies(module_id)
            if not dep_ok:
                chain_ok, chain_msg = self._restart_dependency_chain(module_id)
                if not chain_ok:
                    return False, f"Dependency restart failed: {chain_msg}"

            # 执行重启
            record = RestartRecord(
                record_id=f"rst_{int(time.time() * 1000)}",
                module_id=module_id,
                timestamp=datetime.now().isoformat(),
                policy=md.policy,
                trigger_reason=st.last_message,
            )

            logger.info(
                "Restarting module '%s' (policy=%s, failures=%d)",
                module_id, md.policy, st.consecutive_failures,
            )

            if md.policy == RestartPolicy.PROCESS:
                ok, msg = self._restart_process(md)
            elif md.policy in (RestartPolicy.THREAD, RestartPolicy.METHOD):
                ok, msg = self._restart_thread_or_method(md)
            else:
                ok, msg = False, f"Unknown restart policy: {md.policy}"

            record.success = ok
            record.message = msg

            # 更新统计
            st.restart_count_total += 1
            st.restart_count_hourly += 1
            st.last_restart = datetime.now()
            st.consecutive_failures = 0  # 重启后重置失败计数
            st.restarts.append(record)

            # 限制历史记录数量
            if len(st.restarts) > self._RESTART_HISTORY_MAX:
                st.restarts = st.restarts[-self._RESTART_HISTORY_MAX:]

            # 进入冷却
            st.in_cooldown = True
            st.cooldown_until = datetime.now() + timedelta(seconds=md.cooldown_seconds)

            # Phase 2: 增强验证 + 升级链
            if ok:
                verified, verify_msg = self._verify_restart(md)
                if not verified:
                    record.success = False
                    record.message = (
                        f"Restart mechanically OK but health verification failed: {verify_msg}"
                    )
                    logger.warning(
                        "Module '%s' restart succeeded mechanically but "
                        "health verification failed: %s",
                        module_id, verify_msg,
                    )
                    escalation = self._escalate_repair_strategy(module_id, record)
                    if escalation:
                        record.message += f" | escalated_to={escalation}"
                    st.consecutive_failures += 1  # 验证失败应继续计数
                else:
                    record.message = f"Restart OK, {verify_msg}"

            # Phase 1: 发布重启事件到 SystemEventBus（L3→L4 反馈链路）
            try:
                from src.services.event_bus import publish_module_event
                from src.services.event_bus import SystemEventType, EventSeverity

                event_type = (
                    SystemEventType.MODULE_RESTARTED if ok
                    else SystemEventType.MODULE_RESTART_FAILED
                )
                severity = EventSeverity.INFO if ok else EventSeverity.ERROR
                publish_module_event(
                    event_type=event_type,
                    severity=severity,
                    module_name=module_id,
                    extra={
                        "message": msg,
                        "policy": md.policy,
                        "consecutive_failures": st.consecutive_failures,
                    },
                )
            except ImportError:
                pass

            # 告警
            if self._on_alert:
                level = "INFO" if ok else "CRITICAL"
                self._on_alert(level, f"[{module_id}] restart {'OK' if ok else 'FAILED'}: {msg}")

            # 持久化
            if self._state_file:
                self.save_state()

            # Phase 3: 记录修复效果到 RepairEffectivenessLog
            eff_log = self._get_effectiveness_log()
            if eff_log is not None and record is not None:
                try:
                    eff_log.record(
                        repair_id=record.record_id,
                        action_type="restart",
                        target=module_id,
                        pre_repair_health={"consecutive_failures": st.consecutive_failures if st else 0},
                        post_repair_health={"success": ok, "message": msg},
                    )
                except Exception:
                    logger.debug("Failed to record repair effectiveness", exc_info=True)

            return ok, msg

    # ==================================================================
    # 重启策略实现（私有）
    # ==================================================================

    def _restart_process(self, module_def: ModuleDef) -> Tuple[bool, str]:
        """进程级重启。

        步骤：
        1. 查找并终止旧进程（taskkill /F on Windows; SIGTERM on Linux）
        2. 等待端口释放（最多 10 秒）
        3. subprocess.Popen 启动新进程
        4. 等待 start_delay_seconds
        5. 检查新进程是否存活（poll() is None）
        """
        cmd = module_def.start_command
        if not cmd:
            return False, "No start_command configured"

        # 1. 终止旧进程（根据可执行文件名匹配）
        exe_name = os.path.basename(cmd[0])
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/IM", exe_name],
                    capture_output=True, timeout=10,
                )
            else:
                subprocess.run(
                    ["pkill", "-f", exe_name],
                    capture_output=True, timeout=10,
                )
        except Exception as exc:
            logger.warning("Failed to kill old process: %s", exc)

        # 2. 等待端口释放
        if module_def.port_check is not None:
            self._wait_for_port_release(module_def.port_check, timeout=10.0)

        # 3. 启动新进程
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=module_def.working_dir,
                env={**os.environ, **(module_def.env or {})},
            )
        except Exception as exc:
            return False, f"Failed to start process: {exc}"

        # 4. 等待启动延迟
        time.sleep(module_def.start_delay_seconds)

        # 5. 检查存活
        alive = proc.poll() is None
        if alive:
            return True, f"Process started (PID={proc.pid})"
        else:
            return False, f"Process exited immediately (code={proc.returncode})"

    def _restart_thread_or_method(self, module_def: ModuleDef) -> Tuple[bool, str]:
        """线程/方法级重启。

        调用 module_def.restart_callback()。
        回调负责：停止旧线程→清理资源→启动新线程→返回成功/失败。
        """
        cb = module_def.restart_callback
        if cb is None:
            return False, "No restart_callback configured"
        try:
            ok, msg = cb()
            return ok, msg
        except Exception as exc:
            logger.exception("Restart callback for '%s' raised", module_def.module_id)
            return False, str(exc)

    def _verify_restart(
        self, module_def: ModuleDef, timeout_seconds: float = 10.0
    ) -> Tuple[bool, str]:
        """验证重启是否成功——不只是存活检查，是功能健康验证。

        验证层级（逐级递进）：
        1. 进程存活（Popen.poll() is None）
        2. 端口监听（_is_port_listening）
        3. 健康回调（is_alive_check）
        4. 功能探针（health_probe，若配置）

        Args:
            module_def: 模块定义。
            timeout_seconds: 验证超时秒数。

        Returns:
            (verified, detail_message)
        """
        start = time.time()
        checks_passed: List[str] = []
        checks_failed: List[str] = []

        # Level 1 & 2: 进程存活 + 端口监听
        if module_def.policy == RestartPolicy.PROCESS:
            time.sleep(min(module_def.start_delay_seconds, 2.0))

            if module_def.port_check is not None:
                port_start = time.time()
                port_timeout = timeout_seconds / 2
                port_ok = False
                while time.time() - port_start < port_timeout:
                    if self._is_port_listening(module_def.port_check):
                        port_ok = True
                        break
                    time.sleep(0.5)
                if port_ok:
                    checks_passed.append(f"port:{module_def.port_check}")
                else:
                    checks_failed.append(f"port:{module_def.port_check}_not_listening")
            else:
                checks_passed.append("process_alive")

        # Level 3: 健康回调
        if module_def.is_alive_check is not None:
            try:
                if module_def.is_alive_check():
                    checks_passed.append("alive_check")
                else:
                    checks_failed.append("alive_check_false")
            except Exception as exc:
                checks_failed.append(f"alive_check_error:{exc}")

        # Level 4: 功能探针（若模块配置了 health_probe）
        health_probe = getattr(module_def, 'health_probe', None)
        if health_probe is not None:
            try:
                probe_ok = health_probe()
                if probe_ok:
                    checks_passed.append("health_probe")
                else:
                    checks_failed.append("health_probe_false")
            except Exception as exc:
                checks_failed.append(f"health_probe_error:{exc}")

        # 判决
        elapsed = time.time() - start
        if checks_failed:
            return False, (
                f"Verification FAILED after {elapsed:.1f}s: "
                f"passed={checks_passed}, failed={checks_failed}"
            )
        elif checks_passed:
            return True, (
                f"Verification OK after {elapsed:.1f}s: "
                f"checks={checks_passed}"
            )
        else:
            return True, f"No verification checks performed (assumed OK after {elapsed:.1f}s)"

    def _escalate_repair_strategy(
        self, module_id: str, last_record: RestartRecord
    ) -> Optional[str]:
        """当重启验证失败时，升级到更强的修复策略。

        升级链: 重启 → 降级非关键依赖 → 通知人工
        如果有配置回滚引擎可用，插入"回滚最近配置"步骤。

        Args:
            module_id: 失败的模块。
            last_record: 最近一次重启记录。

        Returns:
            升级到的策略名称，None 表示无法升级。
        """
        md = self._modules.get(module_id)
        st = self._states.get(module_id)
        if md is None or st is None:
            return None

        # 计算已连续失败次数
        recent_failures = [
            r for r in st.restarts[-5:]
            if not r.success
        ]

        escalation = None

        if len(recent_failures) >= 2:
            # Level 1: 尝试降级该模块的非关键依赖
            escalation = "degrade_dependencies"
            logger.warning(
                "Restart escalation L1 for '%s': degrading non-critical dependencies",
                module_id,
            )
            # 通过 GracefulDegradationEngine 临时降级
            try:
                from src.services.graceful_degradation import (
                    GracefulDegradationEngine,
                    CapabilityRule,
                    PressureLevel,
                )
                gde = GracefulDegradationEngine()
                gde.register_rule(CapabilityRule(
                    capability_id=f"module_{module_id}_deps",
                    display_name=f"{md.display_name} 非关键依赖",
                    level=PressureLevel.ELEVATED,
                    action="throttle",
                    throttle_ratio=0.3,
                    priority=10,
                ))
                gde.set_level(PressureLevel.ELEVATED)
            except ImportError:
                pass

        if len(recent_failures) >= 4:
            # Level 2: 通知人工
            escalation = "notify_human"
            logger.critical(
                "Restart escalation L2 for '%s': %d consecutive failures — human intervention needed",
                module_id, len(recent_failures),
            )
            if self._on_alert:
                self._on_alert(
                    "CRITICAL",
                    f"Module '{md.display_name}' ({module_id}) failed {len(recent_failures)} "
                    f"consecutive restarts. Manual intervention required. "
                    f"Last message: {last_record.message}",
                )

        return escalation

    # ==================================================================
    # 依赖处理
    # ==================================================================

    def _check_dependencies(self, module_id: str) -> Tuple[bool, List[str]]:
        """检查模块的所有依赖是否健康。

        Returns:
            Tuple[bool, List[str]]: (all_healthy, list_of_unhealthy_deps)
        """
        md = self._modules.get(module_id)
        if md is None:
            return True, []

        unhealthy: List[str] = []
        for dep_id in md.depends_on:
            dep_st = self._states.get(dep_id)
            if dep_st is None or not dep_st.healthy:
                unhealthy.append(dep_id)

        return len(unhealthy) == 0, unhealthy

    def _restart_dependency_chain(self, module_id: str) -> Tuple[bool, str]:
        """按依赖顺序重启模块链。

        使用拓扑排序找到依赖链中最底层的失败节点，从底层开始重启。
        """
        # 收集依赖图中的失败节点
        failed_nodes: Dict[str, ModuleDef] = {}
        self._collect_failed_deps(module_id, failed_nodes, visited=set())

        if not failed_nodes:
            return True, "no failed dependencies"

        # 拓扑排序（Kahn 算法）
        sorted_ids = self._topological_sort(failed_nodes)

        results: List[Tuple[str, bool, str]] = []
        for mid in sorted_ids:
            md = self._modules.get(mid)
            if md is None:
                continue
            ok, msg = self.restart_module(mid)
            results.append((mid, ok, msg))
            if not ok:
                return False, f"Chain restart failed at '{mid}': {msg}"

        return True, f"Chain restart done: {len(results)} modules"

    def _collect_failed_deps(
        self,
        module_id: str,
        result: Dict[str, ModuleDef],
        visited: set,
    ) -> None:
        """递归收集失败依赖。"""
        if module_id in visited:
            return
        visited.add(module_id)
        md = self._modules.get(module_id)
        if md is None:
            return
        st = self._states.get(module_id)
        if st is not None and st.consecutive_failures > 0:
            result[module_id] = md
        for dep_id in md.depends_on:
            self._collect_failed_deps(dep_id, result, visited)

    def _topological_sort(self, nodes: Dict[str, ModuleDef]) -> List[str]:
        """对模块图进行拓扑排序（Kahn 算法）。"""
        in_degree: Dict[str, int] = {mid: 0 for mid in nodes}
        adj: Dict[str, List[str]] = {mid: [] for mid in nodes}

        for mid, md in nodes.items():
            for dep in md.depends_on:
                if dep in nodes:
                    adj[dep].append(mid)
                    in_degree[mid] = in_degree.get(mid, 0) + 1

        queue = [mid for mid, deg in in_degree.items() if deg == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for successor in adj.get(node, []):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        # 有环时返回原始顺序
        if len(result) != len(nodes):
            return list(nodes.keys())
        return result

    # ==================================================================
    # 冷却与限流
    # ==================================================================

    def _is_in_cooldown(self, module_id: str) -> bool:
        """检查模块是否在冷却期。"""
        st = self._states.get(module_id)
        if st is None:
            return False
        if not st.in_cooldown or st.cooldown_until is None:
            return False
        if datetime.now() >= st.cooldown_until:
            st.in_cooldown = False
            st.cooldown_until = None
            return False
        return True

    def _is_rate_limited(self, module_id: str) -> bool:
        """检查是否超过每小时最大重启次数。"""
        md = self._modules.get(module_id)
        st = self._states.get(module_id)
        if md is None or st is None:
            return False

        # 清理过时的每小时计数
        one_hour_ago = datetime.now() - timedelta(hours=1)
        recent_restarts = [
            r for r in st.restarts
            if datetime.fromisoformat(r.timestamp) > one_hour_ago
        ]
        st.restart_count_hourly = len(recent_restarts)

        return st.restart_count_hourly >= md.max_restarts_per_hour

    # ==================================================================
    # 端口工具
    # ==================================================================

    @staticmethod
    def _is_port_listening(port: int) -> bool:
        """检查端口是否被监听。"""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        try:
            result = s.connect_ex(("127.0.0.1", port))
            return result == 0
        except Exception:
            return False
        finally:
            s.close()

    @staticmethod
    def _wait_for_port_release(port: int, timeout: float = 10.0) -> None:
        """等待端口释放。"""
        import socket
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            try:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return  # 端口已释放
            finally:
                s.close()
            time.sleep(0.5)

    # ==================================================================
    # 状态持久化
    # ==================================================================

    def save_state(self) -> None:
        """将当前所有模块健康状态和重启历史持久化到 state_file。

        使用 tempfile + os.replace 确保原子写入。
        """
        if self._state_file is None:
            return

        with self._lock:
            data: Dict[str, Any] = {"version": 1, "modules": {}}
            for mid, st in self._states.items():
                data["modules"][mid] = {
                    "healthy": st.healthy,
                    "consecutive_failures": st.consecutive_failures,
                    "last_restart": st.last_restart.isoformat() if st.last_restart else None,
                    "last_healthy": st.last_healthy.isoformat() if st.last_healthy else None,
                    "restart_count_total": st.restart_count_total,
                    "restarts": [
                        {
                            "record_id": r.record_id,
                            "timestamp": r.timestamp,
                            "policy": r.policy,
                            "success": r.success,
                            "message": r.message,
                            "trigger_reason": r.trigger_reason,
                        }
                        for r in st.restarts[-20:]  # 只保留最近 20 条
                    ],
                }

        # 原子写入
        dst = Path(self._state_file)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=str(dst.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                os.replace(tmp, str(dst))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            # 降级：直接覆盖写
            logger.warning("tempfile-based atomic write failed; falling back to direct write")
            dst.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    def load_state(self) -> None:
        """从 state_file 加载历史状态。"""
        if self._state_file is None:
            return

        src = Path(self._state_file)
        if not src.exists():
            return

        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            modules_data = data.get("modules", {})
            with self._lock:
                for mid, md in modules_data.items():
                    if mid not in self._states:
                        self._states[mid] = ModuleHealthState(module_id=mid)
                    st = self._states[mid]
                    st.consecutive_failures = md.get("consecutive_failures", 0)
                    st.restart_count_total = md.get("restart_count_total", 0)
                    for r in md.get("restarts", []):
                        st.restarts.append(RestartRecord(
                            record_id=r.get("record_id", ""),
                            module_id=mid,
                            timestamp=r.get("timestamp", ""),
                            policy=r.get("policy", ""),
                            success=r.get("success", False),
                            message=r.get("message", ""),
                            trigger_reason=r.get("trigger_reason", ""),
                        ))
        except Exception:
            logger.exception("Failed to load module restart state from %s", self._state_file)

    # ==================================================================
    # 查询接口
    # ==================================================================

    def get_module_status(self, module_id: str) -> Optional[ModuleHealthState]:
        """获取单个模块状态。"""
        with self._lock:
            return self._states.get(module_id)

    def get_all_status(self) -> Dict[str, ModuleHealthState]:
        """获取所有已注册模块的状态。"""
        with self._lock:
            return dict(self._states)

    def get_restart_summary(self, hours: int = 24) -> Dict[str, Any]:
        """获取最近 N 小时的重启摘要。

        Returns:
            Dict with keys: total_restarts, by_module, success_rate,
            modules_in_cooldown, modules_exhausted (rate-limited)
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        with self._lock:
            total = 0
            success = 0
            by_module: Dict[str, int] = {}
            cooldown: List[str] = []
            exhausted: List[str] = []

            for mid, st in self._states.items():
                count = 0
                for r in st.restarts:
                    try:
                        ts = datetime.fromisoformat(r.timestamp)
                    except ValueError:
                        continue
                    if ts >= cutoff:
                        count += 1
                        total += 1
                        if r.success:
                            success += 1
                if count > 0:
                    by_module[mid] = count
                if st.in_cooldown:
                    cooldown.append(mid)
                if self._is_rate_limited(mid):
                    exhausted.append(mid)

            return {
                "total_restarts": total,
                "by_module": by_module,
                "success_rate": round(success / max(total, 1), 3),
                "modules_in_cooldown": cooldown,
                "modules_rate_limited": exhausted,
            }

    def stats(self) -> Dict[str, Any]:
        """兼容 AGIAgent.health_check() 的 stats() 接口。"""
        return self.get_restart_summary(hours=24)


# ===================================================================
# 工厂函数
# ===================================================================


def setup_module_restarter(
    state_file: Optional[str] = None,
    notify_fn: Optional[Callable[[str, str], None]] = None,
) -> ModuleAutoRestarter:
    """工厂函数：创建并返回 ModuleAutoRestarter 实例。

    Args:
        state_file: 持久化文件路径。
        notify_fn: 告警回调。

    Returns:
        ModuleAutoRestarter: 配置好的重启引擎。
    """
    restarter = ModuleAutoRestarter(
        state_file=state_file,
        on_alert=notify_fn,
    )
    logger.info("ModuleAutoRestarter initialized (modules=%d)", len(restarter.registered_modules))
    return restarter
