# 项目四层架构回头望：L1/L2 归纳总结

**分析日期**: 2026-08-11
**分析背景**: L3（架构级自修复）四个 Phase 已完成，L4（元认知引擎）已交付。现从已实现的 L3/L4 向上反向推演，对 L1（基础数据层）和 L2（业务执行层）进行归纳性定义和盘点。

---

## 总览：四层架构全景

```
┌──────────────────────────────────────────────────────────┐
│  L4  元认知层 (Meta-Cognition)                            │
│  MetaCognitiveEngine                                      │
│  职责：理解系统"为什么这么做"、检测偏差、学习修复策略       │
├──────────────────────────────────────────────────────────┤
│  L3  操作级自修复层 (Operational Self-Healing)            │
│  SelfHealingAction / ModuleAutoRestarter /                │
│  ConfigAutoRollback / GracefulDegradationEngine /         │
│  CodeAwareRepairAgent / RepairEffectivenessLog            │
│  职责：检测→修复→验证闭环，从操作级升级到架构级自修复      │
├──────────────────────────────────────────────────────────┤
│  ═══════════ SystemEventBus (L3↔L4 双向桥接件) ═══════════│
├──────────────────────────────────────────────────────────┤
│  L2  业务执行与分析层 (Business Execution & Analysis)     │
│  Pipeline / Analyzer / Agent子系统 / NotificationSender /  │
│  30+ business service modules / BacktestEngine / API/Web    │
│  职责：股票分析的核心业务逻辑——取数→分析→报告→推送         │
├──────────────────────────────────────────────────────────┤
│  L1  基础数据与设施层 (Infrastructure & Data)             │
│  data_provider / Storage / Config / ExchangeClock /       │
│  Schemas / Repositories / Scheduler / LatencyTracker /    │
│  LLM Backend / StockDomain / NotificationInfra / Diagnostics │
│  职责：提供可靠、可降级的数据获取与持久化基础设施           │
└──────────────────────────────────────────────────────────┘
```

**层间关系**:
- **L1 → L2**: 提供数据流基础设施。L1 不关心业务语义，只保证"数据能拿到、能存、配置正确、时间准确"
- **L2 → L3**: L3 监控 L2 的运行时健康（通过 HealthCheckDaemon），在 L2 出问题时执行操作级修复
- **SystemEventBus (桥接件)**: L3 的修复事件通过 EventBus 推送给 L4；L4 的反省结论通过 EventBus 回写给 L3。EventBus 是 L3 和 L4 之间的**独立双向通道**，不属于任一层独有
- **L4**: 订阅 L3 事件，理解系统行为偏差，学习修复策略有效性，生成内省报告

---

## L1：基础数据与设施层

### 核心职责

L1 是整个系统的"地基"。它提供：

1. **多源数据获取与容错**：通过策略模式实现 8+ 数据源的统一接入，单一数据源失败自动 fallback 到下一个
2. **数据持久化**：SQLite 数据库，所有分析结果和行情数据的落地存储
3. **配置管理**：单例模式的全局配置，`.env` 加载，类型安全的配置访问
4. **时钟同步**：交易所时区映射 + NTP 同步，全项目唯一时间来源
5. **数据合约**：Schema 定义（DecisionAction, DecisionScale, MarketLight, ReportSchema），保证上下游数据格式一致
6. **数据访问抽象**：Repository 模式，隔离存储实现细节

### 模块清单

#### 数据获取与容错

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| DataFetcherManager + BaseFetcher | `data_provider/base.py` | 多源数据获取的策略管理器 | 策略模式，指数退避重试，防封禁流控 |
| 12 个数据源适配器 | `data_provider/*_fetcher.py` | 各数据源的标准化适配 | akshare, baostock, efinance, tushare, yfinance, finnhub, alphavantage, longbridge, pytdx, tencent, tickflow, tw_institutional |
| L2 行情适配器 | `data_provider/l2_fetcher.py` | Level-2 逐笔数据 | 独立于日线数据源，专用接口 |
| 基本面适配器 | `data_provider/fundamental_adapter.py` `yfinance_fundamental_adapter.py` | 财报/估值数据适配 | 跨境双适配（A股 + 美股） |
| 数据质量控制 | `data_provider/quality.py` | 数据完整性校验 | 字段完整性、异常值检测 |
| 本地缓存 | `data_provider/local_store.py` | 行情数据本地缓存 | 减少重复网络请求 |
| CircuitBreaker | `data_provider/realtime_types.py` | 数据源熔断器 | 三阶段自动熔断，与 Config 中的配置联动 |
| 公司行为 | `data_provider/corporate_actions.py` | 分红、拆股等公司行为数据 | |
| 美股指数映射 | `data_provider/us_index_mapping.py` | 美股代码→指数成分映射 | |

