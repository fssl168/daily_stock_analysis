# L1/L2 Retrospective 整改修复计划

**生成日期**: 2026-08-11
**状态**: ✅ 已执行完毕
**前置条件**: 已完成 `docs/L1_L2_RETROSPECTIVE.md` 的全量模块归属审计

---

## 一、整改范围总览

本次整改覆盖三类问题：

| 类别 | 数量 | 严重程度 | 说明 |
|---|---|---|---|
| **A. 模块归属错误** | 11 项 | 高 | 模块已列在 L2 但源码不含业务语义，应归 L1 |
| **B. 整层遗漏** | 3 项 | 严重 | `src/agent/`（17 文件）、`src/llm/`（11 文件）、`src/notification_sender/`（14 文件）完全未出现在 retrospective 中 |
| **C. L2 关键设计特征需重审** | 2 项 | 中 | L2 故障场景→L3、L2 认知偏差→L4 的描述需与实战对齐 |
| **D. 边界判定标准需修订** | 1 项 | 中 | 现有的 L1 vs L2 边界分类表缺少对"LLM 基础设施"、"股票域名基础设施"的细分 |

---

## 二、A 类：模块归属偏差清单与边界复核

### 复核方法

对每个可疑模块执行以下五个维度的判定：

| 判定维度 | L1 特征 | L2 特征 |
|---|---|---|
| **D1: 业务语义** | 零股票分析判断（不含买卖/技术指标/LLM分析） | 包含。LLM prompt、技术指标计算、买卖建议 |
| **D2: 数据方向** | 纯数据供给 / 通路 / 基础设施管理 | 数据消费与分析（"数据意味着什么"） |
| **D3: 失败影响** | 静默降级，不影响终端用户可见的结果 | 失败导致报告缺失、通知延迟、分析错误 |
| **D4: 对外接口性质** | Repo / Schema / Provider / 内部工具 | API / CLI / Web / 通知 / 用户可见 |
| **D5: 可独立测试** | 是，mock 上层即可 | 是，mock L1 数据源即可 |

**判定规则**: 如果全部 5 个维度均符合 L1 特征，则归 L1。如果 D1 + D2 均符合 L1 特征且 D3 和 D4 中至少一项符合 L1 特征，则归 L1。

---

### A-1. TaskQueue (`src/services/task_queue.py`)

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (调度与任务基础设施)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | 管理任务生命周期（pending→running→done），无股票分析语义。防重复提交、SSE 广播——这些是通用基础设施模式 |
| D2 | **L1** | 数据供给：任务队列是 Pipeline 的"任务供给侧"，不消费分析结果 |
| D3 | **L1** | 任务队列故障表现为任务调度延迟而非直接的分析错误 |
| D4 | **L1** | 内部基础设施，API 层通过它调度任务，但 TaskQueue 本身没有用户可见接口 |
| D5 | L1 | mock job 函數即可独立测试 |

**复核结论**: 应归 L1。TaskQueue 是 Pipeline 的"任务供给侧基础设施"，类比 Celery/RQ 等任务队列——分析师用 Celery 时不会说"Celery 是业务层"。当前已在 L1 表中（第 94 行），但 **L2 表中仍有残留**（第 192 行），应删除残留。

**修复**: 从 L2 业务服务层表中删除 TaskQueue 行。

---

### A-2. RuntimeScheduler (`src/services/runtime_scheduler.py`)

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (调度与任务基础设施)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | 全局分析锁 + ENV 开关控制。不包含任何股票分析判断 |
| D2 | **L1** | 调度基础设施——控制"什么时候跑分析"而非"跑出来什么结果" |
| D3 | **L1** | 调度失败影响任务启动时序，但静默降级（跳过已锁定时段） |
| D4 | **L1** | 内部基础设施，API/Web 通过它集成调度，但本身无用户可见接口 |
| D5 | L1 | mock 配置即可独立测试 |

**复核结论**: 应归 L1。当前已在 L1 表中（第 95 行），但 **L2 表中仍有残留**（第 193 行），应删除残留。

**修复**: 从 L2 业务服务层表中删除 RuntimeScheduler 行。

---

### A-3. SystemConfigService (`src/services/system_config_service.py`)

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (持久化与配置)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | `.env` 编辑/校验/导入导出 API。约 5000 行代码，核心逻辑是配置字段校验、LLM 频道连接测试、通知渠道测试——全部是配置基础设施。源码不含任何股票分析语义 |
| D2 | **L1** | 配置管理基础设施——管理的是"系统怎么运行"而非"股票怎么样" |
| D3 | **L1** | 配置错误会导致分析使用错误参数，但这是配置基础设施自身的校验职责。配置服务本身失败不影响已有配置的运行时效果 |
| D4 | **L1** | 面向 Web UI Settings 页面 —— 但这不是用户面向的"分析结果"，而是系统管理的"运维工具" |
| D5 | L1 | mock `.env` 文件即可独立测试 |

**复核结论**: 应归 L1。当前已在 L1 表中（第 80 行），但 **L2 表中仍有残留**（第 208 行），应删除残留。

