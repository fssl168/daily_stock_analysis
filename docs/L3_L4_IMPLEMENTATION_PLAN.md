# L3 架构自修复 & L4 元认知 — 函数级开发实施计划

> 基准日期：2026-08-11
> 参考项目：`D:\laap-AGI`（AGI 外部自我意识架构）
> 目标项目：`D:\leanpython\daily_stock_analysis`（股票智能分析系统）

---

## 一、现状总结

### 1.1 L3 架构自修复 — 已有 vs 缺失

| 已有 | 实现位置 | 缺失 |
|------|----------|------|
| ExchangeClock NTP 自动同步 | `src/utils/exchange_clock.py` | — |
| 数据源多级 fallback（8 层优先级） | `data_provider/` 各 fetcher 的 `priority` 字段 | — |
| CircuitBreaker 三级自动熔断 | `src/config.py` `circuit_breaker_cooldown` | — |
| HealthCheckDaemon 监控（listener/数据源/NTP/券商/系统资源） | `src/services/health_check.py` | — |
| WebSocket 断开自动回退 HTTP 轮询 | MarketListener 内部 | — |
| — | — | **模块故障后自动重启该模块** |
| — | — | **运行中检测到代码回归后自动回滚配置** |
| — | — | **内存泄漏/任务拥堵时自动降级非核心功能** |

### 1.2 L4 元认知 — 已有 vs 缺失

| 已有 | 实现位置 | 缺失 |
|------|----------|------|
| PM Agent `make_decision()` 注入反射记忆到决策上下文 | laap-AGI `aris_brain/cognitive_bus.py` | — |
| BattlePlan 生成三情景预案（强/中/弱） | laap-AGI `laap/agi/autonomy.py` Plan/PlanStep | — |
| 复盘笔记 `to_markdown()` 格式化为自然语言 | laap-AGI `laap/agi/meta_cognitive.py` `get_self_report()` | — |
| — | — | **系统主动推送"我今天为什么没有交易"** |
| — | — | **系统自我诊断"当前延迟为什么比昨天高"并给出根因** |
| — | — | **系统在新任务开始前用自然语言描述当前状态摘要** |

### 1.3 laap-AGI 代码级映射：哪个 laap-AGI 函数融入哪个计划函数

下表按"laap-AGI 源 → 计划目标"逐条列出每个可直接迁移的核心能力的对应关系。标注 **"直接移植"** 表示迁移时只改命名空间和模块边界，不改核心逻辑；标注 **"适配改写"** 表示保留数据流与状态机，但语义域从"代码修复"改为"进程重启/配置回滚/延迟诊断"等 dsa 领域语义。

#### 1.3.1 aris_watchdog.py → ModuleAutoRestarter

| # | laap-AGI 源 | 源文件行号 | 映射到目标函数 | 转换说明 |
|---|-----------|----------|-------------|---------|
| 1 | `check_port(port)` → `(bool, str)` | L156-166 | `ModuleAutoRestarter._restart_process()` 内部端口验证逻辑 | **直接移植**。dsa 的 Listener/API Server 同样需要端口检测 |
| 2 | `check_process_name(name)` → `(bool, str)` | L168-180 | `ModuleAutoRestarter._restart_process()` 内部进程检测逻辑 | **直接移植**，用 wmic 匹配命令行关键字 |
| 3 | `is_alive(pdef)` → `(bool, str)` | L182-186 | `ModuleAutoRestarter._verify_restart()` | **适配改写**：laap-AGI 用 check 字段分发；dsa 改为用 `ModuleDef.restart_strategy` 分发（process/thread/method） |
| 4 | `start_one(pdef)` → bool | L191-224 | `ModuleAutoRestarter._restart_process()` | **适配改写**：laap-AGI 用预定义 ProcessDef 列表；dsa 改为接受 ModuleDef 实例，支持进程/线程/回调三种策略 |
| 5 | `stop_one(pdef)` → None | L265-278 | `ModuleAutoRestarter._restart_process()` 中的停止逻辑 | **直接移植** terminate→wait(5)→kill→wait(3) 三级停止 |
| 6 | `heal_loop(initial_boot)` 主循环 | L287-346 | `ModuleAutoRestarter.update_health()` + `should_restart()` + `restart_module()` | **适配改写**：laap-AGI 是独立阻塞循环；dsa 改为被 HealthCheckDaemon 驱动的非阻塞模型，每次检查周期调用 update_health |
| 7 | `restarts: Dict[str, int]` 重启计数器 | L293 | `ModuleHealthState.consecutive_failures` + `total_restarts` | **直接移植**，但持久化到 dataclass 字段，支持跨进程重启记忆 |
| 8 | `cooldown: Dict[str, float]` 冷却时间 | L294 | `ModuleAutoRestarter._is_in_cooldown()` 中的 `self._cooldown_until` 字典 | **直接移植**冷却机制：正常冷却 60s，连续失败冷却 min(120×count, 600) |
| 9 | 15s 检查间隔 | L346 | 集成到 HealthCheckDaemon 的 `check_interval` 参数 | **适配改写**：laap-AGI 硬编码 sleep(15)；dsa 复用 HealthCheckDaemon 的检查频率配置 |
| 10 | ProcessDef 数据结构 | L58-69 | `ModuleDef` dataclass | **适配改写**：增加 restart_strategy（process/thread/method）、重启回调、每小时限流、依赖声明 |

#### 1.3.2 multi_agent.py SafeRollback → ConfigAutoRollback

| # | laap-AGI 源 | 源文件行号 | 映射到目标函数 | 转换说明 |
|---|-----------|----------|-------------|---------|
| 1 | `SafeRollback.__init__()` 初始化 | L~44-51 | `ConfigAutoRollback.__init__()` | **直接移植**：repo_root、backup_dir、_memory_snapshots dict、rollback_count |
| 2 | `SafeRollback.snapshot(file_path)` → Dict | L~67-98 | `ConfigAutoRollback.create_snapshot(trigger)` | **直接移植三层模型**：Layer1(内存) `_memory_snapshots[path]=content` → Layer2(文件) `{backup_dir}/{ts}_{checksum}.bak` → Layer3(Git) `git hash-object -w` |
| 3 | `SafeRollback.verify_integrity()` → bool | L~100-110 | `ConfigAutoRollback._verify_rollback()` | **直接移植** SHA256[:16] 校验逻辑 |
| 4 | `SafeRollback.rollback(file_path, snapshot_id)` → Dict | L~112-140 | `ConfigAutoRollback.execute_rollback(snapshot_id)` | **直接移植三层回退**：memory → file backup → git checkout，逐层尝试直到成功 |
| 5 | `SafeRollback.cleanup_old_backups(max_age_seconds)` | L~142-155 | `ConfigAutoRollback.cleanup_old_snapshots(max_age_days, keep_min)` | **直接移植**，将秒改为天更符合配置备份语义 |
| 6 | `SafeRollback.stats()` → Dict | L~157-165 | `ConfigAutoRollback.stats()` | **直接移植**格式 |
| 7 | — | — | `ConfigAutoRollback.detect_regression()` + 5 个 `_check_*_regression()` 方法 | **dsa 新增**：laap-AGI SafeRollback 只做备份/回滚不做自动检测；dsa 增加回归信号检测层 |

#### 1.3.3 guardian.py EmergencyStop + DeployChecklist + IntegrityScanner → GracefulDegradationEngine + ConfigAutoRollback

| # | laap-AGI 源 | 源文件行号 | 映射到目标函数 | 转换说明 |
|---|-----------|----------|-------------|---------|
| 1 | `GuardianMode` 四级枚举 | L17-21 | `SystemPressureLevel`（NORMAL/ELEVATED/HIGH/CRITICAL） | **直接移植**模式机结构，语义从"代码修改权限"改为"功能降级等级" |
| 2 | `EmergencyStop.allow_modification(risk_level)` → (bool, str) | L59-78 | `GracefulDegradationEngine.evaluate_pressure(health_metrics)` → SystemPressureLevel | **适配改写**：laap-AGI 判断"是否允许代码修改"；dsa 判断"应降级哪些功能" |
| 3 | `EmergencyStop.switch_mode(new_mode, by, reason)` | L80-98 | `GracefulDegradationEngine.apply_degradations_for_level(level)` | **适配改写**：laap-AGI 写 .guardian_mode 文件；dsa 在执行降级规则后更新内存状态并记录 degradation_history |
| 4 | `EmergencyStop.record_failure(reason)` 三连失败自动升级 | L100-120 | `GracefulDegradationEngine.evaluate_pressure()` 中 `consecutive_threshold_hits` 逻辑 | **直接移植**升级策略：连续 3 次超过阈值→自动升级一级；成功一次递减计数器 |
| 5 | `EmergencyStop.record_success()` 递减失败计数 | L122-132 | 同一逻辑在 evaluate_and_apply() 中 | **直接移植** |
| 6 | `DeployChecklist.run()` 12 点检查 | L145-220 | `ConfigAutoRollback._verify_rollback()` 验证逻辑 | **适配改写**：12 项检查中取 3 项（file_exists、file_not_empty、syntax_valid）用于回滚后验证 |
| 7 | `IntegrityScanner.scan(directory)` → {findings} | L230-310 | `ConfigAutoRollback._verify_rollback()` 完整性检查 | **适配改写**：laap-AGI 扫描整个目录；dsa 仅验证单个 .env 文件，复用 AST parse 和 checksum 比对 |
| 8 | `IntegrityScanner.register_known_good(file_path)` | L312-330 | `ConfigAutoRollback.create_snapshot()` 内部自动注册 checksum | **直接移植**，快照创建时自动保存 SHA256[:16] |
| 9 | `GuardianSystem.gatekeeper()` 三层门控 | L340-380 | `ConfigAutoRollback.auto_rollback_if_needed()` | **适配改写**：Layer1 降级状态检查→Layer2 回归检测→Layer3 配置完整性验证；语义从"代码部署门"改为"配置回滚门" |

