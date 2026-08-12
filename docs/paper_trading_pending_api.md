# Paper Trading 待实现接口文档

本文档列出前端已调用但后端尚未实现的 paper-trading 接口。
后端实现后即可消除前端控制台中的 404 / WebSocket 连接失败错误。

> 前端文件位置：`apps/dsa-web/src/`
> 后端文件位置：`api/v1/endpoints/paper_trading.py`
> 后端 router 已配置 `dependencies=[Depends(require_login)]`，以下接口均需登录。

---

## 1. GET `/api/v1/paper-trading/accounts/{account_id}/latency`

### 功能

返回单个账户的全链路 tick 延迟统计（p50 / p95 / p99），用于前端 `LatencyPanel` 组件展示延迟监控面板。

### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `account_id` | `int` | 模拟交易账户 ID |

### 权限

- `require_login`（router 级别）
- `verify_account_ownership`（账户归属校验）

### 响应

**200 OK**

```json
{
  "tick_total_ms": {
    "p50": 120.5,
    "p95": 340.2,
    "p99": 580.1
  },
  "steps": [
    {
      "name": "data_fetch",
      "p50_ms": 30.1,
      "p95_ms": 85.3,
      "p99_ms": 150.0
    },
    {
      "name": "signal_calc",
      "p50_ms": 45.2,
      "p95_ms": 120.0,
      "p99_ms": 200.5
    },
    {
      "name": "risk_check",
      "p50_ms": 15.0,
      "p95_ms": 50.0,
      "p99_ms": 80.0
    },
    {
      "name": "order_execute",
      "p50_ms": 30.2,
      "p95_ms": 84.9,
      "p99_ms": 149.6
    }
  ]
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `tick_total_ms` | `object` | 全链路总延迟分布 |
| `tick_total_ms.p50` | `float` | 中位数（毫秒） |
| `tick_total_ms.p95` | `float` | P95（毫秒） |
| `tick_total_ms.p99` | `float` | P99（毫秒） |
| `steps` | `array` | 各阶段延迟分解 |
| `steps[].name` | `string` | 阶段名称（如 `data_fetch`、`signal_calc`、`risk_check`、`order_execute`） |
| `steps[].p50_ms` | `float` | 该阶段中位数（毫秒） |
| `steps[].p95_ms` | `float` | 该阶段 P95（毫秒） |
| `steps[].p99_ms` | `float` | 该阶段 P99（毫秒） |

### 前端消费方

| 文件 | 说明 |
|------|------|
| `api/paperTrading.ts` → `getLatency()` | API 封装，404 时返回 `null` |
| `components/paper-trading/LatencyPanel.tsx` | 每 5 秒轮询；收到 `null`（404）后停止轮询 |

### 实现建议

- 延迟数据可从 `PaperTradingService.get_listener()` 返回的 `MarketListener` 的 tick 统计中聚合。
- 如果 `MarketListener` 尚未运行，返回全零的 `tick_total_ms` 和空 `steps` 数组（HTTP 200），而非 404。
- 可使用滑动窗口（如最近 100 个 tick）计算百分位。

---

## 2. WS `/api/v1/paper-trading/{account_id}/ws/quotes`

### 功能

实时推送行情报价。前端 `QuoteTicker` 组件用于展示滚动行情条，`useLivePositions` hook 用于实时计算浮动盈亏。

### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `account_id` | `int` | 模拟交易账户 ID |

### 权限

- `require_login`（router 级别）
- 建议在 WebSocket 握手阶段校验账户归属

### 连接

客户端发起 WebSocket 升级请求：

```
ws://host:port/api/v1/paper-trading/{account_id}/ws/quotes
```

### 推送消息格式

服务端通过 WebSocket 推送 JSON 消息，每条消息代表一个股票的最新报价：

```json
{
  "code": "600519",
  "price": 1685.50,
  "changePct": 2.35,
  "volume": 1234500,
  "timestamp": "2026-08-12T14:30:00.000Z"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | `string` | 股票代码（如 `600519`、`hk00700`、`AAPL`） |
| `price` | `float` | 最新价格 |
| `changePct` | `float` | 涨跌幅（百分比，正涨负跌） |
| `volume` | `float` | 成交量（可选，`useLivePositions` 不消费此字段） |
| `timestamp` | `string` | ISO 8601 时间戳 |

> **注意**：`useLivePositions` 的 `PriceTick` 类型只需要 `code`、`price`、`changePct` 三个字段。`volume` 和 `timestamp` 是 `QuoteTicker` 组件额外使用的字段。

### 前端消费方

| 文件 | 说明 |
|------|------|
| `components/paper-trading/QuoteTicker.tsx` | 滚动行情条，展示最近 12 只股票 |
| `hooks/useLivePositions.ts` | 合并 REST 持仓 + WS 实时价格 → 计算浮动盈亏 |

### 实现建议

- 数据源：`MarketListener` 已经在后台轮询行情，可直接复用其推送回调。
- 参考现有 WebSocket 模式：`api/v1/endpoints/observability.py` 中的 `@router.websocket("/ws/events")` 使用 `SystemEventBus` 订阅 + 队列 + `asyncio.sleep` 推送的模式。
- 推送频率建议：每个 tick 周期（约 3-5 秒）推送一次该账户持仓股票的最新报价。
- 客户端断开时需清理订阅，避免内存泄漏。
- `maxRetries: 5` — 前端在 5 次连接失败后停止重连，后端实现后需确保端点稳定可用。

### 实现参考

```python
@router.websocket("/{account_id}/ws/quotes")
async def ws_quotes(websocket: WebSocket, account_id: int):
    await websocket.accept()
    # 1. 校验账户归属
    # 2. 获取 MarketListener / SharedQuoteCache
    # 3. 订阅行情更新
    # 4. 循环推送 JSON 消息
    # 5. 断开时清理订阅
    ...
```

---

## 3. WS `/api/v1/paper-trading/{account_id}/ws/events`

### 功能

实时推送交易事件流。前端 `EventLogFeed` 展示信号 → 风控 → 熔断 → 委托 → 成交的完整时间线；`RiskAlertToast` 展示风险告警弹窗。

### 路径参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `account_id` | `int` | 模拟交易账户 ID |

### 权限

- `require_login`（router 级别）
- 建议在 WebSocket 握手阶段校验账户归属

### 连接

```
ws://host:port/api/v1/paper-trading/{account_id}/ws/events
```

### 推送消息格式

此端点推送**两种**消息类型，前端通过字段名区分：

#### 类型 A：交易事件（`EventLogFeed` 消费）

前端检查 `eventType` 字段是否存在来识别此类型。

```json
{
  "eventId": "evt-20260812-001",
  "eventType": "signal_generated",
  "code": "600519",
  "orderId": null,
  "side": "buy",
  "price": 1685.50,
  "quantity": 100,
  "strategyName": "momentum_v2",
  "reason": "MACD金叉+量能放大",
  "timestamp": "2026-08-12T14:30:00.000Z"
}
```

**`eventType` 枚举值**

| 值 | 说明 |
|----|------|
| `signal_generated` | 策略信号产生 |
| `risk_check_passed` | 风控检查通过 |
| `risk_check_failed` | 风控检查拒绝 |
| `agent_review_passed` | AI Agent 审核通过 |
| `agent_review_vetoed` | AI Agent 否决 |
| `breaker_check_passed` | 熔断检查通过 |
| `breaker_rejected` | 熔断拒绝 |
| `order_created` | 委托已创建 |
| `order_filled` | 委托已成交 |
| `order_canceled` | 委托已撤销 |
| `order_rejected` | 委托被拒绝 |
| `sl_tp_triggered` | 止损/止盈触发 |
| `position_closed` | 持仓平仓 |
| `extreme_market_activated` | 极端行情启动 |
| `extreme_market_deactivated` | 极端行情解除 |

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `eventId` | `string` | 事件唯一 ID（用于前端 key） |
| `eventType` | `string` | 事件类型（见上表） |
| `code` | `string?` | 关联股票代码（可选） |
| `orderId` | `int?` | 关联委托 ID（可选） |
| `side` | `string?` | 买卖方向（`buy` / `sell`） |
| `price` | `float?` | 成交/委托价格 |
| `quantity` | `float?` | 成交/委托数量 |
| `strategyName` | `string?` | 策略名称 |
| `reason` | `string?` | 事件原因/备注 |
| `timestamp` | `string` | ISO 8601 时间戳 |

#### 类型 B：风险告警（`RiskAlertToast` 消费）

前端检查 `alertType` 字段是否存在来识别此类型。

```json
{
  "alertType": "var_breach",
  "message": "组合 VaR 超过阈值",
  "detail": "当前 VaR: 5.2%, 阈值: 3.0%",
  "level": "danger",
  "timestamp": "2026-08-12T14:30:00.000Z"
}
```

**`alertType` 枚举值**

| 值 | 说明 |
|----|------|
| `var_breach` | VaR（风险价值）突破阈值 |
| `liquidity_warning` | 流动性不足警告 |
| `market_anomaly` | 市场异常 |

**字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `alertType` | `string` | 告警类型（`var_breach` / `liquidity_warning` / `market_anomaly`） |
| `message` | `string` | 告警标题 |
| `detail` | `string?` | 告警详情 |
| `level` | `string` | 告警级别（`warning` / `danger`） |
| `timestamp` | `string` | ISO 8601 时间戳 |

### 前端消费方

| 文件 | 说明 |
|------|------|
| `components/paper-trading/EventLogFeed.tsx` | 时间线日志（检查 `eventType` 字段） |
| `components/paper-trading/RiskAlertToast.tsx` | 风险告警弹窗（检查 `alertType` 字段，8 秒自动消失） |

### 实现建议

- 数据源：`PaperTradingService` 的交易引擎在信号生成、风控检查、委托创建/成交等环节会产生事件。
- 可复用 `SystemEventBus`（与 observability 的 `ws/events` 类似），或为 paper-trading 建立独立的事件总线。
- 推送时机：事件发生时立即推送（非轮询）。
- 客户端断开时需清理订阅。
- `maxRetries: 5` — 前端在 5 次连接失败后停止重连。

### 实现参考

```python
@router.websocket("/{account_id}/ws/events")
async def ws_events(websocket: WebSocket, account_id: int):
    await websocket.accept()
    # 1. 校验账户归属
    # 2. 订阅交易引擎事件 + 风险告警
    # 3. 事件到达时推送 JSON 消息
    # 4. 断开时清理订阅
    ...
```

---

## 汇总

| # | 方法 | 路径 | 前端轮询/重连 | 后端状态 |
|---|------|------|--------------|---------|
| 1 | `GET` | `/api/v1/paper-trading/accounts/{account_id}/latency` | 5s 轮询，404 后停止 | 未实现 |
| 2 | `WS` | `/api/v1/paper-trading/{account_id}/ws/quotes` | 自动重连，5 次后停止 | 未实现 |
| 3 | `WS` | `/api/v1/paper-trading/{account_id}/ws/events` | 自动重连，5 次后停止 | 未实现 |

### 前端已做的优雅降级

在后端实现之前，前端已配置以下降级策略，确保应用不崩溃：

- **`/latency`**：首次 404 后停止轮询，显示"延迟数据不可用"。
- **`ws/quotes`**：5 次失败后停止重连，`QuoteTicker` 显示"等待行情推送"，`useLivePositions` 回退到持仓成本价计算浮动盈亏。
- **`ws/events`**：5 次失败后停止重连，`EventLogFeed` 显示"事件流未连接"，`RiskAlertToast` 不弹出。

后端实现并部署后，前端无需改动——刷新页面即可自动连接。
