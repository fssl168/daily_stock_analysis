# 实时量化交易系统 — 前端毫秒级实施计划

> **目标**：将前端从 "秒级轮询查询系统" 升级为 "毫秒级实时量化执行面板"
> **执行原则**：先建基础（WebSocket 通道 + Ticker），再补齐缺失组件，最后精细打磨
> **基准分析**：`docs/frontend_quant_alignment_gap_analysis.md`（17 项 gap）
> **配套后端**：`docs/realtime_quant_system_implementation_plan.md`（后端已 100% 对齐）

---

## 实施策略

后端核心模块（P0+P1：CircuitBreaker、RiskDaemon、ExchangeClock、LatencyTracker 等）已在本会话中完成代码级对齐（见 `realtime_quant_system_gap_analysis_v2.md`）。前端暴露的核心问题是 **实时性断层**：

| 后端能力 | 前端现状 |
|---------|---------|
| WebSocket 模块已实现（`ws_channel.py`） | ❌ 前端无 WebSocket，仅 30s 轮询 |
| LatencyTracker 后端已记录 | ❌ 前端无消费面板 |
| CircuitBreaker 毫秒级熔断 | ⚠️ 前端 30s 轮询，滞后 30 倍 |
| RiskDaemon VaR/流动性/异常 | ❌ 前端无通知 |
| SignalFusion 融合+漂移权调 | ❌ 前端无可视化 |

> **注意**：前端实施计划中标记了 10 个需后端暴露的新 API 端点（API-01~API-10）。
> 这些端点的后端实现未包含在本会话范围内。建议前端先用 **mock 数据 + stub 端点** 独立开发，
> 后端 API 按 sprint 逐步交付后切换为真实数据。

策略：**Phase 1 建立 WebSocket 基础设施（P0）→ Phase 2 补齐缺失面板（P1）→ Phase 3 精细化打磨（P2）**。

---

## Phase 1：WebSocket 基础设施层（WS-Layer）— 2 天

> 目标：建立前端 WebSocket 连接通道，替换 30s 轮询为实时推送

### WS-001：WebSocket Hook 基础设施

新建：`src/hooks/useWebSocket.ts`

```typescript
// 通用 WebSocket hook — 自动重连 + 指数退避 + 心跳
interface UseWebSocketOptions {
  url: string;
  onMessage: (data: any) => void;
  onOpen?: () => void;
  onClose?: () => void;
  reconnectInterval?: number;  // 默认 1s
  maxReconnectInterval?: number; // 默认 30s
}

function useWebSocket(options: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number>();
  const reconnectDelayRef = useRef(options.reconnectInterval || 1000);

  const connect = useCallback(() => {
    const ws = new WebSocket(options.url);
    ws.onopen = () => {
      reconnectDelayRef.current = options.reconnectInterval ?? 1000;
      options.onOpen?.();
    };
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      options.onMessage(data);
    };
    ws.onclose = () => {
      options.onClose?.();
      reconnectTimerRef.current = window.setTimeout(connect, reconnectDelayRef.current);
      reconnectDelayRef.current = Math.min(
        reconnectDelayRef.current * 2,
        options.maxReconnectInterval ?? 30000,
      );
    };
    ws.onerror = () => ws.close();
    wsRef.current = ws;
  }, [options.url]);

  useEffect(() => { connect(); return () => { wsRef.current?.close(); clearTimeout(reconnectTimerRef.current); }; }, [connect]);

  return { send: (data: any) => wsRef.current?.send(JSON.stringify(data)), close: () => wsRef.current?.close() };
}
```

---

### WS-002：实时行情 Ticker

新建：`src/components/paper-trading/QuoteTicker.tsx`

功能：页面顶部滚动条，展示所有 watched_codes 的最新价、涨跌幅、成交量。

```typescript
// 使用 useWebSocket + SharedQuoteCache → WebSocket 推送逐 tick 更新
// 显示格式：[代码] 18.50 ↑2.3% vol=1.2M
// 5 秒无推送自动降级为 "--" 灰色
```

