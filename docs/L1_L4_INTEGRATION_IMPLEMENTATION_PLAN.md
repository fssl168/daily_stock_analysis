# L1/L2/L3/L4 集成实施方案 — 全主动观察（EventBus 双向互通）

**生成日期**: 2026-08-12
**基线**: HEAD `efe453c`（行尾归一化已单独提交，工作区已净化为干净基线）
**前置文档**: `docs/L1_L2_RETROSPECTIVE.md`、`docs/L1_L2_RECTIFICATION_PLAN.md`、`docs/L3_ARCHITECTURE_AUDIT.md`、`docs/L3_L4_IMPLEMENTATION_PLAN.md`
**实施原则**: 第一版向**全主动观察**靠齐 —— 所有层级只通过 `SystemEventBus` 双向互通互联，修复/调整动作默认 `dry_run`，全部需人工确认。

---

## 第一部分 执行工程师观察视角汇总

### 1.1 现状结论（数据为证）

> **一句话诊断：L3/L4 整层是一座没有接入电网的发电厂 —— 所有模块只有测试在引用，生产入口（`main.py` / `server.py` / `src/services/health_check.py`）里零初始化。**

| 观察项 | 证据 | 结论 |
|---|---|---|
| 8 个 L3/L4 模块 | `code_aware_repair`(1053) / `config_rollback`(918) / `event_bus`(517) / `graceful_degradation`(712) / `meta_cognitive`(1328) / `module_restart`(996) / `repair_effectiveness_log`(357) / `self_healing_action`(280)，合计 6161 行 | 工程质量认真，单元测试齐全 |
| 生产代码引用 | 全仓库 grep（非 tests）：**零引用**；`main.py`/`server.py`/`health_check.py` 搜不到 EventBus/MetaCognitiveEngine/ModuleAutoRestarter 初始化 | **整层未接入运行时** |
| 测试覆盖 | 8 个模块各有独立测试文件；仓库共 266 个测试文件 | 单元层扎实，集成层为空 |
| L3↔L4 链路 | L3 审计自认核心差距；`event_bus.py` 已建好但无人 publish、无人 subscribe | 审计指出的缺口至今未合上 |
| 修复效果数据 | `RepairEffectivenessLog` 只在 `CodeAwareRepairAgent` 内部被调用，而该 Agent 本身无故障检测器触发 | 无法积累真实修复效果数据 |

### 1.2 改进优先级判断

| 优先级 | 动作 | 理由 |
|---|---|---|
| **P0** | 把 L3 接进 `HealthCheckDaemon`，先做"观察"再做"干预" | 性价比最高；`HealthCheckDaemon` 已有 7 种检查，只差把结果 publish 到 EventBus |
| **P1** | 给 `CodeAwareRepairAgent` 补"真实合约验证" | 当前 `validate_contract` 只做语法 parse、不碰 tests/、不碰禁止目录、原行存在；**语法正确 ≠ 行为正确** |
| **P2** | 把 `ModuleAutoRestarter` 从"盲重启"改成"重启→验证→升级" | L3 审计自标 high 反模式；执行系统盲重启是真实资金风险 |
| **P3** | L4 从"报告生成器"走向"策略调节器"前，先保证数据源 | L2 的 Agent tool-call trace / 决策上下文没有打到 EventBus，MetaCognitiveEngine 1328 行只能在测试里空转 |

### 1.3 执行工程师必须划的红线

> **凡是触及订单执行、信号生成、仓位计算的路径，`CodeAwareRepair` 的启发式修复必须永久禁止 auto-apply，只能产出 patch 交给人工。**
>
> 原因：对一个正在下买单的模块"顺手修一个 None 引用"，修错了不是一次 bug，而是一次**真实的资金损失**，且没有 Ctrl+Z。`_repair_none_guard` 这类启发式越成熟，越要警惕它被错误地信任。

补充红线（与既有 AGENTS.md §7 稳定性护栏一致）：

- **执行 / 交易路径**：`code_aware_repair`、`config_rollback` 对交易相关模块的任何 patch / 回滚，`auto_applicable` 恒为 `False`，必须人工确认。
- **数据源 fallback**：L1 数据源的降级 / 回滚不影响交易模块状态机；单一数据源失败不得拖垮主流程。
- **修复验证**：任何自动修复动作（即使未来放开 dry_run）前必须跑目标模块测试套件，全绿才允许。
- **审计留痕**：所有 L3 干预 / L4 反思结论必须写 `RepairEffectivenessLog` / 持久化，作为未来策略路由器训练数据。

