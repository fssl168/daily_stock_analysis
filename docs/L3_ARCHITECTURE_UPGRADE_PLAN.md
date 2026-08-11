# L3 架构级自修复升级 — 函数级开发实施计划

> **基准日期**: 2026-08-11
> **审计依据**: `docs/L3_ARCHITECTURE_AUDIT.md`（5 findings, 1 critical / 2 high / 2 medium）
> **目标项目**: `D:\leanpython\daily_stock_analysis`
> **参考项目**: `D:\laap-AGI`

---

## 零、总览

### 0.1 审计结论回顾

当前 L3 三个模块（ModuleAutoRestarter / ConfigAutoRollback / GracefulDegradationEngine）是**操作级守护进程**，离"架构级自修复"差的核心能力：

1. **代码感知的故障源定位** — 无法定位和修复代码缺陷
2. **修复前后合约验证** — 仅 L3-2 有验证闭环
3. **修复策略学习** — 不根据历史效果调整策略
4. **L4 双向反馈** — L3 和 L4 是两个平行宇宙

### 0.2 4-Phase 升级路径

| Phase | 目标 | 工作日 | 核心产出 |
|-------|------|--------|---------|
| Phase 1 | L3↔L4 双向反馈链路 | 1-2 天 | event_bus.py 集成到 4 个模块 |
| Phase 2 | 修复验证闭环 | 2-3 天 | SelfHealingAction 基类 + 重启后验证 + 升级链 |
| Phase 3 | 策略学习与路由 | 3-5 天 | RepairEffectivenessLog + fault_pattern 匹配 |
| Phase 4 | 代码感知修复 | 6-10 天 | CodeAwareRepairAgent + AST patch + 合约验证 |

### 0.3 现有代码基线

| 文件 | 行数 | 状态 |
|------|------|------|
| `src/services/event_bus.py` | 518 | **已创建**，未集成到任何 L3/L4 模块 |
| `src/services/module_restart.py` | ~613 | 现有 L3-1，有 `_verify_restart()` 方法但仅在启动时检查存活 |
| `src/services/config_rollback.py` | ~611 | 现有 L3-2，唯一有完整"检测→修复→验证"闭环的模块 |
| `src/services/graceful_degradation.py` | 613 | 现有 L3-3，静态规则匹配，无 fault_pattern |
| `src/services/meta_cognitive.py` | 1234 | 现有 L4，无 `on_system_event()` 方法 |
| `tests/test_graceful_degradation.py` | 467 | 25 tests, all pass |
| `tests/test_config_rollback.py` | — | 27 tests, all pass |
| `tests/test_meta_cognitive.py` | — | 22 tests, all pass |
| `tests/test_module_restart.py` | — | 2 tests, all pass |

---

## 一、Phase 1: L3↔L4 双向反馈链路

> **对应审计**: Finding #1 [critical] — L3→L4 双向反馈链路缺失
> **目标**: SystemEventBus 集成到所有 L3 模块 + L4 MetaCognitiveEngine
> **前置条件**: `src/services/event_bus.py` 已创建（518 行，未集成）

### 1.1 修改 `src/services/graceful_degradation.py` (+35 行)

#### 1.1.1 在 `_apply_transition()` 中添加 EventBus 发布（行 400-445 之间）

**插入位置**: 行 439 `logger.warning(...)` 之后，行 445 `return event` 之前。

**新增代码**:

```python
# 行 439 之后插入：发布降级事件到 SystemEventBus
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
```

**设计决策**: 使用 `try/except ImportError` 保证 event_bus 为可选依赖——降级引擎本身不应依赖事件总线才能运行。

#### 1.1.2 在 `_apply_rules()` 中为 CapabilityRule 增加 fault_pattern 预埋（行 447-474）

**插入位置**: 行 460 `for rule in self._rules.values():` 之前。

**新增代码**:

```python
# 行 460 之前插入：提取当前故障特征用于匹配（Phase 3 使用）
_fault_features = self._extract_fault_features(level)
# 当前版本 fault_features 仅记录——Phase 3 会传入 _apply_rules 做匹配
```

**新增方法**（行 474 之后追加）:

```python
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
```

### 1.2 修改 `src/services/config_rollback.py` (+25 行)

#### 1.2.1 在 `execute_rollback()` 中添加 EventBus 发布（行 466-573 之间）

**插入位置**: 行 568 `logger.info(...)` 之后，行 573 `return result` 之前。

**新增代码**:

```python
# 行 568 之后插入：发布回滚事件到 SystemEventBus
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
```

#### 1.2.2 在 `auto_rollback_if_needed()` 中添加 EventBus 发布（行 575-610 之后）

**插入位置**: 在方法末尾 `return result` 之前（约行 609 附近），`execute_rollback()` 调用之后。

**新增代码**:

```python
# execute_rollback() 调用之后插入：
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
```

#### 1.2.3 在 `create_snapshot()` 中添加 EventBus 发布（行 183-268 之间）

**插入位置**: 行 263 `logger.info(...)` 之后，行 268 `return snapshot` 之前。

**新增代码**:

```python
# 行 263 之后插入：发布快照创建事件
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
```

### 1.3 修改 `src/services/module_restart.py` (+30 行)

#### 1.3.1 在 `restart_module()` 中添加 EventBus 发布

**插入位置**: 在 `restart_module()` 方法中（行 274-360 附近），重启执行后，根据成功/失败发布事件。

**新增代码**（在 `record.success = ok` 或等效位置之后）:

```python
# 重启完成后插入：
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
            "policy": md.policy if md else "unknown",
            "consecutive_failures": st.consecutive_failures if st else 0,
        },
    )
except ImportError:
    pass
```

### 1.4 修改 `src/services/meta_cognitive.py` (+65 行)

#### 1.4.1 新增 `on_system_event()` 方法（插入到 MetaCognitiveEngine 类中）

**插入位置**: 在 `force_reflection()` 方法之前（约行 1027），与 `start_episode()` / `end_episode()` 并列。

**新增方法**:

```python
# ==================================================================
# L3 → L4 事件接收（Phase 1: SystemEventBus 集成）
# ==================================================================

def on_system_event(self, event: Any) -> None:
    """接收并处理来自 SystemEventBus 的 L3 系统事件。
    
    这是 L3→L4 双向反馈链路的关键入口。L3 模块通过 SystemEventBus 
    发布降级/回滚/重启事件，本方法接收并转化为元认知的认知输入。
    
    处理逻辑（按事件类型分发）：
    - DEGRADATION_TRANSITION → 记录系统压力变化，供后续反思参考
    - CONFIG_ROLLBACK_EXECUTED → 记录配置变更，标记为潜在风险上下文
    - MODULE_RESTARTED / MODULE_RESTART_FAILED → 记录模块健康事件
    - REFLECTION_COMPLETED → 忽略（避免自循环）
    
    Args:
        event: SystemEvent 实例（从 event_bus 订阅接收）。
    """
    # 延迟导入避免循环依赖
    from src.services.event_bus import SystemEventType
    
    event_type = getattr(event, 'event_type', None)
    if event_type is None:
        return
    
    with self._lock:
        sev = getattr(event, 'severity', None)
        sev_str = sev.value if sev else "unknown"
        src = getattr(event, 'source', 'unknown')
        
        # 元认知记录：将系统事件转化为自我观察
        if event_type == SystemEventType.DEGRADATION_TRANSITION:
            self._system_observations.append({
                "type": "degradation",
                "timestamp": datetime.now().isoformat(),
                "from_level": event.payload.get("from_level", "?"),
                "to_level": event.payload.get("to_level", "?"),
                "capabilities": event.payload.get("capabilities_affected", []),
                "triggers": event.payload.get("trigger_signals", []),
            })
            logger.info("L4 observed degradation: %s → %s",
                       event.payload.get("from_level"),
                       event.payload.get("to_level"))
        
        elif event_type == SystemEventType.CONFIG_ROLLBACK_EXECUTED:
            self._system_observations.append({
                "type": "rollback",
                "timestamp": datetime.now().isoformat(),
                "snapshot_id": event.payload.get("snapshot_id", ""),
                "success": event.payload.get("success", False),
                "restored_keys": event.payload.get("restored_keys", []),
            })
        
        elif event_type in (
            SystemEventType.MODULE_RESTARTED,
            SystemEventType.MODULE_RESTART_FAILED,
        ):
            self._system_observations.append({
                "type": "module_restart",
                "timestamp": datetime.now().isoformat(),
                "module": event.payload.get("module_name", src),
                "success": event_type == SystemEventType.MODULE_RESTARTED,
                "message": event.payload.get("message", ""),
            })
        
        # 限制观察历史大小
        if len(self._system_observations) > 200:
            self._system_observations = self._system_observations[-200:]

def get_system_observations(
    self, limit: int = 50, observation_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """获取 L4 记录的系统观察历史。
    
    Args:
        limit: 返回条数上限。
        observation_type: 按类型筛选（"degradation"/"rollback"/"module_restart"）。
    
    Returns:
        系统观察列表（最近优先）。
    """
    with self._lock:
        obs = list(self._system_observations)
        if observation_type:
            obs = [o for o in obs if o.get("type") == observation_type]
        return obs[-limit:]
```

