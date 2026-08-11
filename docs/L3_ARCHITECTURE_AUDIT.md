# L3 架构审计：操作级守护 vs 架构级自修复

**审计日期**: 2026-08-11
**审计范围**: `src/services/module_restart.py`, `src/services/config_rollback.py`, `src/services/graceful_degradation.py`
**审计框架**: agent-architecture-audit (12-layer agent stack)
**审计目标**: 判断当前 L3 三个模块是"操作级守护"还是"架构级自修复"

---

## Executive Verdict

| 项目 | 判定 |
|---|---|
| **Overall Health** | `operational_guardian` — 非架构级自修复 |
| **Primary Failure Mode** | 操作守卫生（Operational Guardian）: 所有三个模块运行在"监听信号 → 触发预设动作"的操作闭环中，不具备代码感知、AST 分析、自动修复生成或自修改修复管道能力 |
| **Most Urgent Gap** | L3 模块与 L4 MetaCognitiveEngine 之间缺少双向反馈链路：L3 无法将降级/回滚/重启事件作为元认知的输入；L4 的反省结论无法驱动 L3 的修复策略调整 |

**一句话诊断**: 当前 L3 是三个设计良好的**操作级守护进程**（Operational Daemons），用预设规则响应运行时信号。它们离"架构级自修复"差的核心能力是：**代码感知的故障源定位、自动修复生成（patch generation）、修复前后合约验证（verification），以及从 L4 元认知接收策略级调整指令的闭环**。

---

## 12-Layer 审计逐层对照

### Layer 1: System Prompt — ❌ 不适用
L3 模块不涉及 LLM 调用，此层不适用。

### Layer 2-5: Session/Memory/Distillation/Recall — ❌ 不适用
L3 模块是纯 Python 运行时组件，无 LLM 上下文管理。

### Layer 6: Tool Selection — ⚠️ 无动态工具路由

**现状**: 三个模块的功能路由是硬编码的。例如 `GracefulDegradationEngine.tick()` 走固定的 evaluate → hysteresis → apply_rules 路径，没有动态工具选择器。

**差距**: 架构级自修复需要一个**修复策略路由器**（Repair Strategy Router）：根据故障类型、严重程度和历史修复成功率，动态选择修复策略（重启 vs 回滚 vs 降级 vs 修复 patch vs 通知人工）。

**严重度**: `medium` — 当前的单一路径对已知场景够用，但无法处理未见过的故障组合。

### Layer 7: Tool Execution — ⚠️ 操作原子但无验证闭环

**现状**:
- `ModuleAutoRestarter.restart_module()` 执行重启（进程/线程/方法），有冷却期和限流
- `ConfigAutoRollback.execute_rollback()` 原子写入 + `_verify_rollback()` 验证
- `GracefulDegradationEngine._apply_rules()` 修改内部状态

**差距**: `ModuleAutoRestarter` 重启后**不验证重启是否解决了问题**。它只是重启，然后等待下一次健康检查。架构级自修复应该：重启 → 验证 → 若无效则升级修复策略。

`ConfigAutoRollback` 是唯一有验证闭环的模块（`_verify_rollback`），这是正确的方向。

**严重度**: `high` — 盲重启（blind restart）是最常见的运维反模式。

### Layer 8: Tool Interpretation — ⚠️ 信号解释单一

**现状**: 健康信号 → 阈值比较 → 预设动作。没有信号组合语义分析。

**差距**: 架构级自修复应该能识别"错误率上升 + 数据源延迟同时增加"的组合模式对应"网络问题"而非"代码 bug"，从而选择不同的修复路径。当前所有信号都走同一个分支逻辑。

**严重度**: `medium`

### Layer 9: Answer Shaping — ❌ 不适用

### Layer 10: Platform Rendering — ❌ 不适用

### Layer 11: Hidden Repair Loops — ❌ 当前无，但路径缺失

**现状**: 没有隐藏的 LLM 重试/修复循环。

**差距**: 这正是问题所在——L3 没有被设计为"发现 bug → 生成修复 → 验证 → 部署"的闭环。一旦某个模块的预设动作不足以解决问题，系统就停止了。

**严重度**: `critical` — 这是 L3 从操作级升级到架构级的核心缺失。

### Layer 12: Persistence — ✅ 设计合理

三个模块都有持久化机制：
- L3-1: JSON 文件 + 重启历史
- L3-2: 三层备份（内存 → 文件 → Git blob）+ 索引持久化
- L3-3: 事件历史 deque + stats() 接口

这是三个模块中最接近生产级的部分。

---

## 操作级 vs 架构级：核心差距表

