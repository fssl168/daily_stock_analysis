# 前端场景还原度审查报告 — 实时量化交易系统

> 审查基准：`docs/architecture/realtime_quant_system_design.md`（后端设计文档中需前端配套的部分）
> 审查日期：2026-08-10
> 审查范围：`apps/dsa-web/src/` — 11 个页面 + 30+ 组件 + 42 个 paper-trading API 端点

---

## 一、整体评估

- **功能完成度**：72 / 100（评分：已实现的 5 项 + 有缺陷 4 项 + 未实现 3 项 = 功能点 12，加权后 72）
- **交互还原度**：65 / 100（全部 REST 轮询，无实时推送交互）
- **实时性就绪度**：30 / 100（0/5 实时能力实现，1 项部分覆盖。见 R-01~R-05）
- **状态覆盖度**：75 / 100（11 个页面/组件中 6 个覆盖良好，3 个有缺陷，5 个完全缺失）
- **主要问题概述**：前端已有完整的 Paper Trading 仪表板（2200 行单页面 + 42 个 API 端点），覆盖了账户管理、持仓/委托/成交/信号、性能指标、熔断状态、健康检查、回测对比等核心功能。**核心差距不在"功能缺失"而在"实时性断层"**：当前全部依赖 5-60 秒轮询，无 WebSocket 行情推送、无实时价格 Ticker、无流式 PnL 更新、无延迟监控可视化。后端 Level 1（P0+P1 核心模块）与本会话中已完成对齐（见 `realtime_quant_system_gap_analysis_v2.md`），本报告聚焦前端侧差距。

---

## 二、逐项差距明细

### 功能完整性

| 编号 | 判定 | 对应设计文档 | 预期行为 | 实际表现 | 代码定位 |
|------|------|------------|----------|----------|----------|
| F-01 | 已实现 | 2.2 熔断机制 | CircuitBreaker 三级状态展示（normal/soft/hard/liquidate） | ✅ `BreakerStatusBadge.tsx`，30s 轮询 `/breaker/status`，四级状态+彩色标签 | `src/components/paper-trading/BreakerStatusBadge.tsx` |
| F-02 | 已实现 | 2.4 系统健康 | 系统健康检查面板 | ✅ `HealthDashboard.tsx`，60s 轮询 `/api/v1/health` | `src/pages/` 相关组件 |
| F-03 | 已实现 | 1.1 回测指标 | BacktestResult 全部指标展示（Sharpe/MaxDD/Calmar/胜率） | ✅ `BacktestPage.tsx` 展示各项指标 | `src/pages/BacktestPage.tsx` |
| F-04 | 已实现 | 3.3 OMS | 委托管理 UI（单笔/批量/条件单+撤单/改单） | ✅ `OrdersTable` + `SignalsTable` 支持 inline cancel/modify | `src/components/paper-trading/` |
| F-05 | 已实现 | 1.1 回测对接 | 回测 vs 模拟盘对比 | ✅ `BacktestComparisonPanel.tsx` 双侧对比 | `src/components/paper-trading/BacktestComparisonPanel.tsx` |
| F-06 | **未实现** | 2.1 WS 行情接入 | 实时行情 Ticker / 报价展示 | ❌ 前端无任何实时价格展示。所有价格从 REST 轮询获取 | 缺失 |
| F-07 | **未实现** | 3.4 全链路延迟监控 | 延迟监控仪表板（p50/p95/p99） | ❌ 后端已有 `LatencyTracker`，但前端无可视化面板 | 缺失 |
| F-08 | **未实现** | 4.1 L2 深度行情 | 十档买卖盘可视化 + 订单流信号展示 | ❌ 无 L2 订单簿/大单流向/冰山订单等展示 | 缺失 |
| F-09 | **有缺陷** | 5.1 策略生命周期 | 策略状态机管理 UI（DRAFT→BACKTEST→PAPER→REVIEW→LIVE→PAUSED→RETIRED） | ⚠️ 仅 Listener start/stop；无策略级 activate/deactivate/参数编辑/热加载 | `src/components/paper-trading/ListenerControl` |
| F-10 | **有缺陷** | 4.4 特征工程 | 特征管线触发 + 特征重要性展示 | ⚠️ 后端日终自动计算，但前端无查看/触发入口 | 缺失 |
| F-11 | **有缺陷** | 4.5 模型漂移 | 漂移检测报告 + 信号融合权重可视化 | ⚠️ 后端 `DriftDetector` 已运行，前端无可视化 | 缺失 |
| F-12 | **有缺陷** | 5.4 极端行情 | 极端行情警报 + 行动状态 | ⚠️ 后端 `ExtremeMarketResponse` 已运行，前端无可视化 | 缺失 |