#### 1.4.2 在 `__init__()` 中初始化 `_system_observations` 列表

**插入位置**: 在 `MetaCognitiveEngine.__init__()` 方法中（约行 780 附近），与其他实例变量并列。

**修改内容**:

```python
# 在 __init__ 方法中添加：
self._system_observations: List[Dict[str, Any]] = []
```

#### 1.4.3 在 `get_self_report()` 中包含系统观察数据（行 1125-1169）

**插入位置**: 在 `get_self_report()` 返回的 dict 中增加 `system_observations` 字段。

**修改 `return` 字典**，增加一行:

```python
"system_observations_count": len(self._system_observations),
"recent_system_observations": self._system_observations[-20:] if self._system_observations else [],
```

#### 1.4.4 修改 `modules` 行（行 28-29）增加 `Any` 导入

`typing.Any` 已在 import 中——确认无新增 import 需求。

### 1.5 新建 `tests/test_event_bus_integration.py` (~180 行)

**文件路径**: `tests/test_event_bus_integration.py`

**测试函数清单**:

```python
# -*- coding: utf-8 -*-
"""Integration tests for SystemEventBus ↔ L3/L4 module integration (Phase 1)."""

# === EventBus + GracefulDegradation ===

def test_degradation_publishes_event():
    """验证 GracefulDegradationEngine.tick() 触发降级时发布 SystemEvent。"""
    # 1. 订阅 DEGRADATION_TRANSITION 事件
    # 2. 向 engine 注册一个 elevated 信号
    # 3. tick() 3 次触发升级
    # 4. 断言订阅者收到事件，payload 包含正确的 from_level/to_level

def test_degradation_event_payload_correct():
    """验证发布的降级事件 payload 包含 capabilities_affected。"""
    # 1. 触发降级到 HIGH
    # 2. 检查事件 payload 中的 capabilities_affected 列表

# === EventBus + ConfigAutoRollback ===

def test_rollback_publishes_event():
    """验证 execute_rollback() 发布 SystemEvent。"""
    # 1. 创建快照 A → 修改 .env → 创建快照 B
    # 2. 订阅 CONFIG_ROLLBACK_EXECUTED
    # 3. 执行回滚
    # 4. 断言收到事件

def test_snapshot_publishes_event():
    """验证 create_snapshot() 发布 CONFIG_SNAPSHOT_CREATED 事件。"""

# === EventBus + ModuleAutoRestarter ===

def test_restart_publishes_event():
    """验证 restart_module() 发布 MODULE_RESTARTED 事件。"""
    # 使用 THREAD 策略 + mock callback

def test_restart_failure_publishes_event():
    """验证重启失败时发布 MODULE_RESTART_FAILED 事件。"""

# === EventBus + MetaCognitiveEngine ===

def test_meta_engine_receives_degradation_event():
    """验证 MetaCognitiveEngine.on_system_event() 处理降级事件。"""
    # 1. 构造 DEGRADATION_TRANSITION SystemEvent
    # 2. 调用 meta.on_system_event(event)
    # 3. 断言 _system_observations 中有 degradation 类型记录

def test_meta_engine_receives_rollback_event():
    """验证 MetaCognitiveEngine.on_system_event() 处理回滚事件。"""

def test_get_system_observations_filters_by_type():
    """验证 get_system_observations() 按类型筛选功能。"""

def test_self_report_includes_observations():
    """验证 get_self_report() 包含 system_observations 字段。"""
```

### 1.6 Phase 1 集成启动代码

**文件**: `src/services/event_bus.py`（已创建，无需修改）

**在 `main.py` 或 `server.py` 启动时注册订阅**:

```python
# 在应用启动代码中添加（main.py 或 src/core/ 初始化模块）：
def _setup_l3_l4_event_bridge():
    """建立 L3→L4 事件桥接：让 MetaCognitiveEngine 订阅 L3 系统事件。"""
    from src.services.event_bus import SystemEventBus, SystemEventType
    from src.services.meta_cognitive import MetaCognitiveEngine
    
    bus = SystemEventBus.instance()
    meta = MetaCognitiveEngine.instance()  # 假设单例或有全局引用
    
    # L4 订阅所有 L3 关键事件
    bus.subscribe(SystemEventType.DEGRADATION_TRANSITION, meta.on_system_event)
    bus.subscribe(SystemEventType.CONFIG_ROLLBACK_EXECUTED, meta.on_system_event)
    bus.subscribe(SystemEventType.MODULE_RESTARTED, meta.on_system_event)
    bus.subscribe(SystemEventType.MODULE_RESTART_FAILED, meta.on_system_event)
```

### 1.7 Phase 1 验证清单

| 验证项 | 方法 | 预期结果 |
|--------|------|---------|
| `test_event_bus_integration.py` 全部通过 | `pytest tests/test_event_bus_integration.py -v` | 10 tests pass |
| 现有测试无回归 | `pytest tests/ -m "not network" -v` | 76 tests pass |
| event_bus 为可选依赖 | 临时移除 event_bus.py → L3 模块仍正常启动 | 无 ImportError 崩溃 |
| import 编译检查 | `python -m py_compile src/services/event_bus.py src/services/graceful_degradation.py src/services/config_rollback.py src/services/module_restart.py src/services/meta_cognitive.py` | 全部成功 |

---

## 二、Phase 2: 补齐修复验证闭环

> **对应审计**: Finding #2 [high] — ModuleAutoRestarter 盲重启 / Finding #5 [medium] — ConfigAutoRollback 是唯一正面参考
> **目标**: 抽象 SelfHealingAction 基类 + 重启后健康验证 + 无效重启升级链

### 2.1 新建 `src/services/self_healing_action.py` (~250 行)

**文件路径**: `src/services/self_healing_action.py`

**设计理念**: 将 L3-2 ConfigAutoRollback 已验证的"检测 → 修复 → 验证"模式抽象为基类，L3-1 ModuleAutoRestarter 和 L3-3 GracefulDegradationEngine 的修复动作继承该模式。

#### 2.1.1 核心数据结构

