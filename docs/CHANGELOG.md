# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

- [新功能] 实时量化交易系统 v2 全面对齐：23 项后端 gap 全部闭合，覆盖 P0（回测引擎/券商适配/NTP 时钟）→ P1（WebSocket 行情/三级熔断/实时风控守护/健康检查）→ P2（数据质量 Pipeline/行情持久化/OMS 与 RMS 分离/全链路延迟监控/订单幂等化）→ P3（L2 深度行情/信号融合与冲突仲裁/企业事件处理/特征工程管线/策略漂移检测）
- [新功能] Paper Trading 毫秒级实时仪表板：15 个前端组件（QuoteTicker、RiskAlertToast、EventLogFeed、LatencyPanel、ExtremeMarketBanner、DriftPanel、StrategyLifecyclePanel、StrategyLeaderboard、FeaturesPanel、MarketStatusDashboard、CandlestickChart 等），WebSocket 共享单例基础架构
- [新功能] 三级熔断机制（soft 3% / hard 5% / liquidation 8%）+ 24h 冷却期 + VaR 联动
- [新功能] 实时风控守护进程：组合 VaR（历史模拟法+参数法）、流动性风险监控、市场异常检测
- [新功能] 信号融合引擎：多策略加权投票（Sharpe SoftMax）、60% 共识阈值、漂移检测自动降权
- [新功能] 极端行情应对：VIX-like 波动率检波，触发后暂停 buy 信号 + 禁用市价单
- [新功能] 策略生命周期管理：DRAFT → BACKTEST → PAPER → REVIEW → LIVE → PAUSED → RETIRED 七阶段状态机
- [新功能] 完整回测框架：逐 bar 历史回测（前向防作弊）+ 滑点/手续费/涨跌停 + Walk-forward 滚动优化 + 回测 vs 纸面对比
- [新功能] 券商接口适配层：BrokerRouter 多源路由（PaperBroker + EastMoneyBroker），账户级别 broker 解析，券商断连自动 fallback
- [新功能] 统一时钟源 ExchangeClock：NTP 同步，按交易所时区自动校准，全模块统一时间基准
- [新功能] 全链路延迟监控 LatencyTracker：p50/p95/p99 百分位统计 + 步骤级耗时拆分
- [新功能] 系统健康检查 HealthCheckDaemon：6 项检查（listener/数据源/任务队列/系统资源/NTP/券商）
- [新功能] OMS 订单管理系统 + RMS 风控管理系统从 TradingEngine 分离重构
- [新功能] 订单乐观锁并发控制（version 字段 + expected_version）
- [新功能] 日终结算 Settlement 模块：Mark-to-market 持仓市值重估 + 净值曲线计算
- [新功能] 特征工程管线 FeaturePipeline：日终自动计算 SMA/RSI/量能突增/多头排列/L2 买卖不平衡
- [新功能] L2 深度行情 L2Fetcher：十档买卖盘快照 + 订单流信号（大单/冰山/幌骗）
- [新功能] 企业事件处理 CorporateEventCalendar：从 akshare 拉取分红/拆股事件，前复权调整
- [改进] ExchangeClock 替换全部 datetime.now() 调用，消除多时间源不一致问题
- [改进] MarketListener tick 循环接入 LatencyTracker 打点，>1s 自动 WARNING
- [改进] CircuitBreaker 注入 TradingEngine 启动链路，默认启用（可通过环境变量关闭）
- [改进] RiskDaemon 挂载到 MarketListener 每 tick 循环，VaR 告警联动 CircuitBreaker
- [改进] SignalFusionEngine 通过 build_default_listener 默认启用 WEIGHTED_VOTE 模式
- [改进] HealthCheckDaemon 在 server.py 和 main.py 中启动，HEALTH_CHECK_ENABLED 一键激活
- [改进] BrokerRouter 增加 resolve_by_account() + 默认 PaperBroker 注册 + BrokerPosition/BrokerAccount dataclass
- [改进] DataQualityPipeline 补全 5 项检查（停牌检测/价格合理性/量合理性/时间新鲜度/日线缺失）
- [改进] LocalMarketStore schema 补全 adjust_factor 列 + idx_kline_code_date 索引
- [改进] fill_order 调用传入 expected_version 启用乐观锁
- [改进] backtest_adapter 增加 backtest_from_paper_account() passthrough 接口
- [修复] strategies_v2 导入路径迁移为 paper_trading.strategies（测试、trade_engine、pm_agent、smoke 脚本）
- [修复] OMS status 契约漂移：filled 标准化为 executed，保持外部 TradeResult 契约稳定
- [修复] ReflectionEngine 补齐 7 个缺失方法 + ReflectionNote 构造器 + DetachedInstanceError 修复
- [修复] _compute_note_score 的 float('exp(...)') 语法 bug → math.exp()
- [修复] _get_daily_df 兼容 get_daily_historical / get_daily_data 双方法名
- [修复] interceptor.ts 15 个预存在 TS 错误（react-hot-toast / AuthContext / 未定义变量）
- [文档] README 纸面交易章节升级为毫秒级实时量化执行系统
- [文档] docs/INDEX.md 新增 7 个 Paper Trading / 实时量化文档入口
- [文档] docs/full-guide.md 新增纸面交易系统完整章节（配置/功能/架构/API）
- [文档] .env.example 新增 v2 实时量化系统全部环境变量（熔断/风控/WS/信号融合/健康检查/券商/回测等）
- [文档] 新增 6 份专项文档：架构设计 / 后端差距分析 v2 / 后端实施计划 / 前端差距分析 / 前端差距分析 v2 / 前端实施计划
- [测试] paper_trading 测试套件 94/94 全部通过