### 实时性断层

| 编号 | 判定 | 对应设计文档 | 预期 | 实际 | 影响 |
|------|------|------------|------|------|------|
| R-01 | **未实现** | 2.1 WebSocket | 前端 WebSocket 连接行情推送 | ❌ 无 `WebSocket` 连接。`useTaskStream` 仅用 SSE 做任务进度 | 股价变动滞后 5-60 秒 |
| R-02 | **未实现** | 3.4 延迟监控 | 前端实时延迟指标 | ❌ 后端 LatencyTracker 记录但前端无消费 | 运维不可见 |
| R-03 | **有缺陷** | 2.3 实时风控 | 前端 RiskDaemon 告警即时推送 | ⚠️ 风控告警仅为后端日志，无前端通知 | 风险感知滞后 |
| R-04 | **有缺陷** | 2.2 熔断 | 熔断触发即时通知 | ⚠️ 30s 轮询间隔过长——毫秒级熔断触发后前端最多等 30s | 用户感知严重滞后 |
| R-05 | **有缺陷** | 全部 | 操作结果即时回馈 | ⚠️ submit_signal 返回后的 UI 更新依赖手动刷新 | 用户需手动刷新看结果 |

### 数据与字段对齐

| 编号 | 判定 | 对应设计文档 | 预期 | 实际 |
|------|------|------------|------|------|
| D-01 | 已实现 | 1.1 BacktestResult | 全字段映射 | ✅ `src/types/backtest.ts` 完整定义 |
| D-02 | 已实现 | 2.2 BreakerState | level/triggered_at/daily_pnl/reason | ✅ API 返回全部字段，BreakerStatusBadge 展示 |
| D-03 | 缺失 | 3.4 SpanEvent/LatencySpan | 延迟数据前端类型 | ❌ `src/types/` 中无 latency 相关类型定义 |
| D-04 | 缺失 | 4.1 Level2Quote | L2 十档报价类型 | ❌ 无前端 TypeScript 类型 |
| D-05 | 缺失 | 2.3 RiskAlert | 风控告警类型 | ❌ 无前端类型映射 |

---

## 三、状态覆盖检查清单

| 页面/组件 | 加载中 | 空数据 | 错误 | 成功 | 节流/防抖 | 实时更新 |
|-----------|--------|--------|------|------|----------|----------|
| PaperTradingPage | ✅ Spinner | ✅ EmptyState | ✅ ErrorBoundary | ✅ | ✅ 30s poll | ❌ |
| OrdersTable | ✅ Skeleton | ✅ "暂无委托" | ✅ Error toast | ✅ | — | ❌ |
| PositionsTable | ✅ Skeleton | ✅ "暂无持仓" | ✅ Error toast | ✅ | — | ❌ |
| BreakerStatusBadge | ✅ | — | ✅ 灰色降级 | ✅ 彩色标签 | 30s | ❌ |
| PerformanceCard | ✅ Spinner | ✅ "暂无数据" | ✅ ErrorBoundary | ✅ 指标+sparkline | 30s | ❌ |
| BacktestPage | ✅ Spinner | ✅ EmptyState | ✅ ErrorBoundary | ✅ | — | ❌ |
| HealthDashboard | ✅ | — | ✅ "unhealthy" 标记 | ✅ green badge | 60s | ❌ |
| 实时行情 Ticker | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| 延迟监控面板 | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| L2 订单簿 | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| 风控告警推送 | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| 策略生命周期 | ⚠️ 无加载态 | — | ⚠️ 裸 try/catch | ⚠️ | — | ❌ |