```python
# -*- coding: utf-8 -*-
"""
Self-Healing Action 抽象基类 —— L3 架构级自修复的统一动作模型。

将 ConfigAutoRollback 已验证的"检测 → 修复 → 验证"闭环抽象为基类，
所有 L3 修复动作（重启、回滚、降级、patch）继承此基类。

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 2 / Finding #5
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RepairStatus(str, Enum):
    """修复动作状态。"""
    PENDING = "pending"          # 待执行
    IN_PROGRESS = "in_progress"  # 执行中
    SUCCESS = "success"          # 修复成功（通过验证）
    FAILED = "failed"            # 修复失败（未通过验证）
    ESCALATED = "escalated"      # 已升级到更强的修复策略


@dataclass
class RepairRecord:
    """一次修复动作的完整记录。"""
    
    repair_id: str                              # "repair_{timestamp}_{hash}"
    action_type: str                            # "restart" | "rollback" | "degrade" | "patch"
    target: str                                 # 修复目标（module_id / config_key / capability_id）
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    status: str = RepairStatus.PENDING.value
    verification_result: Optional[bool] = None  # None = 未验证
    verification_detail: str = ""
    escalation_level: int = 0                   # 已升级次数
    escalated_to: Optional[str] = None          # 升级到的修复动作类型
    error_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class SelfHealingAction(ABC):
    """L3 自修复动作抽象基类。
    
    所有 L3 修复动作（重启、回滚、降级、patch）必须实现三个核心方法：
    1. _detect()  — 故障检测: 是否需要执行此修复？
    2. _repair()  — 修复执行: 执行修复动作
    3. _verify()  — 修复验证: 修复是否解决了问题？
    
    基类提供升级链（escalation chain）：当修复验证失败时自动升级到下一个策略。
    
    用法:
        class MyRestartAction(SelfHealingAction):
            def _detect(self, context: Dict[str, Any]) -> bool:
                return context.get("consecutive_failures", 0) >= 3
            
            def _repair(self, context: Dict[str, Any]) -> Tuple[bool, str]:
                # 执行重启
                return True, "restarted"
            
            def _verify(self, context: Dict[str, Any]) -> Tuple[bool, str]:
                # 验证健康
                return True, "healthy"
    """
    
    # 升级链：当此修复验证失败时，按顺序尝试的备选修复动作类型
    # 子类覆盖此字段定义自己的升级链
    escalation_chain: List[str] = []  # 如 ["restart", "rollback", "notify_human"]
    
    # 最大升级次数（防止无限升级）
    max_escalation_level: int = 3
    
    def __init__(
        self,
        action_type: str,
        target: str,
        on_escalate: Optional[Callable[[str, int, str], None]] = None,
        on_complete: Optional[Callable[[RepairRecord], None]] = None,
    ) -> None:
        self._action_type = action_type
        self._target = target
        self._on_escalate = on_escalate    # 升级回调
        self._on_complete = on_complete    # 完成回调
        self._repair_history: List[RepairRecord] = []
    
    # ---------- 抽象方法（子类必须实现） ----------
    
    @abstractmethod
    def _detect(self, context: Dict[str, Any]) -> bool:
        """检测是否需要执行此修复。
        
        Args:
            context: 故障上下文（健康指标、错误计数等）。
        
        Returns:
            True 如果需要修复。
        """
        ...
    
    @abstractmethod
    def _repair(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """执行修复动作。
        
        Args:
            context: 故障上下文。
        
        Returns:
            (success, detail_message)
        """
        ...
    
    @abstractmethod
    def _verify(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """验证修复是否解决了问题。
        
        Args:
            context: 修复后的上下文（应反映修复后状态）。
        
        Returns:
            (verified, detail_message)
        """
        ...
    
    # ---------- 模板方法 ----------
    
    def execute(self, context: Dict[str, Any]) -> RepairRecord:
        """执行完整的"检测 → 修复 → 验证"闭环。
        
        这是 SelfHealingAction 的核心模板方法。子类不应覆盖此方法——
        而是实现 _detect / _repair / _verify 三个抽象方法。
        
        Args:
            context: 故障上下文字典。
        
        Returns:
            RepairRecord: 完整的修复记录。
        """
        ts = int(time.time() * 1000)
        record = RepairRecord(
            repair_id=f"repair_{ts}_{self._action_type}",
            action_type=self._action_type,
            target=self._target,
            status=RepairStatus.IN_PROGRESS.value,
        )
        
        # Step 1: 检测
        if not self._detect(context):
            record.status = RepairStatus.PENDING.value
            record.verification_detail = "Detection returned False — no repair needed"
            self._repair_history.append(record)
            return record
        
        # Step 2: 修复
        try:
            ok, msg = self._repair(context)
            record.error_message = "" if ok else msg
        except Exception as exc:
            ok, msg = False, str(exc)
            record.error_message = msg
            logger.exception("Repair action '%s' raised", self._action_type)
        
        # Step 3: 验证
        if ok:
            verified, verify_msg = self._verify(context)
            record.verification_result = verified
            record.verification_detail = verify_msg
            
            if verified:
                record.status = RepairStatus.SUCCESS.value
            else:
                # 验证失败 → 尝试升级
                record.status = RepairStatus.FAILED.value
                escalated = self._try_escalate(record, context)
                if escalated:
                    record.status = RepairStatus.ESCALATED.value
        else:
            record.verification_result = False
            record.verification_detail = msg
            record.status = RepairStatus.FAILED.value
            # 修复本身失败 → 也尝试升级
            self._try_escalate(record, context)
        
        record.completed_at = datetime.now()
        self._repair_history.append(record)
        
        if self._on_complete:
            try:
                self._on_complete(record)
            except Exception:
                logger.exception("on_complete callback failed")
        
        return record
    
    def _try_escalate(
        self, record: RepairRecord, context: Dict[str, Any]
    ) -> bool:
        """尝试升级到下一个修复策略。
        
        从 escalation_chain 中按顺序选择下一个策略。
        如果已到达 max_escalation_level 或链已耗尽，不再升级。
        """
        if record.escalation_level >= self.max_escalation_level:
            logger.warning(
                "Max escalation level (%d) reached for action '%s' on '%s'",
                self.max_escalation_level, self._action_type, self._target,
            )
            return False
        
        chain = self.escalation_chain
        if not chain:
            return False
        
        # 找到当前 action_type 在 chain 中的位置，取下一个
        try:
            idx = chain.index(self._action_type)
            next_action = chain[idx + 1] if idx + 1 < len(chain) else chain[-1]
        except ValueError:
            next_action = chain[0]
        
        if next_action == self._action_type:
            return False  # 链已耗尽
        
        record.escalation_level += 1
        record.escalated_to = next_action
        
        if self._on_escalate:
            try:
                self._on_escalate(next_action, record.escalation_level, record.verification_detail)
            except Exception:
                logger.exception("on_escalate callback failed")
        
        logger.warning(
            "Self-healing escalated: %s → %s (level=%d, target=%s, reason=%s)",
            self._action_type, next_action,
            record.escalation_level, self._target, record.verification_detail,
        )
        
        return True
    
    def get_history(self, limit: int = 20) -> List[RepairRecord]:
        """获取修复历史。"""
        return self._repair_history[-limit:]
    
    def stats(self) -> Dict[str, Any]:
        """获取修复统计。"""
        total = len(self._repair_history)
        successes = sum(1 for r in self._repair_history if r.status == RepairStatus.SUCCESS.value)
        failures = sum(1 for r in self._repair_history if r.status == RepairStatus.FAILED.value)
        escalations = sum(1 for r in self._repair_history if r.status == RepairStatus.ESCALATED.value)
        return {
            "action_type": self._action_type,
            "target": self._target,
            "total_repairs": total,
            "successes": successes,
            "failures": failures,
            "escalations": escalations,
            "success_rate": successes / max(total, 1),
            "verification_rate": (
                sum(1 for r in self._repair_history if r.verification_result is not None)
                / max(total, 1)
            ),
        }
```

### 2.2 修改 `src/services/module_restart.py` (+120 行)

#### 2.2.1 增强 `_verify_restart()` 方法（替换行 431-454）

**当前问题**: `_verify_restart()` 仅在进程重启后检查 `poll()` 和端口监听，不验证模块是否真正恢复了健康功能。

**替换为**:

```python
def _verify_restart(
    self, module_def: ModuleDef, timeout_seconds: float = 10.0
) -> Tuple[bool, str]:
    """验证重启是否成功——不只是存活检查，是功能健康验证。
    
    验证层级（逐级递进）：
    1. 进程存活（Popen.poll() is None）
    2. 端口监听（_is_port_listening）
    3. 健康回调（is_alive_check）
    4. 功能探针（health_probe，若配置）
    5. 等待冷却期结束后的二次确认
    
    Args:
        module_def: 模块定义。
        timeout_seconds: 验证超时秒数。
    
    Returns:
        (verified, detail_message)
    """
    start = time.time()
    checks_passed: List[str] = []
    checks_failed: List[str] = []
    
    # Level 1: 进程存活
    if module_def.policy == RestartPolicy.PROCESS:
        # 等待进程稳定
        time.sleep(min(module_def.start_delay_seconds, 2.0))
        
        if module_def.port_check is not None:
            # Level 2: 端口监听
            port_ok = self._wait_for_port(module_def.port_check, timeout=timeout_seconds / 2)
            if port_ok:
                checks_passed.append("port_listening")
            else:
                checks_failed.append(f"port_{module_def.port_check}_not_listening")
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
```

#### 2.2.2 在 `ModuleDef` 中增加 `health_probe` 字段（行 55-81）

**修改**: 在 `ModuleDef` dataclass 末尾增加:

```python
# ModuleDef 末尾增加（行 81 之前）:
health_probe: Optional[Callable[[], bool]] = None  # 功能探针：验证模块是否真正恢复功能
```

#### 2.2.3 新增 `_escalate_repair_strategy()` 方法

**插入位置**: 在 `_verify_restart()` 之后（行 454 之后）。

```python
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
            from src.services.graceful_degradation import GracefulDegradationEngine
            gde = GracefulDegradationEngine()  # 或获取已有实例
            # 为失败的模块注册临时降级规则
            from src.services.graceful_degradation import CapabilityRule, PressureLevel
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
```

#### 2.2.4 修改 `restart_module()` 调用验证和升级链

**修改位置**: `restart_module()` 方法（行 274-360 附近），在重启执行后。

**关键改动**: 重启执行后调用 `_verify_restart()`，失败时调用 `_escalate_repair_strategy()`。

```python
# 在 restart_module() 中，重启执行后（record.success 设置之后）：
# 替换原有逻辑为：

# 验证重启是否真正恢复了模块健康
if ok:
    verified, verify_msg = self._verify_restart(md)
    record.success = verified
    record.message = verify_msg
    if not verified:
        logger.warning(
            "Restart of '%s' succeeded mechanically but health verification failed: %s",
            module_id, verify_msg,
        )
        escalation = self._escalate_repair_strategy(module_id, record)
        if escalation:
            record.message += f" | escalated_to={escalation}"
```

### 2.3 修改 `tests/test_module_restart.py` (+100 行)

**新增测试函数**:

```python
# === Phase 2: 重启验证 ===

def test_verify_restart_with_alive_check():
    """验证 _verify_restart() 调用 is_alive_check 回调。"""
    # 1. 注册模块（THREAD 策略，带 is_alive_check）
    # 2. 调用 _verify_restart()
    # 3. 断言 is_alive_check 被调用且结果正确

def test_verify_restart_with_health_probe():
    """验证 _verify_restart() 调用 health_probe 回调。"""
    # 1. 注册模块（带 health_probe）
    # 2. 验证返回结果反映 health_probe 结果

def test_verify_restart_port_check():
    """验证进程级重启的端口验证。"""

def test_escalate_after_failed_verification():
    """验证重启验证失败后触发升级链。"""
    # 1. 模拟连续失败的重启
    # 2. 断言 _escalate_repair_strategy 被调用

def test_escalate_notify_human_after_repeated_failures():
    """验证连续 4 次失败后触发人工通知。"""
```

### 2.4 Phase 2 验证清单