**修复**: 从 L2 业务服务层表中删除 SystemConfigService 行。

---

### A-4. AgentModelService (`src/services/agent_model_service.py`)

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (LLM 基础设施)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | "Helpers for exposing configured Agent model deployments" —— 暴露已配置的模型部署信息。不判断买卖，不生成分析 |
| D2 | **L1** | LLM 模型部署元数据——这是"分析引擎用什么模型"的配置查询，不是分析本身 |
| D3 | **L1** | 此模块故障不影响已配置模型的分析流程，仅影响模型列表展示 |
| D4 | **L1** | 内部工具模块，无用户可见接口 |
| D5 | L1 | mock 配置即可独立测试 |

**复核结论**: 应归 L1。这是 LLM 基础设施层的元数据查询工具，类比"显卡驱动查询工具"——它告诉你有什么显卡可用，但不会替你渲染 3D 画面。

**修复**: 从 L2 业务服务层表中删除，在 L1 新增"LLM 基础设施"子表并纳入。

---

### A-5. GenerationBackendStatusService (`src/services/generation_backend_status_service.py`)

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (LLM 基础设施 / 诊断与可观测性)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | "Read-only diagnostics for configured generation backends" —— LLM 后端纯诊断 |
| D2 | **L1** | 诊断基础设施，回答"LLM 后端通不通"而非"股票该不该买" |
| D3 | **L1** | 诊断模块失败不影响分析结果，仅影响 Settings 页面的后端状态展示 |
| D4 | **L1** | 面向 Settings 页面的内部工具 |
| D5 | L1 | mock 配置即可独立测试 |

**复核结论**: 应归 L1。与 `run_diagnostics.py` 同类——都是诊断基础设施。

**修复**: 从 L2 表中删除，在 L1 诊断子表或新增 LLM 基础设施子表中纳入。

---

### A-6. StockIndexRemoteService (`src/services/stock_index_remote_service.py`)

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (数据获取与容错)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | "Best-effort remote cache for the generated stock autocomplete index" —— 远程股票索引的缓存刷新。含 TTL 管理、熔断抑制、原子写入、校验——纯数据缓存基础设施模式 |
| D2 | **L1** | 数据供给——提供股票代码→名称的自动补全数据。不分析数据 |
| D3 | **L1** | 设计为 best-effort：3 次连续失败后 suppression，使用本地缓存 fallback。完美符合 L1 的"静默降级"特征 |
| D4 | **L1** | 内部数据缓存层 |
| D5 | L1 | mock HTTP 响应即可独立测试 |

**复核结论**: 应归 L1。此模块的设计模式（best-effort remote refresh + local cache fallback + circuit breaker）与 L1 的 `data_provider/` 层完全同构。

**修复**: 从 L2 表中删除，移至 L1 的"数据获取与容错"子表。

---

### A-7. StockCodeUtils, MarketSymbolUtils, NameToCodeResolver, StockListParser, ImportParser

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (数据合约与访问抽象 → 新增"股票域名基础设施"子表)

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1** | 这些模块的核心操作是：代码格式标准化（`600519.SH`↔`600519`）、市场前缀映射（`HK`↔`.HK`）、名称→代码查找、导入文本解析。它们处理"标识符转换"——类比 DNS 解析或 URI 标准化 |
| D2 | **L1** | 纯数据标准化通路——把用户的输入标准化为系统内部表示。不消费分析结果 |
| D3 | **L1** | 解析失败表现为"无法识别股票"（静默返回 None / 空），不影响已成功解析的股票分析 |
| D4 | **L1** | 内部工具库，被 Web UI 导入和 API 调用所使用，但本身无用户可见接口 |
| D5 | L1 | 纯函数为主，易于独立测试 |

**复核结论**: 应归 L1。关键论证：这些模块虽然包含"市场"域名知识（如交易所前缀映射），但这属于 **L1 级域名建模**，而非 L2 级业务分析。类比：一个电商系统中，"SKU 编码规则"是 L1 基础设施（数据标准化），"SKU 定价策略"是 L2 业务逻辑。股票代码格式规则 = SKU 编码规则。

**修复**: 从 L2 表中删除这 5 个模块，在 L1 新增"股票域名基础设施"子表并纳入。

---

### A-8. RunDiagnostics, RunFlow, NotificationDiagnostics

**当前归属**: L2 (列在业务服务层)
**建议归属**: L1 (诊断与可观测性)

复核已在之前会话完成，结论不变：这三个模块是纯诊断基础设施。当前已在 L1 表中（第 113-121 行），但 **L2 表中仍有残留**（第 198-199 行、第 210 行），应删除残留。

**修复**: 从 L2 业务服务层表中删除 RunDiagnostics、RunFlow、NotificationDiagnostics 行。

---

### A-9. L1 整层遗漏模块

以下模块在 retrospective 中完全缺失，应纳入 L1：