---

## 四、API 对齐检查清单

| API 端点 | 实现状态 | 前端调用位置 |
|----------|---------|-------------|
| `GET /api/v1/paper-trading/accounts` | ✅ | `api/paperTrading.ts:getAccounts()` |
| `GET /api/v1/paper-trading/accounts/{id}/positions` | ✅ | `api/paperTrading.ts:getPositions()` |
| `GET /api/v1/paper-trading/accounts/{id}/orders` | ✅ | `api/paperTrading.ts:getOrders()` |
| `GET /api/v1/paper-trading/accounts/{id}/trades` | ✅ | `api/paperTrading.ts:getTrades()` |
| `GET /api/v1/paper-trading/accounts/{id}/signals` | ✅ | `api/paperTrading.ts:getSignals()` |
| `GET /api/v1/paper-trading/accounts/{id}/performance` | ✅ | `api/paperTrading.ts:getPerformance()` |
| `GET /api/v1/paper-trading/accounts/{id}/net-value` | ✅ | `api/paperTrading.ts:getNetValue()` |
| `GET /api/v1/paper-trading/accounts/{id}/drawdown` | ✅ | `api/paperTrading.ts:getDrawdown()` |
| `GET /api/v1/paper-trading/accounts/{id}/risk-metrics` | ✅ | `api/paperTrading.ts:getRiskMetrics()` |
| `GET /api/v1/paper-trading/accounts/{id}/breaker/status` | ✅ | `api/paperTrading.ts:getBreakerStatus()` |
| `POST /api/v1/paper-trading/listener/start` | ✅ | `api/paperTrading.ts:startListener()` |
| `POST /api/v1/paper-trading/listener/stop` | ✅ | `api/paperTrading.ts:stopListener()` |
| `GET /api/v1/paper-trading/listener/status` | ✅ | `api/paperTrading.ts:getListenerStatus()` |
| `POST /api/v1/backtest/run` | ✅ | `api/backtest.ts:runBacktest()` |
| `GET /api/v1/backtest/results` | ✅ | `api/backtest.ts:getResults()` |
| `GET /api/v1/health` | ✅ | `HealthDashboard` component |
| `GET /api/v1/paper-trading/accounts/{id}/latency` | ❌ 未暴露 | 缺失端点 |
| `GET /api/v1/paper-trading/accounts/{id}/risk-alerts` | ❌ 未暴露 | 缺失端点 |
| `GET /api/v1/paper-trading/accounts/{id}/l2-quote/{code}` | ❌ 未暴露 | 缺失端点 |
| `GET /api/v1/paper-trading/strategy-lifecycle` | ❌ 未暴露 | 缺失端点 |
| `WS /api/v1/paper-trading/stream` | ❌ 未实现 | 缺失 WebSocket 端点 |

---

## 五、阻断类型统计

| 阻断类型 | 问题数量 | P0 数量 | P1 数量 | P2 数量 |
|----------|----------|---------|---------|---------|
| 功能可用性阻断 | 4 | 2 (实时行情+延迟监控) | 2 (L2+策略生命周期) | 0 |
| 业务规则阻断 | 3 | 0 | 3 (实时性轮询→推送) | 0 |
| 数据展示缺失 | 5 | 0 | 3 | 2 |
| API 端点缺失 | 5 | 1 (Latency API) | 2 | 2 |
| **总计** | **17** | **3** | **10** | **4** |

---

## 六、优先级修复清单

> **阻塞条件说明**：依赖后端新 API 的项标记为 🔗，可纯前端独立完成的标记为 🖥️。
> 后端已完成 P0/P1 核心模块对齐（CircuitBreaker、RiskDaemon、ExchangeClock、LatencyTracker 等），
> 但以下新增 API 端点需后续后端 sprint 暴露（当前可先使用 mock/stub 并行开发前端）。