#### 1.3.4 self_healing.py ErrorMonitor + AutoHealer → ModuleAutoRestarter + LatencySelfDiagnosisEngine

| # | laap-AGI 源 | 源文件行号 | 映射到目标函数 | 转换说明 |
|---|-----------|----------|-------------|---------|
| 1 | `ErrorMonitor._hash_message(msg)` 错误签名去重 | L~90-100 | `ModuleAutoRestarter.update_health()` 中的错误模式匹配 | **适配改写**：laap-AGI 用 SHA256 错误签名去重；dsa 改用健康检查失败类型 + 模块 ID 组成故障签名 |
| 2 | `AUTO_FIX_THRESHOLD = 3`（300s 窗口内） | L~70 | `ModuleAutoRestarter.should_restart()` 中 `consecutive_failures >= 3` | **直接移植**阈值，窗口从 300s 改为 HealthCheckDaemon 默认周期 |
| 3 | `BugReport` / `BugType` / `FixAttempt` dataclass | L88-116 | `RestartRecord` / `ModuleHealthState` dataclass | **适配改写**：laap-AGI 追踪代码 Bug；dsa 追踪模块健康故障与重启尝试 |
| 4 | `AutoHealer.heal()` 周期 scan→classify→generate→deploy | L~240-290 | `ModuleAutoRestarter.restart_module()` 流程 detect→decide→restart→verify | **适配改写**：laap-AGI 是代码修复管线；dsa 是模块重启管线。阶段数相同语义不同 |
| 5 | `AutoHealer.start_background()` / `stop_background()` | L~320-340 | 集成到 HealthCheckDaemon 生命周期 | **适配改写**：laap-AGI 独立线程；dsa 复用 HealthCheckDaemon 的检查循环 |
| 6 | `AutoHealer.stats()` → Dict | L~342-355 | `ModuleAutoRestarter.stats()` | **直接移植**格式 |
| 7 | `ErrorMonitor` 的 error→bug_type 分类逻辑 | L~120-170 | `LatencySelfDiagnosisEngine.diagnose_root_causes()` | **适配改写**：laap-AGI 分类代码 Bug 类型；dsa 分类延迟根因类型（external_api_slowdown / network_instability / system_resource_pressure / llm_rate_limited / task_congestion / recent_config_change / unknown） |
| 8 | `ErrorMonitor._create_bug_report()` → BugReport | L~180-220 | `LatencySelfDiagnosisEngine._compare_with_baseline()` → LatencyAnomaly | **适配改写**：laap-AGI 从错误日志创建 BugReport；dsa 从延迟指标与基线比较创建 LatencyAnomaly |

#### 1.3.5 meta_cognitive.py MetaCognitiveMonitor → WhyNoTradeExplainer + LatencySelfDiagnosisEngine + TaskContextSummarizer

| # | laap-AGI 源 | 源文件行号 | 映射到目标函数 | 转换说明 |
|---|-----------|----------|-------------|---------|
| 1 | `CognitiveEpisode` dataclass（episode_id, timestamp, context, reasoning_trace, action_taken, outcome, confidence, emotional_state, duration_ms） | L34-58 | `NoTradeReason` dataclass（reason_id, category, priority, summary_zh, detail, evidence） | **适配改写**：laap-AGI 记录通用认知片段；dsa 记录特定交易决策原因 |
| 2 | `ReflectionTrigger` 枚举（POST_ACTION, ERROR_DETECTED, CONFIDENCE_LOW, GOAL_CONFLICT, TIME_BASED, USER_REQUEST） | L24-31 | `WhyNoTradeExplainer.analyze_no_trade()` 的触发条件 | **适配改写**：laap-AGI 触发条件改为 dsa 触发条件——每日分析完成后 / 用户手动查询 / 收盘后30分钟 |
| 3 | `MetaCognitiveMonitor.start_episode()` → str | L95-99 | 集成到 `run_full_analysis()` 的 session 管理 | **适配改写**：laap-AGI 手动管理 episode 生命周期；dsa 在分析流程中自动记录 |
| 4 | `MetaCognitiveMonitor._perform_reflection()` 反思执行 | L159-183 | `WhyNoTradeExplainer.analyze_no_trade()` 整体流程 | **适配改写**：laap-AGI 反思单次认知片段；dsa 反思一个交易日的所有决策信号 |
| 5 | `MetaCognitiveMonitor._analyze_reasoning()` → Dict（step_count, has_goal_mention, has_alternatives, has_evidence, depth_score） | L185-214 | `LatencySelfDiagnosisEngine.diagnose_root_causes()` → List[Dict]（root_cause + confidence + evidence） | **适配改写**：laap-AGI 分析推理步骤质量；dsa 分析延迟异常的根因及置信度 |
| 6 | `MetaCognitiveMonitor._detect_biases()` 5 种偏差检测 | L255-300 | `WhyNoTradeExplainer._classify_reasons()` 6 类原因分类 | **适配改写**：laap-AGI 检测"认知偏差"（confirmation_bias/anchoring_bias/emotional_bias/overconfidence/insufficient_reasoning）；dsa 分类"无交易原因"（market_condition/signal_quality/risk_management/technical_issue/schedule/no_opportunity） |
| 7 | `MetaCognitiveMonitor._detect_circularity()` 循环推理检测 | L216-253 | `WhyNoTradeExplainer._classify_reasons()` 中"信号冲突"判断 | **适配改写**：laap-AGI 检测推理步骤循环引用；dsa 检测多个决策信号之间方向冲突 |
| 8 | `MetaCognitiveMonitor._check_auto_reflection()` 自动反思触发 | L143-157 | `WhyNoTradeExplainer.push_explanation()` 自动推送判断 | **适配改写**：laap-AGI 的触发条件(low_confidence/error/time)改为 dsa 的推送判断(无交易信号/收盘后/延迟异常) |
| 9 | `MetaCognitiveMonitor.get_self_report()` → Dict | L332-363 | `LatencySelfDiagnosisEngine.generate_diagnosis_report()` → LatencyDiagnosis | **直接移植**报告结构：total_episodes→total_checks, success_rate→anomaly_rate, bias_distribution→root_cause_distribution |
| 10 | `MetaCognitiveMonitor.generate_introspection_prompt()` → str | L401-433 | `TaskContextSummarizer.generate_summary()` → str | **直接移植**格式模板：累计统计→成功率→常见问题→学习要点→行动建议 |
| 11 | `MetaCognitiveMonitor._llm_reflection()` LLM 深度反思 | L302-330 | `WhyNoTradeExplainer._generate_with_llm()` / `LatencySelfDiagnosisEngine._generate_with_llm()` / `TaskContextSummarizer._generate_with_llm()` | **直接移植** LLM prompt 构建模式：结构化数据→自然语言→fallback 到模板 |
| 12 | `MetaCognitiveMonitor._summarize_biases()` 偏差总结 | L365-378 | `WhyNoTradeExplainer._generate_explanation()` 原因总结 | **适配改写**：laap-AGI 总结认知偏差；dsa 总结无交易原因 |
| 13 | **阈值常量**：LOW_CONFIDENCE_THRESHOLD=0.3, HIGH_CONFIDENCE_THRESHOLD=0.9, MIN_REASONING_STEPS=2, MAX_EPISODES_BEFORE_REFLECTION=5 | L74-77 | 各模块对应阈值 | **适配改写**：LOW_CONFIDENCE→NO_TRADE_SIGNAL_THRESHOLD(0.6), HIGH_CONFIDENCE→PUSH_CONFIDENCE_THRESHOLD(0.8), MIN_REASONING_STEPS→MIN_ANALYSIS_STEPS(3), MAX_EPISODES→MAX_DAILY_CHECKS(1) |

#### 1.3.6 self_model.py EmergentSelfModel → LatencySelfDiagnosisEngine + TaskContextSummarizer