#### 持久化与配置

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 数据库 | `src/storage.py` | SQLite 存储层（SQLAlchemy ORM） | 全项目数据持久化入口，单例模式 |
| 配置管理 | `src/config.py` + `src/core/config_manager.py` + `src/core/config_registry.py` | 全局配置的单例管理 | `.env` → 类型安全访问，包含 CircuitBreaker 配置与 LLM 渠道解析 |
| 系统配置服务 | `src/services/system_config_service.py` | `.env` 线上编辑与校验 API | 读写与校验分离，含 URL 脱敏回填 |

#### 时钟与延迟监控

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 交易所时钟 | `src/utils/exchange_clock.py` | NTP 时钟同步 + 交易所时区 | 单例，NTP → API → 系统时间三级降级 |
| 延迟追踪器 | `src/utils/latency_tracker.py` | 全链路延迟监控（p50/p95/p99） | 滑动窗口 + `LatencySpan` 打点追踪 |

#### LLM 后端基础设施

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| LLM 后端抽象层 | `src/llm/` (11 文件) | LLM 后端的统一接入、路由、参数管理 | 策略模式——LiteLLM / Hermes / 本地 CLI 三种后端可切换 |
| Agent 模型服务 | `src/services/agent_model_service.py` | Agent 模型部署元数据查询 | 暴露已配置的模型列表，供 Settings 页面和 Agent 工厂使用 |
| LLM 后端诊断 | `src/services/generation_backend_status_service.py` | 生成后端的只读状态诊断 | LLM 后端连通性、capability 探测、模型发现 |

> **为何 `src/llm/` 归属 L1：** `src/llm/` 对 `src/agent/`（L2）的关系，在架构上等同于 `data_provider/` 对 `Pipeline` 的关系——都是"供给层对消费层"。`src/llm/` 提供 LLM 后端的接入（LiteLLM / Hermes / 本地 CLI）、路由选择、参数管理（temperature 等）、用量追踪。`src/agent/` 消费这些能力来驱动实际的股票分析。`src/llm/` 不回答"该不该买"——它只回答"LLM 后端通不通、用哪个模型、花了多少 token"。

#### 调度与任务基础设施

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 定时调度器 | `src/scheduler.py` | 轻量级定时任务调度 | 基于 `schedule` 库，支持优雅信号退出 |
| 任务队列 | `src/services/task_queue.py` | 异步分析任务生命周期管理 | 防重复提交 + SSE 事件广播 + 持久化 |
| 运行时调度器 | `src/services/runtime_scheduler.py` | API/Web/Desktop 长驻进程的调度集成 | 全局分析锁 + ENV 开关控制 |

#### 数据合约与访问抽象

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| Schema 层 | `src/schemas/` | 数据合约定义 | DecisionAction, DecisionScale, MarketLight, ReportSchema, AnalysisContextPack |
| Repository 层 | `src/repositories/` | 数据访问抽象（8个 repo） | stock_repo, analysis_repo, alert_repo, backtest_repo, decision_signal_repo, portfolio_repo, intelligence_repo, decision_signal_outcome_repo |
| 股票映射数据 | `src/data/stock_mapping.py` `src/data/stock_index_loader.py` | 本地股票名→代码映射 + 指数成分股加载 | 基础查找表，不依赖外部 API |