| 模块 | 路径 | 建议归属 L1 子表 |
|---|---|---|
| LatencyTracker | `src/utils/latency_tracker.py` | 时钟与延迟监控（当前已在 L1 表中，第 87 行。核查通过） |
| 股票映射数据 | `src/data/stock_mapping.py`, `src/data/stock_index_loader.py` | 数据合约与访问抽象（当前已在 L1 表中，第 103 行。核查通过） |
| 定时调度器 | `src/scheduler.py` | 调度与任务基础设施（当前已在 L1 表中，第 93 行。核查通过） |
| 通知契约 | `src/notification_contracts.py` | 通知基础设施（当前已在 L1 表中，第 109 行。核查通过） |
| 通知路由 | `src/notification_routing.py` | 通知基础设施（当前已在 L1 表中，第 110 行。核查通过） |
| 通知噪音控制 | `src/notification_noise.py` | 通知基础设施（当前已在 L1 表中，第 111 行。核查通过） |

**复核结论**: 这 6 个模块在上一轮编辑中已补入 L1 表。但它们在文档初版中完全缺失的情况仍应记录为"已修复的遗漏"，以便自查。

---

### A-10. HealthCheckDaemon 归属复核

**当前归属**: L2 (L2→L3 的桥梁)
**复核结论**: 维持 L2 归属，不修改。

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L1-like** | 纯检测（detect），不含修复——这是正确的 |
| D2 | **L2-like** | 它的检测目标全部是 L2 范畴的健康指标：任务队列状态、分析 pipeline 健康度 |
| D3 | **L2-like** | HealthCheckDaemon 异常会触发 L3 的修复链，影响面是用户可见的 |
| D4 | N/A | 无独立用户接口 |
| D5 | L1 | 可独立测试 |

**关键论证**: HealthCheckDaemon 虽然在语义上"不做股票分析"（类似 L1），但它是 L2 运行时的"内嵌哨兵"——它的检测对象、检测指标和触发条件全部绑定到 L2 的执行上下文。一个不运行 L2 分析流程的系统不需要 HealthCheckDaemon。因此它属于 L2 而非 L1。

---

## 二-B、B 类：整层遗漏

### B-1. `src/agent/` 子系统（17 文件）

**状态**: 完全不在 retrospective 中
**建议归属**: L2

核心文件：`orchestrator.py`（Agent 编排器——协调多个 LLM agent 完成分析任务）、`executor.py`、`research.py`、`factory.py`、`runner.py`、`stock_scope.py`、`portfolio_manager_agent.py`、`memory.py`、`conversation.py`、`events.py`、`chat_context.py`、`litellm_route_resolution.py`、`llm_adapter.py`、`protocols.py`、`provider_trace.py`、`stream_events.py`

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | **L2** | Agent 子系统的核心职责是编排 LLM agent 完成股票分析——包含分析策略选择、工具调用、研究流程。这是 L2 的"智能分析引擎" |
| D2 | **L2** | Agent 消费 L1 数据（通过 tools/data_tools.py），产出分析结论 |
| D3 | **L2** | Agent 失败导致分析结果缺失或质量下降，用户直接可见 |
| D4 | **L2** | Agent 编排器的输出最终以分析报告形式呈现给用户 |

**遗漏原因分析**: Agent 子系统位于 `src/agent/` 而非 `src/services/`，按目录扫描时容易遗漏。

---

### B-2. `src/llm/` 子系统（11 文件）

**状态**: 完全不在 retrospective 中
**建议归属**: 部分 L1 / 部分 L2

核心文件：`backend_factory.py`, `backend_registry.py`, `errors.py`, `generation_backend.py`, `generation_params.py`, `hermes.py`, `litellm_backend.py`, `local_cli_backend.py`, `provider_cache.py`, `usage.py`

**分层分析**:

| 文件 | 建议归属 | 理由 |
|---|---|---|
| `backend_factory.py` | **L1** | LLM 后端工厂——创建后端实例，纯基础设施 |
| `backend_registry.py` | **L1** | 后端注册中心——管理可用后端列表 |
| `errors.py` | **L1** | LLM 错误类型定义——纯类型基础设施 |
| `generation_backend.py` | **L1** | 生成后端抽象基类——接口定义，纯基础设施 |
| `generation_params.py` | **L1** | 生成参数（temperature, max_tokens 等）——配置基础设施 |
| `hermes.py` | **L1** | Hermes 专用后端适配——数据源适配（类比 `data_provider/` 层的接口适配器） |
| `litellm_backend.py` | **L1** | LiteLLM 后端适配——类比 `data_provider/` 层的 `akshare_fetcher.py` |
| `local_cli_backend.py` | **L1** | 本地 CLI 后端适配——同上 |
| `provider_cache.py` | **L1** | Provider 缓存——纯缓存基础设施 |
| `usage.py` | **L1** | LLM 用量追踪——可观测性基础设施（类比 `LatencyTracker`） |

**复核结论**: 整个 `src/llm/` 子系统应归 **L1**。核心论证：`src/llm/` 对 `src/agent/` 的关系，在架构上等同于 `data_provider/` 对 `Pipeline` 的关系——都是"供给层对消费层"。`src/llm/` 提供 LLM 后端的接入、路由、参数管理、用量追踪能力，`src/agent/` 消费这些能力来完成实际的股票分析。