| # | laap-AGI 源 | 源文件行号 | 映射到目标函数 | 转换说明 |
|---|-----------|----------|-------------|---------|
| 1 | `SkillProfile` dataclass（EMA-based success_rate tracking） | L83-137 | `LatencyBaseline` dataclass（EWMA-based P50/P95/P99 tracking） | **适配改写**：laap-AGI 用 EMA 追踪技能成功率（α=0.1）；dsa 用 EWMA 追踪延迟百分位（同样 α=0.1） |
| 2 | `SkillProfile.record(outcome_score, is_success)` | L114-137 | `LatencySelfDiagnosisEngine.update_baseline(metric_name, latency_samples, window)` | **适配改写**：laap-AGI 追踪单次行动结果；dsa 追踪批量延迟样本 |
| 3 | `SkillProfile.proficiency` 计算（_proficiency_from_stats） | L66-80 | `LatencyAnomaly.deviation_std` 异常程度计算 | **适配改写**：laap-AGI 用 attempts+success_rate 算熟练度；dsa 用 current_value vs baseline P95 + σ 算异常严重度 |
| 4 | `EmergentSelfModel.record_experience()` 经验记录 | L274-361 | 集成到 run_diagnostics 数据写入流 | **适配改写**：laap-AGI 记录通用领域经验；dsa 在每次分析完成时写入延迟数据点 |
| 5 | `EmergentSelfModel.know_what_you_know()` → Dict（strong/weak/unexplored domains + calibration） | L367-438 | `TaskContextSummarizer.collect_system_status()` → SystemContextSummary（modules_healthy/degraded + data_sources + task_queue） | **适配改写**：laap-AGI 审计自身能力；dsa 审计系统运行状态 |
| 6 | `EmergentSelfModel.self_assess(domain, required_proficiency)` → Dict | L440-495 | `TaskContextSummarizer.preflight_check(task_type)` → TaskPreflightResult | **适配改写**：laap-AGI 评估"我是否胜任这个领域"；dsa 评估"系统是否准备好执行这个任务" |
| 7 | `EmergentSelfModel.reflection(depth)` → str（第一人称叙事） | L497-561 | `TaskContextSummarizer.generate_task_brief()` → str（第三人称系统状态） | **适配改写**：laap-AGI 用 "I am..." 叙事风格；dsa 用更客观的系统状态报告风格 |
| 8 | `EmergentSelfModel.stats()` → Dict | L613-632 | `TaskContextSummarizer.stats()` / `LatencySelfDiagnosisEngine.stats()` | **直接移植**格式 |
| 9 | **校准曲线** `_calibration_curve` (confidence_bucket → actual_accuracy) | L197, L696-704 | `LatencySelfDiagnosisEngine._compare_with_baseline()` 比较逻辑 | **适配改写**：laap-AGI 比较预测置信度 vs 实际准确率；dsa 比较当前延迟 vs 历史基线 |

### 1.4 迁移策略总结

| 迁移策略 | 涉及的 laap-AGI 函数/模式数量 | 典型代表 |
|---------|--------------------------|---------|
| **直接移植** | 14 个（约 32%） | SafeRollback.snapshot/rollback/cleanup、check_port/check_process_name、GuardianMode 枚举、三层回退策略、冷却机制、SHA256 校验、stats() 接口格式、LLM prompt 构建模式、generate_introspection_prompt 格式模板 |
| **适配改写** | 30 个（约 68%） | heal_loop→非阻塞式、BugReport→RestartRecord、_detect_biases→_classify_reasons、SkillProfile.proficiency→LatencyAnomaly 严重度、EmergentSelfModel.reflection→TaskContextSummarizer.generate_task_brief |
| **dsa 新增** | 7 个函数 | detect_regression() 及 5 个 _check_*_regression()、register_from_health_daemon() |

**关键适配原则**：
- laap-AGI 用"代码修复"→ dsa 用"进程/线程重启 + 配置回滚"
- laap-AGI 用"认知片段"→ dsa 用"交易日决策信号"
- laap-AGI 用"能力熟练度"→ dsa 用"延迟基线 + 系统健康状态"
- laap-AGI 用独立线程/阻塞循环→ dsa 用 HealthCheckDaemon 驱动的事件模型

---

## 二、L3 模块一：模块自动重启引擎（ModuleAutoRestarter）

### 2.1 模块定位

```
文件: src/services/module_restart.py
依赖: src/services/health_check.py (HealthCheckDaemon)
      src/core/market_review_lock.py (进程锁检测模式)
      laap-AGI aris_watchdog.py (架构参考)
```

### 2.2 核心数据结构

```python
# ====== 2.2.1 模块定义 ======
@dataclass
class ModuleDef:
    """可被自动重启的模块定义"""
    module_id: str                          # 唯一标识，如 "market_listener"
    display_name: str                       # 显示名，如 "市场监听器"
    restart_strategy: str                   # "process" | "thread" | "method"
    # --- 进程级重启 ---
    process_name_pattern: str = ""          # wmic 匹配的命令行关键字
    restart_command: List[str] = field(default_factory=list)  # 重启命令
    cwd: str = ""                           # 工作目录
    log_path: str = ""                      # 日志输出路径
    start_delay_seconds: int = 5            # 启动后等待时间
    # --- 线程/方法级重启 ---
    restart_callback: Optional[Callable[[], bool]] = None  # 重启回调函数
    # --- 通用 ---
    max_restarts_per_hour: int = 3          # 每小时最大重启次数
    cooldown_seconds: int = 60              # 冷却时间
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他 module_id

@dataclass  
class RestartRecord:
    """重启记录"""
    module_id: str
    attempt: int                            # 本次运行周期内的第几次尝试
    timestamp: datetime
    reason: str                             # 触发原因（health check 失败信息）
    success: bool
    new_pid: Optional[int] = None
    error: str = ""

@dataclass
class ModuleHealthState:
    """模块健康状态机"""
    module_id: str
    current_state: str                      # "healthy" | "degraded" | "dead" | "restarting" | "cooldown"
    consecutive_failures: int = 0
    total_restarts: int = 0
    last_healthy: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    last_restart: Optional[datetime] = None
    history: List[RestartRecord] = field(default_factory=list)
```

### 2.3 函数级 API

```python
class ModuleAutoRestarter:
    """
    模块自动重启引擎。

    参考 laap-AGI aris_watchdog.py 的 heal_loop() 架构，
    将重启逻辑从 HealthCheckDaemon 的告警扩展为：检测→决策→重启→验证→记录。
    """

    # ========== 构造函数 ==========

    def __init__(
        self,
        on_restart_event: Callable[[str, str], None],  # (level, message) 回调，用于通知
        state_file: Optional[str] = None,               # 状态持久化路径
    ) -> None:
        """
        初始化重启引擎。
        - 创建空模块注册表
        - 加载历史重启状态（如果 state_file 存在）
        - 初始化每小时重启计数器
        """

    # ========== 模块注册 ==========

    def register_module(self, module_def: ModuleDef) -> None:
        """注册一个可被自动重启的模块。
        
        验证：
        - module_id 唯一性
        - restart_strategy 与所需字段一致
        - dependencies 中引用的模块已注册
        """

    def register_from_health_daemon(
        self,
        listener: Any = None,   # MarketListener 实例
        fetcher: Any = None,    # 数据源 fetcher 实例
        broker: Any = None,     # 券商连接实例
    ) -> List[str]:
        """从现有 HealthCheckDaemon 监控对象自动注册模块。
        
        根据传入的运行时对象创建对应的 ModuleDef：
        - listener → market_listener (线程重启)
        - fetcher → data_fetcher (方法重启)
        - broker → broker_connection (方法重启)
        
        返回注册的 module_id 列表。
        """

    # ========== 健康状态管理 ==========

    def update_health(
        self,
        module_id: str,
        healthy: bool,
        message: str = "",
    ) -> ModuleHealthState:
        """更新模块健康状态。
        
        由 HealthCheckDaemon 在每次检查周期中调用。
        - healthy=True: 重置连续失败计数、记录 last_healthy
        - healthy=False: 递增连续失败计数、记录 last_failure
        - 连续失败超过阈值(3次)时自动触发评估
        """

    def should_restart(self, module_id: str) -> Tuple[bool, str]:
        """判断是否应重启模块。
        
        检查条件：
        1. 连续失败次数 >= 3（与 HealthCheckDaemon 的 ALERT_THRESHOLD 一致）
        2. 不在冷却期
        3. 未超过每小时最大重启次数
        4. 依赖模块均健康
        5. 当前状态不是 "restarting"
        
        返回 (should_restart, reason)
        """

    # ========== 重启执行 ==========

    def restart_module(self, module_id: str) -> RestartRecord:
        """执行模块重启。
        
        流程（参考 aris_watchdog.start_one + heal_loop）：
        1. 状态切换为 "restarting"
        2. 若无依赖问题，先重启依赖模块
        3. 若为进程级：关闭旧进程→等待端口释放→启动新进程→等待→验证存活
        4. 若为线程/方法级：调用 restart_callback
        5. 更新状态：成功→"healthy"、失败→递增计数+进入冷却
        6. 记录 RestartRecord
        7. 通过 on_restart_event 发送通知
        """

    def _restart_process(self, module_def: ModuleDef) -> Tuple[bool, str, Optional[int]]:
        """进程级重启（参考 aris_watchdog.start_one）。
        
        1. 用 wmic 查找旧进程 PID
        2. taskkill /F 强制终止旧进程
        3. 等待端口释放（最多 10 秒）
        4. subprocess.Popen 启动新进程
        5. 等待 start_delay_seconds
        6. 检查新进程是否存活（poll() is None）
        """

    def _restart_thread_or_method(self, module_def: ModuleDef) -> Tuple[bool, str]:
        """线程/方法级重启。
        
        调用 module_def.restart_callback()。
        回调负责：停止旧线程→清理资源→启动新线程→返回成功/失败。
        """

    def _verify_restart(self, module_def: ModuleDef) -> Tuple[bool, str]:
        """验证重启是否成功。
        
        - 进程级：检查进程存活 + 端口监听
        - 线程级：调用 is_alive() 方法（如果存在）
        """

    # ========== 依赖处理 ==========

    def _check_dependencies(self, module_id: str) -> Tuple[bool, List[str]]:
        """检查模块的所有依赖是否健康。
        
        返回 (all_healthy, list_of_unhealthy_deps)
        """

    def _restart_dependency_chain(self, module_id: str) -> List[RestartRecord]:
        """按依赖顺序重启模块链。
        
        拓扑排序所有依赖，从最底层开始重启。
        """

    # ========== 冷却与限流 ==========

    def _is_in_cooldown(self, module_id: str) -> bool:
        """检查模块是否在冷却期。"""

    def _is_rate_limited(self, module_id: str) -> bool:
        """检查是否超过每小时最大重启次数。"""

    # ========== 状态持久化 ==========

    def save_state(self) -> None:
        """将当前所有模块健康状态和重启历史持久化到 state_file。"""

    def load_state(self) -> None:
        """从 state_file 加载历史状态。"""

    # ========== 查询接口 ==========

    def get_module_status(self, module_id: str) -> Optional[ModuleHealthState]:
        """获取单个模块状态。"""

    def get_all_status(self) -> Dict[str, ModuleHealthState]:
        """获取所有已注册模块的状态。"""

    def get_restart_summary(self, hours: int = 24) -> Dict[str, Any]:
        """获取最近 N 小时的重启摘要：
        - 总重启次数
        - 按模块分组
        - 成功率
        - 当前处于冷却/失败状态的模块
        """

    def stats(self) -> Dict[str, Any]:
        """兼容 AGIAgent.health_check() 的 stats() 接口。"""
```