#### 股票域基础设施

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 股票代码工具 | `src/services/stock_code_utils.py` | 股票代码格式标准化 | 多市场（A股/港股/美股）格式识别与互转 |
| 市场代码工具 | `src/services/market_symbol_utils.py` | 市场前缀/后缀映射 | HK↔.HK, SH↔.SH, 美股无后缀 |
| 名称→代码解析 | `src/services/name_to_code_resolver.py` | 股票名称模糊匹配到标准化代码 | 本地映射表 + 远程索引 fallback |
| 股票列表解析 | `src/services/stock_list_parser.py` | 用户输入的股票列表文本解析 | 逗号/空格/换行分隔，批量标准化 |
| 导入解析 | `src/services/import_parser.py` | CSV/Excel/剪贴板统一导入解析管道 | 编码自动检测 + 列名智能匹配 + 单列快速路径 |
| 远程股票索引 | `src/services/stock_index_remote_service.py` | 股票自动补全索引的远程缓存刷新 | best-effort + local cache fallback + circuit breaker |

> **为何这些模块归属 L1 而非 L2：** 这些模块处理的是"标识符转换"——将用户输入的股票名称或代码标准化为系统内部表示。类比电商系统：SKU 编码规则是基础设施（L1），定价策略是业务逻辑（L2）。股票代码格式 = SKU 编码；股票分析 = 定价策略。虽然这些模块包含市场域名知识（如交易所前缀映射），但这属于 **L1 级域名建模**——领域的基础数据结构定义，而非领域业务规则的执行。

#### 通知基础设施

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 通知契约 | `src/notification_contracts.py` | 通知环境变量组的语义校验 | 飞书/Webhook/其他渠道的配置契约 |
| 通知路由 | `src/notification_routing.py` | 通知渠道路由配置 | 按报告类型的渠道分配策略 |
| 通知噪音控制 | `src/notification_noise.py` | 通知静默时段与频控 | 时区感知的静默窗口校验 |

#### 诊断与可观测性

| 模块 | 路径 | 职责 | 关键设计 |
|---|---|---|---|
| 运行诊断上下文 | `src/services/run_diagnostics.py` | 单次分析 trace 的诊断上下文 | ContextVar 线程隔离 + 密钥自动脱敏 |
| 运行流快照 | `src/services/run_flow.py` | 任务→分析→产物的运行流结构化快照 | 4 泳道（入口/数据来源/分析引擎/产物） |
| 通知诊断 | `src/services/notification_diagnostics.py` | 只读通知配置诊断 | 分层 key（minimal/advanced），error/warning/info 三级 |

> **为何 `run_diagnostics.py` / `run_flow.py` / `notification_diagnostics.py` 归属 L1：** 这三个模块是**纯诊断和可观测性基础设施**——它们追踪分析过程、构建运行流快照、诊断通知配置——但不包含任何 "股票该买还是该卖" 的业务语义。它们回答的是 "系统运行得怎么样" 而非 "这只股票怎么样"。类似 `LatencyTracker` 记录延迟而非投资建议。

### L1 的关键设计特征

**容错优先**：每个数据源都可能挂（封IP、API限流、网络故障），L1 的职责不是避免失败，而是失败后无缝切换到下一个数据源。这是 L1 区别于普通"数据层"的核心特征——它不是 CRUD，而是带熔断和降级的容错数据获取。

**标准化接口**：所有数据源通过 `BaseFetcher` 抽象基类统一为标准列名（`date, open, high, low, close, volume, amount, pct_chg`），上层（L2）不需要知道数据来自哪个源。

**时间一致性**：`ExchangeClock` 是"全项目唯一时间来源"，避免各模块各自 `datetime.now()` 导致的时间不一致问题（对金融系统而言这是硬需求）。

**为什么 L1 不是 L2 的一部分**：L1 的模块不包含任何"股票分析"语义。它不判断买入卖出，不计算技术指标，不生成报告。它只回答"数据在不在、能不能拿到、存好了没、时间对不对"这四个问题。这种语义上的"零业务耦合"是 L1 和 L2 的分界线。

---

## L2：业务执行与分析层

### 核心职责

L2 是系统的"血肉"。它的职责是：

1. **核心分析流水线**：编排数据获取 → 技术分析/新闻检索 → LLM 分析 → 报告生成 → 通知推送的完整流程
2. **LLM 驱动的个股分析**：调用 Gemini 进行技术面/基本面/消息面的综合分析
3. **多渠道通知推送**：企业微信、飞书、Telegram、邮件、Pushover
4. **健康监控**：HealthCheckDaemon 周期性检查系统资源（内存/CPU/磁盘）、任务队列、NTP 状态
5. **API 与 Web 服务**：FastAPI 后端 + Vue 前端 + Electron 桌面端
6. **高级业务能力**：回测引擎、决策信号系统、投资组合管理、市场情绪分析、AlphaSift 选股、大盘综述