打个比方：`src/llm/` 是"电网"（基础设施），`src/agent/` 是"用电设备"（业务）。电的传输协议、电压标准、变压器、电表是基础设施问题；电器具体做什么是业务问题。

---

### B-3. `src/notification_sender/` 子系统（14 文件）

**状态**: 完全不在 retrospective 中
**建议归属**: L2

核心文件：`astrbot/`, `custom_webhook/`, `dingtalk/`, `discord/`, `email/`, `feishu/`, `gotify/`, `ntfy/`, `pushover/`, `pushplus/`, `serverchan3/`, `slack/`, `telegram/`, `wechat/`

| 维度 | 判定 | 证据 |
|---|---|---|
| D1 | N/A (不含分析语义) | 通知发送器不含股票分析语义——但它们**承载**分析结果 |
| D2 | **L2** | 通知发送器消费分析产出的报告内容，格式化为渠道特定的消息格式 |
| D3 | **L2** | 通知发送失败直接影响用户能否收到分析报告 |
| D4 | **L2** | 通知是用户可见的最终交付物——这是用户面向的接口 |

**复核结论**: 应归 L2。关键区分：`src/notification_contracts.py` / `notification_routing.py` / `notification_noise.py` 是**通知基础设施**（L1）——它们管理的是"哪些渠道可用、怎么路由、何时不发"。`src/notification_sender/` 是**通知执行**（L2）——它们实际发送分析报告给用户。

**建议命名区分**:
- L1: "通知基础设施"（notification contracts + routing + noise control）——已纳入
- L2: "通知发送器"（notification senders）——待纳入

---

## 二-C、C 类：L2 关键设计特征重审

用户要求重审 L2 文档中的两个关键段落：
1. "为什么 L2 需要 L3"（L2 故障场景）
2. "为什么 L2 需要 L4"（L2 认知偏差）

### C-1. "为什么 L2 需要 L3" 重审

**当前文档内容**:

> L2 的模块在以下场景下会出故障：
> - Pipeline 中的某个步骤因网络/API 问题抛异常 → 需要 L3-1 (ModuleAutoRestarter)
> - 配置文件被错误修改导致分析流程崩溃 → 需要 L3-2 (ConfigAutoRollback)
> - 系统压力过大（CPU/内存）导致分析延迟 → 需要 L3-3 (GracefulDegradationEngine)
> - 代码缺陷导致特定股票分析失败 → 需要 L3-4 (CodeAwareRepairAgent)

**重审结论**: 这段描述总体正确，但有两个不足：

1. **缺少 Agent 子系统的故障场景**: Agent 子系统的工具调用链（tool-call loop）是 L2 最脆弱的部分之一——LLM 幻觉导致 tool 参数错误、tool 返回超大数据集导致内存压力、agent 陷入无限 tool 调用的循环。这些故障场景与 Pipeline 的故障不同——Pipeline 是线性流程，Agent 是有状态循环——应单独列出。

2. **L3 模块的 Phase 编号过时**: 文中使用 L3-1 到 L3-4 的编号。实际 L3 的四个 Phase 是：
   - Phase 1: SystemEventBus（事件基础设施）
   - Phase 2: SelfHealingAction（修复基类框架）
   - Phase 3: RepairEffectivenessLog（修复效果日志）
   - Phase 4: CodeAwareRepairAgent（代码级自修复）
   
   ModuleAutoRestarter / ConfigAutoRollback / GracefulDegradationEngine 不是 Phase，而是 L3 的三个**修复策略模块**——它们都实现 `SelfHealingAction` 基类（Phase 2 产出）。

**建议修改**:
- 补充 Agent 子系统的故障场景
- 将 "L3-1/2/3/4" 改为 "L3 修复策略模块"，并说明它们基于 Phase 2 的 `SelfHealingAction` 基类

### C-2. "为什么 L2 需要 L4" 重审

**当前文档内容**:

> L2 的 LLM 分析流程可能产生：
> - 确认偏差（confirmation bias）：LLM 倾向于确认已有观点
> - 思维循环（circularity）：同一分析模式反复出现
> - 分析质量漂移：随着时间推移分析深度逐渐下降
>
> 这些不是"故障"而是"认知偏差"，不属于 L3 的修复范畴，而是 L4 (MetaCognitiveEngine) 的检测和反思范畴。

**重审结论**: 这段描述概念上正确，但有三个不足：

1. **缺少 Agent 子系统的认知偏差**: Agent 工具调用链引入了 L4 特有的新偏差类型——"工具偏好偏差"（agent 倾向于使用已学会的工具而忽略新工具）、"研究深度衰减"（agent 在并行任务中缩小搜索范围以降低延迟）。

2. **缺少偏差→L4 检测→L2 改善的闭环描述**: 当前文档只说了"L4 检测"，没说明检测结果如何反馈给 L2——是通过 `SystemEventBus` 推送 `bias_detected` 事件后，L4 生成内省报告，人工或自动调整 L2 的 Agent 策略/Prompt 参数。