### 2.4 集成点

```python
# ====== main.py / server.py 集成 ======

def setup_module_restarter(
    listener=None, fetcher=None, broker=None,
    notify_fn: Optional[Callable] = None,
) -> ModuleAutoRestarter:
    """
    工厂函数：创建并配置 ModuleAutoRestarter。
    
    1. 创建 restarter 实例
    2. 从 HealthCheckDaemon 监控对象自动注册模块
    3. 注册通知回调（复用现有 NotificationService）
    4. 返回配置好的 restarter
    """

# ====== HealthCheckDaemon 集成 ======

# 在 HealthCheckDaemon._run_checks() 的检查循环中，
# 每次检查后调用 restarter.update_health(component, status.healthy, status.message)
# 当 should_restart() 返回 True 时，异步执行 restart_module()

# ====== API 端点（可选） ======

# GET  /api/v1/health/modules          → 所有模块状态
# POST /api/v1/health/modules/{id}/restart → 手动触发重启
# GET  /api/v1/health/modules/{id}/history  → 重启历史
```

---

## 三、L3 模块二：配置自动回滚引擎（ConfigAutoRollback）

### 3.1 模块定位

```
文件: src/services/config_rollback.py
依赖: src/core/config_manager.py (ConfigManager 原子读写)
      laap-AGI laap/agi/multi_agent.py SafeRollback (架构参考)
      laap-AGI laap/agi/guardian.py IntegrityScanner (架构参考)
```

### 3.2 核心数据结构

```python
@dataclass
class ConfigSnapshot:
    """配置快照 — 参考 SafeRollback 的三层备份模型"""
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
    """回归信号 — 系统检测到配置可能引起的问题"""
    signal_id: str
    detected_at: datetime
    snapshot_before: str                    # 变更前的 snapshot_id
    snapshot_after: str                     # 变更后的 snapshot_id
    severity: str                           # "critical" | "warning" | "info"
    indicators: List[str]                   # 触发回归检测的具体指标
    auto_rollback_eligible: bool = False    # 是否满足自动回滚条件

@dataclass
class RollbackResult:
    """回滚结果"""
    success: bool
    snapshot_id: str                        # 回滚到的快照 ID
    restored_keys: List[str]                # 被恢复的配置键
    layer_used: str                         # 使用的备份层 "memory"|"file"|"git"
    verified: bool                          # 回滚后是否通过验证
    error: str = ""
```

### 3.3 函数级 API

```python
class ConfigAutoRollback:
    """
    配置自动回滚引擎。

    参考 laap-AGI SafeRollback 的三层备份模型（内存→文件→Git），
    结合 ConfigManager 的原子读写能力，实现：
    1. 配置变更前自动快照
    2. 变更后回归检测
    3. 检测到回归时自动回滚
    """

    # ========== 构造函数 ==========

    def __init__(
        self,
        env_path: Optional[Path] = None,
        snapshot_dir: Optional[Path] = None,
        repo_root: Optional[Path] = None,
        on_rollback_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """
        初始化回滚引擎。
        - 创建 ConfigManager 实例
        - 创建快照目录
        - 加载历史快照索引
        - 初始化回归检测参数（观察窗口、触发阈值）
        """

    # ========== 快照管理（参考 SafeRollback.snapshot） ==========

    def create_snapshot(self, trigger: str = "pre_change") -> ConfigSnapshot:
        """创建当前 .env 的三层快照。
        
        Layer 1 (内存): 保存完整文件内容到 self._memory_snapshots
        Layer 2 (文件): 写入 snapshot_dir/{timestamp}_{checksum}.env.bak
        Layer 3 (Git):   git hash-object -w 保存 blob（如果 Git 可用）
        
        返回 ConfigSnapshot 含 checksum 和 snapshot_id。
        """

    def list_snapshots(self, limit: int = 20) -> List[ConfigSnapshot]:
        """列出最近的配置快照。"""

    def get_snapshot(self, snapshot_id: str) -> Optional[ConfigSnapshot]:
        """获取指定快照的完整信息。"""

    def diff_snapshots(
        self, before_id: str, after_id: str
    ) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        """比较两个快照之间的配置差异。
        
        返回 {key: (old_value, new_value)}。
        """

    # ========== 回归检测 ==========

    def detect_regression(
        self,
        snapshot_before: str,
        snapshot_after: str,
        health_metrics: Dict[str, Any],     # 来自 HealthCheckDaemon 或 RunDiagnostics
        observation_window_seconds: int = 300,
    ) -> Optional[RegressionSignal]:
        """检测配置变更后是否出现回归。
        
        检测信号（任一项触发即产生 RegressionSignal）：
        1. 关键错误率上升：变更后 5 分钟内 ERROR 日志量 > 变更前 2 倍
        2. 任务失败率上升：变更后任务失败率 > 变更前 + 20%
        3. 数据源不可用：新增数据源 fallback/fetch_failed
        4. API 响应时间恶化：p95 延迟 > 变更前 2 倍
        5. 模块健康状态恶化：新增 "dead" 或 "degraded" 模块
        
        返回 RegressionSignal（如果检测到回归），否则 None。
        """

    def _check_error_rate_regression(
        self, before: datetime, after: datetime
    ) -> Optional[Dict[str, Any]]:
        """检查错误率是否恶化。
        
        实现方式：
        - 读取最近的运行诊断数据（run_diagnostics）
        - 比较 before 和 after 窗口的错误计数
        - 计算增长率
        """

    def _check_task_failure_regression(
        self, before: datetime, after: datetime
    ) -> Optional[Dict[str, Any]]:
        """检查任务失败率是否恶化。"""

    def _check_data_source_regression(
        self, before: datetime, after: datetime
    ) -> Optional[Dict[str, Any]]:
        """检查数据源可用性是否恶化。"""

    def _check_latency_regression(
        self, before: datetime, after: datetime
    ) -> Optional[Dict[str, Any]]:
        """检查 API 延迟是否恶化。"""

    def _check_module_health_regression(
        self, before: datetime, after: datetime
    ) -> Optional[Dict[str, Any]]:
        """检查模块健康状态是否恶化。"""

    # ========== 回滚执行（参考 SafeRollback.rollback） ==========

    def execute_rollback(self, snapshot_id: str) -> RollbackResult:
        """执行配置回滚。
        
        参考 SafeRollback.rollback 的三层回退策略：
        1. 尝试 Layer 1（内存快照）
        2. 尝试 Layer 2（备份文件）
        3. 尝试 Layer 3（Git checkout）
        
        回滚后执行验证：
        1. .env 文件语法检查
        2. 关键配置项非空检查
        3. 可选：通知服务连通性检查
        """

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

    def _verify_rollback(self) -> Tuple[bool, str]:
        """验证回滚后的 .env 文件是否合法。
        
        检查：
        - 文件存在且非空
        - 语法有效（每行是注释/空行/KEY=VALUE）
        - 必填配置项存在（LLM_API_KEY 等）
        """

    # ========== 变动前钩子 ==========

    def pre_change_hook(self) -> str:
        """配置变更前调用：创建快照并返回 snapshot_id。
        
        应在 ConfigManager.apply_updates() 之前调用。
        返回 snapshot_id 供后续回归检测使用。
        """

    def post_change_hook(self, snapshot_before: str) -> None:
        """配置变更后调用：创建新快照 + 启动回归检测。
        
        流程：
        1. 创建 post-change 快照
        2. 设置定时器，在 observation_window_seconds 后执行回归检测
        3. 若检测到回归，自动回滚
        """

    # ========== 定时快照 ==========

    def scheduled_snapshot(self) -> ConfigSnapshot:
        """定时创建快照（用于定期备份，而非变更触发）。
        
        可通过 HealthCheckDaemon 或独立定时任务调用。
        """

    # ========== 清理与状态 ==========

    def cleanup_old_snapshots(self, max_age_days: int = 30, keep_min: int = 10) -> int:
        """清理过期快照（参考 SafeRollback.cleanup_old_backups）。"""

    def stats(self) -> Dict[str, Any]:
        """兼容 health_check 的 stats() 接口。"""
```

### 3.4 集成点

```python
# ====== system_config_service.py 集成 ======

# 在 SystemConfigService 写入 .env 前：
#   snapshot_id = rollback_engine.pre_change_hook()
# 在 SystemConfigService 写入 .env 后：
#   rollback_engine.post_change_hook(snapshot_id)

# ====== main.py 集成 ======

# 在应用启动时：
#   rollback_engine = setup_config_rollback(env_path, notify_fn)
#   注册到 HealthCheckDaemon 的检查周期中执行 scheduled_snapshot()

# ====== API 端点（可选） ======

# GET  /api/v1/config/snapshots          → 快照列表
# GET  /api/v1/config/snapshots/{id}/diff → 比较两个快照
# POST /api/v1/config/rollback/{id}       → 手动回滚到指定快照
```

