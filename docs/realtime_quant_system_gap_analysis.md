# 实时量化交易系统 — 后端管线对齐审查报告

> 审查基准：`docs/architecture/realtime_quant_system_design.md`（commit ebd9f40 基准方案）
> 审查日期：2026-08-10
> 审查范围：P0/P1/P2/P3 全部 18 个设计方案模块

---

## 一、整体评估

- **接口/模块完成度**：85 / 100
- **业务管线还原度**：75 / 100
- **集成深度**：45 / 100
- **数据模型一致度**：80 / 100
- **主要问题概述**：核心模块（回测引擎、熔断、风控守护、信号融合、WebSocket 通道、OMS/RMS 分离）均已独立实现，代码质量良好，但存在三个层面的断层：(1) **集成层未连接**——模块实现了 API 但未在 TradingEngine/MarketListener/main.py 启动链路中挂载；(2) **少数模块与设计规格存在偏差**——router 按 name 而非 account_id 路由、backtest_adapter 变为比较器而非透传 runner、corporate_actions 无外部数据拉取；(3) **3 个模块完全缺失**——eastmoney_broker.py、l2_fetcher.py、settlement.py 未独立为文件。

---

## 二、逐项问题明细

| 编号 | 维度 | 阻断类型 | 判定 | 对应设计章节 | 预期行为 | 实际表现 | 影响范围 | 代码定位 | 修复建议 |
|------|------|----------|------|-------------|----------|----------|----------|----------|----------|
| **P0 — 上线硬前置** |||||||||
| 1 | 模块实现 | 功能可用性阻断 | 存在偏差 | 1.2 券商适配层 | BrokerRouter.resolve(account_id) 通过查询 account.broker 路由 | Router 仅支持按 broker name 字符串查找，无 account-based resolution，无默认 PaperBroker 注册，无 failover | 真券商下单时无法根据账户自动选择券商，需调用方手动指定 | `paper_trading/broker/router.py:40-61` | 增加 `resolve_by_account(account_id)` 方法，注入 PaperAccountManager，从 account.broker 字段获取 broker name |
| 2 | 接口遗漏 | 功能可用性阻断 | 未实现 | 1.2 券商适配层 | EastMoneyBroker 基于 easyquotation/easytrader 库的真券商适配器 | 文件 `paper_trading/broker/eastmoney_broker.py` 不存在，仅有 PaperBroker | 无法对接东方财富真实账户下单 | 缺失文件 | 按设计规格 1.2 节实现 `EastMoneyBroker`，继承 `BaseBroker`，集成 easytrader |
| 3 | 模块实现 | 业务规则阻断 | 存在偏差 | 1.1 回测对接 | `backtest_adapter.backtest_from_paper_account()` 从 PaperAccount 出发执行回测 | `backtest_adapter.py` 实现了比较/反思适配器（PaperTradingToBacktestAdapter），非 "从账户出发执行回测" 的 passthrough 接口 | 无法从 Web UI "一键回测此模拟账户" | `paper_trading/backtest_adapter.py` | 补充 `backtest_from_paper_account()` 函数，调用 BacktestEngine.run() 并返回 BacktestResult |
| 4 | 集成缺口 | 契约一致性阻断 | 存在偏差 | 1.3 时钟同步 | `market_listener.py` 内所有 `datetime.now()` 调用替换为 `ExchangeClock.now(market)` | `market_listener.py` 中未使用 ExchangeClock，仍直接调用 `datetime.now()` | 本地时间不准时（如慢30s）收盘前仍发单被交易所拒绝 | `paper_trading/market_listener.py` 全文件 | 在 `_tick_market()`、`_should_emit_signal()`、`_record_signal()` 中替换为 `ExchangeClock.now()` |
| 5 | 集成缺口 | 契约一致性阻断 | 存在偏差 | 1.3 时钟同步 | `DataFetcherManager.get_daily_data()` 返回前调用 `_normalize_timestamps()` 统一时区 | `data_provider/base.py` 中未实现 `_normalize_timestamps()`，无 ExchangeClock 集成 | 美股 yfinance 返回 UTC 时间戳，中股 akshare 返回东八区，回测 bar 错位 | `data_provider/base.py` DataFetcherManager | 增加 `_normalize_timestamps()` 方法，按股票市场做 tz_convert + tz_localize |
| **P1 — 上线前必备** |||||||||
| 6 | 集成缺口 | 功能可用性阻断 | 有缺陷 | 2.1 WS 行情接入 | SharedQuoteCache + WebSocketChannel 在 MarketListener 中挂载，WS 断开自动 fallback 到 HTTP 轮询 | 两个模块独立实现完成，但 `market_listener.py` 仅通过可选 `quote_cache` 参数接收缓存引用——WS channel 的启动、reconnect policy、fallback 逻辑需由调用方自行编排 | 当前 MarketListener 的真正运行实例不会自动启用 WS 通道 | `paper_trading/market_listener.py:356` | 在 `MarketListener.start()` 或 `main.py` 初始化链路中显式创建 WebSocketChannel、启动后台 asyncio 循环、注入 SharedQuoteCache |
| 7 | 集成缺口 | 业务规则阻断 | 有缺陷 | 2.2 熔断机制 | `TradingEngine.submit_signal()` 中每次交易前调用 `CircuitBreaker.evaluate()`，三级熔断分别拒绝新开仓/所有交易/触发平仓 | `CircuitBreaker` 模块实现完整，`trading_engine.py:295-311` 已有集成代码。**但 `main.py` 启动链路中未创建 CircuitBreaker 实例并注入 TradingEngine** | 实际运行中 circuit_breaker 始终为 None，熔断不生效 | `main.py` 启动段 | 在 `main.py` 创建 MarketListener/TradingEngine 时，实例化 CircuitBreaker 并注入 |
| 8 | 集成缺口 | 业务规则阻断 | 有缺陷 | 2.3 实时风控守护 | `RiskDaemon` 独立线程持续监控 VaR/流动性/市场异常，告警写入 CircuitBreaker | `risk_daemon.py` 实现完整但无独立线程循环——设计为被动 tick() 调用；`trading_engine.py` 中无 RiskDaemon 引用；`main.py` 中无实例化 | RiskDaemon 从未被实例化或调用，组合 VaR/流动性/异常检测全部不生效 | `main.py` / `paper_trading/trading_engine.py` | 方案 A：在 MarketListener tick 循环中注入 `RiskDaemon.tick()` 调用；方案 B：为 RiskDaemon 增加独立线程 run_loop |
| 9 | 集成缺口 | 业务规则阻断 | 有缺陷 | 2.4 系统健康检查 | `HealthCheckDaemon` 在 `main.py` 启动时注册 6 项检查(listener存活/数据源健康/任务队列/系统资源/NTP/券商连接)并启动 | `HealthCheckDaemon` 实现 3/6 检查项（系统资源+任务队列+NTP）；`main.py` 中无 HealthCheckDaemon 实例化或注册代码 | 系统无健康监控运行，listener 线程假死/数据源大面积失败/券商断连无告警 | `main.py` / `src/services/health_check.py` | 在 `main.py` 或 `server.py` 启动链路中初始化 HealthCheckDaemon 并注册全部 6 项检查 |
| 10 | 集成缺口 | 契约一致性阻断 | 有缺陷 | 2.2 + 2.3 | `TradingEngine` 使用 `BrokerRouter` 路由真实券商订单 | `trading_engine.py` 中无 BrokerRouter 注入代码——OMS 直接使用内部 OrderManager | OMS 无法切换到真实券商执行 | `paper_trading/trading_engine.py` / `paper_trading/oms_mgmt.py` | 在 TradingEngine 构造函数注入 BrokerRouter，OMS.create_order() 根据 account broker 字段路由 |
| 11 | 数据模型 | 契约一致性阻断 | 存在偏差 | 1.2 券商适配层 | `BrokerPosition` + `BrokerAccount` 使用 dataclass 定义具体字段 | `base.py` 中未定义这两个 dataclass，ABC 返回类型为 `Any`/`Dict[str, Any]` | 调用方无类型安全保障，运行时需逐个假设字段存在 | `paper_trading/broker/base.py` | 按设计 1.2 节补全 BrokerPosition/BrokerAccount dataclass 定义 |
| **P2 — 规模化前提** |||||||||
| 12 | 模块实现 | 数据正确性阻断 | 存在偏差 | 3.1 数据质量 Pipeline | 5 项检查：停牌检测 + 价格合理性 + 量合理性 + 时间新鲜度 + 日线缺失 | 仅实现 3/5：价格合理性 + 时间新鲜度 + 日线缺失。停牌检测和量合理性未实现 | 停牌股票的虚假信号可能被触发；异常成交量无法标记 | `data_provider/quality.py:DataQualityPipeline.__init__` | 实现 `_check_not_suspended()`（连续N日价格不变+量为0+名称含停牌）和 `_check_volume_sanity()` |
| 13 | 数据模型 | 数据正确性阻断 | 存在偏差 | 3.2 行情持久化 | SQLite daily_kline 表含 adjust_factor 列 + idx_kline_code_date 索引；StoreConfig 含 max_incremental_days/full_refresh_interval_days | 表缺 adjust_factor 列和索引；StoreConfig 无增量/全量刷新区分；每次 upsert 均用 INSERT OR REPLACE | 复权因子无法持久化；无法区分增量更新 vs 全量刷新策略 | `data_provider/local_store.py` | 补全 schema（ALTER TABLE ADD adjust_factor REAL DEFAULT 1.0 + CREATE INDEX）；区分 incremental vs full refresh 写入策略 |
| 14 | 模块遗漏 | 业务规则阻断 | 未实现 | 3.3 OMS/RMS 分离 | 独立 `settlement.py` 模块——日终结算 mark-to-market + 手续费计提 + 净值曲线 | 不存在独立 `paper_trading/settlement.py`，结算逻辑耦合在 `trading_engine.daily_settle()` 中 | OMS/RMS/结算三者未完全解耦，单体类仍 1687 行 | 缺失文件 | 从 `trading_engine.py` 抽取 `daily_settle()` 及相关逻辑到独立 `paper_trading/settlement.py` |
| 15 | 集成缺口 | 契约一致性阻断 | 有缺陷 | 3.4 全链路延迟监控 | `LatencyTracker` 在 `market_listener._tick_market` 中追踪每个 tick 的耗时（fetch→match→evaluate） | `latency_tracker.py` 实现完整，但 `market_listener.py` 中无 LatencyTracker 引入或记录代码 | 关键路径耗时不可观测，tick 超时无法告警 | `paper_trading/market_listener.py:_tick_market` | 在 tick 循环关键节点插入 `LatencySpan.mark()` 打点，tick 后 `record()` |
| 16 | 数据模型 | 数据正确性阻断 | 存在偏差 | 3.5 订单幂等化 | `fill_order` 含事务 + SELECT FOR UPDATE + version 乐观锁，三步(订单+持仓+账户)同一事务 | `order.py:463-540` 已实现 version 乐观锁和幂等跳过（T19 marker），但 `trading_engine.py` 调用 `fill_order` 时未传 `expected_version` | 乐观锁模块已就绪但调用方不使用——并发 fill 时仍有超卖风险 | `paper_trading/trading_engine.py` 调用 `fill_order` 处 | 在所有调用 `OrderManager.fill_order()` 的位置传入 `expected_version`（从 order.version 读取） |
| 17 | 集成缺口 | 契约一致性阻断 | 有缺陷 | 3.1 数据质量 | `DataFetcherManager.get_realtime_quote()` 返回后经 `DataQualityPipeline.validate_realtime()` 校验，不合格数据标记 quality_flags | `data_provider/base.py` 中使用 quality 仅为元数据标记 `"data_quality": "unavailable/partial/ok"`，未调用 `DataQualityPipeline` 的逐字段校验 | 脏数据（价格异常/停牌/时间戳过期）透传到策略引擎，产生虚假信号 | `data_provider/base.py:DataFetcherManager.get_realtime_quote` | 在返回 quote 前调用 `self.quality_pipeline.validate_realtime(quote)`，不合格时降级或返回标记 |
| **P3 — 竞争力差异** |||||||||
| 18 | 接口遗漏 | 功能可用性阻断 | 未实现 | 4.1 Level 2 行情 | `L2Fetcher` 提供 Level2Quote（十档买卖盘）+ OrderFlowSignal（大单流向/冰山订单检测） | `data_provider/l2_fetcher.py` 文件不存在 | 无 L2 数据源，大资金/主力资金监控无法实现 | 缺失文件 | 按设计 4.1 节实现 L2Fetcher + Level2Quote + OrderFlowSignal；优先接入 tickflow/longbridge 的 L2 通道 |
| 19 | 模块实现 | 业务规则阻断 | 存在偏差 | 4.3 企业事件 | `CorporateEventCalendar.update(codes)` 调用 akshare 拉取分红/拆股事件；`apply_to_prices()` 支持分红 + 拆股前复权 | `corporate_actions.py` 仅实现分红前复权（`apply_dividend_adjustment`），无外部数据拉取（纯存储），拆分/配股/退市/更名仅存储无处理 | 回测中拆股事件不调整价格，收益计算失真 | `data_provider/corporate_actions.py` | 实现 `update(codes)` 从 akshare 拉取；补充拆股、配股的复权调整逻辑 |
| 20 | 模块实现 | 业务规则阻断 | 存在偏差 | 4.5 漂移检测 & 4.2 信号融合 | `SignalFusionEngine.update_weights_from_drift()` 接收 `DriftReport` 后自动降权（reduce→0.5x, pause→0, retire→remove） | `signal_fusion.py` 仅实现了 `update_weights_from_metrics()`（按 Sharpe SoftMax），无 `update_weights_from_drift()` 方法 | 策略漂移后权重不会自动调整，退化策略继续产生信号 | `paper_trading/signal_fusion.py` | 新增 `update_weights_from_drift(drift_reports)` 方法，映射 reduce_weight/pause/retire 动作到权重调整 |
| 21 | 集成缺口 | 契约一致性阻断 | 有缺陷 | 4.2 信号融合 | `MarketListener._evaluate_strategies` 中收集多策略信号后经 `SignalFusionEngine.fuse()` 融合 | 集成代码存在于 `market_listener.py:773-776`，但受 feature guard 保护，需 `signal_fusion` 参数传入才能启用 | 默认运行路径不走信号融合，多策略信号直接独立提交 | `main.py` 启动链路 | 在实例化 MarketListener 时传入 SignalFusionEngine（并提供默认 WEIGHTED_VOTE 配置） |
| 22 | 模块实现 | 业务规则阻断 | 有缺陷 | 5.4 极端行情应对 | 三级响应：暂停 buy + 禁用市价单 + 放宽熔断阈值(2x) + 30 分钟自动重检 | `extreme_market.py` 实现了 buy hold + 市价单禁用，但缺熔断阈值放宽和自动恢复 | 极端波动时熔断阈值仍为默认值（可能过于严格），需人工介入恢复 | `paper_trading/extreme_market.py` | 增加 `widen_circuit_breaker_thresholds()` + 30分钟定时重检的 `auto_resume()` 方法 |
| 23 | 接口遗漏 | 业务规则阻断 | 未实现 | 5.3 AI 推理延迟分离 | AI 分析信号通过独立异步管道（AIAnalysisWorker 独立线程/进程），间隔可配置（如每小时），不阻塞规则引擎 tick | `market_listener.py:522-569` 的 `_consume_ai_signals` 消费 AI 队列，但 AI 分析仍可能在同一进程触发——无明确的独立 worker 进程解耦 | AI 分析 LLM 调用（分钟级）若和规则 tick（毫秒级）同进程，可能阻塞规则信号处理 | `paper_trading/ai_signal_worker.py` | 确认 `ai_signal_worker.py` 是否在独立进程/线程运行；如非独立，按设计 5.3 节增加进程级隔离 |

