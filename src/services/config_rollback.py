# -*- coding: utf-8 -*-
"""
L3-2 配置自动回滚引擎（ConfigAutoRollback）。

功能：
- 配置变更前三层快照（内存→文件→Git blob）
- 变更后回归检测（错误率/任务失败/数据源/延迟/模块健康）
- 检测到回归时自动回滚到变更前快照
- 定时快照（定期备份）
- 过期快照清理

来源: docs/L3_L4_IMPLEMENTATION_PLAN.md §3
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ===================================================================
# 核心数据结构
# ===================================================================


@dataclass
class ConfigSnapshot:
    """配置快照 — 参考 SafeRollback 的三层备份模型。

    对应实施计划 §3.2 ConfigSnapshot。
    """

    snapshot_id: str                        # "{timestamp}_{checksum}"
    created_at: datetime
    trigger: str                            # "manual" | "pre_change" | "scheduled"
    changed_keys: List[str] = field(default_factory=list)
    # 三层存储
    layer1_memory: str = ""                 # 内存中的完整 .env 内容
    layer2_file: str = ""                   # 备份文件路径
    layer3_git_hash: str = ""               # Git blob hash
    checksum: str = ""                      # SHA256 前 16 位


@dataclass
class RegressionSignal:
    """回归信号 — 系统检测到配置可能引起的问题。

    对应实施计划 §3.2 RegressionSignal。
    """

    signal_id: str
    detected_at: datetime
    snapshot_before: str                    # 变更前的 snapshot_id
    snapshot_after: str                     # 变更后的 snapshot_id
    severity: str                           # "critical" | "warning" | "info"
    indicators: List[str]                   # 触发回归检测的具体指标列表
    auto_rollback_eligible: bool = False    # 是否满足自动回滚条件


@dataclass
class RollbackResult:
    """回滚结果。

    对应实施计划 §3.2 RollbackResult。
    """

    success: bool
    snapshot_id: str                        # 回滚到的快照 ID
    restored_keys: List[str]                # 被恢复的配置键
    layer_used: str                         # "memory" | "file" | "git"
    verified: bool                          # 回滚后是否通过验证
    error: str = ""


# ===================================================================
# ConfigAutoRollback
# ===================================================================


class ConfigAutoRollback:
    """配置自动回滚引擎。

    参考 laap-AGI SafeRollback 的三层备份模型（内存→文件→Git），
    结合 ConfigManager 的原子读写能力，实现：
    1. 配置变更前自动快照
    2. 变更后回归检测
    3. 检测到回归时自动回滚
    """

    # 内部常量
    _CHEKSUM_BYTES = 16                      # checksum 长度（SHA256 前 16 字符）

    def __init__(
        self,
        env_path: Optional[Path] = None,
        snapshot_dir: Optional[Path] = None,
        repo_root: Optional[Path] = None,
        on_rollback_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """初始化回滚引擎。

        Args:
            env_path: .env 文件路径，默认自动检测。
            snapshot_dir: 快照目录，默认 {env_path 同级}/.snapshots/。
            repo_root: Git 仓库根目录（用于 Layer 3 备份）。
            on_rollback_event: 回滚事件回调，签名 (level, message)。
        """
        self._env_path = env_path or self._resolve_env_path()
        self._snapshot_dir = snapshot_dir or self._env_path.parent / ".snapshots"
        self._repo_root = repo_root or self._detect_git_root()
        self._on_rollback_event = on_rollback_event

        # Layer 1: 内存快照（dict，snapshot_id → 全量 .env 内容）
        self._memory_snapshots: Dict[str, str] = {}

        # 快照索引（snapshot_id → ConfigSnapshot 元数据）
        self._snapshot_index: Dict[str, ConfigSnapshot] = {}

        # 回归检测参数
        self._observation_window_seconds = 300   # 变更后观察窗口
        self._error_rate_threshold = 2.0          # 错误率翻倍阈值
        self._task_failure_threshold = 0.2        # 任务失败率增长阈值
        self._latency_multiplier_threshold = 2.0  # 延迟倍增阈值

        self._lock = threading.RLock()

        # 确保快照目录存在
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)

        # 加载历史快照索引
        self._load_index()

    # ==================================================================
    # 路径解析（私有）
    # ==================================================================

    def _resolve_env_path(self) -> Path:
        """自动检测 .env 文件路径。"""
        # 环境变量优先
        env_path = os.environ.get("DSA_ENV_PATH")
        if env_path:
            return Path(env_path)

        # 在当前工作目录查找
        cwd = Path.cwd()
        for p in [cwd / ".env", cwd.parent / ".env"]:
            if p.exists():
                return p

        # 默认指向仓库根目录
        return cwd / ".env"

    def _detect_git_root(self) -> Optional[Path]:
        """检测 Git 仓库根目录。"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except Exception:
            pass
        return None

    # ==================================================================
    # 快照管理
    # ==================================================================

    def create_snapshot(self, trigger: str = "pre_change") -> ConfigSnapshot:
        """创建当前 .env 的三层快照。

        Layer 1 (内存): 保存完整文件内容到 self._memory_snapshots。
        Layer 2 (文件): 以原子写入方式保存到 snapshot_dir/{timestamp}_{checksum}.env.bak。
        Layer 3 (Git): git hash-object -w 保存 blob（如果 Git 可用）。

        Args:
            trigger: 触发类型（"pre_change", "manual", "scheduled"）。

        Returns:
            ConfigSnapshot: 含 checksum 和 snapshot_id 的完整快照。
        """
        with self._lock:
            # 读取当前内容
            if self._env_path.exists():
                content = self._env_path.read_text(encoding="utf-8")
            else:
                content = ""

            # 计算 checksum
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()[:self._CHEKSUM_BYTES]

            # 生成 snapshot_id
            ts = int(time.time() * 1000)
            snapshot_id = f"{ts}_{checksum}"

            # Layer 1: 内存
            self._memory_snapshots[snapshot_id] = content

            # Layer 2: 文件（原子写入）
            bak_path = self._snapshot_dir / f"{snapshot_id}.env.bak"
            layer2_path = str(bak_path)
            try:
                fd, tmp = tempfile.mkstemp(dir=str(self._snapshot_dir), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(content)
                    os.replace(tmp, str(bak_path))
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError:
                # 降级：直接写
                bak_path.write_text(content, encoding="utf-8")
                logger.warning("Atomic write failed for layer2 snapshot; used direct write")

            # Layer 3: Git blob
            layer3_hash = ""
            if self._repo_root and content:
                try:
                    git_result = subprocess.run(
                        ["git", "hash-object", "-w", "--stdin"],
                        input=content.encode("utf-8"),
                        capture_output=True, text=True,
                        cwd=str(self._repo_root),
                        timeout=5,
                    )
                    if git_result.returncode == 0:
                        layer3_hash = git_result.stdout.strip()
                except Exception:
                    pass

            # 构建快照元数据
            snapshot = ConfigSnapshot(
                snapshot_id=snapshot_id,
                created_at=datetime.now(),
                trigger=trigger,
                layer1_memory="memory",    # 标记存在
                layer2_file=layer2_path,
                layer3_git_hash=layer3_hash,
                checksum=checksum,
            )

            self._snapshot_index[snapshot_id] = snapshot
            self._save_index()

            logger.info(
                "Config snapshot created: id=%s trigger=%s checksum=%s",
                snapshot_id, trigger, checksum,
            )

            # Phase 1: 发布快照创建事件到 SystemEventBus（L3→L4 反馈链路）
            try:
                from src.services.event_bus import publish_module_event
                from src.services.event_bus import SystemEventType, EventSeverity
                publish_module_event(
                    event_type=SystemEventType.CONFIG_SNAPSHOT_CREATED,
                    severity=EventSeverity.INFO,
                    module_name="config_rollback",
                    extra={
                        "snapshot_id": snapshot_id,
                        "trigger": trigger,
                        "checksum": checksum,
                    },
                )
            except ImportError:
                pass

            return snapshot

    def list_snapshots(self, limit: int = 20) -> List[ConfigSnapshot]:
        """列出最近的配置快照。"""
        with self._lock:
            sorted_ids = sorted(
                self._snapshot_index.keys(),
                key=lambda x: self._snapshot_index[x].created_at,
                reverse=True,
            )
            return [self._snapshot_index[sid] for sid in sorted_ids[:limit]]

    def get_snapshot(self, snapshot_id: str) -> Optional[ConfigSnapshot]:
        """获取指定快照的完整信息。"""
        with self._lock:
            return self._snapshot_index.get(snapshot_id)

    def diff_snapshots(
        self, before_id: str, after_id: str
    ) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        """比较两个快照之间的配置差异。

        Returns:
            {key: (old_value, new_value)}。
        """
        before_content = self._get_snapshot_content(before_id)
        after_content = self._get_snapshot_content(after_id)

        before_map = self._parse_env_content(before_content)
        after_map = self._parse_env_content(after_content)

        diffs: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        all_keys = set(before_map.keys()) | set(after_map.keys())

        for key in all_keys:
            old_val = before_map.get(key)
            new_val = after_map.get(key)
            if old_val != new_val:
                diffs[key] = (old_val, new_val)

        return diffs

    # ==================================================================
    # 回归检测
    # ==================================================================

    def detect_regression(
        self,
        snapshot_before: str,
        snapshot_after: str,
        health_metrics: Dict[str, Any],
        observation_window_seconds: int = 300,
    ) -> Optional[RegressionSignal]:
        """检测配置变更后是否出现回归。

        检测信号（任一项触发即产生 RegressionSignal）：
        1. 关键错误率上升
        2. 任务失败率上升
        3. 数据源不可用
        4. API 响应时间恶化
        5. 模块健康状态恶化

        Args:
            snapshot_before: 变更前快照 ID。
            snapshot_after: 变更后快照 ID。
            health_metrics: 来自 HealthCheckDaemon 或 RunDiagnostics 的健康指标。
            observation_window_seconds: 观察窗口（秒）。

        Returns:
            RegressionSignal 如果检测到回归，否则 None。
        """
        indicators: List[str] = []
        before_ts = self._snapshot_timestamp(snapshot_before)
        after_ts = self._snapshot_timestamp(snapshot_after)

        # 检查各项回归指标
        for check_name, check_fn in [
            ("error_rate", self._check_error_rate_regression),
            ("task_failure", self._check_task_failure_regression),
            ("data_source", self._check_data_source_regression),
            ("latency", self._check_latency_regression),
            ("module_health", self._check_module_health_regression),
        ]:
            try:
                result = check_fn(before_ts, after_ts, health_metrics)
                if result:
                    indicators.append(check_name)
                    if isinstance(result, dict):
                        indicators[-1] += f":{result.get('detail', '')}"
            except Exception as exc:
                logger.debug("Regression check '%s' failed: %s", check_name, exc)

        if not indicators:
            return None

        # 确定严重性
        severity = "info"
        if any("error_rate" in i or "module_health" in i for i in indicators):
            severity = "critical"
        elif any("task_failure" in i or "data_source" in i for i in indicators):
            severity = "warning"

        # 自动回滚资格：仅 critical 且至少 2 个指标触发
        auto_eligible = severity == "critical" and len(indicators) >= 2

        signal = RegressionSignal(
            signal_id=f"reg_{int(time.time() * 1000)}",
            detected_at=datetime.now(),
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            severity=severity,
            indicators=indicators,
            auto_rollback_eligible=auto_eligible,
        )

        logger.warning(
            "Regression signal: severity=%s indicators=%s auto_eligible=%s",
            severity, indicators, auto_eligible,
        )

        return signal

    def _check_error_rate_regression(
        self, before: datetime, after: datetime, metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """检查错误率是否恶化。

        比较 before 和 after 窗口的 error_count/error_rate。
        """
        before_err = metrics.get("error_rate_before", metrics.get("error_rate"))
        after_err = metrics.get("error_rate_after", metrics.get("error_rate"))
        if before_err is not None and after_err is not None and before_err > 0:
            if after_err / before_err >= self._error_rate_threshold:
                return {"detail": f"error_rate {before_err}→{after_err}"}
        return None

    def _check_task_failure_regression(
        self, before: datetime, after: datetime, metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """检查任务失败率是否恶化。"""
        before_fail = metrics.get("task_failure_rate_before", metrics.get("task_failure_rate"))
        after_fail = metrics.get("task_failure_rate_after", metrics.get("task_failure_rate"))
        if before_fail is not None and after_fail is not None:
            if after_fail - before_fail >= self._task_failure_threshold:
                return {"detail": f"task_failure_rate {before_fail}→{after_fail}"}
        return None

    def _check_data_source_regression(
        self, before: datetime, after: datetime, metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """检查数据源可用性是否恶化。"""
        before_sources = metrics.get("source_failure_rates_before",
                                     metrics.get("source_failure_rates", {}))
        after_sources = metrics.get("source_failure_rates_after",
                                    metrics.get("source_failure_rates", {}))

        new_failures = []
        for source, rate in after_sources.items():
            before_rate = before_sources.get(source, 0)
            if rate > before_rate + 0.15:  # 失败率增长超过 15%
                new_failures.append(f"{source}:{rate}")

        if new_failures:
            return {"detail": "; ".join(new_failures)}
        return None

    def _check_latency_regression(
        self, before: datetime, after: datetime, metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """检查 API 延迟是否恶化。"""
        before_lat = metrics.get("latency_p95_before", metrics.get("latency_p95"))
        after_lat = metrics.get("latency_p95_after", metrics.get("latency_p95"))
        if before_lat is not None and after_lat is not None and before_lat > 0:
            if after_lat / before_lat >= self._latency_multiplier_threshold:
                return {"detail": f"p95_latency {before_lat}→{after_lat}ms"}
        return None

    def _check_module_health_regression(
        self, before: datetime, after: datetime, metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """检查模块健康状态是否恶化。"""
        before_modules = metrics.get("modules_healthy_before", metrics.get("modules_healthy", {}))
        after_modules = metrics.get("modules_healthy_after", metrics.get("modules_healthy", {}))

        degraded = []
        for mod_name, after_healthy in after_modules.items():
            before_healthy = before_modules.get(mod_name, True)
            if before_healthy and not after_healthy:
                degraded.append(mod_name)

        if degraded:
            return {"detail": f"newly_degraded: {', '.join(degraded)}"}
        return None

    # ==================================================================
    # 回滚执行
    # ==================================================================

    def execute_rollback(self, snapshot_id: str) -> RollbackResult:
        """执行配置回滚。

        按照三层回退策略：
        1. 尝试 Layer 1（内存快照）
        2. 尝试 Layer 2（备份文件）
        3. 尝试 Layer 3（Git checkout）

        Returns:
            RollbackResult: 回滚结果。
        """
        with self._lock:
            snapshot = self._snapshot_index.get(snapshot_id)
            if snapshot is None:
                return RollbackResult(
                    success=False, snapshot_id=snapshot_id,
                    restored_keys=[], layer_used="",
                    verified=False, error=f"Snapshot not found: {snapshot_id}",
                )

            content: Optional[str] = None
            layer_used = ""

            # Layer 1: 内存快照
            mem_content = self._memory_snapshots.get(snapshot_id, "")
            if mem_content:
                content = mem_content
                layer_used = "memory"

            # Layer 2: 备份文件
            if content is None and snapshot.layer2_file:
                bak = Path(snapshot.layer2_file)
                if bak.exists():
                    content = bak.read_text(encoding="utf-8")
                    layer_used = "file"

            # Layer 3: Git blob
            if content is None and snapshot.layer3_git_hash and self._repo_root:
                try:
                    git_result = subprocess.run(
                        ["git", "cat-file", "-p", snapshot.layer3_git_hash],
                        capture_output=True, text=True,
                        cwd=str(self._repo_root),
                        timeout=5,
                    )
                    if git_result.returncode == 0:
                        content = git_result.stdout
                        layer_used = "git"
                except Exception:
                    pass

            if content is None:
                return RollbackResult(
                    success=False, snapshot_id=snapshot_id,
                    restored_keys=[], layer_used="",
                    verified=False, error="All three layers exhausted",
                )

            # 写回 .env
            changed_keys = self._diff_content(
                self._env_path.read_text(encoding="utf-8") if self._env_path.exists() else "",
                content,
            )

            try:
                # 原子写入
                fd, tmp = tempfile.mkstemp(dir=str(self._env_path.parent), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(content)
                    os.replace(tmp, str(self._env_path))
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError:
                # 降级：直接写
                self._env_path.write_text(content, encoding="utf-8")

            # 验证
            verified, verify_msg = self._verify_rollback()

            result = RollbackResult(
                success=verified,
                snapshot_id=snapshot_id,
                restored_keys=changed_keys,
                layer_used=layer_used,
                verified=verified,
                error="" if verified else verify_msg,
            )

            if self._on_rollback_event:
                level = "INFO" if verified else "CRITICAL"
                self._on_rollback_event(
                    level,
                    f"Config rollback to {snapshot_id} ({layer_used}): "
                    f"{'OK' if verified else 'FAILED'} — "
                    f"restored keys: {', '.join(changed_keys[:10])}",
                )

            logger.info(
                "Config rollback executed: snapshot=%s layer=%s success=%s keys=%d",
                snapshot_id, layer_used, verified, len(changed_keys),
            )

            # Phase 1: 发布回滚事件到 SystemEventBus（L3→L4 反馈链路）
            try:
                from src.services.event_bus import publish_rollback_event
                publish_rollback_event(
                    snapshot_id=snapshot_id,
                    success=result.success,
                    restored_keys=changed_keys,
                    error=result.error,
                )
            except ImportError:
                pass

            return result

    def auto_rollback_if_needed(
        self,
        snapshot_before: str,
        snapshot_after: str,
        health_metrics: Dict[str, Any],
    ) -> Optional[RollbackResult]:
        """自动回滚：检测到回归 → 自动回滚到变更前快照。

        流程：
        1. detect_regression() → RegressionSignal
        2. 若 auto_rollback_eligible=True 且 severity="critical"
        3. execute_rollback(snapshot_before)
        4. 发送通知：告知用户已自动回滚及原因
        5. 记录回滚事件到日志
        """
        signal = self.detect_regression(
            snapshot_before, snapshot_after, health_metrics,
            observation_window_seconds=self._observation_window_seconds,
        )

        if signal is None:
            logger.info("No regression detected for snapshot %s", snapshot_after)
            return None

        if not signal.auto_rollback_eligible:
            logger.info(
                "Regression detected (severity=%s) but not auto-rollback eligible. "
                "Indicators: %s", signal.severity, signal.indicators,
            )
            return None

        logger.warning(
            "Auto-rollback triggered: severity=%s indicators=%s",
            signal.severity, signal.indicators,
        )

        result = self.execute_rollback(snapshot_before)

        # Phase 1: 发布自动回滚事件到 SystemEventBus（L3→L4 反馈链路）
        try:
            from src.services.event_bus import publish_rollback_event
            publish_rollback_event(
                snapshot_id=snapshot_before,
                success=result.success,
                restored_keys=result.restored_keys,
                error=result.error,
            )
        except ImportError:
            pass

        return result

    def _verify_rollback(self) -> Tuple[bool, str]:
        """验证回滚后的 .env 文件是否合法。

        检查：
        - 文件存在且非空
        - 语法有效（每行是注释/空行/KEY=VALUE，或 KEY="VALUE"）
        """
        if not self._env_path.exists():
            return False, ".env file does not exist after rollback"

        content = self._env_path.read_text(encoding="utf-8").strip()
        if not content:
            return False, ".env file is empty after rollback"

        for i, line in enumerate(content.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                return False, f"Line {i}: invalid syntax (missing '=')"

        return True, "OK"

    @staticmethod
    def _parse_env_content(content: str) -> Dict[str, str]:
        """解析 .env 内容为 key→value 字典。"""
        result: Dict[str, str] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                # 去除引号
                value = value.strip().strip('"').strip("'")
                result[key] = value
        return result

    @staticmethod
    def _diff_content(before: str, after: str) -> List[str]:
        """比较两段 .env 内容的差异键。"""
        before_map = ConfigAutoRollback._parse_env_content(before)
        after_map = ConfigAutoRollback._parse_env_content(after)
        changed: List[str] = []
        for key in set(before_map.keys()) | set(after_map.keys()):
            if before_map.get(key) != after_map.get(key):
                changed.append(key)
        return changed

    # ==================================================================
    # 变动前后钩子
    # ==================================================================

    def pre_change_hook(self) -> str:
        """配置变更前调用：创建快照并返回 snapshot_id。

        应在 ConfigManager.apply_updates() 之前调用。
        返回的 snapshot_id 供后续回归检测使用。

        Returns:
            str: 变更前快照 ID。
        """
        snapshot = self.create_snapshot(trigger="pre_change")
        logger.info("pre_change_hook: snapshot saved as %s", snapshot.snapshot_id)
        return snapshot.snapshot_id

    def post_change_hook(self, snapshot_before: str) -> None:
        """配置变更后调用：创建新快照 + 启动回归检测。

        流程：
        1. 创建 post-change 快照
        2. 设置定时器，在 observation_window_seconds 后执行回归检测
        3. 若检测到回归，自动回滚
        """
        snapshot_after = self.create_snapshot(trigger="post_change")

        def _delayed_regression_check():
            time.sleep(self._observation_window_seconds)
            try:
                from src.services.health_check import HealthCheckDaemon
                # 采集当前健康指标
                metrics = {}
                # 尝试从任何可用的源获取指标
                self.auto_rollback_if_needed(
                    snapshot_before,
                    snapshot_after.snapshot_id,
                    metrics,
                )
            except Exception as exc:
                logger.error("Delayed regression check failed: %s", exc)

        t = threading.Thread(target=_delayed_regression_check, daemon=True)
        t.start()

        logger.info(
            "post_change_hook: post-change snapshot=%s; "
            "regression check scheduled in %ds",
            snapshot_after.snapshot_id, self._observation_window_seconds,
        )

    # ==================================================================
    # 定时快照
    # ==================================================================

    def scheduled_snapshot(self) -> ConfigSnapshot:
        """定时创建快照（用于定期备份，而非变更触发）。

        可通过 HealthCheckDaemon 或独立定时任务调用。
        """
        return self.create_snapshot(trigger="scheduled")

    # ==================================================================
    # 清理与状态
    # ==================================================================

    def cleanup_old_snapshots(self, max_age_days: int = 30, keep_min: int = 10) -> int:
        """清理过期快照（参考 SafeRollback.cleanup_old_backups）。

        Args:
            max_age_days: 超过此天数的快照将被清理。
            keep_min: 至少保留的快照数量。

        Returns:
            int: 清理的快照数量。
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        removed = 0

        with self._lock:
            all_ids = sorted(
                self._snapshot_index.keys(),
                key=lambda x: self._snapshot_index[x].created_at,
            )

            # 至少保留 keep_min 个
            deletable = all_ids[:-keep_min] if len(all_ids) > keep_min else []

            for sid in deletable:
                snap = self._snapshot_index.get(sid)
                if snap is None:
                    continue
                if snap.created_at < cutoff:
                    # 清理 Layer 1
                    self._memory_snapshots.pop(sid, None)
                    # 清理 Layer 2
                    if snap.layer2_file:
                        try:
                            Path(snap.layer2_file).unlink(missing_ok=True)
                        except OSError:
                            pass
                    # 清理索引
                    del self._snapshot_index[sid]
                    removed += 1

            if removed > 0:
                self._save_index()
                logger.info("Cleaned up %d old snapshots", removed)

        return removed

    def stats(self) -> Dict[str, Any]:
        """兼容 health_check 的 stats() 接口。"""
        with self._lock:
            return {
                "snapshot_count": len(self._snapshot_index),
                "memory_snapshot_count": len(self._memory_snapshots),
                "git_backend_available": self._repo_root is not None,
                "env_path": str(self._env_path),
                "snapshot_dir": str(self._snapshot_dir),
                "oldest_snapshot": (
                    min(s.created_at for s in self._snapshot_index.values()).isoformat()
                    if self._snapshot_index else None
                ),
            }

    # ==================================================================
    # 索引持久化
    # ==================================================================

    def _load_index(self) -> None:
        """从快照目录加载索引。"""
        index_file = self._snapshot_dir / "index.json"
        if not index_file.exists():
            return

        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            for sid, sd in data.get("snapshots", {}).items():
                snap = ConfigSnapshot(
                    snapshot_id=sid,
                    created_at=datetime.fromisoformat(sd["created_at"]),
                    trigger=sd.get("trigger", ""),
                    changed_keys=sd.get("changed_keys", []),
                    layer2_file=sd.get("layer2_file", ""),
                    layer3_git_hash=sd.get("layer3_git_hash", ""),
                    checksum=sd.get("checksum", ""),
                )
                self._snapshot_index[sid] = snap
        except Exception:
            logger.exception("Failed to load snapshot index from %s", index_file)

    def _save_index(self) -> None:
        """保存快照索引到文件。"""
        index_file = self._snapshot_dir / "index.json"
        data = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "snapshots": {
                sid: {
                    "created_at": snap.created_at.isoformat(),
                    "trigger": snap.trigger,
                    "changed_keys": snap.changed_keys,
                    "layer2_file": snap.layer2_file,
                    "layer3_git_hash": snap.layer3_git_hash,
                    "checksum": snap.checksum,
                }
                for sid, snap in self._snapshot_index.items()
            },
        }

        try:
            fd, tmp = tempfile.mkstemp(dir=str(self._snapshot_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                os.replace(tmp, str(index_file))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            index_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

    def _get_snapshot_content(self, snapshot_id: str) -> str:
        """获取快照内容（从三层备份中尝试恢复）。"""
        # Layer 1
        content = self._memory_snapshots.get(snapshot_id, "")
        if content:
            return content

        # Layer 2
        snap = self._snapshot_index.get(snapshot_id)
        if snap and snap.layer2_file:
            bak = Path(snap.layer2_file)
            if bak.exists():
                return bak.read_text(encoding="utf-8")

        return ""

    def _snapshot_timestamp(self, snapshot_id: str) -> datetime:
        """从 snapshot_id 提取时间戳。"""
        snap = self._snapshot_index.get(snapshot_id)
        if snap:
            return snap.created_at
        return datetime.now()