---

## 四、L3 模块三：优雅降级引擎（GracefulDegradation）

### 4.1 模块定位

```
文件: src/services/degradation_engine.py
依赖: src/services/health_check.py (HealthCheckDaemon 系统资源指标)
      laap-AGI laap/agi/guardian.py EmergencyStop (模式机参考)
      src/config.py (功能开关配置)
```

### 4.2 核心数据结构

```python
class SystemPressureLevel(str, Enum):
    """系统压力等级 — 参考 EmergencyStop 的四级模式"""
    NORMAL = "normal"           # 一切正常，所有功能启用
    ELEVATED = "elevated"       # 轻度压力，降级非关键功能
    HIGH = "high"               # 高度压力，暂停大部分非核心功能
    CRITICAL = "critical"       # 临界压力，仅保留核心分析流程

@dataclass
class DegradationRule:
    """降级规则：定义何时降级哪些功能"""
    rule_id: str
    description: str                        # 人类可读描述
    pressure_threshold: SystemPressureLevel  # 触发降级的压力等级
    # 功能控制
    target_feature: str                     # 受影响的功能标识
    degradation_action: str                 # "disable" | "throttle" | "reduce_quality" | "defer"
    # 条件
    condition_metric: str                   # 触发指标：memory_pct | cpu_pct | task_queue_depth | error_rate
    condition_threshold: float              # 阈值
    condition_duration_seconds: int = 120   # 持续时间（避免瞬时抖动误触发）
    # 恢复
    auto_recover: bool = True               # 压力解除后自动恢复
    recover_cooldown_seconds: int = 300     # 恢复冷却时间

@dataclass 
class DegradationState:
    """当前降级状态"""
    current_pressure: SystemPressureLevel = SystemPressureLevel.NORMAL
    active_degradations: Dict[str, DegradationRule] = field(default_factory=dict)
    pressure_since: Optional[datetime] = None   # 进入当前压力等级的时间
    last_evaluated: Optional[datetime] = None
    degradation_history: List[Dict] = field(default_factory=list)
```

### 4.3 函数级 API

```python
class GracefulDegradationEngine:
    """
    优雅降级引擎。

    参考 laap-AGI EmergencyStop 的四级模式切换机制，
    将系统资源指标映射为功能降级决策。
    """

    # ========== 构造函数 ==========

    def __init__(
        self,
        on_degradation_event: Callable[[str, str], None],
        state_file: Optional[str] = None,
    ) -> None:
        """
        初始化降级引擎。
        - 加载预设降级规则
        - 从 state_file 恢复上次降级状态
        - 初始化当前压力等级为 NORMAL
        """

    # ========== 规则管理 ==========

    def register_rule(self, rule: DegradationRule) -> None:
        """注册降级规则。"""

    def load_preset_rules(self) -> List[DegradationRule]:
        """加载预设降级规则。
        
        预设规则（可按需扩展）：
        
        1. 内存 > 85% → ELEVATED → disable chip_distribution（筹码分布计算耗内存）
        2. 内存 > 90% → HIGH → disable realtime_websocket（回退 HTTP 轮询）
        3. CPU > 90% → ELEVATED → throttle background_tasks（降低后台任务频率）
        4. CPU > 95% → HIGH → defer non_critical_analysis（延迟非核心分析）
        5. 任务队列 > 20 → ELEVATED → reduce_quality news_search（减少新闻搜索深度）
        6. 任务队列 > 50 → HIGH → skip news_search + skip fundamental_pipeline
        7. 磁盘 > 95% → CRITICAL → disable auto_save + reduce log_level
        8. 数据源错误率 > 30% → ELEVATED → enable aggressive_cache（启用激进缓存）
        """

    # ========== 压力评估 ==========

    def evaluate_pressure(
        self, health_metrics: Dict[str, Any]
    ) -> SystemPressureLevel:
        """评估当前系统压力等级。
        
        输入：来自 HealthCheckDaemon 的最新健康指标
        输出：当前应处于的压力等级
        
        评估逻辑（参考 EmergencyStop 的 escalation）：
        1. 收集所有条件指标当前值
        2. 检查是否有 CRITICAL 条件触发
        3. 检查是否有 HIGH 条件触发
        4. 检查是否有 ELEVATED 条件触发
        5. 若无任何触发，返回 NORMAL
        
        压力等级只升不降（除非所有条件消失超过 cooldown 时间）。
        """

    def _evaluate_single_metric(
        self, metric_name: str, current_value: float
    ) -> SystemPressureLevel:
        """评估单个指标对应的压力等级。"""

    # ========== 降级执行 ==========

    def apply_degradation(self, rule: DegradationRule) -> bool:
        """执行单条降级规则。
        
        根据 degradation_action 执行对应操作：
        - "disable": 通过配置开关关闭功能
        - "throttle": 降低执行频率
        - "reduce_quality": 减少数据量/深度
        - "defer": 推迟到低压力时段执行
        
        返回是否成功执行。
        """

    def apply_degradations_for_level(
        self, level: SystemPressureLevel
    ) -> List[DegradationRule]:
        """执行指定压力等级的所有降级规则。
        
        遍历所有规则，对 threshold <= level 的规则执行 apply_degradation。
        已在降级状态中的规则跳过。
        """

    def recover_degradation(self, rule_id: str) -> bool:
        """恢复单条降级规则。
        
        反向操作：重新启用被降级的功能。
        """

    def recover_all(self) -> List[str]:
        """恢复所有降级。
        
        当压力恢复到 NORMAL 且超过 recover_cooldown_seconds 时调用。
        """

    # ========== 主循环 ==========

    def evaluate_and_apply(
        self, health_metrics: Dict[str, Any]
    ) -> Tuple[SystemPressureLevel, List[DegradationRule], List[str]]:
        """主入口：评估压力 → 执行降级/恢复 → 发送通知。
        
        在每个 HealthCheckDaemon 周期中调用。
        
        返回 (current_level, newly_degraded, newly_recovered)
        """

    # ========== 状态查询 ==========

    def get_active_degradations(self) -> Dict[str, DegradationRule]:
        """获取当前活跃的降级状态。"""

    def get_pressure_summary(self) -> Dict[str, Any]:
        """获取压力摘要：
        - 当前压力等级
        - 已持续时间
        - 活跃降级数
        - 最近降级/恢复历史
        """

    def stats(self) -> Dict[str, Any]:
        """兼容 health_check 的 stats() 接口。"""
```

### 4.4 集成点

```python
# ====== health_check.py 集成 ======

# 在 HealthCheckDaemon._run_checks() 中：
#   每次检查周期结束后，调用：
#     degradation_engine.evaluate_and_apply(latest_health_metrics)

# ====== 各功能模块集成 ======

# 每个可降级的功能模块需要实现：
#   def set_degradation_mode(self, mode: str) -> None
# 降级引擎通过此接口控制功能开关，而不是直接修改全局配置。
```

---

## 五、L4 模块四：无交易原因解释推送（WhyNoTradeExplainer）

### 5.1 模块定位

```
文件: src/services/whynotrade_explainer.py
依赖: src/services/decision_signal_service.py (决策信号)
      src/services/alert_service.py (告警)
      src/notification.py (通知推送)
      src/core/market_review.py (大盘复盘)
      laap-AGI laap/agi/meta_cognitive.py MetaCognitiveMonitor (架构参考)
```

### 5.2 核心数据结构

```python
@dataclass
class NoTradeReason:
    """无交易原因的结构化表示"""
    reason_id: str
    category: str                           # "market_condition" | "signal_quality" | 
                                            # "risk_management" | "technical_issue" | 
                                            # "schedule" | "no_opportunity"
    priority: int                           # 1=主要原因, 2=次要原因
    summary_zh: str                         # 中文摘要（一句话）
    summary_en: str                         # 英文摘要
    detail: str                             # 详细解释（2-3 句自然语言）
    evidence: Dict[str, Any]                # 支撑证据（数值、引用）
    related_signals: List[str] = field(default_factory=list)  # 相关决策信号 ID

@dataclass
class NoTradeReport:
    """无交易日报"""
    report_id: str
    date: str                               # "2026-08-11"
    stock_code: str
    stock_name: str
    has_position: bool                      # 是否持仓
    reasons: List[NoTradeReason] = field(default_factory=list)
    market_context: str = ""                # 当日市场环境概述
    decision_history: str = ""              # 最近决策信号总结
    human_readable: str = ""                # 完整的自然语言解释
    generated_at: datetime = field(default_factory=datetime.now)
```

### 5.3 函数级 API