3. **遗漏了与 Agent 子系统的集成关系**: 现在 L2 包含完整的 Agent 子系统后，L4 与 L2 的关系从"监控分析偏差"扩展为"监控分析偏差 + 监控 Agent 行为偏差"。

**建议修改**:
- 扩充 L4 检测的偏差类型，纳入 Agent 特有偏差
- 补充闭环反馈路径描述

---

## 三、整改执行方案

### 执行优先级

| 优先级 | 阶段 | 内容 | 预计改动量 |
|---|---|---|---|
| P0 | 第一阶段 | L2 表删除残留（A 类：从 L2 表清除 11 个已移走的模块） | ~20 行删除 |
| P0 | 第一阶段 | 新增 B 类整层遗漏（agent / llm / notification_sender） | ~50 行新增 |
| P1 | 第二阶段 | L1 表补充（新增 LLM 基础设施 + 股票域名基础设施子表） | ~30 行新增 |
| P1 | 第二阶段 | C 类 L2 特征重审更新 | ~20 行修改 |
| P2 | 第三阶段 | D 类边界判定标准修订 | ~15 行新增 |
| P2 | 第三阶段 | 全景图更新、交叉引用修正 | ~10 行调整 |
| P3 | 第四阶段 | CHANGELOG 更新 + 自审 | ~5 行 |

### 第一阶段（P0）：修复高严重度问题

#### 步骤 1: 从 L2 业务服务层表中清除已移走的模块

**位置**: `docs/L1_L2_RETROSPECTIVE.md` 第 162-210 行，"业务服务层（L2 范畴）" 表

**操作**: 从表中删除以下 11 行：

```
| TaskQueue | ... (第 192 行)
| RuntimeScheduler | ... (第 193 行)
| RunDiagnostics | ... (第 198 行)
| RunFlow | ... (第 199 行)
| AgentModelService | ... (第 201 行)
| GenerationBackendStatusService | ... (第 202 行)
| NameToCodeResolver | ... (第 203 行)
| StockCodeUtils | ... (第 204 行)
| StockIndexRemoteService | ... (第 205 行)
| StockListParser | ... (第 206 行)
| MarketSymbolUtils | ... (第 207 行)
| SystemConfigService | ... (第 208 行)
| ImportParser | ... (第 209 行)
| NotificationDiagnostics | ... (第 210 行)
```

共删除 14 行。

#### 步骤 2: 新增 Agent 子系统子表

**位置**: 在 L2 模块清单中，"核心流水线与入口" 子表之后，新增 "Agent 子系统" 子表

**内容**:

```markdown
#### Agent 子系统（LLM 驱动的智能分析引擎）

| 模块 | 路径 | 职责 |
|---|---|---|
| AgentOrchestrator | `src/agent/orchestrator.py` | 多 Agent 编排——协调研究 Agent、分析 Agent 完成分析任务 |
| AgentExecutor | `src/agent/executor.py` | Agent 执行器——管理 tool-call 生命周期 |
| AgentRunner | `src/agent/runner.py` | Agent 运行入口——任务接收→Agent 分配→结果收集 |
| ResearchAgent | `src/agent/research.py` | 研究 Agent——新闻检索、基本面查询、技术指标计算 |
| PortfolioManagerAgent | `src/agent/portfolio_manager_agent.py` | 投资组合管理 Agent |
| AgentMemory | `src/agent/memory.py` | Agent 会话记忆——跨轮次上下文保持 |
| AgentFactory | `src/agent/factory.py` | Agent 工厂——按任务类型创建对应 Agent |
| AgentEvents | `src/agent/events.py` | Agent 事件定义——tool_call / tool_result / completion 等 |
| AgentProtocols | `src/agent/protocols.py` | Agent 协议定义——输入/输出 Schema |
| LLMAdapter | `src/agent/llm_adapter.py` | LLM 适配层——将 LLM 响应标准化为 Agent 协议 |
| LiteLLMRouteResolution | `src/agent/litellm_route_resolution.py` | LiteLLM 路由解析 |
| StockScope | `src/agent/stock_scope.py` | 股票分析范围定义 |
| ProviderTrace | `src/agent/provider_trace.py` | LLM Provider 调用追踪 |
| Conversation | `src/agent/conversation.py` | 会话管理 |
| ChatContext | `src/agent/chat_context.py` | 聊天上下文管理 |
| StreamEvents | `src/agent/stream_events.py` | 流式事件处理 |
```

#### 步骤 3: 新增通知发送器子表

**位置**: 在 L2 模块清单中，"Agent 子系统" 之后，新增 "通知发送器" 子表

**内容**:

```markdown
#### 通知发送器（用户可见的分析结果交付）

| 渠道 | 路径 | 职责 |
|---|---|---|
| 企业微信 | `src/notification_sender/wechat/` | 企业微信 Webhook 通知 |
| 飞书 | `src/notification_sender/feishu/` | 飞书 Webhook + App Bot 通知 |
| 钉钉 | `src/notification_sender/dingtalk/` | 钉钉 Webhook 通知 |
| Telegram | `src/notification_sender/telegram/` | Telegram Bot 通知 |
| 邮件 | `src/notification_sender/email/` | SMTP 邮件通知 |
| Pushover | `src/notification_sender/pushover/` | Pushover 推送通知 |
| ntfy | `src/notification_sender/ntfy/` | ntfy.sh 推送通知 |
| Gotify | `src/notification_sender/gotify/` | Gotify 推送通知 |
| PushPlus | `src/notification_sender/pushplus/` | PushPlus 推送通知 |
| ServerChan3 | `src/notification_sender/serverchan3/` | Server酱³ 推送通知 |
| 自定义 Webhook | `src/notification_sender/custom_webhook/` | 自定义 Webhook 通知 |
| Discord | `src/notification_sender/discord/` | Discord Webhook/Bot 通知 |
| Slack | `src/notification_sender/slack/` | Slack Webhook/Bot 通知 |
| AstrBot | `src/notification_sender/astrbot/` | AstrBot 推送通知 |

> **为何 `notification_sender/` 归属 L2 而非 L1：** 通知发送器消费分析报告内容，格式化为渠道特定的消息并实际发送。它承载的是"告诉用户分析结论"的业务动作，不同于 L1 的"通知基础设施"（`notification_contracts.py` / `notification_routing.py` / `notification_noise.py`）——后者管理的是"哪些渠道可用、怎么路由、何时不发"的基础配置，不涉及实际的消息发送和内容格式化。
```

### 第二阶段（P1）：补充 L1 表和 L2 特征重审

#### 步骤 4: 在 L1 新增"LLM 后端基础设施"子表

**位置**: L1 模块清单中，在"时钟与延迟监控"之后新增

**内容**:

```markdown
#### LLM 后端基础设施

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| LLM 后端抽象层 | `src/llm/` (11 文件) | LLM 后端的统一接入、路由、参数管理 | 策略模式——LiteLLM / Hermes / 本地 CLI 三种后端可切换 |
| Agent 模型服务 | `src/services/agent_model_service.py` | Agent 模型部署元数据查询 | 暴露已配置的模型列表，供 Settings 页面和 Agent 工厂使用 |
| LLM 后端诊断 | `src/services/generation_backend_status_service.py` | 生成后端的只读状态诊断 | LLM 后端连通性、capability 探测、模型发现 |
| LLM 用量追踪 | `src/llm/usage.py` | LLM token 用量与成本追踪 | 按 provider/model 聚合，与 LiteLLM 回调集成 |

> **为何 `src/llm/` 归属 L1：** `src/llm/` 对 `src/agent/`（L2）的关系，在架构上等同于 `data_provider/` 对 `Pipeline` 的关系——都是"供给层对消费层"。`src/llm/` 提供 LLM 后端的接入（LiteLLM / Hermes / 本地 CLI）、路由选择、参数管理（temperature, max_tokens）、用量追踪能力。`src/agent/` 消费这些能力来驱动实际的股票分析。`src/llm/` 不回答"该不该买"——它只回答"LLM 后端通不通、用哪个模型、花了多少 token"。
```

#### 步骤 5: 在 L1 新增"股票域基础设施"子表

**位置**: L1 模块清单中，在"数据合约与访问抽象"之后新增

**内容**:

```markdown
#### 股票域基础设施

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 股票代码工具 | `src/services/stock_code_utils.py` | 股票代码格式标准化 | 支持 A 股/港股/美股多市场格式识别与互转 |
| 市场代码工具 | `src/services/market_symbol_utils.py` | 市场前缀/后缀映射 | HK→.HK, SH→.SH, 美股无后缀 |
| 名称→代码解析 | `src/services/name_to_code_resolver.py` | 股票名称模糊匹配到标准化代码 | 本地映射表 + 远程索引 fallback |
| 股票列表解析 | `src/services/stock_list_parser.py` | 用户输入的股票列表文本解析 | 逗号/空格/换行分隔，批量标准化 |
| 导入解析 | `src/services/import_parser.py` | CSV/Excel/剪贴板的股票导入解析 | 编码自动检测，列名智能匹配，单列快速路径 |
| 远程股票索引 | `src/services/stock_index_remote_service.py` | 股票自动补全索引的远程缓存刷新 | best-effort + local cache fallback + circuit breaker |

> **为何这些模块归属 L1 而非 L2：** 这些模块处理的是"标识符转换"——将用户输入的股票名称或代码标准化为系统内部表示。类比电商系统的 SKU 编码规则——编码规则是基础设施（L1），定价策略是业务逻辑（L2）。股票代码格式 = SKU 编码；股票分析 = 定价策略。虽然这些模块包含了市场域名知识（如交易所前缀映射），但这属于 **L1 级域名建模**——领域的基础数据结构定义，而非领域业务规则的执行。
```

#### 步骤 6: C 类 L2 特征重审更新