API：`WS /api/v1/paper-trading/ws/quotes`（需后端暴露——推送结构：`{code, price, change_pct, volume, timestamp}`）

---

### WS-003：熔断/风控告警 WebSocket 推送

改动：`src/components/paper-trading/BreakerStatusBadge.tsx`

将当前 30s 轮询 `getBreakerStatus()` 替换为 WebSocket 推送：

```typescript
// 现有: setInterval(() => api.getBreakerStatus(accountId), 30000)
// 改为: useWebSocket({ url: `/ws/paper-trading/${accountId}/events`, onMessage: (evt) => {
//   if (evt.type === 'breaker') setBreakerState(evt.data);
//   if (evt.type === 'risk_alert') showRiskAlertToast(evt.data);
// }})
```

新增：`src/components/paper-trading/RiskAlertToast.tsx`
- VaR breaching → 红色 toast："组合 VaR 95% 告警：$X,XXX（阈值 $Y,YYY）"
- liquidity warning → 黄色 toast："[代码] 流动性警告：换手率 0.2%（< 0.5% 阈值）"
- market anomaly → 红色 toast："[市场] 波动率异常：当前波动率 65% vs 历史均值 18%"

---

### WS-004：实时 PnL 流式更新

改动：`src/pages/PaperTradingPage.tsx` → PositionsTable

```typescript
// 现有: positions 从 REST GET 获取，静态展示
// 改为:
// 1. REST 获取基础持仓数据（code/qty/avg_cost）
// 2. useWebSocket 逐 tick 接收最新价（code → price）
// 3. 本地计算 floating PnL = (latestPrice - avgCost) * qty
// 4. 实时更新表格 "浮动盈亏" 列（绿色正/红色负，0.5s 过渡动画）
```

---

### WS-005：事件日志流

新建：`src/components/paper-trading/EventLogFeed.tsx`

```typescript
// WebSocket 订阅 `/ws/paper-trading/{id}/events`
// 事件类型: signal_generated → risk_check_passed → agent_review_approved
//           → breaker_check_ok → order_created → order_filled
// 滚动时间线展示，每个事件带彩色标签 + 时间戳 + 关联 ID
```

---

## Phase 2：缺失面板补齐层（Panel-Layer）— 3 天

> 目标：补齐后端已运行但前端尚未可视化的所有模块面板

### PN-001：延迟监控仪表板

新建：`src/components/paper-trading/LatencyPanel.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/latency
// 返回: { tick_total_ms_p50, p95, p99, steps: [{name:"fetch_prices", p50_ms, p95_ms}, ...] }
// 展示: 当前 tick 耗时大数字卡片 + 各步骤耗时细分折线图(Chart.js)
// 阈值线: 1s (WARNING) 标黄，2s (ERROR) 标红
```

API（需后端暴露）：`GET /api/v1/paper-trading/accounts/{id}/latency`

---

### PN-002：策略生命周期管理

新建：`src/components/paper-trading/StrategyLifecyclePanel.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/strategies
// 返回策略列表: [{name, state, sharpe, winRate, weights, approvals: [{from, to, operator, ts}]}]
// 功能:
// - 7 状态指示器 (DRAFT→BACKTEST→PAPER→REVIEW→LIVE→PAUSED→RETIRED)
// - activate/deactivate 按钮（调用 PUT 状态流转）
// - 审批历史折叠面板
// - 策略参数预览（YAML snippet）
```

API（需后端暴露）：`GET/PUT /api/v1/paper-trading/accounts/{id}/strategies`

---

### PN-003：L2 深度行情订单簿

新建：`src/components/paper-trading/L2OrderBook.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/l2-quote/{code} + WebSocket 增量推送
// 展示: 双侧柱状图（买盘红→绿渐变/卖盘绿→红渐变）
// 顶部: 代码、最新价、加权买盘/卖盘、深度加权价差
// 中间: 买一→买十（左侧）| 卖一→卖十（右侧）
// 底部: bid_ask_imbalance 百分比仪表盘 + 大单流向指示器
```