```python
class WhyNoTradeExplainer:
    """
    无交易原因解释引擎。

    参考 laap-AGI MetaCognitiveMonitor 的认知片段追踪和自动反思机制，
    在交易日结束后分析"为什么没有做出交易决策"，生成自然语言解释并推送。
    """

    # ========== 构造函数 ==========

    def __init__(
        self,
        llm_client: Optional[Any] = None,   # 用于自然语言生成
        db_session_factory: Optional[Any] = None,
    ) -> None:
        """初始化解释引擎。"""

    # ========== 原因分析 ==========

    def analyze_no_trade(
        self, stock_code: str, date: Optional[str] = None
    ) -> NoTradeReport:
        """分析某只股票在指定日期没有交易决策的原因。
        
        分析流程（参考 MetaCognitiveMonitor._perform_reflection）：
        1. 获取当日分析报告（如果有）
        2. 获取最近的活跃决策信号
        3. 获取当日市场环境
        4. 获取系统运行状态（数据源可用性、分析是否完成）
        5. 调用原因分类器
        6. 生成自然语言解释
        """

    def analyze_no_trade_batch(
        self, stock_codes: List[str], date: Optional[str] = None
    ) -> List[NoTradeReport]:
        """批量分析多只股票的无交易原因。"""

    # ========== 原因分类器 ==========

    def _classify_reasons(
        self,
        analysis_result: Optional[Dict],
        active_signals: List[Dict],
        market_context: Dict,
        system_status: Dict,
        has_position: bool,
    ) -> List[NoTradeReason]:
        """分类无交易原因。
        
        原因分类体系：
        
        A. market_condition（市场环境）：
           - 大盘处于下跌趋势，不宜买入
           - 市场波动率过高，观望中
           - 交易量萎缩，流动性不足
           - 非交易日
        
        B. signal_quality（信号质量）：
           - 没有产生 buy/sell 信号（置信度不足）
           - 多个信号冲突，无法确定方向
           - 信号置信度低于阈值（<0.6）
           - 技术指标未形成共振
        
        C. risk_management（风险管理）：
           - 已有持仓达到仓位上限
           - 止损/止盈未触发
           - 单日涨跌幅超限价
           - 在决策冷静期内
        
        D. technical_issue（技术原因）：
           - 数据源异常，分析未完成
           - LLM 调用失败
           - 分析超时
        
        E. schedule（时间原因）：
           - 非交易时段
           - 定时分析尚未执行
        
        F. no_opportunity（无机会）：
           - 关注列表中没有达到买入条件的股票
           - 持仓中没有达到卖出条件的股票
        """

    def _check_market_condition_reasons(
        self, market_context: Dict
    ) -> List[NoTradeReason]:
        """检查市场环境相关原因。"""

    def _check_signal_quality_reasons(
        self, analysis_result: Optional[Dict], active_signals: List[Dict]
    ) -> List[NoTradeReason]:
        """检查信号质量相关原因。"""

    def _check_risk_management_reasons(
        self, has_position: bool, active_signals: List[Dict]
    ) -> List[NoTradeReason]:
        """检查风险管理相关原因。"""

    def _check_technical_reasons(
        self, system_status: Dict
    ) -> List[NoTradeReason]:
        """检查技术原因。"""

    # ========== 自然语言生成 ==========

    def _generate_explanation(self, report: NoTradeReport) -> str:
        """生成自然语言解释文本。
        
        格式参考 MetaCognitiveMonitor.generate_introspection_prompt()：
        
        【{stock_name}（{stock_code}）今日无交易决策】
        
        主要原因：{primary_reason.summary_zh}
        
        详细分析：
        {reason_1.detail}
        {reason_2.detail}
        ...
        
        今日市场环境：{market_context}
        
        当前持仓状态：{position_summary}
        
        明日关注：{next_steps}
        """

    def _generate_with_llm(self, report: NoTradeReport) -> str:
        """使用 LLM 生成更自然的解释文本。
        
        参考 MetaCognitiveMonitor._llm_reflection() 的 prompt 构建方式，
        将结构化原因转换为流畅的自然语言段落。
        """

    def _summarize_decision_history(
        self, stock_code: str, days: int = 7
    ) -> str:
        """总结最近 N 天的决策信号历史。"""

    # ========== 推送 ==========

    def push_explanation(
        self,
        report: NoTradeReport,
        channels: Optional[List[str]] = None,
    ) -> bool:
        """将无交易解释推送到通知渠道。
        
        默认在每日分析完成后，若没有任何交易信号产生，自动推送。
        可通过 channels 参数指定推送渠道。
        """

    # ========== 批量日报 ==========

    def generate_daily_no_trade_summary(
        self, date: Optional[str] = None
    ) -> str:
        """生成当日所有关注股票的无交易总结。
        
        格式：
        【{date} 交易决策总结】
        
        今日共关注 {N} 只股票，其中：
        - 产生交易信号 {X} 只
        - 无交易信号 {Y} 只
        
        [无交易详情]
        ...
        """

    def stats(self) -> Dict[str, Any]:
        """统计信息。"""
```

### 5.4 集成点

```python
# ====== main.py run_full_analysis() 集成 ======

# 在每日分析完成后：
#   if no_trade_signals_generated_today:
#       report = explainer.analyze_no_trade(stock_code)
#       explainer.push_explanation(report)

# ====== 调度任务集成 ======

# 注册为定时任务：每日收盘后 30 分钟执行
# python main.py --explain-no-trade --stocks <watchlist>

# ====== Bot 命令 ======

# /whynotrade <stock_code> → 即时查询无交易原因
```

---

## 六、L4 模块五：延迟自诊断引擎（LatencySelfDiagnosis）

### 6.1 模块定位

```
文件: src/services/latency_diagnosis.py
依赖: src/services/run_diagnostics.py (运行诊断数据)
      src/services/run_flow.py (运行流快照)
      src/services/health_check.py (系统资源历史)
      laap-AGI laap/agi/meta_cognitive.py MetaCognitiveMonitor._analyze_reasoning (分析模式参考)
      laap-AGI laap/agi/self_model.py EmergentSelfModel.self_assess (校准模式参考)
```

### 6.2 核心数据结构

```python
@dataclass
class LatencyBaseline:
    """延迟基准线 — 参考 EmergentSelfModel 的能力追踪"""
    metric_name: str                        # 指标名：api_p95 | data_fetch_p95 | analysis_p95 | llm_p95
    window: str                             # "hourly" | "daily" | "weekly"
    samples: int                            # 样本数
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    std_ms: float
    updated_at: datetime

@dataclass
class LatencyAnomaly:
    """延迟异常"""
    anomaly_id: str
    detected_at: datetime
    metric_name: str
    current_value_ms: float
    baseline_value_ms: float                # 同比基准值
    deviation_pct: float                    # 偏离百分比
    deviation_std: float                    # 偏离标准差倍数
    severity: str                           # "critical" | "warning" | "info"
    duration_seconds: float                 # 异常持续时间

@dataclass
class LatencyDiagnosis:
    """延迟诊断结果 — 参考 MetaCognitiveMonitor 的反思输出"""
    diagnosis_id: str
    generated_at: datetime
    anomalies: List[LatencyAnomaly] = field(default_factory=list)
    root_causes: List[Dict[str, Any]] = field(default_factory=list)
    contributing_factors: List[str] = field(default_factory=list)
    human_readable: str = ""                # 自然语言诊断报告
    recommendations: List[str] = field(default_factory=list)
```

### 6.3 函数级 API