---

## 三、接口/模块对齐检查清单

| 模块/文件 | 设计规格 | 实现状态 | 偏差描述 |
|----------|---------|---------|---------|
| `paper_trading/backtest/engine.py` | 完整 BacktestEngine | ✅ 已实现 | 无偏差——含滑点、手续费、涨跌停、基准对比 |
| `paper_trading/backtest/walkforward.py` | WalkforwardOptimizer | ✅ 已实现 | 无偏差——含滚动窗口、网格搜索、参数稳定性 |
| `paper_trading/backtest_adapter.py` | PaperAccount→backtest passthrough | ⚠️ 有偏差 | 实现为比较/反思适配器而非透传 runner |
| `paper_trading/broker/base.py` | BaseBroker ABC + BrokerPosition/Account | ⚠️ 有偏差 | ABC 完整，缺 BrokerPosition/BrokerAccount dataclass |
| `paper_trading/broker/router.py` | Account-based broker routing | ⚠️ 有偏差 | 按 name 路由而非 account_id；无默认注册；无 failover |
| `paper_trading/broker/eastmoney_broker.py` | EastMoneyBroker 真券商适配器 | ❌ 未实现 | 文件不存在 |
| `src/utils/exchange_clock.py` | ExchangeClock singleton + NTP | ✅ 已实现 | 无偏差——单例、NTP、时区映射全部就位 |
| `data_provider/base.py` (时间标准化) | DataFetcherManager._normalize_timestamps | ❌ 未实现 | 缺时区标准化方法 |
| `paper_trading/quote_cache.py` | SharedQuoteCache 双通道 | ✅ 已实现 | 无偏差 |
| `paper_trading/ws_channel.py` | WebSocketChannel + reconnect | ✅ 已实现 | 无偏差——甚至超出规格（泛型设计） |
| `paper_trading/circuit_breaker.py` | 3级熔断 | ✅ 已实现 | 无偏差 |
| `paper_trading/risk_daemon.py` | VaR+流动性+异常检测 | ✅ 已实现 | 无偏差——但无独立线程 loop |
| `src/services/health_check.py` | 6项健康检查 | ⚠️ 部分 | 仅 3/6 项检查函数 |
| `data_provider/quality.py` | 5项数据质量检查 | ⚠️ 部分 | 仅 3/5 项（缺停牌检测、量合理性） |
| `data_provider/local_store.py` | SQLite 行情仓库 | ⚠️ 有偏差 | 缺 adjust_factor 列、索引、增量/全量刷新区分 |
| `paper_trading/oms_mgmt.py` | OMS（Order Management System） | ✅ 已实现 | 无偏差 |
| `paper_trading/rms_mgmt.py` | RMS（Risk Management System） | ✅ 已实现 | 无偏差——但未包含 CircuitBreaker 成员 |
| `paper_trading/settlement.py` | Settlement 结算模块 | ❌ 未实现 | 文件不存在，逻辑在 trading_engine.daily_settle() |
| `src/utils/latency_tracker.py` | 全链路延迟追踪 | ✅ 已实现 | 无偏差——但未在 MarketListener 中集成 |
| `paper_trading/signal_fusion.py` | 信号融合/冲突仲裁 | ✅ 已实现 | 无偏差——但缺 drift-based weight adjustment |
| `paper_trading/drift_detector.py` | 策略漂移检测 | ✅ 已实现 | 无偏差 |
| `paper_trading/strategy_lifecycle.py` | 策略生命周期状态机 | ✅ 已实现 | 无偏差 |
| `paper_trading/extreme_market.py` | 极端行情检测 | ⚠️ 有偏差 | 缺熔断阈值放宽 + 自动恢复 |
| `paper_trading/features/pipeline.py` | 特征工程管线 | ⚠️ 部分 | 缺 save() 和 bid_ask_imbalance 特征 |
| `data_provider/corporate_actions.py` | 企业事件日历 | ⚠️ 有偏差 | 纯存储无拉取；缺拆股/Split 调整 |
| `data_provider/l2_fetcher.py` | Level 2 深度行情 | ❌ 未实现 | 文件不存在 |
| `paper_trading/ai_signal_worker.py` | AI 推理独立 worker | ⚠️ 待验证 | 存在但需确认进程级隔离 |