| 能力维度 | 当前 L3 (操作级) | 架构级自修复 (目标) | 差距 |
|---|---|---|---|
| **故障检测** | 健康信号 + 阈值 | 健康信号 + 阈值 + 异常模式识别 + 因果推断 | 中等 |
| **根因定位** | ❌ 无 | 基于调用栈/日志/依赖图的根因分析 | **大** |
| **修复策略选择** | 硬编码: 重启/回滚/降级 | 动态路由器: 根据故障类型 + 历史成功率选择策略 | 中等 |
| **代码修复生成** | ❌ 无 | AST 级 diff 生成 + patch 应用 | **大** |
| **修复验证** | 仅 L3-2 有 | 每个修复动作后必须验证 | **大** |
| **策略学习** | ❌ 无 | 从修复历史中学习，调整策略优先级 | **大** |
| **L4 联动** | ❌ 无 | L3 事件 → L4 认知输入; L4 反省 → L3 策略调整 | **大** |
| **合约验证** | ❌ 无 | 修复前后业务合约的一致性检查 | **大** |

---

## Severity-Ranked Findings

### Finding #1 [critical] L3→L4 双向反馈链路缺失

**机制**: L3 模块产生降级事件、回滚事件、重启事件，但这些事件**仅记录到日志和内存历史**，没有推送至 `MetaCognitiveEngine` 作为认知输入。反过来，L4 的 `force_reflection()` 产生的反省结论也**没有任何机制可以驱动 L3 的策略调整**。

**Source Layer**: Layer 11 (Hidden Repair Loops) — 缺少的不是隐藏循环，而是显式的闭环连接。

**Root Cause**: L3 和 L4 是独立开发的（按任务规划分阶段），但缺少一个集成层（Integration Bus / Event Bus）。

**Evidence**:
- `graceful_degradation.py:439`: `logger.warning("Degradation: %s → %s, ...")` — 只记录日志
- `config_rollback.py`: `execute_rollback()` 返回 `RollbackResult`，但调用方没有转发给 L4
- `module_restart.py`: 重启记录仅写入 JSON 文件
- `meta_cognitive.py`: `start_episode()` 接受 `stock_code`，但不接受 `DegradationEvent` 或 `RollbackResult`

**Confidence**: 0.95

**Recommended Fix**: 引入 `SystemEventBus` 作为 L3/L4 之间的消息通道：
```python
# src/services/event_bus.py
class SystemEventBus:
    def publish(self, event: SystemEvent) -> None: ...
    def subscribe(self, event_type: type, handler: Callable) -> None: ...

# L3 发布
event_bus.publish(DegradationOccurred(from_level, to_level, affected_caps))

# L4 订阅
event_bus.subscribe(DegradationOccurred, meta_engine.on_system_event)
```

### Finding #2 [high] ModuleAutoRestarter 盲重启（无验证闭环）

**机制**: `restart_module()` 执行重启后立即返回，不验证重启是否恢复了模块健康。

**Source Layer**: Layer 7 (Tool Execution)

**Root Cause**: 设计上缺少 post-restart health verification step。

**Evidence**:
- `module_restart.py`: `_restart_process()`, `_restart_thread()`, `_restart_method()` 都是 fire-and-forget
- 没有 `_verify_restart()` 方法
- 健康状态依赖外部 `HealthCheckDaemon` 的下一次 tick 来更新——可能是几秒后

**Confidence**: 0.90

**Recommended Fix**: 在重启后增加验证步骤：
```python
def restart_module(self, module_name: str) -> RestartRecord:
    record = self._do_restart(module_name)
    record.verified = self._verify_restart(module_name, timeout_seconds=5)
    if not record.verified:
        record.escalation = self._escalate_repair_strategy(module_name, record)
    return record
```

### Finding #3 [high] 降级引擎缺少修复策略路由器

**机制**: 当压力升级时，`_apply_rules()` 总是执行相同的能力裁剪（disable/throttle/defer），不根据故障类型动态选择。

**Source Layer**: Layer 6 (Tool Selection)

**Root Cause**: `CapabilityRule` 是静态注册的，没有与故障模式关联。

**Evidence**:
- `graceful_degradation.py:185`: `_register_default_rules()` — 硬编码规则
- `graceful_degradation.py:447`: `_apply_rules()` — 纯静态匹配
- 一个 `error_rate` 升高可能是网络问题（应 throttle 网络调用），也可能是 CPU 问题（应 defer 非关键计算）。当前规则不区分两者。

**Confidence**: 0.80

**Recommended Fix**: 扩展 `CapabilityRule` 增加 `fault_pattern` 字段，在 `_apply_rules()` 中匹配当前故障模式后才激活对应规则。

### Finding #4 [medium] 缺少修复历史学习机制

**机制**: 所有三个模块都有事件记录，但不分析历史事件以优化未来的修复策略。例如：如果某模块在过去 10 次重启中有 8 次在重启后 5 分钟内再次失败，系统应该停止盲重启并通知人工。

**Source Layer**: Layer 12 (Persistence) — 有持久化但无学习