API（需后端暴露）：`GET /api/v1/paper-trading/accounts/{id}/l2-quote/{code}`

---

### PN-004：策略性能对比看板

新建：`src/components/paper-trading/StrategyLeaderboard.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/strategies/performance
// 展示: 表格: [策略名 | Sharpe | 胜率 | MaxDD% | Calmar | 日均收益 | 当前权重 ]
// 排序列: Sharpe (默认降序)
// 每行右侧小 sparkline (近 N 日累计收益)
// "权重" 列显示 SignalFusion 当前权重 → 漂移检测降权时显黄/红
```

API（需后端暴露）：`GET /api/v1/paper-trading/accounts/{id}/strategies/performance`

---

### PN-005：漂移检测面板

新建：`src/components/paper-trading/DriftPanel.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/drift
// 返回: DriftReport[] → {strategy_name, is_drifting, rolling_sharpe, sharpe_trend, consecutive_losing_days, recommended_action}
// 展示: 表格 [策略名 | 滑动Sharpe趋势 | 连续亏损天数 | 建议动作(彩色标签) ]
// 动作颜色: keep=绿, reduce_weight=黄, pause=橙, retire=红
// 点击策略行展开滚动 Sharpe 折线图
```

API（需后端暴露）：`GET /api/v1/paper-trading/accounts/{id}/drift`

---

### PN-006：极端行情状态横幅

改动：`src/pages/PaperTradingPage.tsx` → 页面顶部插入新组件

新建：`src/components/paper-trading/ExtremeMarketBanner.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/extreme-market
// 返回: { is_active, market, current_vol, historical_vol, ratio, actions, detected_at }
// 激活时: 页面顶部红色横幅 "⚠ 极端行情({market})：当前波动率{current_vol}为历史均值{historical_vol}的{ratio}倍"
//         子行显示 actions (如 "已暂停规则策略buy信号，仅执行止损")
// 非激活: 不渲染
```

API（需后端暴露）：`GET /api/v1/paper-trading/accounts/{id}/extreme-market`

---

### PN-007：特征工程面板

新建：`src/components/paper-trading/FeaturesPanel.tsx`

```typescript
// 数据源: GET /api/v1/paper-trading/accounts/{id}/features
// 返回: { as_of: date, features: [{code, date, sma_crossover, rsi, volume_spike, ma_alignment, bid_ask_imbalance}], skipped_codes }
// 展示: 表格 [代码 | 日期 | SMA信号 | RSI(数值+颜色) | 量能突增 | 多头排列 | 买卖不平衡]
// 按钮: "重新计算特征" → POST trigger feature pipeline
// 状态: pipeline running → spinner
```

API（需后端暴露）：`GET/POST /api/v1/paper-trading/accounts/{id}/features`

---

### PN-008：策略参数编辑器

改动：`src/pages/PaperTradingPage.tsx` → Strategies tab 内嵌

新建：`src/components/paper-trading/StrategyEditor.tsx`

```typescript
// 功能: 可编辑 YAML 参数面板（fast/slow/period/multiplier 等）
// "保存" 按钮 → PUT 策略 YAML（后端需暴露写端点）
// "回测快速验证" 按钮 → 触发 backtest run + SSE 进度条
```

API（需后端暴露）：`GET/PUT /api/v1/paper-trading/accounts/{id}/strategies/{name}`

---

### PN-009：TypeScript 类型补全 🖥️

改动：`src/types/paperTrading.ts`