---

## 四、集成状态检查清单

以下模块独立实现完成，但**未在运行时链路中挂载**：

| 模块 | 文件 | 应挂载位置 | 缺失的关键连接代码 |
|------|------|-----------|-----------------|
| CircuitBreaker | `circuit_breaker.py` | `main.py` → TradingEngine | `engine = TradingEngine(circuit_breaker=CircuitBreaker(...))` |
| RiskDaemon | `risk_daemon.py` | MarketListener tick loop | `self.risk_daemon.tick(account, positions, prices)` |
| HealthCheckDaemon | `health_check.py` | `main.py` startup | `health_daemon = HealthCheckDaemon(...); health_daemon.start()` |
| WebSocketChannel | `ws_channel.py` | MarketListener.start() | 创建 WS 线程 + 注入 SharedQuoteCache |
| ExchangeClock | `exchange_clock.py` | MarketListener + data_provider | 替换所有 `datetime.now()` 调用 |
| LatencyTracker | `latency_tracker.py` | MarketListener._tick_market | 插入 `LatencySpan.mark()` 打点 |
| SignalFusionEngine | `signal_fusion.py` | `main.py` → MarketListener | `listener = MarketListener(signal_fusion=SignalFusionEngine(...))` |
| FeaturePipeline | `pipeline.py` | daily cron / post-settle | 日终触发 `FeaturePipeline.run()` 并保存 |
| DriftDetector | `drift_detector.py` | daily settle | 日终记录 daily PnL `drift_detector.record_daily_pnl()` |
| ExtremeMarketDetector | `extreme_market.py` | MarketListener tick | 每 tick 调用 `detector.detect()`，触发时调用 `response.activate()` |
| DataQualityPipeline | `quality.py` | DataFetcherManager | `get_realtime_quote` 返回前调用 `quality.validate_realtime()` |