**Root Cause**: 持久化数据只用于审计/展示，不用于策略优化。

**Evidence**:
- `module_restart.py`: 重启历史仅用于 `get_restart_summary()`
- `graceful_degradation.py:181`: 事件历史仅用于 `get_degradation_summary()` 和 `get_event_history()`
- `config_rollback.py`: 快照索引仅用于查找回滚源

**Confidence**: 0.85

**Recommended Fix**: 在 L3 模块中增加 `_analyze_effectiveness()` 方法，周期性评估修复动作的实际效果，并将结果写入一个 `RepairEffectivenessLog`。这个日志同时作为 L4 元认知的输入。

### Finding #5 [medium] ConfigAutoRollback 是唯一接近架构级自修复的模块

**机制**: L3-2 有三层备份、5 维度回归检测、原子写入、回滚后验证——这已经包含了"检测 → 修复 → 验证"的完整闭环。它是三个 L3 模块中设计最成熟的。

**Source Layer**: Layer 7 (Tool Execution) — 正向案例

**Evidence**:
- `config_rollback.py`: `execute_rollback()` → `_verify_rollback()` → 返回 `RollbackResult(verified=True/False)`
- `config_rollback.py`: `auto_rollback_if_needed()` 组合了检测+回滚
- `config_rollback.py`: `detect_regression()` 覆盖 5 个维度

**Confidence**: 0.95

**Recommended Fix**: 将 L3-2 的"检测 → 修复 → 验证"模式抽象为 `SelfHealingAction` 基类，L3-1 和 L3-3 复用该模式。

---

## Ordered Fix Plan

### Phase 1: 建立 L3/L4 双向反馈链路 (1-2 工作日)

| Order | Goal | Why Now | Expected Effect |
|---|---|---|---|
| 1 | 实现 `SystemEventBus` — L3 事件发布 / L4 订阅 | 当前 L3 和 L4 是隔离的信息孤岛 | L3 每个操作级事件都成为 L4 的认知输入 |
| 2 | L4 的 `force_reflection()` 结论回写 L3 策略参数 | L4 的反省目前是"只读"的 | L3 的行为可以随系统自我认知动态调整 |

### Phase 2: 补齐修复验证闭环 (2-3 工作日)

| Order | Goal | Why Now | Expected Effect |
|---|---|---|---|
| 3 | `ModuleAutoRestarter` 增加 post-restart health verification | 盲重启是危险的反模式 | 重启失败时自动升级到更强的修复策略 |
| 4 | 将 L3-2 的"检测 → 修复 → 验证"模式抽象为 `SelfHealingAction` 基类 | L3-2 是目前唯一有完整闭环的模块 | L3-1 和 L3-3 共享同样的验证模式 |

### Phase 3: 策略学习与路由 (3-5 工作日)

| Order | Goal | Why Now | Expected Effect |
|---|---|---|---|
| 5 | 实现 `RepairEffectivenessLog` + 历史分析 | 持久化的历史数据被浪费 | 失败率高的修复策略自动降级，被更有效的替代 |
| 6 | `GracefulDegradationEngine` 增加故障模式匹配 | 当前不区分故障根因 | 同一压力等级下可以执行不同的能力裁剪策略 |

### Phase 4: 代码感知修复 (需要 LLM 集成，6-10 工作日)

| Order | Goal | Why Now | Expected Effect |
|---|---|---|---|
| 7 | 实现 `CodeAwareRepairAgent` — AST 级故障分析 + patch 生成 | 这是从"操作级"到"架构级"的质变 | 系统能自动修复代码缺陷而不仅仅是重启/降级 |
| 8 | 修复前后合约验证 (contract validation) | 自动 patch 可能引入新 bug | 修复后的代码必须通过现有测试套件验证 |

---

## 结论

当前 L3 三个模块是**设计良好的操作级守护进程**，它们在各自领域（进程管理、配置管理、能力裁剪）提供了有效的故障响应能力。但它们不是架构级自修复，因为：

1. **没有代码感知能力** — 无法定位和修复代码缺陷
2. **没有修复策略学习** — 不根据历史效果调整策略
3. **没有与 L4 元认知联动** — L3 和 L4 是两个平行宇宙

**核心判断**: 不需要推翻重做。当前 L3 是一个坚实的基础。升级路径是：先建立 L3↔L4 的双向反馈链路（Phase 1），再补齐修复验证闭环（Phase 2），再增加策略学习（Phase 3），最后引入代码感知修复（Phase 4）。每完成一个 Phase，系统离"架构级自修复"就更近一步。

**数据支撑**:
- 全部 76 个测试通过 (25 graceful_degradation + 27 config_rollback + 22 meta_cognitive + 2 module_restart)
- 修复了 1 个根本性架构 bug (PressureLevel enum comparison)
- 识别了 5 个 severity-ranked findings (1 critical, 2 high, 2 medium)