### 模块清单

#### 核心流水线与入口

| 模块 | 路径 | 职责 |
|---|---|---|
| Pipeline | `src/core/pipeline.py` | 核心分析流程编排（取数→分析→报告→通知） |
| Analyzer | `src/analyzer.py` | LLM 驱动的个股分析（GeminiAnalyzer） |
| NotificationService | `src/notification.py` | 多渠道通知推送（5 个渠道） |
| SearchService | `src/search_service.py` | 新闻/消息检索 |
| main.py | `main.py` | CLI 入口（--debug, --dry-run, --stocks, --schedule, --serve） |
| server.py | `server.py` | FastAPI 服务入口 |
| API | `api/app.py`, `api/deps.py` | REST API 路由 |

#### Agent 子系统（LLM 驱动的智能分析引擎）

| 模块 | 路径 | 职责 |
|---|---|---|
| AgentOrchestrator | `src/agent/orchestrator.py` | 多 Agent 编排——协调研究/分析/决策 Agent 完成分析任务 |
| AgentRunner | `src/agent/runner.py` | Agent 运行入口——任务接收→Agent 分配→结果收集 |
| AgentExecutor | `src/agent/executor.py` | Agent 执行器——管理 tool-call 生命周期与 LLM 交互 |
| AgentFactory | `src/agent/factory.py` | Agent 工厂——按任务类型与策略创建对应 Agent |
| AgentProtocols | `src/agent/protocols.py` | Agent 协议定义——输入/输出 Schema 与类型合约 |
| AgentEvents | `src/agent/events.py` | Agent 事件定义——tool_call/tool_result/completion 等 |
| AgentMemory | `src/agent/memory.py` | Agent 跨轮次会话记忆 |
| Conversation | `src/agent/conversation.py` | 多轮对话管理与上下文持久化 |
| ChatContext | `src/agent/chat_context.py` | 聊天上下文构建与裁剪 |
| StreamEvents | `src/agent/stream_events.py` | LLM 流式响应事件处理 |
| LiteLLMRouteResolution | `src/agent/litellm_route_resolution.py` | LiteLLM 路由解析 |
| LLMAdapter | `src/agent/llm_adapter.py` | LLM 适配层——标准化不同后端的响应格式 |
| ProviderTrace | `src/agent/provider_trace.py` | LLM Provider 调用追踪 |
| StockScope | `src/agent/stock_scope.py` | 股票分析范围定义 |
| ResearchAgent | `src/agent/research.py` | 研究 Agent——新闻检索、基本面查询、技术指标计算 |
| PortfolioManagerAgent | `src/agent/portfolio_manager_agent.py` | 投资组合管理 Agent |
| Skill 子系统 | `src/agent/skills/` (5 文件) | Agent 技能模块化——聚合/路由/默认策略 |
| Strategy 子系统 | `src/agent/strategies/` (4 文件) | Agent 策略——聚合/路由/策略 Agent |
| Agent Tools | `src/agent/tools/` (6 文件) | Agent 工具集——数据分析/回测/行情/搜索/注册 |

#### 通知发送器（用户可见的分析结果交付）

| 渠道 | 路径 | 职责 |
|---|---|---|
| 企业微信 | `src/notification_sender/wechat_sender.py` | 企业微信 Webhook 通知 |
| 飞书 | `src/notification_sender/feishu_sender.py` | 飞书 Webhook + App Bot 通知 |
| 钉钉 | `src/notification_sender/dingtalk_sender.py` | 钉钉 Webhook 通知 |
| Telegram | `src/notification_sender/telegram_sender.py` | Telegram Bot 通知 |
| 邮件 | `src/notification_sender/email_sender.py` | SMTP 邮件通知 |
| Pushover | `src/notification_sender/pushover_sender.py` | Pushover 推送通知 |
| ntfy | `src/notification_sender/ntfy_sender.py` | ntfy.sh 推送通知 |
| Gotify | `src/notification_sender/gotify_sender.py` | Gotify 推送通知 |
| PushPlus | `src/notification_sender/pushplus_sender.py` | PushPlus 推送通知 |
| ServerChan3 | `src/notification_sender/serverchan3_sender.py` | Server酱³ 推送通知 |
| 自定义 Webhook | `src/notification_sender/custom_webhook_sender.py` | 自定义 Webhook 通知 |
| Discord | `src/notification_sender/discord_sender.py` | Discord Webhook/Bot 通知 |
| Slack | `src/notification_sender/slack_sender.py` | Slack Webhook/Bot 通知 |
| AstrBot | `src/notification_sender/astrbot_sender.py` | AstrBot 推送通知 |