新增类型定义：
```typescript
interface LatencySpan { trace_id, operation, total_ms, steps: Record<string, number> }
interface Level2Quote { code, timestamp, bid_prices, bid_volumes, ask_prices, ask_volumes, bid_ask_imbalance, weighted_bid, weighted_ask, depth_weighted_spread }
interface OrderFlowSignal { code, large_buy_orders, large_sell_orders, net_flow, iceberg_detected, spoofing_detected }
interface RiskAlert { alert_type, detail, detected_at }
interface DriftReport { strategy_name, is_drifting, rolling_sharpe, sharpe_trend, consecutive_losing_days, recommended_action }
interface ExtremeMarketAlert { market, current_vol, historical_vol, ratio, actions, detected_at }
```

---

### PN-010：操作结果乐观更新 🖥️

改动：`src/pages/PaperTradingPage.tsx` → `submit_signal` 调用后

```typescript
// 现有: 提交信号后等 30s 轮询刷新 → 用户看到旧数据
// 改为:
// 1. submit_signal 调用后，立即乐观插入新信号到本地 signals 列表
// 2. 标记 status = "pending" 带 spinner
// 3. 5 秒后异步 refreshSignals() → status 从"pending"更新为"executed/rejected"
// 4. 失败时 toast.error("signal rejected: {reason}") 并从本地列表移除
```

---

## Phase 3：精细化打磨层（Polish-Layer）— 2 天

> 目标：K 线图、多市场状态、手机适配、暗色主题

### PL-001：K 线/分时图组件
- **接受标准**：点击 `PositionsTable` 任一行代码，下方展开 Chart.js candlestick 图（最近 90 日 OHLC）
- 数据源复用 `_get_daily_df` 本地缓存（local_store → 600 days）
- 工作量：L（需引入 Chart.js candlestick plugin + 响应式尺寸处理）

### PL-002：多交易所/市场状态仪表板
- **接受标准**：页面顶部 3 列卡片（CN/HK/US），绿色=已连接+盘中、黄色=已连接+休市、红色=断开、灰色=节假日
- WebSocket 连接状态每交易所一行
- 工作量：S

### PL-003：手机端响应式适配
- **接受标准**：`PaperTradingPage` 在 375px 宽度下所有 tab 可访问、表格水平滚动、操作按钮触控友好（≥44px）
- 策略：`@media (max-width: 768px)` 堆叠布局，tab 行改为下拉选择器
- 工作量：L（2200 行页面重构）

### PL-004：暗色主题
- **接受标准**：Settings 页新增 "主题" 下拉（亮色/暗色/跟随系统），切换即时生效，所有 Paper Trading 组件正确配色
- 新增 `ThemeContext` + CSS 变量（`--bg-primary`, `--text-primary`, `--border` 等）
- 工作量：M

---

## 验证矩阵

| 阶段 | 验证项 | 方式 | 通过标准 |
|------|--------|------|---------|
| Phase 1 完成后 | WebSocket 连接+心跳 | 开发者工具 Network tab | WS 101 Switching Protocols，无断连超过 30s |
| Phase 1 完成后 | QuoteTicker 实时更新 | 目视检查 | 价格每 tick 更新，滞后 < 500ms |
| Phase 1 完成后 | Breaker/Alert 推送 | 触发模拟熔断 | Badge 即时变色 + Toast 弹出 |
| Phase 1 完成后 | PnL 流式计算 | 定位持仓表 | 浮动盈亏列实时变色 |
| Phase 2 完成后 | 各面板渲染 | 专项截图×8 | 8 个新面板无空白/无报错 |
| Phase 2 完成后 | 全部 TS 类型无断线 | `npx tsc --noEmit` | 0 errors |
| Phase 3 完成后 | 手机端可操作 | Chrome DevTools 375px | 所有 tab 可访问、按钮 ≥44px |
| 全 Phase 完成 | Lint+Build | `npm run lint && npm run build` | 0 errors |

---

## 工作量说明

| 标记 | 含义 | 约当人天 |
|------|------|---------|
| S | Small — 半日以内 | ≤0.5 天 |
| M | Medium — 1 日 | 1 天 |
| L | Large — 1.5~2 日 | 1.5-2 天 |