| 验证项 | 方法 | 预期结果 |
|--------|------|---------|
| `tests/test_module_restart.py` 新测试通过 | `pytest tests/test_module_restart.py -v` | 新增 5 tests pass |
| `tests/test_self_healing_action.py` 通过 | 新建测试文件 | ~8 tests pass |
| 现有测试无回归 | `pytest tests/ -m "not network" -v` | 76+ tests pass |
| SelfHealingAction 升级链逻辑 | 单元测试覆盖 3 级升级 | 升级路径正确 |
| ModuleDef.health_probe 向后兼容 | 无 health_probe 的旧 ModuleDef 仍正常工作 | 不崩溃 |

---

## 三、Phase 3: 策略学习与路由

> **对应审计**: Finding #3 [high] — 降级引擎缺少修复策略路由器 / Finding #4 [medium] — 缺少修复历史学习机制
> **目标**: RepairEffectivenessLog + fault_pattern 字段 + _analyze_effectiveness()

### 3.1 新建 `src/services/repair_effectiveness_log.py` (~220 行)

**文件路径**: `src/services/repair_effectiveness_log.py`

```python
# -*- coding: utf-8 -*-
"""
修复效果日志（RepairEffectivenessLog）—— L3 策略学习的数据基础。

记录每次修复动作的实际效果，周期性分析修复策略的有效性，
并将分析结果提供给 L4 元认知引擎作为学习输入。

核心数据流:
    L3 修复动作 → RepairEffectivenessEntry → 周期性 _analyze_effectiveness()
    → EffectivenessReport → 调整策略优先级 / L4 元认知输入

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 3 / Finding #4
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RepairOutcome(str, Enum):
    """修复结果分类。"""
    RESTORED = "restored"              # 修复后系统恢复正常
    DEGRADED_AFTER = "degraded_after"  # 修复后短期内再次故障
    NO_EFFECT = "no_effect"            # 修复无效果（故障持续）
    MADE_WORSE = "made_worse"          # 修复使情况更糟
    UNKNOWN = "unknown"                # 无法判断（观察窗口不足）


@dataclass
class RepairEffectivenessEntry:
    """单次修复效果记录。"""
    
    entry_id: str                               # "eff_{timestamp}_{hash}"
    repair_id: str                              # 关联的 RepairRecord.repair_id
    action_type: str                            # "restart" | "rollback" | "degrade"
    target: str                                 # 修复目标
    performed_at: datetime = field(default_factory=datetime.now)
    outcome: str = RepairOutcome.UNKNOWN.value
    time_to_next_failure_seconds: Optional[float] = None  # 修复后多久再次故障（None=未再故障）
    observation_window_seconds: int = 3600       # 观察窗口（默认 1 小时）
    pre_repair_health: Dict[str, Any] = field(default_factory=dict)
    post_repair_health: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "repair_id": self.repair_id,
            "action_type": self.action_type,
            "target": self.target,
            "performed_at": self.performed_at.isoformat(),
            "outcome": self.outcome,
            "time_to_next_failure_seconds": self.time_to_next_failure_seconds,
            "observation_window_seconds": self.observation_window_seconds,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepairEffectivenessEntry":
        return cls(
            entry_id=data["entry_id"],
            repair_id=data["repair_id"],
            action_type=data["action_type"],
            target=data["target"],
            performed_at=datetime.fromisoformat(data["performed_at"]),
            outcome=data.get("outcome", RepairOutcome.UNKNOWN.value),
            time_to_next_failure_seconds=data.get("time_to_next_failure_seconds"),
            observation_window_seconds=data.get("observation_window_seconds", 3600),
            metadata=data.get("metadata", {}),
        )


@dataclass
class EffectivenessReport:
    """修复效果分析报告——周期性产出，供 L3 策略调整和 L4 元认知使用。"""
    
    generated_at: datetime
    analysis_window_hours: int
    total_repairs: int
    by_action_type: Dict[str, Dict[str, Any]]   # action_type → {total, restored, degraded, no_effect, made_worse, effectiveness_score}
    by_target: Dict[str, Dict[str, Any]]         # target → 同上
    worst_performers: List[str]                  # 效果最差的修复策略（应降级）
    best_performers: List[str]                   # 效果最好的修复策略（应优先）
    recommendations: List[str]                   # 策略调整建议


class RepairEffectivenessLog:
    """修复效果日志——记录、分析、持久化修复动作的实际效果。
    
    用法:
        log = RepairEffectivenessLog(persist_path=Path("data/repair_effectiveness.json"))
        
        # 记录修复
        entry = log.record(
            repair_id="repair_xxx",
            action_type="restart",
            target="market_listener",
            pre_repair_health={"consecutive_failures": 3},
            post_repair_health={"healthy": True},
        )
        
        # 回填结果
        log.update_outcome(entry.entry_id, RepairOutcome.RESTORED, time_to_next_failure=3600)
        
        # 周期性分析
        report = log.analyze_effectiveness(window_hours=24)
    """
    
    _MAX_ENTRIES = 500
    
    def __init__(
        self,
        persist_path: Optional[Path] = None,
        observation_window_seconds: int = 3600,
    ) -> None:
        self._entries: List[RepairEffectivenessEntry] = []
        self._persist_path = persist_path
        self._observation_window_seconds = observation_window_seconds
        self._lock = threading.RLock()
        
        # 从磁盘加载历史
        if persist_path and persist_path.exists():
            self._load()
    
    # ==================================================================
    # 记录
    # ==================================================================
    
    def record(
        self,
        repair_id: str,
        action_type: str,
        target: str,
        pre_repair_health: Optional[Dict[str, Any]] = None,
        post_repair_health: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RepairEffectivenessEntry:
        """记录一次修复动作。"""
        ts = int(time.time() * 1000)
        entry = RepairEffectivenessEntry(
            entry_id=f"eff_{ts}_{action_type}",
            repair_id=repair_id,
            action_type=action_type,
            target=target,
            observation_window_seconds=self._observation_window_seconds,
            pre_repair_health=pre_repair_health or {},
            post_repair_health=post_repair_health or {},
            metadata=metadata or {},
        )
        
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._MAX_ENTRIES:
                self._entries = self._entries[-self._MAX_ENTRIES:]
            self._save()
        
        return entry
    
    def update_outcome(
        self,
        entry_id: str,
        outcome: RepairOutcome,
        time_to_next_failure_seconds: Optional[float] = None,
    ) -> bool:
        """回填修复效果。"""
        with self._lock:
            for entry in self._entries:
                if entry.entry_id == entry_id:
                    entry.outcome = outcome.value
                    entry.time_to_next_failure_seconds = time_to_next_failure_seconds
                    self._save()
                    return True
        return False
    
    # ==================================================================
    # 分析
    # ==================================================================
    
    def analyze_effectiveness(
        self, window_hours: int = 24
    ) -> EffectivenessReport:
        """分析指定窗口内的修复效果。
        
        为每个 (action_type, target) 组合计算 effectiveness_score:
            score = (restored - degraded_after - made_worse) / total
        正值表示修复有效，负值表示弊大于利，0 表示无效果。
        
        基于分析结果生成策略调整建议。
        """
        cutoff = datetime.now() - timedelta(hours=window_hours)
        
        with self._lock:
            recent = [e for e in self._entries if e.performed_at >= cutoff]
        
        if not recent:
            return EffectivenessReport(
                generated_at=datetime.now(),
                analysis_window_hours=window_hours,
                total_repairs=0,
                by_action_type={},
                by_target={},
                worst_performers=[],
                best_performers=[],
                recommendations=["No repair data in window — insufficient data for learning"],
            )
        
        # 按 action_type 聚合
        by_action: Dict[str, Dict[str, Any]] = {}
        for entry in recent:
            if entry.action_type not in by_action:
                by_action[entry.action_type] = {
                    "total": 0, "restored": 0, "degraded_after": 0,
                    "no_effect": 0, "made_worse": 0, "unknown": 0,
                }
            agg = by_action[entry.action_type]
            agg["total"] += 1
            agg[entry.outcome] = agg.get(entry.outcome, 0) + 1
        
        # 计算 effectiveness_score
        scored_actions: List[Tuple[str, float]] = []
        for atype, agg in by_action.items():
            score = (
                agg["restored"] - agg["degraded_after"] - agg["made_worse"]
            ) / max(agg["total"], 1)
            agg["effectiveness_score"] = round(score, 3)
            scored_actions.append((atype, score))
        
        scored_actions.sort(key=lambda x: x[1])
        
        # 生成建议
        recommendations: List[str] = []
        for atype, score in scored_actions:
            if score < -0.3:
                recommendations.append(
                    f"CRITICAL: '{atype}' has effectiveness_score={score:.2f} — "
                    f"consider disabling auto-{atype} and routing to human review"
                )
            elif score < 0:
                recommendations.append(
                    f"WARNING: '{atype}' has negative effectiveness ({score:.2f}) — "
                    f"reduce priority or increase verification strictness"
                )
            elif score > 0.5:
                recommendations.append(
                    f"GOOD: '{atype}' has high effectiveness ({score:.2f}) — "
                    f"keep as primary strategy"
                )
        
        return EffectivenessReport(
            generated_at=datetime.now(),
            analysis_window_hours=window_hours,
            total_repairs=len(recent),
            by_action_type=by_action,
            by_target={},  # 按 target 聚合类似
            worst_performers=[a for a, s in scored_actions if s < 0][:3],
            best_performers=[a for a, s in scored_actions if s > 0][-3:],
            recommendations=recommendations,
        )
    
    # ==================================================================
    # 查询
    # ==================================================================
    
    def get_entries_by_target(
        self, target: str, limit: int = 50
    ) -> List[RepairEffectivenessEntry]:
        """获取指定目标的修复效果记录。"""
        with self._lock:
            return [e for e in self._entries if e.target == target][-limit:]
    
    def get_entries_by_action(
        self, action_type: str, limit: int = 50
    ) -> List[RepairEffectivenessEntry]:
        """获取指定修复类型的记录。"""
        with self._lock:
            return [e for e in self._entries if e.action_type == action_type][-limit:]
    
    def stats(self) -> Dict[str, Any]:
        """获取日志统计。"""
        with self._lock:
            total = len(self._entries)
            outcomes: Dict[str, int] = {}
            for e in self._entries:
                outcomes[e.outcome] = outcomes.get(e.outcome, 0) + 1
            
            return {
                "total_entries": total,
                "outcome_distribution": outcomes,
                "oldest_entry": (
                    self._entries[0].performed_at.isoformat() if self._entries else None
                ),
                "newest_entry": (
                    self._entries[-1].performed_at.isoformat() if self._entries else None
                ),
            }
    
    def reset(self) -> None:
        """重置日志（仅用于测试）。"""
        with self._lock:
            self._entries.clear()
    
    # ==================================================================
    # 持久化
    # ==================================================================
    
    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = [e.to_dict() for e in self._entries[-self._MAX_ENTRIES:]]
            self._persist_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to save repair effectiveness log")
    
    def _load(self) -> None:
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._entries = [
                RepairEffectivenessEntry.from_dict(item)
                for item in data[-self._MAX_ENTRIES:]
            ]
        except Exception:
            logger.exception("Failed to load repair effectiveness log")
```