> **为何 `notification_sender/` 归属 L2 而非 L1：** 通知发送器消费分析报告内容，格式化为渠道特定消息并实际发送。它承载的是"告诉用户分析结论"的业务动作。L1 的"通知基础设施"（`notification_contracts.py` / `notification_routing.py` / `notification_noise.py`）管理的是"哪些渠道可用、怎么路由、何时不发"的配置层——不涉及实际消息发送和内容格式化。

#### 业务服务层（L2 范畴）

| 模块 | 路径 | 职责 |
|---|---|---|
| StockService | `src/services/stock_service.py` | 股票 CRUD |
| AnalysisService | `src/services/analysis_service.py` | 分析记录管理 |
| AnalyzerService | `src/services/analyzer_service.py` | 分析器调度 |
| AlertService | `src/services/alert_service.py` | 告警规则引擎 |
| AlertWorker | `src/services/alert_worker.py` | 告警后台 worker |
| AlertIndicators | `src/services/alert_indicators.py` | 告警指标计算 |
| PortfolioService | `src/services/portfolio_service.py` | 投资组合管理 |
| PortfolioImportService | `src/services/portfolio_import_service.py` | 组合导入 |
| PortfolioRiskService | `src/services/portfolio_risk_service.py` | 组合风控 |
| PortfolioAlerts | `src/services/portfolio_alerts.py` | 组合告警 |
| BacktestService | `src/services/backtest_service.py` | 回测服务 |
| DailyMarketContext | `src/services/daily_market_context.py` | 每日大盘背景 |
| DecisionSignalService | `src/services/decision_signal_service.py` | 决策信号服务 |
| DecisionSignalExtractor | `src/services/decision_signal_extractor.py` | 信号提取 |
| DecisionSignalSummary | `src/services/decision_signal_summary.py` | 信号汇总 |
| DecisionSignalOutcomeService | `src/services/decision_signal_outcome_service.py` | 信号结果跟踪 |
| DecisionSignalReassessService | `src/services/decision_signal_reassess_service.py` | 信号再评估 |
| DecisionSignalDataQuality | `src/services/decision_signal_data_quality.py` | 信号数据质量 |
| DecisionProfilePolicy | `src/services/decision_profile_policy.py` | 决策画像策略 |
| HistoryService | `src/services/history_service.py` | 历史数据服务 |
| HistoryComparisonService | `src/services/history_comparison_service.py` | 历史对比 |
| HistoryLoader | `src/services/history_loader.py` | 历史加载 |
| MarketLightService | `src/services/market_light_service.py` | 市场信号灯 |
| MarketLightAlerts | `src/services/market_light_alerts.py` | 信号灯告警 |
| IntelligenceService | `src/services/intelligence_service.py` | 情报聚合 |
| TaskService | `src/services/task_service.py` | 任务管理 |
| ReportRenderer | `src/services/report_renderer.py` | 报告渲染 |
| ImageStockExtractor | `src/services/image_stock_extractor.py` | 图片股票提取 |
| SocialSentimentService | `src/services/social_sentiment_service.py` | 社交媒体情绪 |
| AlphaSiftService | `src/services/alphasift_service.py` | Alpha 因子选股 |
| AnalysisContextBuilder | `src/services/analysis_context_builder.py` | 分析上下文构建 |

#### 核心业务引擎（src/core/ 中非 L3/L4 部分）