**并行前提**：同一 Phase 内无文件冲突的任务可并行。Phase 1 中 WS-001 必须先做（基础设施），WS-002/003/004/005 可并行。Phase 2 中 9 个 PN-xxx 全部可并行（每个新建独立文件）。

---

## 新增后端 API 汇总（前端对齐需后端暴露）

| 编号 | 端点 | 方法 | 用途 | 前端消费者 |
|------|------|------|------|-----------|
| API-01 | `/ws/quotes/{account_id}` | WS | 实时行情推送 | QuoteTicker + PnLStreaming |
| API-02 | `/ws/events/{account_id}` | WS | 事件流推送 | BreakerBadge + RiskAlert + EventLog |
| API-03 | `/accounts/{id}/latency` | GET | 延迟统计 | LatencyPanel |
| API-04 | `/accounts/{id}/strategies` | GET/PUT | 策略 CRUD + 状态流转 | StrategyLifecycle + Leaderboard |
| API-05 | `/accounts/{id}/l2-quote/{code}` | GET | 十档报价 | L2OrderBook |
| API-06 | `/accounts/{id}/drift` | GET | 漂移报告 | DriftPanel |
| API-07 | `/accounts/{id}/extreme-market` | GET | 极端行情状态 | ExtremeMarketBanner |
| API-08 | `/accounts/{id}/features` | GET/POST | 特征查看+触发 | FeaturesPanel |
| API-09 | `/accounts/{id}/strategies/{name}` | PUT | 策略参数编辑 | StrategyEditor |
| API-10 | `/accounts/{id}/risk-alerts` | GET | 最近风控告警 | RiskAlertToast |

---

## 全量进度汇总

| Phase | 编号 | 内容 | 工作量 | 依赖 |
|-------|------|------|--------|------|
| WS | WS-001 | useWebSocket hook | S | 无 |
| WS | WS-002 | QuoteTicker 组件 | M | WS-001 + API-01 |
| WS | WS-003 | BreakerBadge→WS + RiskAlertToast | M | WS-001 + API-02 |
| WS | WS-004 | 实时 PnL 流式更新 | M | WS-001 + API-01 |
| WS | WS-005 | EventLogFeed 事件流 | M | WS-001 + API-02 |
| PN | PN-001 | LatencyPanel 延迟仪表板 | M | API-03 |
| PN | PN-002 | StrategyLifecyclePanel | M | API-04 |
| PN | PN-003 | L2OrderBook 深度行情 | L | API-05 |
| PN | PN-004 | StrategyLeaderboard 策略看板 | M | API-04 |
| PN | PN-005 | DriftPanel 漂移检测 | S | API-06 |
| PN | PN-006 | ExtremeMarketBanner 极端行情 | S | API-07 |
| PN | PN-007 | FeaturesPanel 特征工程 | S | API-08 |
| PN | PN-008 | StrategyEditor 参数编辑 | M | API-09 |
| PN | PN-009 | TS 类型补全 | S | 无 |
| PL | PL-001 | K 线/分时图 | L | 无 |
| PL | PL-002 | 多交易所仪表板 | S | 无 |
| PL | PL-003 | 手机端适配 | L | 无 |
| PL | PL-004 | 暗色主题 | M | 无 |

**Phase 1**: 5 项 × M/S = ~2 天（WS-001 先做，其余可并行）
**Phase 2**: 9 项 × M/L = ~3 天（PN-003 L2 订单簿独占最多，其余可并行）
**Phase 3**: 4 项 × M/L = ~2 天

**总计预估**：7 个工作日（单人），或 4-5 个工作日（双人）。

**关键路径**：WS-001 → WS-002 + WS-003WS-004WS-005（并行）→ PN-序列（可按 M/S/L 自由调度）。

---

*计划生成时间: 2026-08-10 | 目标: 毫秒级实时量化交易执行系统 — 前端面板*