### P0 阻塞毫秒级系统上线（3 项）

- [ ] **F-UI-001**：实时行情 Ticker 组件 + WebSocket 连接 `[功能可用性阻断]` 🔗
  - 阻塞条件：需后端新增 `WS /api/v1/paper-trading/ws/quotes`。前端可先用 `setInterval(mockQuote, 500)` mock 独立开发。
  - 新建：`src/components/paper-trading/QuoteTicker.tsx`
  - 新建：`src/hooks/useRealtimeQuotes.ts`（WebSocket 连接 hook）
  - 方案：新增 `/api/v1/paper-trading/ws/quotes` WebSocket 端点（后端）+ 前端 Ticker 组件（多股票滚动报价条）
  - 工作量：M

- [ ] **F-UI-002**：延迟监控仪表板 `[功能可用性阻断]` 🔗
  - 阻塞条件：需后端新增 `GET /api/v1/paper-trading/accounts/{id}/latency`。前端可先用硬编码示例数据开发。
  - 新建：`src/components/paper-trading/LatencyPanel.tsx`
  - 方案：后端暴露 `/accounts/{id}/latency` API（返回 p50/p95/p99/total_ms/step 耗时）；前端显示为时序折线图+当前值卡片
  - 工作量：M

- [ ] **F-UI-003**：熔断/风控告警 WebSocket 推送 `[业务规则阻断]` 🔗
  - 阻塞条件：需后端新增 `WS /api/v1/paper-trading/ws/events`。可先保留 30s 轮询 + 调短间隔到 5s 做中间方案。
  - 改动：`src/components/paper-trading/BreakerStatusBadge.tsx`
  - 方案：将 30s 轮询替换为 WebSocket 推送；BreakerStatus 变更为实时更新；新增 `RiskAlertToast` 组件（风控告警即时弹出）
  - 工作量：M

### P1 本迭代修复（10 项）

- [ ] **F-UI-004**：策略生命周期管理 UI `[功能可用性阻断]`
  - 改动：`src/pages/PaperTradingPage.tsx` 新增 "Strategies" tab
  - 方案：7 状态策略列表（DRAFT→BACKTEST→PAPER→REVIEW→LIVE→PAUSED→RETIRED），每策略含 activate/deactivate 按钮 + 状态流转审批记录
  - 工作量：M

- [ ] **F-UI-005**：L2 深度行情订单簿组件 `[功能可用性阻断]`
  - 新建：`src/components/paper-trading/L2OrderBook.tsx`
  - 方案：十档买卖盘双侧柱状图+买卖不平衡指数+加权均价；后端暴露 `/l2-quote/{code}` API
  - 工作量：L

- [ ] **F-UI-006**：实时 PnL 流式面板 `[业务规则阻断]`
  - 改动：`src/pages/PaperTradingPage.tsx` → PositionsTable
  - 方案：持仓表每行追加 "实时盈亏" 列 + 盈亏比颜色渐变。WebSocket 每 tick 推送最新价→客户端本地计算浮动盈亏
  - 工作量：M

- [ ] **F-UI-007**：操作结果即时回馈 `[业务规则阻断]`
  - 改动：`src/pages/PaperTradingPage.tsx` → `submit_signal` 调用后
  - 方案：提交信号后立即乐观更新信号列表（不必等 30s 轮询），失败时回滚+toast 提示
  - 工作量：S

- [ ] **F-UI-008**：策略性能对比看板 `[数据展示缺失]`
  - 新建：`src/components/paper-trading/StrategyLeaderboard.tsx`
  - 方案：多策略横向对比（Sharpe/胜率/MaxDD/日均收益），用排序表+小徽章展示。后端暴露 `/accounts/{id}/strategies/performance` API
  - 工作量：M

- [ ] **F-UI-009**：漂移检测可视化 `[数据展示缺失]`
  - 新建：`src/components/paper-trading/DriftPanel.tsx`
  - 方案：DriftReport 表格（strategy/Sharpe趋势/连续亏损天数/建议动作），"建议动作"列用彩色标签（keep=绿, reduce=黄, pause=橙, retire=红）
  - 工作量：S