| 模块 | 路径 | 职责 |
|---|---|---|
| Pipeline | `src/core/pipeline.py` | 核心分析流程编排 |
| BacktestEngine | `src/core/backtest_engine.py` | 回测引擎 |
| MarketReview | `src/core/market_review.py` | 大盘综述 |
| MarketProfile | `src/core/market_profile.py` | 大盘画像 |
| MarketStrategy | `src/core/market_strategy.py` | 大盘策略 |
| MarketReviewRuntime | `src/core/market_review_runtime.py` | 大盘综述运行时 |
| MarketReviewLock | `src/core/market_review_lock.py` | 大盘综述锁 |
| TradingCalendar | `src/core/trading_calendar.py` | 交易日历 |

#### 健康监控（L2 与 L3 的桥梁）

| 模块 | 路径 | 职责 | 为什么归 L2 |
|---|---|---|---|
| HealthCheckDaemon | `src/services/health_check.py` | 周期性检查系统资源、任务队列、NTP 状态 | 它只负责"检测"，不负责"修复"——修复是 L3 的职责 |

### L2 的关键设计特征

**Pipeline 是 L2 的主动脉**：`Pipeline.run_analysis()` 是所有个股分析流程的唯一入口。数据从 L1 的 `DataFetcherManager` 流入，经过 `GeminiAnalyzer` 的 LLM 分析，再通过 `NotificationService` 推送到各个渠道。这是一个严格编排的顺序流程：`fetch → analyze → report → notify`。

**L2 的复杂度来源**：L2 的模块数量不是架构膨胀，而是业务复杂度的自然映射——股票分析需要处理 Agent 编排、技术面分析、基本面分析、消息面分析、LLM 调用、报告渲染、多渠道推送、回测、决策跟踪、信号评估、大盘综述、投资组合管理、告警规则引擎……每个模块解决一个明确的业务子问题。

**为什么 L2 需要 L3**：L2 的模块在以下场景下会出故障：

**Pipeline 级故障**:
- Pipeline 中某个步骤因网络/API 问题抛异常 → `ModuleAutoRestarter` 重启对应线程/进程
- 配置文件被错误修改导致分析流程崩溃 → `ConfigAutoRollback` 回滚到上一个已知良好的配置
- 系统压力过大（CPU/内存）导致分析延迟 → `GracefulDegradationEngine` 动态降低并发/跳过非关键步骤
- 代码缺陷导致特定股票分析失败 → `CodeAwareRepairAgent` 尝试 AST 级代码修复

**Agent 级故障**（Agent 子系统特有）:
- LLM 幻觉导致 tool 调用参数错误 → `AgentExecutor` 内置参数校验 + retry
- tool 返回超大数据集导致 Agent 内存压力 → `AgentMemory` 上下文裁剪 + `GracefulDegradationEngine` 介入
- Agent 陷入无限 tool-call 循环 → `AgentRunner` 的 `max_tool_calls` 上限 + `ModuleAutoRestarter`

以上修复策略模块均基于 Phase 2 的 `SelfHealingAction` 基类实现，遵循 detect→repair→verify 闭环。修复效果由 Phase 3 的 `RepairEffectivenessLog` 周期性分析。

**为什么 L2 需要 L4**：L2 的 Agent 分析流程可能产生几类偏差：

**LLM 分析偏差**:
- 确认偏差（confirmation bias）：LLM 倾向于确认已有观点，忽略反面证据
- 思维循环（circularity）：同一分析模式反复出现，缺乏新视角
- 分析质量漂移：随着时间推移分析深度逐渐下降

**Agent 行为偏差**（Agent 子系统特有）:
- 工具偏好偏差：Agent 倾向于使用已学会的工具而忽略新引入的工具
- 研究深度衰减：Agent 在并行任务中缩小搜索范围以降低延迟，导致分析片面
- 策略退化：Agent 的 tool-call 序列逐渐简化为最少步骤，跳过复杂但重要的分析路径

这些不是"故障"而是"认知偏差"，不属于 L3 的操作级修复范畴（代码没坏、配置没坏、模块没挂），而是 L4 (MetaCognitiveEngine) 的检测和反思范畴。

**闭环反馈路径**:
1. L4 通过 `SystemEventBus` 订阅 L2 Agent 的分析产出与 tool-call trace
2. L4 的 `MetaCognitiveEngine` 检测偏差 → 生成内省报告 + 策略调整建议
3. 策略调整建议通过 `SystemEventBus` 回写 → L2 `AgentFactory` 调整 Agent 策略参数
4. 人工审核内省报告 → 决定是否调整 Prompt 模板或 Agent 配置