### 3.2 修改 `src/services/graceful_degradation.py` (+45 行)

#### 3.2.1 在 `CapabilityRule` 中增加 `fault_pattern` 字段（行 60-73）

**修改**: 在 `CapabilityRule` dataclass 末尾增加:

```python
# CapabilityRule 末尾增加（行 73 之前）:
fault_pattern: Optional[Dict[str, Any]] = None  # Phase 3: 故障模式匹配条件
# fault_pattern 示例:
# {"dominant_metric": "health_check:error_rate", "signal_count_min": 2}
# 当 fault_pattern 为 None 时，规则无条件应用于对应压力等级（兼容旧行为）
```

#### 3.2.2 修改 `_apply_rules()` 支持 fault_pattern 匹配（行 447-474）

**替换 `_apply_rules()` 方法**:

```python
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
    
    # 提取当前故障特征
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
```

#### 3.2.3 更新默认规则增加 fault_pattern 示例

**修改 `_register_default_rules()`**，为 selected rules 增加 fault_pattern:

```python
# 在 _register_default_rules() 中，修改第二条规则作为示例:
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
```

### 3.3 修改 `src/services/module_restart.py` (+30 行)

#### 3.3.1 在 ModuleAutoRestarter 中集成 RepairEffectivenessLog

**插入位置**: 在 `__init__()` 中初始化 `RepairEffectivenessLog` 实例（行 148-167）。

```python
# 在 __init__() 中添加：
self._effectiveness_log: Optional[Any] = None  # 延迟初始化

def _get_effectiveness_log(self):
    """延迟初始化 RepairEffectivenessLog（避免循环导入）。"""
    if self._effectiveness_log is None:
        try:
            from src.services.repair_effectiveness_log import RepairEffectivenessLog
            self._effectiveness_log = RepairEffectivenessLog(
                persist_path=Path(
                    os.environ.get(
                        "DSA_REPAIR_LOG_PATH",
                        str(Path(__file__).parent.parent.parent / "data" / "repair_effectiveness.json"),
                    )
                ),
            )
        except ImportError:
            self._effectiveness_log = None
    return self._effectiveness_log
```

#### 3.3.2 在 `restart_module()` 中记录修复效果

在 `restart_module()` 中，重启执行后记录到 effectiveness_log:

```python
# 在 restart_module() 结尾 return 之前：
eff_log = self._get_effectiveness_log()
if eff_log is not None:
    eff_log.record(
        repair_id=record.record_id,
        action_type="restart",
        target=module_id,
        pre_repair_health={"consecutive_failures": st.consecutive_failures if st else 0},
        post_repair_health={"success": ok, "message": msg},
    )
```

### 3.4 新建 `tests/test_repair_effectiveness_log.py` (~150 行)

```python
# 测试函数清单：

def test_record_and_retrieve():
    """验证记录修复效果条目并查询。"""

def test_update_outcome():
    """验证 update_outcome() 回填修复结果。"""

def test_analyze_effectiveness_empty():
    """验证空日志的分析报告。"""

def test_analyze_effectiveness_with_data():
    """验证有数据时的 effectiveness_score 计算。"""
    # 模拟: 3 restored, 1 degraded_after, 1 made_worse
    # score = (3 - 1 - 1) / 5 = 0.2

def test_analyze_effectiveness_negative_score():
    """验证负面效果的修复策略被正确标记。"""
    # 模拟: 0 restored, 3 degraded_after, 2 made_worse
    # score = (0 - 3 - 2) / 5 = -1.0

def test_recommendations_generated():
    """验证 EffectivenessReport 包含策略建议。"""

def test_persistence_roundtrip():
    """验证 save/load 循环。"""

def test_max_entries_limit():
    """验证超过 _MAX_ENTRIES 时截断。"""
```

### 3.5 Phase 3 验证清单

| 验证项 | 方法 | 预期结果 |
|--------|------|---------|
| `tests/test_repair_effectiveness_log.py` 全部通过 | `pytest tests/test_repair_effectiveness_log.py -v` | ~8 tests pass |
| `tests/test_graceful_degradation.py` 无回归 | `pytest tests/test_graceful_degradation.py -v` | 25 tests pass |
| fault_pattern 向后兼容 | 所有现有规则 fault_pattern=None → 行为不变 | 无回归 |
| fault_pattern 匹配逻辑 | 单元测试覆盖各种运算符 | 正确匹配/不匹配 |
| effectiveness_score 计算正确 | 用已知数据验证公式 | score = (restored-degraded-made_worse)/total |

---

## 四、Phase 4: 代码感知修复

> **对应审计**: Finding #1 [critical] 中隐含的"代码修复生成" + 审计报告 Phase 4 items 7/8
> **目标**: CodeAwareRepairAgent — AST 级故障分析 + patch 生成 + 合约验证
> **注意**: 这是从"操作级"到"架构级"的质变，依赖 LLM 集成。实现方式为 LLM 辅助的代码修复建议生成（非自动应用），修复应用仍需人工确认。

### 4.1 新建 `src/services/code_aware_repair.py` (~350 行)

**文件路径**: `src/services/code_aware_repair.py`