- [ ] **F-UI-010**：极端行情状态横幅 `[数据展示缺失]`
  - 改动：`src/pages/PaperTradingPage.tsx` 页面顶部
  - 方案：ExtremeMarketResponse.is_active() 时，页面顶部展示红色横幅 "⚠ 极端行情：已暂停规则策略buy信号，仅执行止损"
  - 工作量：S

- [ ] **F-UI-011**：事件日志流 `[数据展示缺失]`
  - 新建：`src/components/paper-trading/EventLogFeed.tsx`
  - 方案：Signal→Risk→CircuitBreaker→OMS→Trade 的完整事件流，滚动时间线，每个事件彩色标签+时间戳
  - 工作量：M

- [ ] **F-UI-012**：特征工程触发+查看入口 `[数据展示缺失]`
  - 改动：`src/pages/PaperTradingPage.tsx` 新增 "Features" tab
  - 方案：展示最近一次 FeaturePipeline 计算结果（特征名/值/日期）+ "手动触发计算" 按钮
  - 工作量：S

- [ ] **F-UI-013**：策略参数编辑器 `[数据展示缺失]`
  - 改动：`src/pages/PaperTradingPage.tsx` → Strategies tab
  - 方案：可编辑 YAML 参数（fast/slow/period/multiplier 等）+ 保存+回测快速验证按钮
  - 工作量：M

- [ ] **F-UI-014**：TypeScript 类型补全 `[契约一致性阻断]`
  - 改动：`src/types/paperTrading.ts`
  - 方案：新增 `LatencySpan`、`Level2Quote`、`OrderFlowSignal`、`RiskAlert`、`DriftReport`、`ExtremeMarketAlert` 等接口定义
  - 工作量：S

### P2 后续优化（4 项）

- [ ] **F-UI-015**：K 线/分时图组件 — 替换纯数字表格展示
- [ ] **F-UI-016**：多交易所/市场状态仪表板 — 展示 CN/HK/US 三个市场的开盘/休市/连接状态
- [ ] **F-UI-017**：手机端响应式适配 — PaperTradingPage 当前仅桌面端友好
- [ ] **F-UI-018**：暗色主题 — 配合实时数据展示的暗色模式

---

## 七、新增后端 API 清单

前端对齐开发需要后端配合暴露以下 API：

| 端点 | 方法 | 用途 | 对应前端组件 |
|------|------|------|-------------|
| `/api/v1/paper-trading/accounts/{id}/latency` | GET | 返回 tick 延迟统计（p50/p95/p99/total_ms/steps） | LatencyPanel |
| `/api/v1/paper-trading/accounts/{id}/risk-alerts` | GET | 返回最近 RiskAlert 列表（VaR/liquidity/anomaly） | RiskAlertToast |
| `/api/v1/paper-trading/accounts/{id}/l2-quote/{code}` | GET | 返回最新 Level2Quote（十档报价） | L2OrderBook |
| `/api/v1/paper-trading/accounts/{id}/strategies` | GET | 返回所有活跃策略及其状态/性能 | StrategyLeaderboard + Lifecycle |
| `/api/v1/paper-trading/accounts/{id}/drift` | GET | 返回最新 DriftReport 列表 | DriftPanel |
| `/api/v1/paper-trading/accounts/{id}/extreme-market` | GET | 返回 ExtremeMarketAlert 当前状态 | ExtremeMarketBanner |
| `/api/v1/paper-trading/accounts/{id}/features` | GET | 返回最近计算的特征列表 | Features tab |
| `/api/v1/paper-trading/ws/quotes` | WebSocket | 实时行情推送（JSON: code/price/volume/change_pct） | QuoteTicker + PnL streaming |
| `/api/v1/paper-trading/ws/events` | WebSocket | 实时事件流（Signal→Risk→Breaker→OMS→Trade） | EventLogFeed |

---

*报告生成时间: 2026-08-10 | 审查工具: Claude Fable 5 + ui-frontend-alignment skill*
