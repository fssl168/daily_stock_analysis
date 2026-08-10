# 实时量化交易系统 — 毫秒级执行实施计划

> **目标**：打造毫秒级实时量化交易执行系统
> **执行原则**：自行决策，不等待用户确认，逐项闭合
> **基准差距分析**：`docs/realtime_quant_system_gap_analysis.md`（23 项 gap）
> **基准设计文档**：`docs/architecture/realtime_quant_system_design.md`

---

## 实施理念

当前代码库有 **23 项差距**，但实际结构非常健康：

- **15 个核心模块已独立实现且代码质量良好**
- **真问题不在"没写代码"，而在"启动链路没连线"**
- 11 个模块是"库就绪但未挂载"（Library-Ready, Not Wired）

因此实施策略是：**先连线（Wire），再补缺（Build），最后校验（Verify）**。每完成一层，该层就变为可运行的执行系统。

---

## Phase 0：基础连线层（Wire-Layer）— 2 天

> 目标：所有已实现但未挂载的模块，在启动链路中连接。Phase 0 完成后，8 个 P0/P1 即时就绪。

### T-001：ExchangeClock 全线替换 datetime.now()

**GAP-004 + GAP-005** | 契约一致性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/market_listener.py` | 全文件 `datetime.now()` 调用 | 替换为 `ExchangeClock.now(market)` |
| `paper_trading/market_listener.py:811,825` | `_should_emit_signal()` / `_record_signal()` | `ExchangeClock.now("cn")` |
| `paper_trading/market_listener.py:138-172` | `is_market_open_now()` | 已接受 `now` 参数，确认传入 ExchangeClock |
| `data_provider/base.py` | `DataFetcherManager.get_daily_data()` | 新增 `_normalize_timestamps()` 方法 |

**`data_provider/base.py` _normalize_timestamps 实现：**

```python
@staticmethod
def _normalize_timestamps(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    from src.utils.exchange_clock import EXCHANGE_TIMEZONES
    if df.index.tz is not None:
        tz = EXCHANGE_TIMEZONES["us"] if _is_us_market(stock_code) else EXCHANGE_TIMEZONES["cn"]
        df.index = df.index.tz_convert(tz).tz_localize(None)
    return df
```

**验证方式**：启动 listener 后 grep 全量 `datetime.now()` 调用，确认 paper_trading/*.py 中无裸调用。

---

### T-002：BrokerRouter 升级 + PaperBroker 默认注册

**GAP-001** | 功能可用性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/broker/router.py` | 新增方法 | `resolve_by_account(account_id)` |
| `paper_trading/broker/router.py:19` | `__init__` | 增加 `_register_defaults()` 调用 |
| `paper_trading/broker/base.py` | 文件顶部 | 补全 `BrokerPosition` / `BrokerAccount` dataclass |

**router.py 新增方法：**

```python
def resolve_by_account(self, account_id: int) -> BaseBroker:
    from paper_trading.account import PaperAccountManager
    account = PaperAccountManager().get(account_id)
    broker_name = getattr(account, "broker", "paper")
    key = str(broker_name).strip().lower()
    return self._brokers.get(key, self._brokers.get("paper"))

def _register_defaults(self):
    from paper_trading.broker.paper_broker import PaperBroker
    if "paper" not in self._brokers:
        self.register("paper", PaperBroker())
```

**base.py 补全 dataclass：**

```python
@dataclass
class BrokerPosition:
    code: str; name: str; quantity: int; available_quantity: int
    avg_cost: float; current_price: float; market_value: float
    profit_loss: float; profit_loss_pct: float

@dataclass
class BrokerAccount:
    account_id: str; total_assets: float; available_cash: float
    frozen_cash: float; positions: List[BrokerPosition]
```

---

### T-003：CircuitBreaker 挂载到 TradingEngine 启动链路

**GAP-007** | 业务规则阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| 运行时启动点 | 创建 TradingEngine 实例处 | 注入 CircuitBreaker |

**改动策略**：TradingEngine 已有 `circuit_breaker` 参数（`trading_engine.py:103`），只需在启动点实例化并传入。

需要找到 paper_trading 的引擎启动点（可能在 `paper_trading/market_listener.py` 的调用链或 `main.py` 中 paper_trading 启动段）。

**验证方式**：启动后打印 `engine.circuit_breaker` 非 None。

---

### T-004：HealthCheckDaemon 全量启动

**GAP-009** | 业务规则阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `server.py:49-56` | `__main__` 段 | 补全 6 项健康检查注册 |

**改动（替换现有 `server.py:49-56` 段）：**

```python
if os.getenv("HEALTH_CHECK_ENABLED", "").strip().lower() in ("1", "true", "yes"):
    from src.services.health_check import (
        HealthCheckDaemon, check_ntp_sync, check_system_resources, check_task_queue
    )
    daemon = HealthCheckDaemon(
        on_alert=lambda level, msg: logging.getLogger("health").warning("[%s] %s", level, msg),
    )
    daemon.register(check_ntp_sync)
    daemon.register(check_system_resources)
    daemon.register(check_task_queue)
    # listener-alive / data-source-health / broker-connection 由调用方 lambda 注入
    if "market_listener" in dir():
        daemon.register(lambda: check_listener_alive(listener))
    daemon.start()
```

---

### T-005：MarketListener tick 循环接入 LatencyTracker

**GAP-015** | 契约一致性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/market_listener.py:451` | `_tick_all_markets()` | 插入 LatencySpan 打点 |

**改动：**

```python
def _tick_all_markets(self):
    from src.utils.latency_tracker import LatencySpan
    import uuid
    span = LatencySpan("tick_market", str(uuid.uuid4())[:8])
    # ... existing logic ...
    span.mark("fetch_prices_done")
    # ... existing logic ...
    span.mark("match_orders_done")
    # ... existing logic ...
    result = span.finish()
    if result["total_ms"] > 1000:
        logger.warning("Slow tick: %.1fms", result["total_ms"])
```

---

### T-006：TradingEngine 调用 fill_order 传入 expected_version

**GAP-016** | 数据正确性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/trading_engine.py` | 调用 `order_mgr.fill_order()` 处 | 传入 `expected_version=order.version` |
| `paper_trading/oms_mgmt.py` | 调用 `fill_order()` 处 | 同上 |

**伪代码：**

```python
order = self.order_mgr.get_order(order_id)
result = self.order_mgr.fill_order(
    order_id=order_id,
    fill_price=fill_price,
    fill_quantity=fill_quantity,
    expected_version=order.version,  # 新增
)
if result is None:
    return TradeResult(status="retry", reason="version conflict")
```

---

## Phase 1：集成深度层（Deep-Integration）— 3 天

> 目标：对所有"库已就绪，但需编排级逻辑"的模块完成端到端集成。Phase 1 完成后，全链路可观测、风控可执行、行情可双通道。

### T-007：WebSocket 行情双通道启动编排

**GAP-006** | 功能可用性阻断 | 工作量：M

涉及文件：`main.py`（或 paper_trading 启动段）

**编排逻辑（伪代码）：**

```python
# 1. 创建 SharedQuoteCache
quote_cache = SharedQuoteCache(max_age_seconds=5.0)

# 2. 如果启用 WS
if config.paper_trading_enable_websocket:
    ws_channel = WebSocketChannel(
        quote_cache=quote_cache,
        watched_codes=watched_codes,
        on_disconnect=lambda: logger.warning("WS disconnected, fallback to poll"),
    )
    # 启动 asyncio 线程
    import threading, asyncio
    def _ws_thread():
        loop = asyncio.new_event_loop()
        loop.run_until_complete(ws_channel.run_forever(
            url=config.paper_trading_ws_url,
            auth_token=config.paper_trading_ws_token,
        ))
    threading.Thread(target=_ws_thread, daemon=True, name="ws-channel").start()

# 3. MarketListener 注入 quote_cache
listener = MarketListener(
    engine=engine,
    data_fetcher=fetcher,
    strategies=strategies,
    config=MarketListenerConfig(...),
    quote_cache=quote_cache,
)
listener.start()
```

---

### T-008：RiskDaemon 挂载到 MarketListener Tick 循环

**GAP-008** | 业务规则阻断 | 工作量：M

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/market_listener.py` | `_tick_market()` | 每 tick 后调用 `RiskDaemon.tick()` |

**改动（在 `_tick_market` 末尾）：**

```python
if self._risk_daemon is not None:
    account = self.engine.account_mgr.snapshot(self.config.account_id)
    positions = self.engine.position_mgr.list_positions(self.config.account_id)
    alerts = self._risk_daemon.tick(account, positions, latest_prices)
    for alert in alerts:
        logger.warning("RiskDaemon alert: type=%s detail=%s", alert.alert_type, alert.detail)
        # VaR breach 时带 current_var 重新评估熔断
        if alert.alert_type == RiskAlertType.VAR_BREACH and self.engine.circuit_breaker:
            self.engine.circuit_breaker.evaluate(
                current_pnl=account.total_assets - account.initial_capital,
                initial_capital=account.initial_capital,
                current_var=alert.detail.var_95_pct if hasattr(alert.detail, 'var_95_pct') else None,
            )
```

**MarketListener.__init__ 新增参数：**

```python
risk_daemon: Optional[RiskDaemon] = None,
```

---

### T-009：SignalFusionEngine 默认启用

**GAP-021** | 业务规则阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| MarketListener 创建点 | 注入 SignalFusionEngine | 默认 WEIGHTED_VOTE 模式 + 60% 共识阈值 |

```python
from paper_trading.signal_fusion import SignalFusionEngine, FusionMethod
fusion = SignalFusionEngine(method=FusionMethod.WEIGHTED_VOTE)
listener = MarketListener(engine=engine, ..., signal_fusion=fusion)
```

**验证**：`market_listener.py:773-776` 已有 integration 代码（`if self._signal_fusion:`），只需保证参数传入。

---

### T-010：DriftDetector 日终挂载

**GAP-020** | 业务规则阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/market_listener.py:994` | post-settle hooks | 新增 drift_detector.record_daily_pnl() |
| `paper_trading/signal_fusion.py` | 新增方法 | `update_weights_from_drift()` |

**signal_fusion.py 新增方法：**

```python
def update_weights_from_drift(self, drift_reports: Dict[str, DriftReport]):
    for name, report in drift_reports.items():
        if report.recommended_action == "reduce_weight":
            self._strategy_weights[name] = self._strategy_weights.get(name, 1.0) * 0.5
        elif report.recommended_action == "pause":
            self._strategy_weights[name] = 0.0
        elif report.recommended_action == "retire":
            self._strategy_weights.pop(name, None)
```

---

### T-011：ExtremeMarketDetector 挂载

**GAP-022** | 业务规则阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/market_listener.py` | `_tick_market()` | 每 tick 检测 + 激活/恢复 |
| `paper_trading/extreme_market.py` | 新增方法 | `auto_resume()` + `widen_circuit_breaker()` |

```python
# extreme_market.py 新增
def auto_resume(self):
    if self._activated_at and (datetime.now() - self._activated_at).total_seconds() > 1800:
        self.deactivate()

def widen_circuit_breaker(self, cb):
    cb.config.soft_threshold_pct *= 2.0
    cb.config.hard_threshold_pct *= 2.0
```

---

### T-012：DataQualityPipeline 集成到 DataFetcherManager

**GAP-017** | 数据正确性阻断 | 工作量：M

| 文件 | 位置 | 改动 |
|------|------|------|
| `data_provider/base.py` | `get_realtime_quote()` 返回前 | 调用 `quality.validate_realtime(quote)` |
| `data_provider/quality.py` | `__init__` | 补全 `_check_not_suspended` + `_check_volume_sanity` |

**quality.py 新增 2 个检查：**

```python
@staticmethod
def _check_not_suspended(quote) -> Dict:
    """Check if stock is suspended: price unchanged N days, or name contains '停牌'."""
    name = getattr(quote, "name", "")
    if "停牌" in str(name):
        return {"name": "suspended_check", "passed": False, "detail": "name contains 停牌"}
    # TODO: add multi-day price-unchanged check once daily history is available in context
    return {"name": "suspended_check", "passed": True, "detail": ""}

@staticmethod
def _check_volume_sanity(quote) -> Dict:
    vol = getattr(quote, "volume", None)
    if vol is not None and vol < 0:
        return {"name": "volume_sanity", "passed": False, "detail": f"negative volume={vol}"}
    return {"name": "volume_sanity", "passed": True, "detail": ""}
```

---

### T-013：FeaturePipeline 日终触发

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/features/pipeline.py` | 新增 | `save()` 方法（parquet 持久化） |
| `paper_trading/market_listener.py:994` | post-settle hooks | 日终调用 `FeaturePipeline.run()` + `save()` |

```python
# pipeline.py 新增
def save(self, features: pd.DataFrame, as_of: date):
    path = f"data/features/{as_of.strftime('%Y%m%d')}.parquet"
    features.to_parquet(path)
```

---

## Phase 2：补缺构建层（Build-Layer）— 4 天

> 目标：实现设计文档中已有详细接口定义但尚未编码的 3 个关键模块。

### T-014：EastMoneyBroker 真券商适配器

**GAP-002** | 功能可用性阻断 | 工作量：M

新建文件：`paper_trading/broker/eastmoney_broker.py`

**实现要点**（按设计文档 1.2.3 节接口）：

- 继承 `BaseBroker`
- 封装 `easytrader.use('eastmoney')`
- `submit_order()` → `client.buy/sell` → 返回 broker_order_id
- `cancel_order()` → `client.cancel_entrust`
- `query_positions()` → 解析 `client.position` 列表
- `query_account()` → 解析 `client.balance`
- `is_connected()` → try/except ping
- 配置项：`BROKER_EASTMONEY_USER` / `BROKER_EASTMONEY_PASSWORD`

> **注意**：`easytrader` 需要通过 Windows COM 连接到东方财富客户端（xiadan.exe），因此此模块仅在 Windows 环境下可用。需在 `is_connected()` 中做好环境检测和降级。

---

### T-015：Settlement 独立模块

**GAP-014** | 业务规则阻断 | 工作量：L

从 `trading_engine.py` 的 `daily_settle()` 方法（L922+）中抽取。

新建文件：`paper_trading/settlement.py`

```python
class Settlement:
    """日终结算：mark-to-market + 手续费计提 + 净值曲线计算"""
    def __init__(self, account_mgr, position_mgr, fee_model): ...
    def daily_settle(self, account_id, target_date) -> DailySettleResult: ...
    def mark_to_market(self, account_id, latest_prices) -> List[PositionPnL]: ...
    def compute_net_value_curve(self, account_id, start_date, end_date) -> pd.DataFrame: ...
```

**TradingEngine 改动**：`daily_settle()` 方法改为委托调用 `self.settlement.daily_settle()`。

---

### T-016：L2Fetcher 深度行情

**GAP-018** | 功能可用性阻断 | 工作量：M

新建文件：`data_provider/l2_fetcher.py`

```python
@dataclass
class Level2Quote:
    code: str; timestamp: datetime
    bid_prices: List[float]; bid_volumes: List[int]
    ask_prices: List[float]; ask_volumes: List[int]
    bid_ask_imbalance: float; weighted_bid: float; weighted_ask: float

@dataclass
class OrderFlowSignal:
    code: str; large_buy_orders: int; large_sell_orders: int
    net_flow: float; iceberg_detected: bool; spoofing_detected: bool

class L2Fetcher(BaseFetcher):
    def get_level2_quote(self, stock_code: str) -> Optional[Level2Quote]: ...
    def get_order_flow(self, stock_code: str) -> Optional[OrderFlowSignal]: ...
```

**优先接入 tickflow L2 WebSocket**（如果 API key 配置了 `TICKFLOW_API_KEY`），否则可后续对接。

---

## Phase 3：存量纠正层（Fix-Layer）— 3 天

> 目标：修复已实现但与设计规格有偏差的模块。

### T-017：backtest_adapter 补充 passthrough 接口

**GAP-003** | 业务规则阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/backtest_adapter.py` | 新增函数 | `backtest_from_paper_account()` |

```python
def backtest_from_paper_account(account_id, strategies, start_date, end_date) -> BacktestResult:
    from data_provider.base import DataFetcherManager; from paper_trading.backtest.engine import BacktestEngine
    fetcher = DataFetcherManager(); codes = _get_watched_codes(account_id)
    daily_data = {}
    for code in codes:
        df, _ = fetcher.get_daily_data(code, start_date=start_date, end_date=end_date, days=9999)
        if df is not None and not df.empty: daily_data[code] = df
    engine = BacktestEngine(BacktestConfig(...))
    return engine.run(codes, strategies, daily_data)
```

---

### T-018：CorporateActions 补全数据拉取 + 拆股调整

**GAP-019** | 数据正确性阻断 | 工作量：M

| 文件 | 位置 | 改动 |
|------|------|------|
| `data_provider/corporate_actions.py` | 新增方法 | `update(codes)` 从 akshare 拉取 |
| `data_provider/corporate_actions.py` | 新增方法 | `apply_split_adjustment()` |

```python
def update(self, codes: List[str]):
    import akshare as ak
    for code in codes:
        try:
            df = ak.stock_dividents_cninfo(symbol=code)
            for _, row in df.iterrows():
                self.add_event(CorporateEvent(code=code, event_date=..., event_type="dividend", details={...}))
        except Exception: continue

def apply_to_prices(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
    """统一入口：遍历所有事件，应用分红+拆股前复权"""
    events = self._events.get(code, [])
    df = df.copy(); df["adj_factor"] = 1.0
    for event in sorted(events, key=lambda e: e.event_date, reverse=True):
        mask = df.index <= pd.Timestamp(event.event_date)
        if event.event_type == "dividend": ...
        elif event.event_type == "split":
            df.loc[mask, "adj_factor"] *= event.details["split_ratio"]
    for col in ["open", "high", "low", "close"]: df[col] *= df["adj_factor"]
    return df
```

---

### T-019：LocalMarketStore 补全 schema + 增量刷新

**GAP-013** | 数据正确性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `data_provider/local_store.py` | `_init_schema()` | ALTER TABLE 增加 `adjust_factor` 列 |
| `data_provider/local_store.py` | `_init_schema()` | CREATE INDEX idx_kline_code_date |
| `data_provider/local_store.py` | `StoreConfig` | 新增 `max_incremental_days` / `full_refresh_interval_days` |

**验证**：`sqlite3 data/market_data.db ".schema daily_kline"` 确认列和索引存在。

---

### T-020：OMS 注入 BrokerRouter

**GAP-011** | 契约一致性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/oms_mgmt.py:__init__` | 新增参数 | `broker_router: Optional[BrokerRouter] = None` |
| `paper_trading/trading_engine.py:154` | OMS 创建处 | 传入 broker_router |

```python
# oms_mgmt.py
def __init__(self, ..., broker_router: Optional[BrokerRouter] = None):
    self.broker_router = broker_router
```

---

### T-021：AI Signal Worker 进程隔离确认

**GAP-023** | 功能可用性阻断 | 工作量：S

| 文件 | 位置 | 改动 |
|------|------|------|
| `paper_trading/ai_signal_worker.py:44-50` | `start()` | 已实现独立 daemon 线程 ✅ |

**已有实现**：`AISignalWorker` 在独立 `daemon=True` 线程中运行，`_loop()` 按 `_interval` 间隔执行分析。与 MarketListener 主线程不阻塞规则 tick。

**确认项**：验证 `AISignalWorker.start()` 在 MarketListener 启动链路中被调用。如果未调用，则需在 `main.py` 或 MarketListener 初始化段补充启动代码。

---

### T-022：DataQualityPipeline 全 5 项检查

**GAP-012** | 已在 T-012 中覆盖

已在 Phase 1 的 T-012 中实现停牌检测和量合理性检查。无需额外任务。

---

### T-023：LocalMarketStore 集成到 MarketListener

**GAP-018** | 已在 Phase 1 集成任务中覆盖

在 `market_listener.py:_get_daily_df()` 中增加优先读本地、过期才拉远的逻辑。

---

## 全量进度汇总

| 编号 | 内容 | Phase | 工作量 | 依赖 |
|------|------|-------|--------|------|
| T-001 | ExchangeClock 全线替换 | P0 | S | 无 |
| T-002 | BrokerRouter 升级 + dataclass 补全 | P0 | S | T-001 |
| T-003 | CircuitBreaker 挂载启动链路 | P0 | S | T-002 |
| T-004 | HealthCheckDaemon 全量启动 | P0 | S | 无 |
| T-005 | LatencyTracker 接入 tick 循环 | P0 | S | 无 |
| T-006 | fill_order 传入 expected_version | P0 | S | 无 |
| T-007 | WebSocket 双通道启动编排 | P1 | M | T-002 |
| T-008 | RiskDaemon 挂载 tick 循环 | P1 | M | T-003 |
| T-009 | SignalFusionEngine 默认启用 | P1 | S | 无 |
| T-010 | DriftDetector 日终 + 融合权调 | P1 | S | T-009 |
| T-011 | ExtremeMarketDetector 挂载 | P1 | S | T-003 |
| T-012 | DataQualityPipeline 集成 fetcher | P1 | M | 无 |
| T-013 | FeaturePipeline 日终触发 | P1 | S | 无 |
| T-014 | EastMoneyBroker 新建 | P2 | M | T-002 |
| T-015 | Settlement 独立模块 | P2 | L | 无 |
| T-016 | L2Fetcher 新建 | P2 | M | T-007 |
| T-017 | backtest_adapter passthrough | P2 | S | 无 |
| T-018 | CorporateActions 数据拉取 | P2 | M | 无 |
| T-019 | LocalMarketStore schema 补全 | P2 | S | 无 |
| T-020 | OMS 注入 BrokerRouter | P2 | S | T-002 |
| T-021 | AI Worker 隔离确认 | P2 | S | 无 |
| T-022 | DataQuality 5项全量 | P1 | 已覆盖 | T-012 |
| T-023 | LocalStore 集成 listener | P2 | S | T-019 |

**Phase 0**: 6 项 × S = ~2 天（可并行）
**Phase 1**: 7 项 × M/S = ~3 天（T-007/T-008 串行，其余可并行）
**Phase 2**: 7 项 × M/L = ~4 天（T-014/T-015/T-016 可并行）
**Phase 3**: 5 项 × S/M = ~3 天（可全并行）

**总计预估**：12 个工作日（单人），或 6-8 个工作日（双人）。

---

## 毫秒级执行路径确认

实施完成后，一条信号的端到端路径如下：

```
WebSocket 推送 (tickflow/longbridge, <100ms latency)
    → SharedQuoteCache.update() (<1ms, RLock)
    → MarketListener._tick_market() (500ms interval in WS mode)
      → _fetch_latest_prices() → cache.get_all() (<1ms)
      → match_pending_orders() (RuleEngine, <5ms)
      → _evaluate_strategies() (RuleEngine per code, <10ms)
        → SignalFusionEngine.fuse() (<1ms)
        → TradingEngine.submit_signal()
          → RMS.pre_trade_check() (<1ms)
          → CircuitBreaker.evaluate() (<1ms)
          → OMS.create_order() (<5ms)
          → OMS.execute_market() → broker.submit_order() (<50ms via easytrader)
    → LatencyTracker.record() (<1ms)
```

**端到端关键路径耗时**（WS 模式下）：< 200ms（不含券商实际成交延迟）
**规则 tick 间隔**：500ms（WS 模式）/ 10s（轮询兜底）
**AI 分析延迟**：完全隔离，不阻塞规则引擎

---

## 验证矩阵

| 层级 | 验证内容 | 方式 | 通过标准 |
|------|---------|------|---------|
| P0 基础连线 | 6 个模块全部挂载 | 启动后 `grep` 确认非 None | 无 None wiring |
| P0 基础连线 | CI gate 通过 | `./scripts/ci_gate.sh` | 0 错误 |
| P1 集成深度 | WebSocket 行情接收 | 日志输出 `[ws-channel]` 行 | 连续 60s 持续推送 |
| P1 集成深度 | 熔断触发回归测试 | 注入模拟亏损数据 | 正确拒绝 buy/sell |
| P1 集成深度 | 风控守护告警 | 注入低流动性持仓 | RiskAlert 日志输出 |
| P2 补缺构建 | EastMoneyBroker 连接 | Windows 环境 + 东方财富客户端 | `is_connected() == True` |
| P2 补缺构建 | Settlement 独立运行 | 调用 `settlement.daily_settle()` | 净值曲线生成 |
| P2 补缺构建 | L2 十档报价解析 | tickflow WebSocket 连接 | Level2Quote 字段齐全 |

---

*计划生成时间: 2026-08-10 | 目标: 毫秒级实时量化交易执行系统*