**位置**: 文档 "为什么 L2 需要 L3" 段（约第 237-241 行）

**修改前**:

```markdown
**为什么 L2 需要 L3**：L2 的模块在以下场景下会出故障：
- Pipeline 中的某个步骤因网络/API 问题抛异常 → 需要 L3-1 (ModuleAutoRestarter)
- 配置文件被错误修改导致分析流程崩溃 → 需要 L3-2 (ConfigAutoRollback)
- 系统压力过大（CPU/内存）导致分析延迟 → 需要 L3-3 (GracefulDegradationEngine)
- 代码缺陷导致特定股票分析失败 → 需要 L3-4 (CodeAwareRepairAgent)
```

**修改后**:

```markdown
**为什么 L2 需要 L3**：L2 的模块在以下场景下会出故障：

**Pipeline 级故障**:
- Pipeline 中某个步骤因网络/API 问题抛异常 → ModuleAutoRestarter 重启对应线程/进程
- 配置文件被错误修改导致分析流程崩溃 → ConfigAutoRollback 回滚到上一个已知良好的配置
- 系统压力过大（CPU/内存）导致分析延迟 → GracefulDegradationEngine 动态降低并发/跳过非关键步骤
- 代码缺陷导致特定股票分析失败 → CodeAwareRepairAgent 尝试 AST 级代码修复

**Agent 级故障**（Agent 子系统特有）:
- LLM 幻觉导致 tool 调用参数错误 → AgentExecutor 内置参数校验 + retry
- tool 返回超大数据集导致 Agent 内存压力 → AgentMemory 的上下文裁剪 + GracefulDegradationEngine 介入
- Agent 陷入无限 tool-call 循环 → AgentRunner 的 max_tool_calls 上限 + ModuleAutoRestarter

以上修复策略模块均基于 Phase 2 的 `SelfHealingAction` 基类实现，遵循 detect→repair→verify 闭环。修复效果由 Phase 3 的 `RepairEffectivenessLog` 周期性分析。
```

**位置**: 文档 "为什么 L2 需要 L4" 段（约第 243-248 行）

**修改前**:

```markdown
**为什么 L2 需要 L4**：L2 的 LLM 分析流程可能产生：
- 确认偏差（confirmation bias）：LLM 倾向于确认已有观点
- 思维循环（circularity）：同一分析模式反复出现
- 分析质量漂移：随着时间推移分析深度逐渐下降

这些不是"故障"而是"认知偏差"，不属于 L3 的修复范畴，而是 L4 (MetaCognitiveEngine) 的检测和反思范畴。
```

**修改后**:

```markdown
**为什么 L2 需要 L4**：L2 的 Agent 分析流程可能产生几类偏差：

**LLM 分析偏差**:
- 确认偏差（confirmation bias）：LLM 倾向于确认已有观点，忽略反面证据
- 思维循环（circularity）：同一分析模式反复出现，缺乏新视角
- 分析质量漂移：随着时间推移分析深度逐渐下降

**Agent 行为偏差**（Agent 子系统特有）:
- 工具偏好偏差：Agent 倾向于使用已学会的工具而忽略新引入的工具
- 研究深度衰减：Agent 在并行任务中缩小搜索范围以降低延迟，导致分析片面
- 策略退化：Agent 的 tool-call 序列逐渐简化为最少步骤，跳过复杂但重要的分析路径

这些不是"故障"而是"认知偏差"，不属于 L3 的操作级修复范畴，而是 L4 (MetaCognitiveEngine) 的检测和反思范畴。

**闭环反馈路径**:
1. L4 通过 SystemEventBus 订阅 L2 Agent 的分析产出和 tool-call trace
2. L4 的 MetaCognitiveEngine 检测偏差 → 生成内省报告 + 策略调整建议
3. 策略调整建议通过 SystemEventBus 回写 → L2 Agent Factory 调整 Agent 策略参数
4. 人工审核内省报告 → 决定是否调整 Prompt 模板或 Agent 配置
```

### 第三阶段（P2）：边界判定修订

#### 步骤 7: 扩展边界判定表

**位置**: 文档 "L1 vs L2 的边界判定" 段（第 252-268 行）

**修改**: 在现有判定表下方，新增细分类别：

```markdown
### 边界模糊地带的分层指南

以下场景在 L1/L2 分层时容易混淆：

| 场景 | 归 L1 | 归 L2 | 判据 |
|---|---|---|---|
| LLM 后端管理 | `src/llm/`（后端接入/路由/参数/用量） | `src/agent/`（使用 LLM 完成分析） | 供给 vs 消费 |
| 通知相关 | 契约/路由/噪音控制（配置层） | 实际发送消息（执行层） | 配置管理 vs 消息交付 |
| 股票代码处理 | 格式标准化/解析/缓存（标识符转换） | 股票筛选/排序/推荐（分析决策） | 数据标准化 vs 数据分析 |
| 配置相关 | `.env` 编辑/校验/导入导出 | 根据配置值改变分析行为 | 配置管理 vs 配置消费 |
| 诊断相关 | 诊断基础设施（trace/flow/snapshot） | 诊断结果的业务解释 | 工具 vs 结论 |
| 调度相关 | 任务生命周期管理/并发控制 | 基于市场条件的调度策略 | 基础设施 vs 策略 |
```