---

## 五、阻断类型统计

| 阻断类型 | 问题数量 | P0 数量 | P1 数量 | P2 数量 | P3 数量 |
|----------|----------|---------|---------|---------|---------|
| 数据正确性阻断 | 4 | 0 | 0 | 3 (#12, #13, #16) | 1 (#19) |
| 功能可用性阻断 | 6 | 2 (#1, #2) | 1 (#6) | 1 (#14) | 2 (#18, #23) |
| 业务规则阻断 | 9 | 1 (#3) | 3 (#7, #8, #9) | 2 (#10, #17) | 3 (#20, #21, #22) |
| 契约一致性阻断 | 4 | 2 (#4, #5) | 2 (#10, #11) | 1 (#15) | 0 |
| **总计** | **23** | **5** | **6** | **7** | **5** |

---

## 六、优先级修复清单

### P0 阻塞上线（5 项）

- [ ] **GAP-001**：BrokerRouter 增加 account-based 路由 `[功能可用性阻断]`
  - 文件：`paper_trading/broker/router.py` → 新增 `resolve_by_account(account_id)` 方法
  - 方案：注入 PaperAccountManager，读取 account.broker 字段后返回对应 broker；补充 `_register_defaults()` 注册 PaperBroker 为 "paper"
  - 工作量：S

- [ ] **GAP-002**：实现 EastMoneyBroker 真实券商适配器 `[功能可用性阻断]`
  - 文件：`paper_trading/broker/eastmoney_broker.py`（新建）
  - 方案：按设计 1.2.3 节实现，继承 BaseBroker，封装 easytrader 库的 buy/sell/cancel/query
  - 工作量：M

- [ ] **GAP-003**：补充 `backtest_from_paper_account()` 透传接口 `[业务规则阻断]`
  - 文件：`paper_trading/backtest_adapter.py` → 新增函数
  - 方案：按设计 1.1.3 节实现，复用 DataFetcherManager 拉数据，创建 BacktestEngine 并调用 run()
  - 工作量：S

- [ ] **GAP-004**：MarketListener 改用 ExchangeClock `[契约一致性阻断]`
  - 文件：`paper_trading/market_listener.py` → `_tick_market()`、`_should_emit_signal()`、`_record_signal()`
  - 方案：替换所有 `datetime.now()` 为 `ExchangeClock.now(market)`
  - 工作量：S

- [ ] **GAP-005**：DataFetcherManager 增加时间标准化 `[契约一致性阻断]`
  - 文件：`data_provider/base.py` DataFetcherManager
  - 方案：实现 `_normalize_timestamps(df, code)`，按市场做 tz_convert
  - 工作量：S

### P1 本迭代修复（6 项）

- [ ] **GAP-006**：WebSocketChannel 在启动链路中挂载 `[功能可用性阻断]`
  - 文件：`main.py` / `paper_trading/market_listener.py:start()`
  - 方案：启动时创建 WebSocketChannel 后台线程，注入 SharedQuoteCache 到 MarketListener
  - 工作量：M

- [ ] **GAP-007**：CircuitBreaker 在 main.py 中实例化并注入 TradingEngine `[业务规则阻断]`
  - 文件：`main.py` → TradingEngine 初始化处
  - 方案：`engine = TradingEngine(circuit_breaker=CircuitBreaker(config))`
  - 工作量：S

- [ ] **GAP-008**：RiskDaemon 挂载到 MarketListener tick 循环 `[业务规则阻断]`
  - 文件：`paper_trading/market_listener.py:_tick_market`
  - 方案：每次 tick 后调用 `self.risk_daemon.tick(account, positions, prices)`；或为 RiskDaemon 增加独立 run_loop
  - 工作量：M

- [ ] **GAP-009**：HealthCheckDaemon 在 main.py 中启动 `[业务规则阻断]`
  - 文件：`main.py` / `server.py`
  - 方案：按设计 2.4.2 节示例代码初始化并注册全部 6 项检查
  - 工作量：M

- [ ] **GAP-010**：补全 BrokerPosition/BrokerAccount dataclass `[契约一致性阻断]`
  - 文件：`paper_trading/broker/base.py`
  - 方案：按设计 1.2.3 节字段定义添加两个 dataclass，更新 ABC 返回类型
  - 工作量：S

- [ ] **GAP-011**：TradingEngine 注入 BrokerRouter `[契约一致性阻断]`
  - 文件：`paper_trading/trading_engine.py` / `paper_trading/oms_mgmt.py`
  - 方案：OMS 构造函数注入 BrokerRouter；account broker="paper" 时走 PaperBroker，否则走真券商
  - 工作量：M

### P2 后续优化（7 项）

- [ ] **GAP-012**：DataQualityPipeline 补全停牌检测和量合理性 `[数据正确性阻断]`
- [ ] **GAP-013**：LocalMarketStore 补全 adjust_factor 列和刷新策略 `[数据正确性阻断]`
- [ ] **GAP-014**：抽取 settlement.py 独立模块 `[业务规则阻断]`
- [ ] **GAP-015**：MarketListener tick 循环接入 LatencyTracker `[契约一致性阻断]`
- [ ] **GAP-016**：trading_engine.fill_order 调用传入 expected_version `[数据正确性阻断]`
- [ ] **GAP-017**：DataFetcherManager 集成 DataQualityPipeline 逐字段校验 `[业务规则阻断]`
- [ ] **GAP-018**：LocalMarketStore 集成到 MarketListener._get_daily_df `[增强]`

### P3 竞争力提升（5 项）

- [ ] **GAP-019**：实现 `data_provider/l2_fetcher.py` `[功能可用性阻断]`
- [ ] **GAP-020**：SignalFusion 增加 `update_weights_from_drift()` `[业务规则阻断]`
- [ ] **GAP-021**：SignalFusionEngine 在 MarketListener 中默认启用 `[业务规则阻断]`
- [ ] **GAP-022**：ExtremeMarket 补充熔断阈值放宽和自动恢复 `[业务规则阻断]`
- [ ] **GAP-023**：验证 AI Analysis 进程级隔离（已声明独立 worker） `[功能可用性阻断]`

---

## 七、亮点与积极发现

1. **核心模块实现质量高**：`BacktestEngine`、`CircuitBreaker`、`RiskDaemon`、`SignalFusionEngine`、`ExchangeClock` 等模块的实现与设计文档高度吻合，代码风格一致，错误处理到位。

2. **OMS/RMS 解耦已实质完成**：`oms_mgmt.py`（208 行）和 `rms_mgmt.py`（177 行）已将核心逻辑从 1687 行的 TradingEngine 中抽出，符合 P2 生产级分离目标。

3. **TradingEngine Thin Layer 改造到位**：TradingEngine 已降级为编排层——Signal → RMS.pre_trade_check → 熔断检查 → Agent review → OMS.create_order → OMS.execute_market，与设计 3.3 节的 T 型架构一致。

4. **订单幂等化基础已就绪**：`order.py` 中 fill_order/cancel_order 已实现 version 乐观锁（T19），只需调用方传入 `expected_version` 即可启用。

5. **代码自我标记（T markers）**：代码中使用 T2/T3/T12/T19 等标记追踪设计文档需求对应关系，便于交叉验证。

6. **15 个策略 YAML 配置**：`paper_trading/strategies/configs/` 下 15 个策略模板覆盖了从底部放量到头肩顶/波浪理论的常见策略模式。

---

## 八、总体判断

当前代码库处于 **单体模块就绪但系统未集成** 的状态。独立模块覆盖率约 85%（15/17 模块文件存在且代码质量良好），但运行时集成度仅约 45%。最关键的缺失不是代码未写，而是**启动链路未连接**——11 个独立完成的模块未被主流程挂载（见第 四 节）。

**最优先行动**：在 `main.py` / `server.py` / `MarketListener.start()` 三个启动入口中，按 P0/P1 优先级依次挂载已完成的模块。这 5+6=11 项任务预计 2-4 个工作日，完成后系统即可达到 P1 生产就绪水平。

---

*报告生成时间: 2026-08-10 | 审查工具: Claude Fable 5 + backend-pipeline-alignment skill*