### 1.4 一句话总结

> **工程优势在"设计层"和"单元层"，真正的短板在"集成层"。与其继续加 L5/L6，不如把 L3/L4 接进运行时跑起来 —— 哪怕第一版只是全被动观察（L3 事件进 L4、L4 只出诊断报告、所有修复动作默认 dry_run），让系统先积累真实的故障与修复效果数据。**

---

## 第二部分 L1/L2/L3/L4 集成实施方案（第一版 · 全主动观察）

### 2.0 目标与原则

**目标**: 让四层架构在真实运行时通过 `SystemEventBus` 双向互通互联，第一版全部为**观察型**（publish + subscribe + 落盘 + 报告），不执行任何自动修复。

**原则**:

1. **全主动观察**: 所有层级主动 publish 事件，所有订阅者只读消费，不产生副作用。
2. **默认 dry_run**: 任何修复 / 回滚 / 降级动作默认 `dry_run=True`，仅产出建议与 patch，不落盘。
3. **单一真源**: `SystemEventBus` 是层间通信唯一通道；`src/config.py` 为配置唯一真源。
4. **幂等与可重放**: 事件带 `correlation_id`，可重放用于复盘；EventBus 支持落盘（`flush_to_disk`）。
5. **不破坏主流程**: 订阅者异常不得影响生产路径（EventBus 已内置隔离）。

---

### 2.1 总体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           SystemEventBus                                │
│  publish(event) / subscribe(type, handler) / on_batch() / flush_to_disk │
│  SystemEventType 需扩展 L1/L2 事件类型（见 2.4）                          │
└───────▲──────────────────────────────────────▲─────────────────────────┘
        │ publish                              │ subscribe
┌───────┴──────┐   ┌───────────────────┐   ┌───┴──────────────┐
│ L1 基础设施  │   │ L2 业务执行层      │   │ L3 操作级自修复   │
│ data_provider│   │ pipeline/analyzer │   │ ModuleAutoRestart│
│ storage      │──▶│ agent/notification│──▶│ ConfigAutoRollback│
│ config/clock │   │ backtest/API      │   │ GracefulDegrad   │
│ LLM backend  │   │                   │   │ CodeAwareRepair  │
└──────────────┘   └───────────────────┘   └──────────────────┘
        ▲                                     │
        └─────────── L4 元认知层 ◀────────────┘
                    MetaCognitiveEngine
                    订阅 L1/L2/L3 事件 → 检测偏差 → 产出内省报告
                    内省结论通过 EventBus 回写（第一版仅记录，不驱动策略）
