# -*- coding: utf-8 -*-
"""
L3-3 优雅降级引擎（GracefulDegradationEngine）。

功能：
- 4 级压力等级：NORMAL → ELEVATED → HIGH → CRITICAL
- 每级对应不同的能力裁剪策略
- 健康信号聚合 + EMA 趋势跟踪
- 自动升级/降级 + 手动锁止
- 降级事件审计日志

来源: docs/L3_L4_IMPLEMENTATION_PLAN.md §4
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ===================================================================
# 核心枚举
# ===================================================================


class PressureLevel(str, Enum):
    """4 级压力等级。

      NORMAL   — 全功能运行
      ELEVATED — 延迟非核心分析
      HIGH     — 暂停非关键数据源
      CRITICAL — 仅核心报告，暂停所有非必须模块
    """

    def __new__(cls, value: str, order: int) -> "PressureLevel":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj._order = order
        return obj

    NORMAL = ("normal", 0)
    ELEVATED = ("elevated", 1)
    HIGH = ("high", 2)
    CRITICAL = ("critical", 3)


# ===================================================================
# 能力裁剪规则
# ===================================================================


@dataclass
class CapabilityRule:
    """能力裁剪规则 — 当达到某压力等级时启用/禁用指定能力。

    对应实施计划 §4.2 CapabilityRule。
    """

    capability_id: str                     # 能力标识，如 "chip_distribution"
    display_name: str                      # 显示名称
    level: PressureLevel                   # 在哪个压力等级触发
    action: str = "disable"                # "disable" | "throttle" | "defer"
    throttle_ratio: float = 0.5            # throttle action 时保留的比例
    defer_batch_size: int = 10             # defer action 时的批量大小
    priority: int = 0                     # 低优先级的能力先裁剪

    # Phase 3: 故障模式匹配
    fault_pattern: Optional[Dict[str, Any]] = None  # 故障模式匹配条件
    # fault_pattern 示例:
    # {"dominant_metric_contains": "latency", "signal_count_min": 2}
    # 当 fault_pattern 为 None 时，规则无条件应用于对应压力等级（兼容旧行为）


# ===================================================================
# 数据结构
# ===================================================================


@dataclass
class HealthSignal:
    """健康信号 — 来自 HealthCheckDaemon 或其它源的指标。

    对应实施计划 §4.2 HealthSignal。
    """

    source: str                            # 信号来源，如 "health_check", "diagnostics"
    metric: str                            # 指标名
    value: float                           # 当前值
    threshold_normal: float                # NORMAL 上限
    threshold_elevated: float              # ELEVATED 上限
    threshold_high: float                  # HIGH 上限
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DegradationEvent:
    """降级事件 — 记录每次压力等级变化。

    对应实施计划 §4.2 DegradationEvent。
    """

    event_id: str
    timestamp: datetime
    from_level: PressureLevel
    to_level: PressureLevel
    trigger_signals: List[str]             # 触发此次变化的信号源指标
    reason: str
    capabilities_affected: List[str]       # 受影响的能力


# ===================================================================
# GracefulDegradationEngine
# ===================================================================


class GracefulDegradationEngine:
    """优雅降级引擎。

    监听多个健康信号，当压力超过阈值时自动降级系统能力；
    当信号恢复到正常范围时自动升级（恢复能力）。

    核心机制：
    - 多信号聚合：N 个信号中只要有 1 个进入更高等级，整体压力至少为该等级
    - EMA 趋势跟踪：防止瞬时抖动误触发
    - 滞后（Hysteresis）：升级到新等级需要确认，降级到低等级需要更长的确认窗口
    - 手动锁止：支持手动锁止在当前等级，禁止自动升级
    """

    # EMA 平滑因子（0 < alpha <= 1，越小越平滑）
    _EMA_ALPHA = 0.3
    # 升级确认窗口（连续 N 次同等级才触发升级）
    _UPGRADE_CONFIRM_COUNT = 2
    # 降级确认窗口（连续 N 次低等级才触发降级，应大于升级窗口）
    _DOWNGRADE_CONFIRM_COUNT = 5

    def __init__(
        self,
        on_level_change: Optional[Callable[[PressureLevel, PressureLevel, str], None]] = None,
        history_size: int = 100,
    ) -> None:
        """初始化降级引擎。

        Args:
            on_level_change: 等级变化回调，签名 (from_level, to_level, reason)。
            history_size: 事件历史保留数量。
        """
        self._on_level_change = on_level_change

        # 当前压力等级
        self._current_level: PressureLevel = PressureLevel.NORMAL
        self._lock = threading.RLock()

        # 注册的健康信号
        self._signals: Dict[str, HealthSignal] = {}

        # EMA 跟踪（signal_id → ema_value）
        self._ema_values: Dict[str, float] = {}

        # 确认计数器
        # upgrade_confirms[target_level] = 连续确认次数
        self._upgrade_confirms: Dict[PressureLevel, int] = {
            lvl: 0 for lvl in PressureLevel
        }
        self._downgrade_confirms: Dict[PressureLevel, int] = {
            lvl: 0 for lvl in PressureLevel
        }

        # 能力裁剪规则
        self._rules: Dict[str, CapabilityRule] = {}
        # 被禁用的能力集合
        self._disabled_capabilities: set = set()
        # 被节流的能力
        self._throttled_capabilities: Dict[str, float] = {}

        # 手动锁止
        self._manual_lock_level: Optional[PressureLevel] = None

        # 事件历史
        self._event_history: deque = deque(maxlen=history_size)
        self._event_counter = 0

        # 注册默认能力裁剪规则
        self._register_default_rules()

    # ==================================================================
    # 规则管理
    # ==================================================================

    def _register_default_rules(self) -> None:
        """注册默认能力裁剪规则。

        按优先级从低到高排列，低优先级在最前面。
        """
        defaults = [
            # ELEVATED: 延迟非核心分析
            CapabilityRule(
                capability_id="chip_distribution",
                display_name="筹码分布",
                level=PressureLevel.ELEVATED,
                action="defer",
                defer_batch_size=20,
                priority=0,
            ),
            CapabilityRule(
                capability_id="fundamental_pipeline",
                display_name="基本面深度分析",
                level=PressureLevel.ELEVATED,
                action="throttle",
                throttle_ratio=0.5,
                priority=1,
            ),
            # HIGH: 暂停非关键数据源
            CapabilityRule(
                capability_id="news_fetch",
                display_name="新闻抓取",
                level=PressureLevel.HIGH,
                action="disable",
                priority=2,
                # Phase 3: 仅在延迟为主的故障模式时禁用新闻抓取
                # 如果是错误率升高导致的 HIGH，不改新闻抓取（可能是新闻 API 本身的问题）
                fault_pattern={
                    "dominant_metric_contains": "latency",
                    "signal_count_min": 2,
                },
            ),
            CapabilityRule(
                capability_id="eastmoney_patch",
                display_name="东方财富补丁数据",
                level=PressureLevel.HIGH,
                action="disable",
                priority=3,
            ),
            CapabilityRule(
                capability_id="extended_technical_indicators",
                display_name="扩展技术指标",
                level=PressureLevel.HIGH,
                action="disable",
                priority=4,
            ),
            # CRITICAL: 仅核心报告
            CapabilityRule(
                capability_id="notification_push",
                display_name="通知推送",
                level=PressureLevel.CRITICAL,
                action="throttle",
                throttle_ratio=0.3,
                priority=5,
            ),
            CapabilityRule(
                capability_id="non_core_data_sources",
                display_name="非核心数据源",
                level=PressureLevel.CRITICAL,
                action="disable",
                priority=6,
            ),
            CapabilityRule(
                capability_id="multi_market_analysis",
                display_name="多市场分析",
                level=PressureLevel.CRITICAL,
                action="disable",
                priority=7,
            ),
        ]
        for rule in defaults:
            self._rules[rule.capability_id] = rule

    def register_rule(self, rule: CapabilityRule) -> None:
        """注册（或覆盖）能力裁剪规则。"""
        with self._lock:
            self._rules[rule.capability_id] = rule

    def unregister_rule(self, capability_id: str) -> None:
        """移除能力裁剪规则。"""
        with self._lock:
            self._rules.pop(capability_id, None)

    def get_rules(self) -> List[CapabilityRule]:
        """获取所有已注册的规则。"""
        with self._lock:
            return list(self._rules.values())

    # ==================================================================
    # 信号管理
    # ==================================================================

    def register_signal(self, signal: HealthSignal) -> None:
        """注册或更新健康信号。

        如果 signal_id 已存在，更新值和时间戳。
        """
        signal_id = f"{signal.source}:{signal.metric}"
        with self._lock:
            self._signals[signal_id] = signal

            # 更新 EMA
            if signal_id in self._ema_values:
                self._ema_values[signal_id] = (
                    self._EMA_ALPHA * signal.value
                    + (1 - self._EMA_ALPHA) * self._ema_values[signal_id]
                )
            else:
                self._ema_values[signal_id] = signal.value

    def get_signal(self, source: str, metric: str) -> Optional[HealthSignal]:
        """获取注册的信号。"""
        signal_id = f"{source}:{metric}"
        with self._lock:
            return self._signals.get(signal_id)

    def clear_signals(self) -> None:
        """清除所有注册的信号（用于重置）。"""
        with self._lock:
            self._signals.clear()
            self._ema_values.clear()

    # ==================================================================
    # 压力等级评估
    # ==================================================================

    def evaluate_level(self) -> PressureLevel:
        """评估当前压力等级。

        基于所有注册信号的 EMA 值与各自阈值比较：
        - 任一信号的 EMA 超过 CRITICAL → CRITICAL
        - 任一信号的 EMA 超过 HIGH → HIGH
        - 任一信号的 EMA 超过 ELEVATED → ELEVATED
        - 全部在线内 → NORMAL
        """
        with self._lock:
            if not self._signals:
                return PressureLevel.NORMAL

            worst = PressureLevel.NORMAL
            for sid, signal in self._signals.items():
                ema = self._ema_values.get(sid, signal.value)

                if ema > signal.threshold_high:
                    worst = PressureLevel.CRITICAL
                    break  # 已到最高，无需继续
                elif ema > signal.threshold_elevated:
                    if worst._order < PressureLevel.HIGH._order:
                        worst = PressureLevel.HIGH
                elif ema > signal.threshold_normal:
                    if worst._order < PressureLevel.ELEVATED._order:
                        worst = PressureLevel.ELEVATED

            return worst

    def tick(self) -> Optional[DegradationEvent]:
        """执行一次评估周期。

        1. 根据当前信号评估压力等级
        2. 应用滞后确认逻辑
        3. 若等级变化，应用能力裁剪规则
        4. 记录降级事件

        Returns:
            DegradationEvent 如果等级发生变化，否则 None。
        """
        target = self.evaluate_level()

        with self._lock:
            current = self._current_level

            # 手动锁止：不允许自动升级
            if self._manual_lock_level is not None:
                # 只允许往更高级别方向走
                if target._order <= self._manual_lock_level._order:
                    return None
                # target 比 lock 更严重 → 允许
                target = max(target, self._manual_lock_level, key=lambda l: l._order)

            if target == current:
                # 重置确认计数器
                for lvl in PressureLevel:
                    self._upgrade_confirms[lvl] = 0
                    self._downgrade_confirms[lvl] = 0
                return None

            # 升级（压力增大）
            if target._order > current._order:
                self._upgrade_confirms[target] += 1
                # 重置降级确认
                for lvl in PressureLevel:
                    if lvl._order > target._order:
                        self._downgrade_confirms[lvl] = 0

                if self._upgrade_confirms[target] >= self._UPGRADE_CONFIRM_COUNT:
                    return self._apply_transition(current, target)

            # 降级（压力减小）
            else:
                self._downgrade_confirms[target] += 1
                # 重置升级确认
                for lvl in PressureLevel:
                    if lvl._order < target._order:
                        self._upgrade_confirms[lvl] = 0

                if self._downgrade_confirms[target] >= self._DOWNGRADE_CONFIRM_COUNT:
                    return self._apply_transition(current, target)

            return None

    def _apply_transition(
        self, from_level: PressureLevel, to_level: PressureLevel
    ) -> DegradationEvent:
        """执行压力等级转换：应用能力裁剪规则。"""
        # 确定触发信号
        trigger_signals: List[str] = []
        for sid, signal in self._signals.items():
            ema = self._ema_values.get(sid, signal.value)
            if to_level == PressureLevel.CRITICAL:
                if ema > signal.threshold_high:
                    trigger_signals.append(sid)
            elif to_level == PressureLevel.HIGH:
                if ema > signal.threshold_elevated:
                    trigger_signals.append(sid)
            elif to_level == PressureLevel.ELEVATED:
                if ema > signal.threshold_normal:
                    trigger_signals.append(sid)

        # 应用规则
        self._current_level = to_level
        capabilities_affected = self._apply_rules(to_level)

        # 记录事件
        self._event_counter += 1
        event = DegradationEvent(
            event_id=f"degrade_{self._event_counter}_{int(time.time()*1000)}",
            timestamp=datetime.now(),
            from_level=from_level,
            to_level=to_level,
            trigger_signals=trigger_signals,
            reason=f"Pressure level changed: {from_level.value} → {to_level.value}",
            capabilities_affected=capabilities_affected,
        )
        self._event_history.append(event)

        # 回调
        if self._on_level_change:
            self._on_level_change(from_level, to_level, event.reason)

        logger.warning(
            "Degradation: %s → %s, affected: %s, triggers: %s",
            from_level.value, to_level.value,
            capabilities_affected, trigger_signals,
        )

        # Phase 1: 发布降级事件到 SystemEventBus（L3→L4 反馈链路）
        try:
            from src.services.event_bus import publish_degradation_event
            publish_degradation_event(
                from_level=from_level.value,
                to_level=to_level.value,
                capabilities_affected=capabilities_affected,
                trigger_signals=trigger_signals,
            )
        except ImportError:
            pass  # event_bus 为可选依赖，加载失败时不阻塞降级流程

        return event

    def _apply_rules(self, level: PressureLevel) -> List[str]:
        """应用能力裁剪规则——支持故障模式匹配（Phase 3）。

        如果 CapabilityRule.fault_pattern 不为 None，则只有当前故障特征
        匹配该 pattern 时才激活规则。否则（fault_pattern=None），行为与旧版本一致。

        Returns:
            受影响的能力 ID 列表。
        """
        affected: List[str] = []
        self._disabled_capabilities.clear()
        self._throttled_capabilities.clear()

        # Phase 3: 提取当前故障特征用于 fault_pattern 匹配
        fault_features = self._extract_fault_features(level)

        for rule in self._rules.values():
            if level._order < rule.level._order:
                continue  # 未到达规则要求的压力等级

            # Phase 3: fault_pattern 匹配
            if rule.fault_pattern is not None:
                if not self._match_fault_pattern(rule.fault_pattern, fault_features):
                    continue  # 故障模式不匹配，跳过此规则

            # 压力等级到达 + 故障模式匹配（或无条件）→ 裁剪
            if rule.action == "disable":
                self._disabled_capabilities.add(rule.capability_id)
                affected.append(rule.capability_id)
            elif rule.action == "throttle":
                self._throttled_capabilities[rule.capability_id] = rule.throttle_ratio
                affected.append(rule.capability_id)
            elif rule.action == "defer":
                # defer: 批量延迟处理
                affected.append(rule.capability_id)

        return affected

    def _match_fault_pattern(
        self, pattern: Dict[str, Any], features: Dict[str, Any]
    ) -> bool:
        """将当前故障特征与规则的 fault_pattern 进行匹配。

        支持的匹配运算符（pattern key 后缀）:
        - `key` (无后缀) → features[key] == pattern[key]
        - `key_min` → features[key] >= pattern[key_min]
        - `key_max` → features[key] <= pattern[key_max]
        - `key_contains` → pattern[key_contains] in features[key]
        - `key_in` → features[key] in pattern[key_in]

        Returns:
            True 如果所有 pattern 条件都匹配。
        """
        for pkey, pval in pattern.items():
            # 解析运算符后缀
            if pkey.endswith("_min"):
                fkey = pkey[:-4]
                fval = features.get(fkey)
                if fval is None or not (fval >= pval):
                    return False
            elif pkey.endswith("_max"):
                fkey = pkey[:-4]
                fval = features.get(fkey)
                if fval is None or not (fval <= pval):
                    return False
            elif pkey.endswith("_contains"):
                fkey = pkey[:-9]
                fval = features.get(fkey)
                if fval is None or pval not in fval:
                    return False
            elif pkey.endswith("_in"):
                fkey = pkey[:-3]
                fval = features.get(fkey)
                if fval is None or fval not in pval:
                    return False
            else:
                # 精确匹配
                fval = features.get(pkey)
                if fval != pval:
                    return False

        return True

    def _extract_fault_features(self, level: PressureLevel) -> Dict[str, Any]:
        """提取当前故障特征（供 Phase 3 fault_pattern 匹配使用）。

        当前版本仅做信号聚合，返回特征字典。
        Phase 3 会扩展 CapabilityRule.fault_pattern 字段，
        本方法返回的特征用于匹配规则中的 fault_pattern。
        """
        features: Dict[str, Any] = {
            "pressure_level": level.value,
            "signal_count": len(self._signals),
            "trigger_sources": [],
            "dominant_metric": None,
            "max_ema_value": 0.0,
        }
        for sid, signal in self._signals.items():
            ema = self._ema_values.get(sid, signal.value)
            features["trigger_sources"].append(sid)
            if ema > features["max_ema_value"]:
                features["max_ema_value"] = ema
                features["dominant_metric"] = sid
        return features

    # ==================================================================
    # 能力查询
    # ==================================================================

    def is_enabled(self, capability_id: str) -> bool:
        """检查指定能力当前是否可用。"""
        with self._lock:
            if self._current_level == PressureLevel.NORMAL:
                return True
            return capability_id not in self._disabled_capabilities

    def get_throttle_ratio(self, capability_id: str) -> float:
        """获取能力的节流比例（1.0 = 全速，0.0 = 暂停）。"""
        with self._lock:
            return self._throttled_capabilities.get(capability_id, 1.0)

    def get_deferred_batch_size(self, capability_id: str) -> Optional[int]:
        """获取延迟批量大小。如果能力被 defer，返回 batch_size。"""
        with self._lock:
            for rule in self._rules.values():
                if rule.capability_id == capability_id and rule.action == "defer":
                    if self._current_level._order >= rule.level._order:
                        return rule.defer_batch_size
            return None

    # ==================================================================
    # 手动控制
    # ==================================================================

    def lock_level(self, level: Any) -> None:
        """手动锁止压力等级。

        锁止后系统不会自动降级到更低等级，但可以自动升级到更高级别。

        Args:
            level: PressureLevel 枚举值或字符串 ("normal"/"elevated"/"high"/"critical")。
        """
        if isinstance(level, str):
            level = PressureLevel(level)
        with self._lock:
            self._manual_lock_level = level
            logger.info("Pressure level locked to >= %s", level.value)

    def unlock(self) -> None:
        """解除手动锁止。"""
        with self._lock:
            self._manual_lock_level = None
            logger.info("Pressure level lock released")

    def set_level(self, level: PressureLevel) -> Optional[DegradationEvent]:
        """手动设置压力等级（用于测试或手动干预）。"""
        with self._lock:
            if level == self._current_level:
                return None
            return self._apply_transition(self._current_level, level)

    # ==================================================================
    # 状态查询
    # ==================================================================

    @property
    def current_level(self) -> PressureLevel:
        """当前压力等级。"""
        return self._current_level

    @property
    def disabled_capabilities(self) -> List[str]:
        """当前被禁用的能力列表。"""
        with self._lock:
            return list(self._disabled_capabilities)

    def get_degradation_summary(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """获取降级摘要。

        Args:
            since: 只包含此时间之后的事件。None = 所有历史。

        Returns:
            包含事件列表、统计信息、当前状态的字典。
        """
        with self._lock:
            events = list(self._event_history)
            if since:
                events = [e for e in events if e.timestamp >= since]

            level_transitions = {}
            for e in events:
                key = f"{e.from_level.value}→{e.to_level.value}"
                level_transitions[key] = level_transitions.get(key, 0) + 1

            return {
                "current_level": self._current_level.value,
                "manual_lock": self._manual_lock_level.value if self._manual_lock_level else None,
                "total_events": self._event_counter,
                "recent_events": len(events),
                "transitions": level_transitions,
                "disabled_capabilities": list(self._disabled_capabilities),
                "throttled_capabilities": dict(self._throttled_capabilities),
                "signal_count": len(self._signals),
                "signal_summary": {
                    sid: self._ema_values.get(sid, s.value)
                    for sid, s in self._signals.items()
                },
            }

    def get_event_history(self, limit: int = 20) -> List[DegradationEvent]:
        """获取最近的降级事件。"""
        with self._lock:
            return list(self._event_history)[-limit:]

    def stats(self) -> Dict[str, Any]:
        """兼容 health_check 的 stats() 接口。"""
        with self._lock:
            return {
                "current_level": self._current_level.value,
                "signal_count": len(self._signals),
                "rule_count": len(self._rules),
                "disabled_capabilities": len(self._disabled_capabilities),
                "throttled_capabilities": len(self._throttled_capabilities),
                "total_degradation_events": self._event_counter,
                "manual_lock": self._manual_lock_level is not None,
            }

    def reset(self) -> None:
        """重置引擎到初始状态（用于测试）。"""
        with self._lock:
            self._current_level = PressureLevel.NORMAL
            self._signals.clear()
            self._ema_values.clear()
            self._disabled_capabilities.clear()
            self._throttled_capabilities.clear()
            self._manual_lock_level = None
            self._event_history.clear()
            self._event_counter = 0
            for lvl in PressureLevel:
                self._upgrade_confirms[lvl] = 0
                self._downgrade_confirms[lvl] = 0