### 第四阶段（P3）：交叉引用修正与交付

#### 步骤 8: 全景架构图更新

检查全景图（第 10-35 行）是否准确反映 B 类整层遗漏的纳入。当前 L2 行已列出 "Agent子系统 / LLM子系统"，但 LLM 子系统实际归 L1。

**修改前** L2 行:
```
│  L2  业务执行与分析层 (Business Execution & Analysis)     │
│  Pipeline / Analyzer / Agent子系统 / LLM子系统 /          │
│  Notification / 60+ business service modules / API/Web    │
```

**修改后** L2 行:
```
│  L2  业务执行与分析层 (Business Execution & Analysis)     │
│  Pipeline / Analyzer / Agent子系统 / NotificationSender / │
│  30+ business service modules / API/Web                   │
```

**修改后** L1 行:
```
│  L1  基础数据与设施层 (Infrastructure & Data)             │
│  data_provider / Storage / Config / ExchangeClock /       │
│  Schemas / Repositories / Scheduler / LatencyTracker /    │
│  LLM Backend / StockDomain / NotificationInfra / Diagnostics │
```

#### 步骤 9: CHANGELOG 更新

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段新增：

```
- [文档] L1/L2 Retrospective 整改：修正 14 个模块归属 + 补充 Agent/LLM/NotificationSender 三个整层遗漏 + L2 特征重审
```

#### 步骤 10: 自审清单

执行前自审：

- [ ] 所有从 L2 删除的行是否在 L1 有对应条目？
- [ ] 新增子表的模块数量是否与源码目录一致？
- [ ] L3/L4 模块（code_aware_repair, repair_effectiveness_log, event_bus, module_restart, config_rollback, graceful_degradation, metacognitive_engine）是否未被误纳入 L1 或 L2？
- [ ] `src/llm/` 归 L1 的论证是否自洽？（关键：L1 的定义是"零业务语义"，LLM 后端管理确实不含股票分析语义）
- [ ] HealthCheckDaemon 的 L2 归属论证是否充分？
- [ ] 已做的 3 个 Edit 是否需要调整？

---

## 四、风险与注意事项

1. **`src/llm/` 归 L1 的争议风险**: 有人可能认为 LLM 后端管理与股票分析紧密耦合（例如 generation_params 中的 temperature 会影响分析质量）。我的论证是：temperature 是 LLM 基础设施参数，调整 temperature 不意味着调整分析策略——就像调整数据库连接池大小不意味着调整 SQL 查询逻辑。分析策略（使用哪个 agent、用什么 prompt、选择什么工具）属于 L2；LLM 后端参数（temperature、max_tokens、模型路由）属于 L1。

2. **不修改源码**: 本次整改仅涉及 `docs/L1_L2_RETROSPECTIVE.md`——这是架构文档的分类修正，不涉及任何代码改动。源码的目录结构、import 关系、模块接口保持不变。

3. **已做的 3 个 Edit 的处理**: 在上一轮会话中，已对文档做了 3 个 Edit（全景图更新、层间关系更新、L1 表扩展）。本计划假设这 3 个 Edit 是有效的，并在它们的**基础上**继续整改。如果执行中发现这 3 个 Edit 与新改动冲突，需要先调整。

4. **文档膨胀控制**: 当前 retrospective 约 328 行，整改后预计约 420 行。增长主要来自三个整层遗漏的纳入（agent / llm / notification_sender 都是大量子模块）。如果担心文档过长，可将详细模块清单移入附录或拆分到独立文档——但当前阶段建议保持单文件完整性。

---

## 五、执行概览表

| 步骤 | 操作 | 位置 | 改动量 |
|---|---|---|---|
| 1 | 从 L2 表删除 14 行已移走模块 | 第 192-210 行 | -14 行 |
| 2 | 新增 L2 "Agent 子系统" 子表（16 模块） | 核心流水线之后 | +25 行 |
| 3 | 新增 L2 "通知发送器" 子表（14 模块） | Agent 子系统之后 | +25 行 |
| 4 | 新增 L1 "LLM 后端基础设施" 子表 | 时钟与延迟之后 | +15 行 |
| 5 | 新增 L1 "股票域基础设施" 子表 | 数据合约之后 | +15 行 |
| 6a | 重写 "为什么 L2 需要 L3" 段 | 第 237-241 行 | 替换 ~10 行 |
| 6b | 重写 "为什么 L2 需要 L4" 段 | 第 243-248 行 | 替换 ~15 行 |
| 7 | 新增 "边界模糊地带的分层指南" 表 | 边界判定段之后 | +20 行 |
| 8 | 全景架构图更新 | 第 10-35 行 | 修改 2 行 |
| 9 | CHANGELOG 更新 | `docs/CHANGELOG.md` | +1 行 |
| 10 | 自审 | — | 自查 |