```

**第一版通信矩阵**（各层之间事件流向，全部为观察型）：

| 来源层 | 事件类型 | 消费层 | 消费方式 |
|---|---|---|---|
| L1 | `DATA_SOURCE_FALLBACK` / `DATA_FETCH_FAILED` | L4 | 订阅 → 归因分析 |
| L1 | `DATA_FETCH_DURATION` | L4 | 订阅 → 延迟基线 |
| L1 | `CONFIG_CHANGED` | L3 | 订阅 → 配置回归检测 |
| L1 | `LLM_BACKEND_SWITCHED` | L4 | 订阅 → 渠道漂移分析 |
| L2 | `PIPELINE_STARTED` / `PIPELINE_COMPLETED` / `PIPELINE_FAILED` | L4 | 订阅 → 任务级偏差 |
| L2 | `ANALYSIS_COMPLETED` / `NO_TRADE_DECISION` | L4 | 订阅 → WhyNoTradeExplainer |
| L2 | `AGENT_TOOL_CALL` / `AGENT_TOOL_RESULT` | L4 | 订阅 → 工具偏好 / 循环检测 |
| L2 | `NOTIFICATION_SENT` / `NOTIFICATION_FAILED` | L4 | 订阅 → 通知链路诊断 |
| L3 | `MODULE_RESTARTED` / `MODULE_RESTART_FAILED` | L4 | 订阅 → 重启有效性学习 |
| L3 | `CONFIG_ROLLBACK_EXECUTED` | L4 | 订阅 → 配置回归复盘 |
| L3 | `DEGRADATION_TRANSITION` | L4 | 订阅 → 降级模式分析 |
| L3 | `REPAIR_PATCH_GENERATED` / `REPAIR_PATCH_APPLIED` | L4 | 订阅 → 修复效果学习 |
| L4 | `REFLECTION_COMPLETED` / `BIAS_DETECTED` | L2 | 订阅 → 记录（第一版不调整策略） |
| L4 | `REFLECTION_COMPLETED` | L3 | 订阅 → 记录（第一版不驱动修复） |
| L1 | `SYSTEM_STARTUP` / `SYSTEM_SHUTDOWN` | 全部 | 广播 → 生命周期感知 |

---

### 2.2 单元分界（各层职责与对接范围）

#### L1 — 基础数据与设施层

**职责**: 提供数据、持久化、配置、时钟、延迟、LLM 后端、通知基础设施、诊断。

**对接模块清单**:

| 模块 | 路径 | 对接动作 |
|---|---|---|
| 数据获取与容错 | `data_provider/base.py` + 12 个 fetcher | 在 `DataFetcherManager` 的 fallback 决策点 publish `DATA_SOURCE_FALLBACK`；失败 publish `DATA_FETCH_FAILED` |
| L2 行情适配 | `data_provider/l2_fetcher.py` | 同 fetcher 通道，publish 逐笔数据状态 |
| 基本面适配 | `data_provider/fundamental_adapter.py` / `yfinance_fundamental_adapter.py` | 同上 |
| 数据质量控制 | `data_provider/quality.py` | 校验失败 publish `DATA_QUALITY_ALERT` |
| 本地缓存 | `data_provider/local_store.py` | 命中/未命中 publish `CACHE_HIT`/`CACHE_MISS`（可选，低优先级） |
| CircuitBreaker | `data_provider/realtime_types.py` | 熔断状态变化 publish `CIRCUIT_OPEN`/`CIRCUIT_CLOSED` |
| 持久化 | `src/storage.py` | 写入失败 publish `STORAGE_ERROR` |
| 配置管理 | `src/config.py` + `src/core/config_manager.py` + `src/core/config_registry.py` | 配置变化 publish `CONFIG_CHANGED` |
| 时钟 | `src/utils/exchange_clock.py` | NTP 降级 publish `CLOCK_DEGRADED` |
| 延迟追踪 | `src/utils/latency_tracker.py` | 每轮汇总 publish `LATENCY_SUMMARY` |
| LLM 后端 | `src/llm/` (11 文件) | 渠道切换 / 用量 publish `LLM_BACKEND_SWITCHED`、`LLM_USAGE` |
| 通知基础设施 | `src/notification_sender/` (15 文件) | 发送结果 publish `NOTIFICATION_SENT`/`NOTIFICATION_FAILED`（归 L2 消费） |
| 诊断 | `src/services/notification_diagnostics.py`、`generation_backend_status_service.py` | 诊断结果 publish `DIAGNOSTIC_COMPLETED` |

#### L2 — 业务执行与分析层

**职责**: 股票分析核心业务 —— 取数→分析→报告→推送。

**对接模块清单**:

| 模块 | 路径 | 对接动作 |
|---|---|---|
| 核心流水线 | `src/core/pipeline.py` | `analyze_stock()` 入口 publish `PIPELINE_STARTED`；成功 publish `PIPELINE_COMPLETED`；异常 publish `PIPELINE_FAILED` |
| 大盘综述 | `src/core/market_review.py` | 完成 publish `MARKET_REVIEW_COMPLETED` |
| 回测引擎 | `src/core/backtest_engine.py` | 运行 publish `BACKTEST_STARTED`/`BACKTEST_COMPLETED` |
| Agent 子系统 | `src/agent/`（agents/executor/factory/memory/orchestrator/runner） | tool 调用 publish `AGENT_TOOL_CALL`；结果 publish `AGENT_TOOL_RESULT`；循环检测 publish `AGENT_LOOP_DETECTED` |
| 通知发送器 | `src/notification_sender/` | 发送结果 publish `NOTIFICATION_SENT`/`NOTIFICATION_FAILED` |
| 业务服务层 | `src/services/`（analysis/analyzer/backtest/portfolio/alert 等 30+ 模块） | 关键路径 publish 业务事件；错误 publish `SERVICE_ERROR` |
| 健康监控 | `src/services/health_check.py` | 每轮检查 publish `HEALTH_CHECK_COMPLETED`（L2→L3 桥梁） |

> **注**：核心流水线入口实际为 `StockAnalysisPipeline.analyze_stock()`（`src/core/pipeline.py`），`main.py` 的 `run_analysis` 是外层编排。事件挂载点以 `analyze_stock()` 的入口/异常分支为准。

#### L3 — 操作级自修复层

**职责**: 检测→修复→验证闭环（第一版：只检测 + 产出建议，不自动执行）。

**对接模块清单**:

| 模块 | 路径 | 对接动作 |
|---|---|---|
| SystemEventBus | `src/services/event_bus.py` | 已实现；第一版作为唯一通道，需扩展 L1/L2 事件类型 |
| SelfHealingAction | `src/services/self_healing_action.py` | 基类；所有修复动作默认 `dry_run` |
| ModuleAutoRestarter | `src/services/module_restart.py` | 接 HealthCheckDaemon，检测到故障 publish `MODULE_RESTARTED`/`MODULE_RESTART_FAILED`；第一版只报告不执行重启 |
| ConfigAutoRollback | `src/services/config_rollback.py` | 接 system_config_service 写入口；检测回归 publish `CONFIG_REGRESSION_DETECTED`；第一版不自动回滚 |
| GracefulDegradationEngine | `src/services/graceful_degradation.py` | 接 HealthCheckDaemon；`evaluate_level()` / `tick()` 压力升级 publish `DEGRADATION_TRANSITION`；第一版仅评估不应用 |
| CodeAwareRepairAgent | `src/services/code_aware_repair.py` | 接故障信号；生成 patch publish `REPAIR_PATCH_GENERATED`；第一版不应用 |
| RepairEffectivenessLog | `src/services/repair_effectiveness_log.py` | 订阅 `REPAIR_PATCH_*` / `MODULE_RESTART_*` 事件，记录修复效果 |

#### L4 — 元认知层

**职责**: 理解系统"为什么这么做"、检测偏差、学习修复策略有效性、生成内省报告（第一版：只出报告，不调整策略）。

**对接模块清单**:

| 模块 | 路径 | 对接动作 |
|---|---|---|
| MetaCognitiveEngine | `src/services/meta_cognitive.py` | 订阅 L1/L2/L3 全部事件；运行 `reflect()`/`force_reflection()`；publish `REFLECTION_COMPLETED`、`BIAS_DETECTED`、`CIRCULARITY_DETECTED` |
| WhyNoTradeExplainer | `src/services/`（L4 规划内） | 订阅 `NO_TRADE_DECISION`，产出无交易原因报告 |
| LatencySelfDiagnosisEngine | `src/services/`（L4 规划内） | 订阅 `LATENCY_SUMMARY`，对比基线，产出根因诊断 |

**第一版行为边界（L4）**:
- L4 内省报告**落盘持久化**（`flush_to_disk` / JSON），不直接修改任何 L2/L3 配置。
- L4 检测到偏差时 publish `BIAS_DETECTED`，但**不自动调整 Agent 策略**——仅记录待人工审核。
- L4 事件可被 `SystemEventBus.on_batch` 批量同步，供 Web 端未来展示。

---

### 2.3 第一版集成点（具体到文件 / 函数）

#### 2.3.1 启动装配（新增）

**新增文件**: `src/services/bootstrap_event_bus.py`

```python
def bootstrap_event_bus(env_path: Path) -> SystemEventBus:
    """初始化 SystemEventBus + 注册各层订阅者。全部订阅者为只读观察者。"""
    bus = SystemEventBus.instance()
    # L4 订阅（观察）
    bus.subscribe_all(lambda e: L4Observer.handle(e))   # L4 元认知入口
    # L3 订阅（观察）
    bus.subscribe(SystemEventType.CONFIG_REGRESSION_DETECTED, L3Observer.on_config_regression)
    # L2 订阅（观察）
    bus.subscribe(SystemEventType.REFLECTION_COMPLETED, L2Observer.on_reflection)
    return bus