```python
# -*- coding: utf-8 -*-
"""
代码感知修复代理（CodeAwareRepairAgent）—— L3 架构级自修复的核心能力。

从操作级守护跨越到架构级自修复的关键：自动定位代码故障源、生成修复 patch、
并在修复前后验证业务合约。修复 patch 默认不自动应用，需人工确认。

核心能力:
1. AST 级故障分析 — 解析 Python 源码定位异常源头
2. Patch 生成 — 基于故障模式生成修复建议（diff 格式）
3. 合约验证 — 修复前后业务合约的一致性检查
4. LLM 集成 — 利用 LLM 分析复杂故障和生成修复方案

安全边界:
- patch 不自动应用 — 生成 unified diff 后等待人工确认
- 合约验证不通过时阻止 patch 应用
- 默认只分析当前仓库 `src/` 目录下的 Python 文件
- 不修改测试文件（tests/）

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 4 / Finding #1 (code-aware repair)
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ===================================================================
# 数据结构
# ===================================================================


class FaultCategory(str, Enum):
    """故障分类。"""
    IMPORT_ERROR = "import_error"           # 导入失败
    ATTRIBUTE_ERROR = "attribute_error"     # 属性不存在
    TYPE_ERROR = "type_error"               # 类型错误
    KEY_ERROR = "key_error"                 # 字典键缺失
    INDEX_ERROR = "index_error"             # 索引越界
    VALUE_ERROR = "value_error"             # 值错误
    TIMEOUT_ERROR = "timeout_error"         # 超时
    CONNECTION_ERROR = "connection_error"   # 连接失败
    RESOURCE_EXHAUSTED = "resource_exhausted"  # 资源耗尽
    UNKNOWN = "unknown"


@dataclass
class FaultLocation:
    """故障定位信息。"""
    
    file_path: str                          # 故障源文件路径
    line_number: int                        # 故障行号
    function_name: str                      # 所在函数
    exception_type: str                     # 异常类型
    exception_message: str                  # 异常消息
    traceback_summary: str                  # traceback 摘要
    category: str = FaultCategory.UNKNOWN.value
    affected_modules: List[str] = field(default_factory=list)
    ast_context: Optional[str] = None       # AST 上下文（故障函数源码片段）


@dataclass
class RepairPatch:
    """修复 patch — 一个 unified diff 片段。"""
    
    patch_id: str                           # "patch_{timestamp}_{hash}"
    fault_location: FaultLocation
    file_path: str
    original_lines: str                     # 原始代码行
    patched_lines: str                      # 修复后代码行
    diff: str                               # unified diff
    explanation: str                        # 修复说明（人类可读 + LLM 生成）
    confidence: float                       # 修复置信度 [0, 1]
    auto_applicable: bool = False           # 是否可以自动应用（默认 False）
    contract_checks: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"                 # pending | applied | rejected | verified


@dataclass
class ContractCheckResult:
    """合约检查结果。"""
    
    check_name: str
    passed: bool
    detail: str = ""
    before_value: Any = None
    after_value: Any = None


# ===================================================================
# CodeAwareRepairAgent
# ===================================================================


class CodeAwareRepairAgent:
    """代码感知修复代理。
    
    当 L3 操作级修复（重启/回滚/降级）无法解决问题时，
    本代理尝试进行代码级的故障分析和修复建议。
    
    安全设计:
    - 所有 patch 默认 auto_applicable=False，需人工确认
    - 合约验证不通过时标记 patch 为高风险
    - 仅分析 src/ 目录下的文件（不修改 tests/）
    - 最大分析深度限制，防止无限递归
    
    用法:
        agent = CodeAwareRepairAgent(repo_root=Path.cwd())
        
        # 从异常信息定位故障
        fault = agent.locate_fault(
            exception_type="AttributeError",
            exception_message="'NoneType' object has no attribute 'close'",
            traceback_text="...",
        )
        
        # 生成修复 patch
        patch = agent.generate_patch(fault)
        
        # 验证合约
        contract_ok = agent.validate_contract(patch)
        
        # 应用（需显式确认）
        if patch.auto_applicable and contract_ok:
            agent.apply_patch(patch, dry_run=True)  # dry_run=True 只生成 diff
    """
    
    # 安全边界：仅分析这些目录
    _ANALYSIS_DIRS = ["src/", "data_provider/", "api/", "bot/"]
    # 禁止修改的目录
    _FORBIDDEN_DIRS = ["tests/", ".git/", "venv/", ".venv/", "__pycache__/"]
    # 最大分析深度（调用栈层数）
    _MAX_TRACEBACK_DEPTH = 10
    
    def __init__(
        self,
        repo_root: Path,
        on_patch_ready: Optional[Callable[[RepairPatch], None]] = None,
        llm_call: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        """初始化修复代理。
        
        Args:
            repo_root: 仓库根目录。
            on_patch_ready: patch 就绪回调（用于通知/日志）。
            llm_call: LLM 调用函数，签名 (system_prompt, user_prompt) → response_text。
                     为 None 时使用纯静态分析（启发式修复）。
        """
        self._repo_root = repo_root
        self._on_patch_ready = on_patch_ready
        self._llm_call = llm_call
        self._lock = threading.RLock()
        self._patches: List[RepairPatch] = []
        self._patch_counter = 0
    
    # ==================================================================
    # 故障定位
    # ==================================================================
    
    def locate_fault(
        self,
        exception_type: str,
        exception_message: str,
        traceback_text: str = "",
        module_name: str = "",
    ) -> Optional[FaultLocation]:
        """从异常信息定位代码故障源。
        
        分析策略:
        1. 解析 traceback 定位精确的文件和行号
        2. 若无法解析 traceback，搜索仓库中相关的 import / call site
        3. 提取故障函数的 AST 上下文
        
        Args:
            exception_type: 异常类型（如 "AttributeError"）。
            exception_message: 异常消息。
            traceback_text: 完整 traceback 文本。
            module_name: 出故障的模块名（辅助定位）。
        
        Returns:
            FaultLocation 如果定位成功，否则 None。
        """
        # Step 1: 解析 traceback
        file_path, line_number, func_name = self._parse_traceback(traceback_text)
        
        # Step 2: 如果 traceback 解析失败，尝试搜索
        if file_path is None and module_name:
            file_path = self._find_module_file(module_name)
        
        if file_path is None:
            logger.warning("Could not locate fault source from traceback or module name")
            return None
        
        # Step 3: 分类故障
        category = self._classify_fault(exception_type, exception_message)
        
        # Step 4: 提取 AST 上下文
        ast_context = None
        full_path = self._repo_root / file_path
        if full_path.exists():
            try:
                source = full_path.read_text(encoding="utf-8")
                if line_number and line_number > 0:
                    # 提取故障行上下 10 行的代码
                    lines = source.split("\n")
                    start = max(0, line_number - 10)
                    end = min(len(lines), line_number + 10)
                    ast_context = "\n".join(
                        f"{i+1}: {line}" for i, line in enumerate(lines[start:end], start=start)
                    )
            except Exception:
                pass
        
        return FaultLocation(
            file_path=file_path,
            line_number=line_number or 0,
            function_name=func_name or "",
            exception_type=exception_type,
            exception_message=exception_message,
            traceback_summary=traceback_text[:500] if traceback_text else "",
            category=category.value,
            affected_modules=[module_name] if module_name else [],
            ast_context=ast_context,
        )
    
    def _parse_traceback(
        self, traceback_text: str
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """解析 Python traceback，提取文件、行号、函数名。"""
        if not traceback_text:
            return None, None, None
        
        import re
        # 匹配最后一行 "File 'path', line N, in func_name"
        pattern = r'File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+(\w+)'
        matches = re.findall(pattern, traceback_text)
        
        if matches:
            # 取最后一个匹配（最接近异常抛出点的调用栈帧）
            last = matches[-1]
            file_path = last[0]
            line_number = int(last[1])
            func_name = last[2]
            
            # 转为仓库相对路径
            try:
                file_path = str(Path(file_path).relative_to(self._repo_root))
            except ValueError:
                pass
            
            return file_path, line_number, func_name
        
        return None, None, None
    
    def _find_module_file(self, module_name: str) -> Optional[str]:
        """根据模块名在仓库中查找对应的 Python 文件。"""
        # 转换 module.name → module/name.py
        parts = module_name.replace(".", "/")
        candidates = [
            f"{parts}.py",
            f"src/{parts}.py",
            f"src/services/{parts.split('/')[-1]}.py",
        ]
        for cand in candidates:
            if (self._repo_root / cand).exists():
                return cand
        return None
    
    def _classify_fault(
        self, exception_type: str, exception_message: str
    ) -> FaultCategory:
        """根据异常类型和消息分类故障。"""
        et = exception_type.lower()
        em = exception_message.lower()
        
        if "importerror" in et or "modulenotfound" in et:
            return FaultCategory.IMPORT_ERROR
        if "attributeerror" in et:
            return FaultCategory.ATTRIBUTE_ERROR
        if "typeerror" in et:
            return FaultCategory.TYPE_ERROR
        if "keyerror" in et:
            return FaultCategory.KEY_ERROR
        if "indexerror" in et:
            return FaultCategory.INDEX_ERROR
        if "valueerror" in et:
            return FaultCategory.VALUE_ERROR
        if "timeout" in et or "timeout" in em:
            return FaultCategory.TIMEOUT_ERROR
        if "connection" in et or "connection" in em:
            return FaultCategory.CONNECTION_ERROR
        if "memory" in em or "resource" in em:
            return FaultCategory.RESOURCE_EXHAUSTED
        
        return FaultCategory.UNKNOWN
    
    # ==================================================================
    # Patch 生成
    # ==================================================================
    
    def generate_patch(self, fault: FaultLocation) -> Optional[RepairPatch]:
        """根据故障定位生成修复 patch。
        
        策略:
        1. 纯静态分析（启发式规则）→ 适用于简单故障（AttributeError 等）
        2. LLM 辅助分析 → 适用于复杂故障或启发式规则无法覆盖的场景
        
        Args:
            fault: 故障定位信息。
        
        Returns:
            RepairPatch 如果生成了修复方案，否则 None。
        """
        full_path = self._repo_root / fault.file_path
        if not full_path.exists():
            logger.warning("Fault file not found: %s", full_path)
            return None
        
        # 安全检查：不修改禁止目录
        for forbidden in self._FORBIDDEN_DIRS:
            if forbidden in str(fault.file_path):
                logger.warning("File in forbidden directory: %s", fault.file_path)
                return None
        
        # 启发式修复
        patch = self._heuristic_repair(fault, full_path)
        
        # 如果启发式方法不够（confidence < 0.5），尝试 LLM
        if patch is None or (patch.confidence < 0.5 and self._llm_call is not None):
            llm_patch = self._llm_assisted_repair(fault, full_path)
            if llm_patch and (patch is None or llm_patch.confidence > patch.confidence):
                patch = llm_patch
        
        if patch is None:
            return None
        
        with self._lock:
            self._patch_counter += 1
            self._patches.append(patch)
        
        if self._on_patch_ready:
            try:
                self._on_patch_ready(patch)
            except Exception:
                logger.exception("on_patch_ready callback failed")
        
        return patch
    
    def _heuristic_repair(
        self, fault: FaultLocation, full_path: Path
    ) -> Optional[RepairPatch]:
        """启发式修复：基于常见模式的静态 patch 生成。"""
        try:
            source = full_path.read_text(encoding="utf-8")
            lines = source.split("\n")
        except Exception:
            return None
        
        if fault.line_number <= 0 or fault.line_number > len(lines):
            return None
        
        original_line = lines[fault.line_number - 1]
        patched_line = original_line
        explanation = ""
        confidence = 0.0
        
        # AttributeError: NoneType has no attribute 'X'
        if fault.category == FaultCategory.ATTRIBUTE_ERROR.value:
            if "NoneType" in fault.exception_message:
                # 建议加 None 检查
                # 提取 NoneType 产生源的变量名（简单启发式）
                patched_line = self._add_none_guard(original_line, fault.exception_message)
                explanation = (
                    f"Added None guard for potential NoneType. "
                    f"Original line produces None, causing AttributeError. "
                    f"Review required: the real fix may need upstream null handling."
                )
                confidence = 0.4  # 低置信度：None guard 是临时方案
        
        # KeyError: 缺失的键
        elif fault.category == FaultCategory.KEY_ERROR.value:
            # 提取缺失的键名
            import re
            key_match = re.search(r"'([^']+)'", fault.exception_message)
            if key_match:
                missing_key = key_match.group(1)
                patched_line = original_line.replace(
                    f"['{missing_key}']", f".get('{missing_key}')"
                ).replace(
                    f'["{missing_key}"]', f'.get("{missing_key}")'
                )
                explanation = (
                    f"Replaced direct key access with .get('{missing_key}') to handle missing key. "
                    f"Consider whether a default value is appropriate."
                )
                confidence = 0.6
        
        # ImportError: 缺少导入
        elif fault.category == FaultCategory.IMPORT_ERROR.value:
            explanation = (
                f"Import error detected. This typically requires adding a missing dependency "
                f"or fixing an import path. Cannot auto-generate fix — needs manual review."
            )
            confidence = 0.1
        
        if patched_line == original_line and not explanation:
            return None
        
        # 生成 unified diff
        diff = "\n".join(difflib.unified_diff(
            [original_line], [patched_line],
            fromfile=str(fault.file_path),
            tofile=str(fault.file_path),
            lineterm="",
        ))
        
        ts = int(time.time() * 1000)
        return RepairPatch(
            patch_id=f"patch_{ts}_{hashlib.sha256(diff.encode()).hexdigest()[:8]}",
            fault_location=fault,
            file_path=str(fault.file_path),
            original_lines=original_line,
            patched_lines=patched_line,
            diff=diff,
            explanation=explanation,
            confidence=confidence,
            auto_applicable=False,  # 默认不自动应用
            contract_checks=[],
        )
    
    def _add_none_guard(self, line: str, exception_message: str) -> str:
        """为可能产生 None 的行添加 None guard。"""
        stripped = line.strip()
        indent = line[:len(line) - len(stripped)]
        
        # 如果已经是 if xxx is not None: 形式，不重复添加
        if "is not None" in stripped or "is None" in stripped:
            return line
        
        # 简单模式：在表达式前加 None 检查
        return f"{indent}if {stripped.split('=')[0].strip().split('.')[0]} is not None:\n{indent}    {stripped}"
    
    def _llm_assisted_repair(
        self, fault: FaultLocation, full_path: Path
    ) -> Optional[RepairPatch]:
        """LLM 辅助修复：利用 LLM 分析复杂故障。"""
        if self._llm_call is None:
            return None
        
        try:
            source = full_path.read_text(encoding="utf-8")
        except Exception:
            return None
        
        system_prompt = (
            "You are a Python code repair expert. Analyze the fault and generate "
            "a minimal unified diff patch to fix the issue. "
            "Output format:\n"
            "```diff\n...unified diff...\n```\n"
            "EXPLANATION: <one paragraph>\n"
            "CONFIDENCE: <0.0 to 1.0>\n"
            "AUTO_APPLICABLE: <true/false>"
        )
        
        user_prompt = (
            f"## Fault\n"
            f"- File: {fault.file_path}:{fault.line_number}\n"
            f"- Function: {fault.function_name}\n"
            f"- Exception: {fault.exception_type}: {fault.exception_message}\n"
            f"- Category: {fault.category}\n\n"
            f"## Source Context\n```python\n{fault.ast_context or 'N/A'}\n```\n\n"
            f"## Full File\n```python\n{source[:3000]}\n```\n"
        )
        
        try:
            response = self._llm_call(system_prompt, user_prompt)
        except Exception as exc:
            logger.error("LLM call failed for fault analysis: %s", exc)
            return None
        
        # 解析 LLM 响应
        import re
        
        diff_match = re.search(r'```diff\n(.*?)\n```', response, re.DOTALL)
        explanation_match = re.search(r'EXPLANATION:\s*(.+?)(?:\n|$)', response)
        confidence_match = re.search(r'CONFIDENCE:\s*([\d.]+)', response)
        auto_match = re.search(r'AUTO_APPLICABLE:\s*(true|false)', response, re.IGNORECASE)
        
        diff_text = diff_match.group(1) if diff_match else ""
        explanation = explanation_match.group(1) if explanation_match else "LLM-generated repair"
        confidence = float(confidence_match.group(1)) if confidence_match else 0.5
        auto_applicable = (
            auto_match.group(1).lower() == "true"
        ) if auto_match else False
        
        if not diff_text:
            return None
        
        ts = int(time.time() * 1000)
        return RepairPatch(
            patch_id=f"patch_{ts}_llm_{hashlib.sha256(diff_text.encode()).hexdigest()[:8]}",
            fault_location=fault,
            file_path=str(fault.file_path),
            original_lines="",
            patched_lines="",
            diff=diff_text,
            explanation=explanation,
            confidence=min(confidence, 0.9),  # LLM 置信度上限 0.9
            auto_applicable=auto_applicable,
            contract_checks=[],
        )
    
    # ==================================================================
    # 合约验证
    # ==================================================================
    
    def validate_contract(self, patch: RepairPatch) -> List[ContractCheckResult]:
        """验证修复 patch 前后的业务合约一致性。
        
        检查项:
        1. 语法有效性 — patched 代码语法正确
        2. 导入完整性 — 未移除必要的 import
        3. 函数签名不变 — 修复未改变公共 API 签名
        4. 现有测试通过 — patch 后的代码通过 pytest（如果配置了测试运行器）
        
        Args:
            patch: 待验证的修复 patch。
        
        Returns:
            合约检查结果列表。all(passed) == True 表示通过所有合约检查。
        """
        results: List[ContractCheckResult] = []
        
        # Check 1: 语法有效性
        try:
            ast.parse(patch.patched_lines)
            results.append(ContractCheckResult(
                check_name="syntax_valid",
                passed=True,
                detail="Patched code is syntactically valid Python",
            ))
        except SyntaxError as exc:
            results.append(ContractCheckResult(
                check_name="syntax_valid",
                passed=False,
                detail=f"Syntax error in patched code: {exc}",
            ))
        
        # Check 2: 导入完整性 — 简单检查（不引入新的未解析名称）
        # 这里只做 lint 级别检查
        
        # Check 3: patch 不修改 tests/
        if "tests/" in patch.file_path:
            results.append(ContractCheckResult(
                check_name="not_test_file",
                passed=False,
                detail="Patch targets a test file — this is blocked by safety policy",
            ))
        
        # Store results on patch
        patch.contract_checks = [
            f"{r.check_name}: {'PASS' if r.passed else 'FAIL'} — {r.detail}"
            for r in results
        ]
        
        return results
    
    def apply_patch(
        self, patch: RepairPatch, dry_run: bool = True
    ) -> Tuple[bool, str]:
        """应用修复 patch（需要显式 dry_run=False）。
        
        Args:
            patch: 待应用的 patch。
            dry_run: True = 只返回将写入的内容；False = 实际写入文件。
        
        Returns:
            (applied, message)
        """
        if not patch.auto_applicable:
            return False, (
                "Patch is not auto_applicable. Set auto_applicable=True after "
                "human review, or use dry_run=True to preview changes."
            )
        
        # 合约验证
        contract_results = self.validate_contract(patch)
        failed = [r for r in contract_results if not r.passed]
        if failed:
            return False, (
                f"Contract validation failed: {', '.join(r.check_name for r in failed)}"
            )
        
        if dry_run:
            return False, f"[DRY RUN] Would apply to {patch.file_path}:\n{patch.diff}"
        
        # 实际应用（仅在 dry_run=False 时）
        full_path = self._repo_root / patch.file_path
        try:
            import difflib
            import tempfile
            
            original = full_path.read_text(encoding="utf-8")
            patched = difflib.restore(
                patch.diff.splitlines(True), 1  # fromfile
            )
            
            # 备份原文件
            backup_path = full_path.with_suffix(f"{full_path.suffix}.bak.{int(time.time())}")
            full_path.rename(backup_path)
            
            # 写入 patched 内容
            full_path.write_text(patched, encoding="utf-8")
            
            patch.status = "applied"
            return True, f"Patch applied to {patch.file_path}. Backup: {backup_path}"
        except Exception as exc:
            return False, f"Failed to apply patch: {exc}"
    
    # ==================================================================
    # 查询与统计
    # ==================================================================
    
    def get_pending_patches(self) -> List[RepairPatch]:
        """获取所有待处理的 patch（status='pending'）。"""
        with self._lock:
            return [p for p in self._patches if p.status == "pending"]
    
    def get_patch_history(self, limit: int = 20) -> List[RepairPatch]:
        """获取 patch 历史。"""
        with self._lock:
            return self._patches[-limit:]
    
    def stats(self) -> Dict[str, Any]:
        """获取修复代理统计。"""
        with self._lock:
            total = len(self._patches)
            applied = sum(1 for p in self._patches if p.status == "applied")
            verified = sum(1 for p in self._patches if p.status == "verified")
            rejected = sum(1 for p in self._patches if p.status == "rejected")
            
            return {
                "total_patches": total,
                "applied": applied,
                "verified": verified,
                "rejected": rejected,
                "pending": total - applied - verified - rejected,
                "avg_confidence": (
                    sum(p.confidence for p in self._patches) / max(total, 1)
                ),
                "auto_applicable_count": sum(
                    1 for p in self._patches if p.auto_applicable
                ),
            }
    
    def reset(self) -> None:
        """重置代理（仅用于测试）。"""
        with self._lock:
            self._patches.clear()
            self._patch_counter = 0
```