---

## L1 vs L2 的边界判定

一个直观的测试：**一个模块如果被问到"你认为这只股票该买还是该卖？"**

| 能回答 | 不能回答 |
|---|---|
| L2 (Analyzer, Pipeline) | L1 (DataFetcher, Storage, Config) |

更形式化的判定标准：

| 维度 | L1 | L2 |
|---|---|---|
| 业务语义 | 零。不包含任何"股票分析"判断 | 包含。LLM prompt、技术指标计算、买卖建议 |
| 数据方向 | 数据供给（只负责"拿到数据"） | 数据消费与分析（"数据意味着什么"） |
| 失败处理 | 静默降级（换数据源、重试） | 影响到用户可见结果（报告缺失、通知延迟） |
| 对外接口 | Repo/Schema/Provider（内部基础设施） | API/CLI/Web/通知（用户面向） |
| 可独立测试 | 是（mock 上层即可） | 是（mock L1 数据源即可） |

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

---

## HealthCheckDaemon 的特殊地位：L2 的"哨兵"

`HealthCheckDaemon`（`src/services/health_check.py`）是一个值得单独讨论的模块。它周期性检查系统资源（内存/CPU/磁盘）、任务队列状态、NTP 同步状态，并将结果发布给 L3 的 `GracefulDegradationEngine`。

它属于 **L2** 而非 L3，原因是：
- 它只做"检测"（detect），不做"修复"（repair）
- 它不知道检测到的问题该用什么策略修复——那是 L3 的职责
- 它的输出是健康指标（health metrics），不是修复动作

它在架构中的位置是 **L2 → L3 的桥梁**：

```
HealthCheckDaemon (L2) ──health metrics──▶ GracefulDegradationEngine (L3)
                                           ModuleAutoRestarter (L3)
```

这是一个重要的架构决策：**检测和修复的分层分离**。L2 负责知道"出问题了"，L3 负责知道"怎么修"。这种分离避免了紧耦合——未来可以替换检测机制（例如从本地 daemon 换为 Prometheus exporter）而不影响修复逻辑。

---

## L1/L2 与 L3/L4 的依赖方向

关键约束：**依赖方向必须单向向下**。

```
L4 (元认知) ──订阅事件──▶ L3 (自修复) ──监控──▶ L2 (业务) ──获取数据──▶ L1 (数据)
         ◀──策略调整──                ◀──修复动作──                ◀──数据写入──
```

| 依赖 | 方向 | 机制 |
|---|---|---|
| L2 → L1 | 数据获取 | `DataFetcherManager.fetch()` |
| L2 → L1 | 数据存储 | `get_db().save()` |
| L3 → L2 | 健康监控 | `HealthCheckDaemon.tick()` → health metrics |
| L3 → L2 | 配置管理 | `ConfigAutoRollback` 直接操作配置文件 |
| L3 → L2 | 模块重启 | `ModuleAutoRestarter` 重启 L2 中的线程/进程 |
| L4 → L3 | 事件订阅 | `SystemEventBus().subscribe(DegradationOccurred, ...)` |
| L3 → L4 | 事件推送 | `SystemEventBus().publish(event)` |
| L4 → L3 | 策略反馈 | `MetaCognitiveEngine.reflection_insight` → L3 策略参数调整 |

**L1 和 L2 不需要知道 L3/L4 的存在。**这是正确的架构分层——基础设施层和业务层不应该感知到自愈层和元认知层。

---

## 总结：四层架构的职责一句话

| 层 | 一句话 |
|---|---|
| **L1** | "我能把数据拿到、存好，不管来源出什么问题。" |
| **L2** | "我能分析这只股票该买还是该卖，并把结果告诉你。" |
| **L3** | "如果 L2 出故障，我能自动修复它——重启、回滚、降级、甚至改代码。" |
| **L4** | "我能反思 L2 的分析有没有偏差、L3 的修复有没有效果，并持续改进。" |

**L1 和 L2 共同构成了一个"没有自愈能力的正常股票分析系统"**——数据可靠（L1）、分析完整（L2）、但出了问题需要人工介入。

**L3 和 L4 是叠加在这个基础上的"自主运行增强层"**——让系统能在无人值守的情况下持续运行、自我修复、自我改进。