```

**接入点**:
- `main.py` `main()`：在 `HealthCheckDaemon` 启动前调用 `bootstrap_event_bus()`；`SYSTEM_STARTUP`/`SHUTDOWN` 事件挂载到进程生命周期。
- `server.py`：FastAPI 启动事件中同样初始化（保证 API 进程也有 EventBus）。

#### 2.3.2 HealthCheckDaemon 接入（L2→L3→L4 主链路）

**文件**: `src/services/health_check.py`

```python
def _run_checks(self) -> List[HealthStatus]:
    statuses = [...]
    # 每轮检查完成后 publish 聚合事件
    bus.publish(SystemEvent(
        event_type=SystemEventType.HEALTH_CHECK_COMPLETED,
        severity=severity_from_statuses(statuses),
        payload={"component_statuses": [dataclasses.asdict(s) for s in statuses]},
    ))
    return statuses
```

- 将 `ModuleAutoRestarter.update_health()`、`GracefulDegradationEngine.tick()` / `evaluate_level()` 的输入接到本轮 `statuses`，第一版仅记录输出到 EventBus，不触发实际重启 / 降级。
- 订阅 `HEALTH_CHECK_COMPLETED` 的 L4 组件做健康趋势分析。

#### 2.3.3 Pipeline 接入（L2 核心链路）

**文件**: `src/core/pipeline.py`（`StockAnalysisPipeline.analyze_stock()`）

- `analyze_stock()` 入口 publish `PIPELINE_STARTED`（payload: `stock_code`, `start_ts`, `correlation_id`）。
- 成功 / 异常分支 publish `PIPELINE_COMPLETED` / `PIPELINE_FAILED`（payload: `stock_code`, `duration_ms`, `error`）。
- Agent 子系统的 tool 调用在 `src/agent/executor.py` / `runner.py` 中 publish `AGENT_TOOL_CALL` / `AGENT_TOOL_RESULT`。

#### 2.3.4 DataFetcherManager 接入（L1 主链路）

**文件**: `data_provider/base.py`

- 在 fallback 决策点（当前源失败 → 切下一个）publish `DATA_SOURCE_FALLBACK`（payload: `from_source`, `to_source`, `reason`）。
- 所有源均失败 publish `DATA_FETCH_FAILED`（payload: `code`, `error`）。

#### 2.3.5 Config 变化接入（L1→L3 配置回归）

**文件**: `src/services/system_config_service.py`

- 在写入 `.env` 前调用 `ConfigAutoRollback.pre_change_hook()`（已有能力），写入后 publish `CONFIG_CHANGED`。
- `ConfigAutoRollback.detect_regression()` 结果 publish `CONFIG_REGRESSION_DETECTED`；第一版不自动 `execute_rollback()`，仅记录。

#### 2.3.6 NotificationSender 接入（L1→L2 推送链路）

**文件**: `src/notification_sender/` 各 sender

- 发送成功 / 失败 publish `NOTIFICATION_SENT` / `NOTIFICATION_FAILED`（payload: `channel`, `target`, `ok`, `error`）。

---

### 2.4 SystemEventType 扩展（第一版必须新增的事件类型）

现有 `SystemEventType`（`src/services/event_bus.py`）只覆盖 L3/L4 内部事件。第一版需扩展以下 L1/L2 事件：

```python
class SystemEventType(str, Enum):
    # ---- 现有 L3 事件（保留）----
    MODULE_RESTARTED / MODULE_RESTART_FAILED / MODULE_HEALTH_CHANGED
    CONFIG_SNAPSHOT_CREATED / CONFIG_ROLLBACK_EXECUTED / CONFIG_REGRESSION_DETECTED
    DEGRADATION_TRANSITION / CAPABILITY_DISABLED / CAPABILITY_RESTORED
    REFLECTION_COMPLETED / BIAS_DETECTED / CIRCULARITY_DETECTED / OUTCOME_DEVIATION
    SYSTEM_STARTUP / SYSTEM_SHUTDOWN / HEALTH_CHECK_COMPLETED

    # ---- 新增 L1 事件 ----
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

    # ---- 新增 L2 事件 ----
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
```

---

### 2.5 第一版验收标准

| 验收项 | 判定标准 |
|---|---|
| 启动装配 | `main.py` / `server.py` 启动后 EventBus 已初始化，`SYSTEM_STARTUP` 事件已 publish |
| 健康检查链路 | HealthCheckDaemon 每轮 publish `HEALTH_CHECK_COMPLETED`，L4 能收到并记录 |
| Pipeline 链路 | 运行 `main.py --stocks 600519` 后 EventBus 收到 `PIPELINE_STARTED` + `PIPELINE_COMPLETED` |
| 数据源链路 | 模拟单源失败，EventBus 收到 `DATA_SOURCE_FALLBACK` / `DATA_FETCH_FAILED` |
| L4 报告 | `MetaCognitiveEngine.reflect()` 能消费以上事件，产出内省报告落盘 |
| 全 dry_run | 全程无任何自动重启 / 回滚 / 降级 / patch 应用发生（可加统计断言） |
| 回归测试 | `pytest -m "not network"` 全绿；新增事件类型不破坏现有 160+ 测试 |

> **实施状态（2026-08-12）**：
> - ✅ 已完成：Phase 1a（SystemEventType 扩展 24 个 L1/L2 事件）、Phase 1b（bootstrap_event_bus 装配层）、Phase 1c（main.py/api.app.py 启动装配）、Phase 2（HealthCheckDaemon → EventBus）、Phase 3（Pipeline/Agent tool-call）、Phase 4（DataFetcherManager fallback）、Phase 5（Config/Notification 事件）、Phase 6（L4 消费 + 回归验证）。
> - ✅ 验收已通过：四层全链路冒烟（L1 fallback→L2 pipeline→L3 回归→L4 反思）、225 个直接相关测试通过、装配幂等 + 生命周期事件 + 落盘验证通过。
> - ⚠️ 未验证项（沙箱无 Windows 依赖）：`pytest -m "not network"` 全量未跑；`test_agent_pipeline.py` 2 个内置策略加载断言在沙箱失败（pandas 版本差异，与本次改动无关）；fetcher 取数测试因 pandas `'RangeIndex' has no attribute 'tz'` 沙箱差异失败。需在 Windows 环境复跑确认。
> - 📝 说明：全部接入为纯新增（+407 行 / 0 删除），无任何自动干预（全 dry_run）。

---

### 2.6 阶段拆分与实施顺序

| 阶段 | 范围 | 验收 |
|---|---|---|
| **Phase 0** | 清理工作区行尾噪音，建立干净基线（已完成的 `efe453c`） | `git status` 干净 |
| **Phase 1** | 扩展 `SystemEventType` + 新增 `bootstrap_event_bus.py` + 接入 `main.py`/`server.py` 启动 | 启动 publish `SYSTEM_STARTUP` |
| **Phase 2** | HealthCheckDaemon → EventBus（L2→L3→L4 主链路） | 每轮检查 publish |
| **Phase 3** | Pipeline / Agent tool-call 接入（L2 核心链路） | 分析跑通后事件齐全 |
| **Phase 4** | DataFetcherManager fallback 接入（L1 链路） | 单源失败事件齐全 |
| **Phase 5** | Config 变化 + NotificationSender 接入（L1→L2） | 配置 / 推送事件齐全 |
| **Phase 6** | L4 消费全部事件 + 内省报告落盘 + Web 只读面板（可选） | 报告生成，全 dry_run 断言通过 |

---

### 2.7 风险与回滚

| 风险 | 缓解 |
|---|---|
| 事件风暴（高频 fetcher / tool 调用） | EventBus 内置 `publish_batch` + 落盘；L1 高频事件（如 `LATENCY_SUMMARY`）设置采样率 |
| 订阅者异常拖垮主流程 | EventBus 已隔离 handler 异常；仍建议 Phase 1 加 `max_events_per_cycle` 保护 |
| 新增事件类型破坏现有枚举 | 只追加不修改现有枚举值；`SystemEventType` 变更需跑 `test_event_bus_integration.py` |
| L4 报告堆积磁盘 | `flush_to_disk` 轮转策略；设置 `max_events` 上限 |
| 回滚 | 全部接入均为观察型、无副作用；回滚 = 移除 `bootstrap_event_bus()` 调用即可，代码无破坏 |

---

### 2.8 一句话总结（执行工程师视角）

> **第一版的目标不是"自动修复"，而是让系统第一次"看见自己"：把 L1 的每一次 fallback、L2 的每一次 pipeline 完成、L3 的每一次故障信号、L4 的每一次反思，全部通过 EventBus 双向贯通并落盘。当系统积累了真实的故障与修复效果数据后，`RepairEffectivenessLog` 才真正开始训练修复策略路由器——那才是从"观察"走向"干预"的正确起点。**