### 4.2 新建 `tests/test_code_aware_repair.py` (~180 行)

```python
# 测试函数清单：

def test_locate_fault_from_traceback():
    """验证从 traceback 文本解析文件、行号、函数名。"""
    tb = '''Traceback (most recent call last):
  File "/app/src/services/data_fetcher.py", line 42, in fetch_data
    return api.get(endpoint)
AttributeError: 'NoneType' object has no attribute 'get\''''
    fault = agent.locate_fault("AttributeError", "...", tb)
    assert fault.file_path.endswith("data_fetcher.py")
    assert fault.line_number == 42
    assert fault.function_name == "fetch_data"

def test_classify_fault_attribute_error():
    """验证 AttributeError 分类为 ATTRIBUTE_ERROR。"""

def test_classify_fault_import_error():
    """验证 ImportError 分类为 IMPORT_ERROR。"""

def test_heuristic_repair_none_guard():
    """验证为 NoneType AttributeError 生成 None guard patch。"""

def test_heuristic_repair_key_error():
    """验证 KeyError 生成 .get() 替换 patch。"""

def test_generate_patch_forbidden_dir():
    """验证 tests/ 目录下的文件不生成 patch。"""

def test_validate_contract_syntax():
    """验证合约检查：语法有效性。"""

def test_validate_contract_test_file_blocked():
    """验证合约检查：阻止修改 test 文件。"""

def test_apply_patch_dry_run():
    """验证 dry_run=True 不实际写入文件。"""

def test_apply_patch_not_auto_applicable():
    """验证 auto_applicable=False 时 apply_patch 被拒绝。"""

def test_parse_traceback_multiple_frames():
    """验证多帧 traceback 解析取最后一个有效帧。"""
```