```python
class LatencySelfDiagnosisEngine:
    """
    延迟自诊断引擎。

    参考 laap-AGI MetaCognitiveMonitor 的反思触发、推理分析、偏差检测模式，
    以及 EmergentSelfModel 的能力追踪和校准机制。
    
    核心能力：
    1. 延迟基准线维护与追踪
    2. 延迟异常自动检测
    3. 根因分析与归因
    4. 自然语言诊断报告生成
    """

    # ========== 构造函数 ==========

    def __init__(
        self,
        baseline_db_path: Optional[str] = None,
        llm_client: Optional[Any] = None,
    ) -> None:
        """初始化诊断引擎。"""

    # ========== 基准线管理（参考 EmergentSelfModel.SkillProfile） ==========

    def update_baseline(
        self, metric_name: str, latency_samples: List[float], window: str = "daily"
    ) -> LatencyBaseline:
        """更新延迟基准线。
        
        使用指数加权移动平均（EWMA，α=0.1）更新 P50/P95/P99，
        类似 EmergentSelfModel 对 success_rate 的 EMA 更新方式。
        """

    def get_baseline(
        self, metric_name: str, window: str = "daily"
    ) -> Optional[LatencyBaseline]:
        """获取指定指标的延迟基准线。"""

    def get_all_baselines(self) -> Dict[str, LatencyBaseline]:
        """获取所有指标的延迟基准线。"""

    # ========== 数据采集 ==========

    def collect_latency_metrics(self) -> Dict[str, List[float]]:
        """从运行诊断数据中采集当前延迟指标。
        
        采集维度：
        - api_p95: API 端点的 P95 延迟
        - data_fetch_p95: 数据源获取的 P95 延迟（按数据源细分）
        - analysis_p95: AI 分析阶段的 P95 延迟
        - llm_p95: LLM 调用的 P95 延迟（按模型细分）
        - task_queue_wait_p95: 任务排队等待时间
        - notification_p95: 通知发送延迟
        
        数据来源：
        - run_diagnostics 中的时序数据
        - run_flow 中的执行阶段耗时
        """

    # ========== 异常检测 ==========

    def detect_anomalies(self) -> List[LatencyAnomaly]:
        """检测延迟异常。
        
        对比当前指标值与历史基准线，检测条件（参考 MetaCognitiveMonitor 的阈值触发）：
        1. 当前值 > 基准 P95 × 2.0（严重偏离）
        2. 当前值 > 基准 P95 × 1.5（中度偏离）
        3. 当前值 > 基准 P95 + 3σ（统计异常）
        
        返回按严重性排序的异常列表。
        """

    def _compare_with_baseline(
        self, metric_name: str, current_value_ms: float, baseline: LatencyBaseline
    ) -> Optional[LatencyAnomaly]:
        """单指标对比检测。"""

    # ========== 根因分析（参考 MetaCognitiveMonitor._analyze_reasoning） ==========

    def diagnose_root_causes(
        self, anomaly: LatencyAnomaly
    ) -> List[Dict[str, Any]]:
        """对单个异常进行根因分析。
        
        分析维度：
        1. 数据源侧：对比各数据源的独立延迟，判断是否特定数据源变慢
        2. 网络侧：NTP 偏移是否增大、外部 API 超时率
        3. 系统资源侧：CPU/内存/磁盘是否异常（关联 HealthCheckDaemon 数据）
        4. LLM 侧：模型响应时间是否变慢、是否触发 rate limit
        5. 任务队列侧：是否因任务积压导致排队延迟增加
        6. 代码变更侧：最近是否有配置变更/代码部署（关联 ConfigAutoRollback 快照）
        
        根因类型：
        - external_api_slowdown: 外部数据源响应变慢
        - network_instability: NTP 偏移增大、丢包率上升
        - system_resource_pressure: CPU/内存/磁盘压力导致处理变慢
        - llm_rate_limited: LLM API 被限流
        - task_congestion: 任务队列积压
        - recent_config_change: 最近的配置变更导致
        - unknown: 无法确定
        """

    def _check_data_source_latency(self) -> Dict[str, float]:
        """检查各数据源的独立延迟。"""

    def _check_network_health(self) -> Dict[str, Any]:
        """检查网络健康：NTP 偏移、外部 API 连通性。"""

    def _check_system_resources_at_time(
        self, timestamp: datetime
    ) -> Dict[str, Any]:
        """获取指定时间的系统资源快照。"""

    def _check_llm_latency(self) -> Dict[str, Any]:
        """检查各 LLM 后端的延迟。"""

    def _check_recent_changes(self, lookback_minutes: int = 60) -> List[Dict]:
        """检查最近的配置变更和代码部署。"""

    # ========== 对比分析 ==========

    def compare_with_yesterday(self) -> Dict[str, Any]:
        """与昨天同时段延迟对比。
        
        这是用户明确要求的能力："当前延迟为什么比昨天高"。
        
        返回：
        - 各指标昨日 vs 今日对比
        - 偏离最大的指标排名
        - 最可能的根因假设
        """

    def _time_aligned_comparison(
        self, metric_name: str, today_samples: List[float]
    ) -> Dict[str, Any]:
        """时间对齐比较：今天 vs 昨天同一时刻。"""

    # ========== 诊断报告生成 ==========

    def generate_diagnosis_report(
        self, include_comparison: bool = True
    ) -> LatencyDiagnosis:
        """生成完整的延迟诊断报告。
        
        参考 MetaCognitiveMonitor.get_self_report() 的结构：
        1. 异常摘要
        2. 昨日对比
        3. 根因分析
        4. 自然语言描述
        5. 改进建议
        """

    def _to_natural_language(self, diagnosis: LatencyDiagnosis) -> str:
        """将诊断结果转换为自然语言描述。
        
        格式：
        【延迟诊断报告 — {datetime}】
        
        📊 当前延迟状态：
        - API 响应 P95: {value}ms（昨日同期: {yesterday_value}ms，变化: +{delta}%）
        - ...
        
        ⚠️ 检测到以下异常：
        1. api_p95 延迟异常升高（当前: {current}ms，基准: {baseline}ms，偏离: +{pct}%）
           → 根因: {root_cause}
           → 影响: {impact}
        
        🔍 根因分析：
        {detailed_analysis}
        
        💡 建议：
        {recommendations}
        """

    def _generate_with_llm(self, diagnosis: LatencyDiagnosis) -> str:
        """使用 LLM 生成更流畅的自然语言诊断报告。"""

    # ========== 主动推送 ==========

    def push_diagnosis_if_anomalous(
        self, severity_threshold: str = "warning"
    ) -> bool:
        """当检测到严重异常时，主动推送诊断报告。
        
        在以下时机触发：
        1. 每日分析完成后，如果今日延迟显著高于昨日
        2. 检测到 CRITICAL 延迟异常时
        """

    # ========== 持久化 ==========

    def save_diagnosis(self, diagnosis: LatencyDiagnosis) -> str:
        """保存诊断结果到数据库，返回 diagnosis_id。"""

    def get_diagnosis_history(
        self, metric_name: Optional[str] = None, days: int = 7
    ) -> List[LatencyDiagnosis]:
        """获取历史诊断记录。"""

    def stats(self) -> Dict[str, Any]:
        """统计信息。"""
```

### 6.4 集成点

```python
# ====== run_diagnostics.py 集成 ======

# 在每次运行诊断数据写入时：
#   latency_engine.collect_latency_metrics()
#   latency_engine.update_baseline(metric_name, samples)

# ====== main.py 集成 ======

# 在每日分析完成后：
#   latency_engine.detect_anomalies()
#   latency_engine.push_diagnosis_if_anomalous()

# ====== CLI 命令 ======

# python main.py --latency-diagnosis          → 即时生成延迟诊断报告
# python main.py --latency-compare-yesterday  → 与昨日延迟对比

# ====== API 端点（可选） ======

# GET /api/v1/diagnostics/latency/current    → 当前延迟状态
# GET /api/v1/diagnostics/latency/history    → 历史延迟趋势
# GET /api/v1/diagnostics/latency/compare    → 与昨日对比
```

---

## 七、L4 模块六：任务前状态摘要引擎（TaskContextSummarizer）

### 7.1 模块定位

```
文件: src/services/task_context_summarizer.py
依赖: src/services/daily_market_context.py (每日市场上下文)
      src/services/run_diagnostics.py (系统状态)
      src/services/health_check.py (健康状态)
      laap-AGI laap/agi/meta_cognitive.py generate_introspection_prompt (格式参考)
      laap-AGI laap/agi/self_model.py reflection() (叙事风格参考)
```

### 7.2 核心数据结构

```python
@dataclass
class SystemContextSummary:
    """系统状态上下文摘要"""
    summary_id: str
    generated_at: datetime
    # 系统状态
    uptime_hours: float
    modules_healthy: int
    modules_total: int
    modules_degraded: List[str]
    # 数据状态
    data_sources_available: int
    data_sources_total: int
    data_source_failures: Dict[str, float]  # 数据源名 → 失败率
    ntp_synced: bool
    # 任务状态
    active_tasks: int
    pending_tasks: int
    task_queue_depth: int
    last_analysis_time: Optional[datetime]
    # 市场状态
    trading_day: bool
    market_phase: str                       # "pre_market" | "trading" | "post_market" | "closed"
    # 资源状态
    memory_usage_pct: float
    cpu_usage_pct: float
    disk_usage_pct: float
    # 降级状态
    degradation_level: str
    active_degradations: List[str]
    # 自然语言摘要
    human_readable: str = ""
    attention_items: List[str] = field(default_factory=list)

@dataclass
class TaskPreflightResult:
    """任务前置检查结果"""
    ready: bool
    warnings: List[str]                     # 注意事项
    blockers: List[str]                     # 阻止任务执行的硬性条件
    context_summary: SystemContextSummary
    suggested_actions: List[str] = field(default_factory=list)
```

### 7.3 函数级 API

```python
class TaskContextSummarizer:
    """
    任务前状态摘要引擎。

    参考 laap-AGI MetaCognitiveMonitor.generate_introspection_prompt() 的格式，
    以及 EmergentSelfModel.reflection() 的叙事风格，
    在每次新任务开始前生成系统当前状态的完整自然语言摘要。
    """

    # ========== 构造函数 ==========

    def __init__(
        self,
        llm_client: Optional[Any] = None,
    ) -> None:
        """初始化摘要引擎。"""

    # ========== 数据采集 ==========

    def collect_system_status(self) -> SystemContextSummary:
        """采集当前系统完整状态。
        
        数据来源：
        - HealthCheckDaemon.stats() → 模块健康、系统资源
        - 数据源 fetcher._daily_source_health → 数据源可用性
        - TaskQueue.get_task_stats() → 任务队列
        - ExchangeClock.is_synced() → 时间同步
        - TradingCalendar → 交易日判断
        - GracefulDegradationEngine.get_active_degradations() → 降级状态
        - ModuleAutoRestarter.get_all_status() → 模块重启状态
        """

    def _collect_module_health(self) -> Tuple[int, int, List[str]]:
        """采集模块健康状态。"""

    def _collect_data_source_status(self) -> Tuple[int, int, Dict[str, float]]:
        """采集数据源状态。"""

    def _collect_task_status(self) -> Tuple[int, int, int]:
        """采集任务队列状态。"""

    def _collect_market_phase(self) -> Tuple[bool, str]:
        """采集当前市场阶段。"""

    def _collect_resource_status(self) -> Tuple[float, float, float]:
        """采集系统资源状态。"""

    # ========== 前置检查 ==========

    def preflight_check(
        self, task_type: str = "stock_analysis"
    ) -> TaskPreflightResult:
        """任务执行前的完整前置检查。
        
        根据任务类型检查必要条件：
        - stock_analysis: NTP 同步、至少 1 个数据源可用、交易日
        - market_review: 同上 + 大盘复盘锁未占用
        - backtest: 历史数据可用
        - notification: 至少 1 个通知渠道配置
        
        返回 TaskPreflightResult 含 ready/warnings/blockers。
        """

    def _check_task_prerequisites(self, task_type: str) -> Tuple[List[str], List[str]]:
        """检查特定任务类型的前置条件。"""

    # ========== 自然语言生成 ==========

    def generate_summary(
        self, context: SystemContextSummary, audience: str = "human"
    ) -> str:
        """生成自然语言状态摘要。
        
        格式参考 MetaCognitiveMonitor.generate_introspection_prompt()：
        
        【系统状态摘要 — {datetime}】
        
        🟢 系统运行正常，已持续运行 {uptime_hours} 小时。
        
        📡 数据源：{available}/{total} 可用
           - 异常：{failure_details}
        
        📊 任务状态：
           - 活跃任务：{active}，等待中：{pending}
           - 最近分析完成时间：{last_analysis}
        
        🏥 模块健康：
           - {healthy}/{total} 模块正常
           {degraded_details}
        
        📈 市场状态：
           - 今日为{trading_or_not}交易日
           - 当前阶段：{market_phase}
        
        💻 系统资源：
           - 内存 {memory}%，CPU {cpu}%，磁盘 {disk}%
        
        ⚠️ 需要关注：
           {attention_items}
        """

    def generate_task_brief(
        self,
        task_type: str,
        task_params: Dict[str, Any],
    ) -> str:
        """生成任务执行前的简要说明。
        
        例如：
        "即将开始 600519（贵州茅台）的日线分析。
         当前系统状态良好，8/9 数据源可用。
         今日为交易日，东财数据源近期失败率较高（12%），
         将以腾讯财经为主数据源。
         预计分析耗时约 45 秒。"
        """

    def _generate_with_llm(self, context: SystemContextSummary) -> str:
        """使用 LLM 生成更自然的摘要文本。
        
        参考 EmergentSelfModel.reflection() 的第一人称叙事风格。
        """

    # ========== 定时/事件触发 ==========

    def on_task_start(self, task_type: str, task_params: Dict) -> TaskPreflightResult:
        """任务开始时调用：执行前置检查 + 生成状态摘要。
        
        如果 preflight_check 返回 ready=False，记录 warning 但不阻止任务执行
        （除非 blocker 是硬性条件如"非交易日"）。
        """

    def on_task_complete(self, task_type: str, result: Dict) -> None:
        """任务完成时调用：更新状态追踪。"""

    # ========== 持久化 ==========

    def save_summary(self, summary: SystemContextSummary) -> str:
        """保存摘要到数据库。"""

    def get_recent_summaries(self, limit: int = 10) -> List[SystemContextSummary]:
        """获取最近的系统状态摘要。"""

    def stats(self) -> Dict[str, Any]:
        """统计信息。"""
```