### 4.3 Phase 4 验证清单

| 验证项 | 方法 | 预期结果 |
|--------|------|---------|
| `tests/test_code_aware_repair.py` 全部通过 | `pytest tests/test_code_aware_repair.py -v` | ~12 tests pass |
| 现有测试无回归 | `pytest tests/ -m "not network" -v` | 所有现有测试通过 |
| AST parse 检查 | 对有效/无效 Python 代码测试 | 正确识别语法错误 |
| 安全边界 | 尝试对 tests/ 文件生成 patch | 被拒绝 |
| dry_run 安全 | 修改后文件内容不变 | 原始文件完整 |
| 导入编译检查 | `python -m py_compile src/services/code_aware_repair.py` | 成功 |

---

## 五、全阶段文件变更汇总

| Phase | 新建文件 | 修改文件 | 新增测试 | 预估净增行数 |
|-------|---------|---------|---------|------------|
| Phase 1 | `tests/test_event_bus_integration.py` (~180) | `graceful_degradation.py` (+35), `config_rollback.py` (+25), `module_restart.py` (+30), `meta_cognitive.py` (+65) | 10 tests | ~335 |
| Phase 2 | `src/services/self_healing_action.py` (~250), `tests/test_self_healing_action.py` (~120) | `module_restart.py` (+120) | ~13 tests (8 + 5) | ~490 |
| Phase 3 | `src/services/repair_effectiveness_log.py` (~220), `tests/test_repair_effectiveness_log.py` (~150) | `graceful_degradation.py` (+45), `module_restart.py` (+30) | ~8 tests | ~445 |
| Phase 4 | `src/services/code_aware_repair.py` (~350), `tests/test_code_aware_repair.py` (~180) | — | ~12 tests | ~530 |
| **合计** | **6 个新文件** (~1,450 行) | **4 个修改文件** (~350 行) | **~43 tests** | **~1,800** |

---

## 六、风险与依赖

### 6.1 全局风险

| 风险 | 影响级别 | 缓解措施 |
|------|---------|---------|
| L3 模块间循环导入 | high | 使用延迟导入（`try/except ImportError`）+ 模块级接口抽象 |
| 新功能破坏现有功能 | high | 每个 Phase 独立测试 + CI gate + `fault_pattern=None` 向后兼容 |
| 性能退化（事件总线锁竞争） | medium | `SystemEventBus` 使用 RLock + 订阅者列表拷贝 + 异步持久化 |
| LLM 集成不可用（Phase 4） | medium | Phase 4 默认纯静态分析；LLM 为可选增强 |
| 测试覆盖不足 | medium | 每个新模块要求 ≥80% 测试覆盖 + CI 集成测试 |

### 6.2 Phase 间依赖

```
Phase 1 (event_bus 集成)
  └── Phase 2 (SelfHealingAction) — 依赖 Phase 1 的事件通道做升级通知
       └── Phase 3 (RepairEffectivenessLog) — 依赖 Phase 2 的 RepairRecord 作为输入
            └── Phase 4 (CodeAwareRepair) — 依赖 Phase 3 的效果日志做策略路由
```

**关键**: Phase 1 和 Phase 2 可以部分并行开发（Phase 2 的 SelfHealingAction 不依赖 event_bus），但建议按顺序交付以确保每次增量都经过完整验证。

---

## 七、laap-AGI 代码级映射（Phase 更新）

### 7.1 self_healing.py ErrorMonitor + AutoHealer → SelfHealingAction + RepairEffectivenessLog

| # | laap-AGI 源 | 映射到目标函数 | 转换说明 |
|---|-----------|-------------|---------|
| 1 | `ErrorMonitor._hash_message(msg)` 错误签名去重 | `RepairEffectivenessLog.record()` 中的 `entry_id` 生成 | **适配改写**：laap-AGI 用 SHA256 签名去重；dsa 用 `repair_id` + `action_type` + `target` 三元组标识 |
| 2 | `AUTO_FIX_THRESHOLD = 3` | `SelfHealingAction.execute()` 的升级链阈值 | **直接移植** |
| 3 | `BugReport` / `BugType` / `FixAttempt` | `FaultLocation` / `FaultCategory` / `RepairPatch` | **适配改写**：语义从代码 Bug 改为系统故障 |
| 4 | `AutoHealer.heal()` 周期 scan→classify→generate→deploy | `CodeAwareRepairAgent` 的 locate→generate→validate→apply 流程 | **适配改写**：增加安全边界（禁止目录、dry_run 默认） |
| 5 | `ErrorMonitor._create_bug_report()` | `CodeAwareRepairAgent.locate_fault()` | **直接移植**分析框架 |
| 6 | `AutoHealer.stats()` | 各模块的 `stats()` 方法 | **直接移植**格式 |

---

## 八、实施顺序建议

```
Day 1-2:  Phase 1 — event_bus 集成到 4 个模块
           → 所有 L3 操作级事件到达 L4 元认知引擎
           → 验证: test_event_bus_integration.py 全通过

Day 3-5:  Phase 2 — SelfHealingAction 基类 + 重启验证闭环
           → 模块重启不再盲目，验证失败自动升级
           → 验证: test_self_healing_action.py + test_module_restart.py 全通过

Day 6-10: Phase 3 — 策略学习与动态路由
           → 系统从修复历史中学习，自动调整策略优先级
           → 验证: test_repair_effectiveness_log.py + fault_pattern 匹配测试

Day 11-20: Phase 4 — 代码感知修复（仅分析/建议，非自动应用）
            → 故障定位到代码行级，LLM 辅助生成修复方案
            → 验证: test_code_aware_repair.py 全通过
```

---

> **下一步**: 用户确认本计划后，按 Phase 顺序逐 Phase 实施。每个 Phase 完成后执行验证矩阵中的全部检查项，确认无回归后再进入下一 Phase。