### 7.4 集成点

```python
# ====== main.py run_full_analysis() 集成 ======

# 在 run_full_analysis() 开始时：
#   preflight = summarizer.on_task_start("stock_analysis", task_params)
#   logger.info(preflight.context_summary.human_readable)
#   if preflight.blockers:
#       logger.warning(f"Task blocked: {preflight.blockers}")
#       return

# ====== API 端点 ======

# GET /api/v1/system/status-summary  → 当前系统状态摘要
# GET /api/v1/system/preflight/{task_type} → 任务前置检查

# ====== Bot 命令 ======

# /status → 即时获取系统状态摘要
# /preflight <task> → 任务前置检查
```

---

## 八、依赖关系与执行顺序

```
                        ┌────────────────────────┐
                        │   HealthCheckDaemon     │ (已有)
                        │   (健康检查守护进程)      │
                        └───────────┬────────────┘
                                    │ 提供健康指标
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
        ┌─────────────────┐ ┌───────────────┐ ┌──────────────────┐
        │ ModuleAuto-      │ │ GracefulDegra-│ │ ConfigAutoRoll-  │
        │ Restarter (L3-1) │ │ dation (L3-3) │ │ back (L3-2)     │
        │ 模块自动重启      │ │ 优雅降级      │ │ 配置自动回滚      │
        └────────┬────────┘ └───────┬───────┘ └────────┬─────────┘
                 │                  │                   │
                 │    ┌─────────────┴──────────┐        │
                 │    │  RunDiagnostics        │ (已有) │
                 │    │  (运行诊断数据)         │        │
                 │    └─────────────┬──────────┘        │
                 │                  │                   │
                 ▼                  ▼                   ▼
        ┌─────────────────────────────────────────────────────┐
        │              L4 元认知层                              │
        ├─────────────────┬─────────────────┬─────────────────┤
        │ WhyNoTrade-     │ LatencySelf-    │ TaskContext-    │
        │ Explainer(L4-1) │ Diagnosis(L4-2) │ Summarizer(L4-3)│
        │ 无交易原因解释   │ 延迟自诊断      │ 任务前状态摘要    │
        └────────┬────────┴────────┬────────┴────────┬────────┘
                 │                 │                 │
                 └─────────────────┼─────────────────┘
                                   │
                                   ▼
                        ┌────────────────────┐
                        │  NotificationService│ (已有)
                        │  (多渠道路由推送)    │
                        └────────────────────┘
```

**执行顺序建议：**

1. **第一轮（基础层，< 3 天）**：ModuleAutoRestarter + GracefulDegradationEngine
   - 依赖最少，直接复用 HealthCheckDaemon
   - 实现后立即提升系统自愈能力

2. **第二轮（保护层，< 3 天）**：ConfigAutoRollback
   - 依赖 ConfigManager（已有）
   - 为配置变更提供安全网

3. **第三轮（认知层，< 5 天）**：TaskContextSummarizer + WhyNoTradeExplainer
   - 依赖前两层提供的数据
   - 直接面向用户价值

4. **第四轮（深层认知，< 5 天）**：LatencySelfDiagnosisEngine
   - 依赖 RunDiagnostics 的历史数据积累
   - 需要足够基线数据才能有效

---

## 九、关键设计决策

### 9.1 为什么不直接移植 laap-AGI 代码？

| 原因 | 说明 |
|------|------|
| 不同项目范式 | laap-AGI 是通用 AGI 框架，daily_stock_analysis 是领域专用系统 |
| 不同故障模型 | laap-AGI 关注代码 Bug 自修复，daily_stock_analysis 关注运行时可恢复故障 |
| 不同元认知粒度 | laap-AGI 是"认知片段"级追踪，daily_stock_analysis 需要"交易日决策"级追踪 |
| Python 版本 | 两项目可能使用不同 Python 版本和依赖 |

**策略**：参考架构设计和数据流模式，用 daily_stock_analysis 的现有模块进行适配实现。

### 9.2 为什么所有 L4 模块都有 `_generate_with_llm()` 方法？

LLM 用于将结构化数据转换为流畅的自然语言。当 LLM 不可用时（离线、降级），回退到模板化文本生成。这与现有 `src/services/analysis_context_builder.py` 中 LLM 调用的 fallback 模式一致。

### 9.3 为什么降级引擎不直接改写全局配置？

在运行时直接修改 `.env` 会产生不必要的持久化副作用，且在进程重启后可能残留降级状态。降级引擎通过在内存中维护降级状态，并通过模块接口（`set_degradation_mode()`）控制功能开关，确保重启后自动恢复到正常状态。

### 9.4 配置回滚 vs 系统重启

配置回滚只回滚 `.env` 配置文件本身，不重启进程。大多数配置变更在 daily_stock_analysis 中通过重新读取 `.env` 生效（如数据源优先级）。需要重启才能生效的变更（如端口号）由 ModuleAutoRestarter 配合处理。

---

## 十、验证矩阵

| 模块 | 单元测试 | 集成测试 | 手动验证 |
|------|---------|---------|---------|
| ModuleAutoRestarter | 重启决策逻辑、冷却/限流、依赖链 | 与 HealthCheckDaemon 联动、进程重启端到端 | 手动 kill 进程验证自动恢复 |
| ConfigAutoRollback | 快照创建/回滚、三层恢复、diff | 与 ConfigManager 联动、回归检测准确性 | 手动修改配置触发回归 |
| GracefulDegradationEngine | 压力评估、规则匹配、恢复逻辑 | 资源压力模拟、功能实际降级/恢复 | 压测触发多级降级 |
| WhyNoTradeExplainer | 原因分类器、模板生成 | 与决策信号服务联动、推送端到端 | 实际无交易日验证解释合理性 |
| LatencySelfDiagnosisEngine | 异常检测、根因分析、基线更新 | 与 RunDiagnostics 联动、延迟注入 | 模拟各种延迟场景验证根因准确率 |
| TaskContextSummarizer | 状态采集、前置检查、摘要生成 | 各数据源采集集成、任务启动联动 | 每日分析前验证摘要准确性 |

---

## 十一、风险与回滚

### 风险点

1. **ModuleAutoRestarter**：进程级重启在 Windows 上依赖 `wmic`/`taskkill`，非 Windows 环境需适配。
2. **ConfigAutoRollback**：回归检测的假阳性可能触发不必要的回滚。建议 `auto_rollback_eligible` 默认关闭，仅 CRITICAL 信号且人工确认后执行。
3. **GracefulDegradationEngine**：降级过于激进可能导致分析结果质量下降。建议 ELEVATED 级别降级仅影响非用户可见功能。
4. **LatencySelfDiagnosisEngine**：基线数据不足时诊断准确率低。前 7 天只收集数据不诊断。
5. **跨平台兼容**：部分模块（ModuleAutoRestarter 的进程管理）在 Linux/Docker 环境需要不同实现路径。

### 回滚方式

- 所有新模块通过配置开关控制启用/禁用（`.env` 中 `ENABLE_MODULE_RESTART`、`ENABLE_CONFIG_ROLLBACK` 等）
- 关闭对应开关即可完全禁用该模块
- 各模块的持久化状态独立存储，删除对应状态文件即可重置
