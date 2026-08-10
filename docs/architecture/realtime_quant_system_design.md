# 实时量化交易系统 — 实现方案级架构设计

> 基于 `daily_stock_analysis` 代码库（commit `ebd9f40`）的逐函数级现状分析
> 每条方案给出：现状定位 → 差距 → 接口定义 → 数据流 → 关键伪代码 → 配置项

---

## 目录

- [1. P0 — 上线硬前置](#1-p0--上线硬前置)
  - [1.1 完整回测框架](#11-完整回测框架)
  - [1.2 券商接口适配层](#12-券商接口适配层)
  - [1.3 时钟同步与时间一致性](#13-时钟同步与时间一致性)
- [2. P1 — 上线前必备](#2-p1--上线前必备)
  - [2.1 WebSocket 行情接入整合 MarketListener](#21-websocket-行情接入整合-marketlistener)
  - [2.2 熔断机制](#22-熔断机制)
  - [2.3 实时风控守护进程](#23-实时风控守护进程)
  - [2.4 系统健康检查与告警](#24-系统健康检查与告警)
- [3. P2 — 规模化前提](#3-p2--规模化前提)
  - [3.1 行情数据质量 Pipeline](#31-行情数据质量-pipeline)
  - [3.2 行情数据持久化仓库](#32-行情数据持久化仓库)
  - [3.3 OMS/RMS 生产级分离](#33-omsrms-生产级分离)
  - [3.4 全链路延迟监控](#34-全链路延迟监控)
  - [3.5 订单状态机幂等化](#35-订单状态机幂等化)
- [4. P3 — 竞争力差异](#4-p3--竞争力差异)
  - [4.1 Level 2 深度行情](#41-level-2-深度行情)
  - [4.2 信号融合与冲突仲裁](#42-信号融合与冲突仲裁)
  - [4.3 企业事件处理](#43-企业事件处理)
  - [4.4 特征工程管线](#44-特征工程管线)
  - [4.5 在线学习与模型漂移检测](#45-在线学习与模型漂移检测)
- [5. 其他关键能力](#5-其他关键能力)
  - [5.1 策略生命周期管理](#51-策略生命周期管理)
  - [5.2 网络冗余与灾备](#52-网络冗余与灾备)
  - [5.3 AI 推理延迟分离处理](#53-ai-推理延迟分离处理)
  - [5.4 极端行情应对](#54-极端行情应对)
- [6. 实施路线图](#6-实施路线图)

---

# 1. P0 — 上线硬前置

## 1.1 完整回测框架

### 1.1.1 现状

```
src/core/backtest_engine.py    ← 已存在但未与 paper_trading 对接
paper_trading/backtest_adapter.py ← P2 阶段占位
paper_trading/strategies/engine/rule_engine.py:54-110
  └── evaluate() 只对最新两根 bar 做"快照判断"
  └── evaluate_multi_timeframe() 同周期 AND 共识 (line 112-168)
```

**核心问题**：`RuleEngine.evaluate()` 的输入是 `df.iloc[-1]` 和 `df.iloc[-2]`（`rule_engine.py:76-77`），返回"当前时刻是否触发"。这不是回测，是实盘快照。没有任何循环遍历历史 bar 逐日模拟的能力。

### 1.1.2 差距

| 缺失项 | 影响 |
|---|---|
| 逐日/逐 tick 历史回测循环 | 无法验证策略历史表现 |
| 滑点/手续费/涨跌停模拟 | 回测收益虚高，实盘亏损 |
| Walk-forward 优化 | 参数过拟合，样本外失效 |
| 参数敏感性分析 | 不知道策略对参数有多敏感 |
| 基准对比（benchmark） | 不知道跑没跑赢指数 |

### 1.1.3 实现方案

#### 核心数据流

```
历史K线 (DataFetcherManager.get_daily_data)
  │
  ▼
BacktestEngine (新建 paper_trading/backtest/engine.py)
  │
  ├── for each bar_date in date_range:
  │     ├── 更新持仓市值 (mark_to_market)
  │     ├── RuleEngine.evaluate(df[:today])  ← 关键：each bar sees only history up to that point
  │     ├── 模拟成交 (滑点模型 + 手续费)
  │     ├── 检查止损止盈
  │     └── 记录每日 snapshot
  │
  ▼
PerformanceReport (收益曲线 / Sharpe / MaxDD / 胜率 / 盈亏比)
```

#### 接口定义

```python
# paper_trading/backtest/engine.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import date
import pandas as pd

@dataclass
class BacktestConfig:
    """回测配置 — 对齐 paper_trading/risk.py:RiskConfig"""
    initial_cash: float = 100_000.0
    start_date: date = date(2020, 1, 1)
    end_date: date = date.today()
    benchmark_code: str = "000300"        # 沪深300
    slippage_bps: float = 5.0              # 滑点 5bp (0.05%)
    commission_bps: float = 2.5            # 佣金 2.5bp
    stamp_duty_bps: float = 10.0           # 印花税 10bp (仅卖出)
    min_commission: float = 5.0            # 最低佣金 5 元
    lot_size: int = 100                    # 每手股数
    enable_limit_up_down: bool = True      # 涨跌停限制
    max_position_pct: float = 0.30         # 单票最大仓位

@dataclass
class DailySnapshot:
    date: date
    cash: float
    total_assets: float
    positions: Dict[str, float]           # code → market_value
    daily_return: float
    cumulative_return: float
    benchmark_return: float               # 同期基准收益

@dataclass
class BacktestResult:
    config: BacktestConfig
    snapshots: List[DailySnapshot]
    trades: List[Dict]                     # 对齐 TradingEngine.TradeResult.to_dict()
    # --- 绩效指标 ---
    total_return: float
    annual_return: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration: int
    win_rate: float
    profit_loss_ratio: float
    avg_hold_days: float
    calmar_ratio: float
    benchmark_return: float
    excess_return: float                   # alpha

class BacktestEngine:
    """逐 bar 历史回测引擎 — 复用 RuleEngine + FeeModel + SLTP"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self.rule_engine = RuleEngine()
        self.fee_model = FeeModel()         # 复用 paper_trading/fees.py
        # 虚拟持仓管理（不碰真实 DB）
        self._cash = config.initial_cash
        self._positions: Dict[str, Dict] = {}  # code → {qty, avg_cost}
        self._snapshots: List[DailySnapshot] = []
        self._trades: List[Dict] = []
        self._daily_pnl: Dict[date, float] = {}  # 用于 MaxDD 计算

    def run(
        self,
        codes: List[str],
        strategies: List[RuleStrategy],    # 来自 paper_trading/strategies/engine/schema.py
        daily_data: Dict[str, pd.DataFrame],  # code → df (columns: open/high/low/close/volume)
        benchmark_df: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """主回测循环"""
        ...

    def _simulate_fill(
        self,
        code: str,
        side: str,
        signal_price: float,
        quantity: float,
        bar: pd.Series,
    ) -> Optional[Dict]:
        """模拟成交——含滑点 + 涨跌停检查 + 手续费"""
        # 1. 滑点模型：买入 = signal × (1 + slippage)，卖出 = signal × (1 - slippage)
        # 2. 涨跌停检查：买入价触及涨停 → 无法成交 → 顺延到下一 bar
        # 3. 检查 bar 的 high/low 范围：限价单的 price 必须在 [low, high] 内
        ...

    def _ensure_no_lookahead(self, df: pd.DataFrame, bar_index: int) -> pd.DataFrame:
        """关键防作弊：截断到 bar_index，确保没有未来数据"""
        return df.iloc[:bar_index + 1]
```

#### Walk-forward 优化

```python
# paper_trading/backtest/walkforward.py

@dataclass
class WalkforwardConfig:
    train_window_days: int = 504           # 2 年训练窗口
    test_window_days: int = 126            # 6 个月测试窗口
    step_days: int = 63                     # 步进 3 个月
    param_grid: Dict[str, List[float]] = field(default_factory=dict)

class WalkforwardOptimizer:
    """滚动优化 → 避免参数过拟合"""

    def run(self, strategy: RuleStrategy, data: pd.DataFrame, config: WalkforwardConfig):
        """
        对每个窗口:
          1. [t, t+504] 训练 → 网格搜索最优参数
          2. [t+504, t+630] 测试 → 用最优参数回测（样本外）
          3. 记录该窗口的样本外指标
          4. 窗口滑动 63 天
        最终输出:
          - 样本外 Sharpe 分布 (boxplot 判稳健性)
          - 参数稳定性 (最优参数是否随窗口漂移)
        """
        ...
```

#### 与现有 paper_trading 的对接

```python
# paper_trading/backtest_adapter.py (现有的占位文件 → 充实)

def backtest_from_paper_account(
    account_id: int,
    strategies: List[RuleStrategy],
    start_date: date,
    end_date: date,
) -> BacktestResult:
    """
    从现有 PaperAccount 的资金/持仓出发做回测。
    复用 data_provider.DataFetcherManager 拉历史数据。
    """
    from data_provider.base import DataFetcherManager
    fetcher = DataFetcherManager()
    codes = _get_watched_codes(account_id)

    # 拉历史日线
    daily_data = {}
    for code in codes:
        df, _ = fetcher.get_daily_data(code, start_date=start_date, end_date=end_date, days=9999)
        if df is not None and not df.empty:
            daily_data[code] = df

    engine = BacktestEngine(BacktestConfig(
        initial_cash=_get_account_cash(account_id),
        start_date=start_date,
        end_date=end_date,
    ))
    return engine.run(codes, strategies, daily_data)
```

#### 配置项

```ini
# .env 新增
BACKTEST_DEFAULT_COMMISSION_BPS=2.5
BACKTEST_DEFAULT_SLIPPAGE_BPS=5.0
BACKTEST_TRADING_DAYS_PER_YEAR=242
BACKTEST_RISK_FREE_RATE=0.03          # 3% 无风险利率 (Sharpe 计算用)
BACKTEST_ENABLE_LIMIT_UP_DOWN=true
BACKTEST_ENABLE_DIVIDEND_ADJUST=true  # 分红除权调整 (依赖 4.3 企业事件)
```

#### 关键实施步骤

1. **Phase 1**：`BacktestEngine.run()` — 单策略单股票循环回测，输出 daily PnL
2. **Phase 2**：滑点 + 涨跌停 + 手续费的 `_simulate_fill`
3. **Phase 3**：`WalkforwardOptimizer` — 滚动窗口避免过拟合
4. **Phase 4**：`PerformanceReport` — 指标计算 + `reports/backtest_*.html` 可视化报告
5. **Phase 5**：对接 `backtest_adapter.py` → API 端点 `/api/v1/paper_trading/{id}/backtest`

---

## 1.2 券商接口适配层

### 1.2.1 现状

```
paper_trading/trading_engine.py
  └── submit_signal() (line 144) → 操作内部 PaperAccount
  └── _execute_market_order() (line 366) → 模拟成交
  └── OrderManager (order.py) → 操作本地 SQLite

无任何外部券商 API 调用
```

### 1.2.2 差距

| 缺失项 | 影响 |
|---|---|
| 订单路由到真实券商 | 永远在纸上交易 |
| 处理部分成交 | 模拟盘都是 All-or-Nothing 成交 |
| 撤单失败处理 | 真实撤单可能被拒绝（已成交/已排队） |
| 账户同步（持仓/资金） | 真实账户和模拟账户可能漂移 |
| 券商限流/连接管理 | 频繁下单可能被 ban |

### 1.2.3 实现方案

#### 架构：多源适配器（对齐 `data_provider/BaseFetcher` 的多源模式）

```
              ┌────────────────────────┐
              │   TradingEngine        │
              │  (submit_signal 不变)  │
              └──────────┬─────────────┘
                         │
              ┌──────────▼─────────────┐
              │   BrokerRouter         │  ← 新增：根据 account.broker 路由
              │   (paper_trading/      │
              │    broker/router.py)   │
              └──────────┬─────────────┘
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                  ▼
  ┌──────────┐   ┌──────────────┐   ┌──────────────┐
  │ Paper    │   │ 东方财富/    │   │ 华泰/国泰    │
  │ Broker   │   │ easyquotation│   │ (未来)       │
  │ (现有)   │   │ (免费 L1)   │   │              │
  └──────────┘   └──────────────┘   └──────────────┘
```

#### 接口定义

```python
# paper_trading/broker/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum

class BrokerOrderStatus(str, Enum):
    """对齐 paper_trading/order.py:OrderStatus，增加真实券商状态"""
    PENDING = "pending"
    QUEUED = "queued"            # 已排队等待交易所确认
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"          # 当日未成交自动失效 (A股)

@dataclass
class BrokerPosition:
    code: str
    name: str
    quantity: int
    available_quantity: int      # 可用（未冻结）
    avg_cost: float
    current_price: float
    market_value: float
    profit_loss: float
    profit_loss_pct: float

@dataclass
class BrokerAccount:
    account_id: str              # 券商账户号
    total_assets: float
    available_cash: float
    frozen_cash: float
    positions: List[BrokerPosition]

class BaseBroker(ABC):
    """券商适配器抽象基类 — 对齐 data_provider/base.py:BaseFetcher"""

    @abstractmethod
    def submit_order(self, order_req: "OrderRequest") -> Dict:
        """提交订单 → 返回券商订单 ID + 状态"""
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """撤单 → 返回是否成功"""
        ...

    @abstractmethod
    def query_order(self, broker_order_id: str) -> Dict:
        """查询订单状态"""
        ...

    @abstractmethod
    def query_positions(self) -> List[BrokerPosition]:
        """查询持仓"""
        ...

    @abstractmethod
    def query_account(self) -> BrokerAccount:
        """查询账户资金"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """连接健康检查"""
        ...
```

#### Router——生产/模拟透明切换

```python
# paper_trading/broker/router.py

class BrokerRouter:
    """根据 PaperAccount 的 broker 字段路由到正确的适配器"""

    def __init__(self):
        self._brokers: Dict[str, BaseBroker] = {}
        self._register_defaults()

    def _register_defaults(self):
        from paper_trading.broker.paper_broker import PaperBroker
        self._brokers["paper"] = PaperBroker()  # 现有模拟盘

    def register(self, name: str, broker: BaseBroker):
        self._brokers[name] = broker

    def resolve(self, account_id: int) -> BaseBroker:
        """
        根据 account.broker 返回适配器。
        如果 broker 是 "paper"，走 PaperBroker（复用现有逻辑）。
        如果 broker 是 "eastmoney"，走 EastMoneyBroker。
        """
        from paper_trading.account import PaperAccountManager
        account = PaperAccountManager().get(account_id)
        broker_name = getattr(account, "broker", "paper")
        return self._brokers.get(broker_name, self._brokers["paper"])

    def submit_signal(
        self,
        account_id: int,
        signal: Signal,
        order_type: OrderType = OrderType.MARKET,
    ) -> TradeResult:
        """
        对齐 TradingEngine.submit_signal 的现有签名，
        但根据 account 路由到真实/模拟券商。
        """
        ...
```

#### TradingEngine 改动——最小侵入

```python
# paper_trading/trading_engine.py 改动 (在 submit_signal 中)

def submit_signal(self, account_id, signal, ...):
    # [现有] 持久化 signal (line 158) — 保持不变
    # [现有] 风控检查 (line 206-228) — 保持不变
    # [现有] Agent 审查 (line 234-290) — 保持不变

    # [新增] 根据 account broker 路由
    broker = self.broker_router.resolve(account_id)
    broker_order_id = broker.submit_order(order_req)
    # 记录 broker_order_id 到 PaperOrder 表 ← 新增字段
    ...
```

#### 券商特定适配器示例（东方财富 easyquotation）

```python
# paper_trading/broker/eastmoney_broker.py

class EastMoneyBroker(BaseBroker):
    """基于 easyquotation 库的东方财富接口 (L1 行情 + 模拟委托)"""

    def __init__(self, user: str, password: str):
        import easytrader
        self._client = easytrader.use('eastmoney')
        self._client.connect(r'C:\Program Files\东方财富\xiadan.exe')
        self._client.prepare(user, password, comm_password=None)

    def submit_order(self, order_req: OrderRequest) -> Dict:
        if order_req.order_type == OrderType.MARKET:
            result = self._client.buy(
                order_req.code, order_req.price, order_req.quantity
            )
        else:
            result = self._client.buy(
                order_req.code, order_req.price, order_req.quantity
                # easyquotation 的 buy/sell 自动按市价
            )
        return {
            "broker_order_id": result.get("entrust_no"),
            "status": BrokerOrderStatus.QUEUED.value,
            "filled_quantity": 0,
            "filled_price": None,
        }

    def cancel_order(self, broker_order_id: str) -> bool:
        return self._client.cancel_entrust(broker_order_id)

    def query_positions(self) -> List[BrokerPosition]:
        positions = self._client.position
        return [
            BrokerPosition(
                code=p["证券代码"],
                name=p["证券名称"],
                quantity=p["股票余额"],
                available_quantity=p["可用余额"],
                avg_cost=p["成本价"],
                current_price=p["市价"],
                market_value=p["市值"],
                profit_loss=p["盈亏"],
                profit_loss_pct=p["盈亏比例(%)"],
            )
            for p in positions
        ]
```

#### 关键陷阱与对策

| 陷阱 | 对策 |
|---|---|
| 真实撤单可能失败（已成交） | `query_order` 确认状态后再标记本地 OrderManager 状态 |
| 部分成交 | `BrokerOrderStatus.PARTIALLY_FILLED` → `OrderManager` 记录 fill_quantity < quantity |
| 账户漂移（本地 DB vs 券商） | 每日开盘前/收盘后全量同步 `query_positions()` + `query_account()` |
| 券商 API 限流 | `BrokerRouter` 维护 per-broker 请求速率限制器 (token bucket) |
| 连接断开 | `is_connected()` 心跳检测 → 断开超过 N 秒自动 failover 到 PaperBroker（兜底） |

#### 配置项

```ini
# .env 新增
BROKER_EASTMONEY_USER=your_account
BROKER_EASTMONEY_PASSWORD=your_password
BROKER_CONNECT_TIMEOUT_SECONDS=10
BROKER_ORDER_TIMEOUT_SECONDS=30
BROKER_FAILOVER_TO_PAPER_ON_DISCONNECT=true  # 券商断开→自动切模拟
BROKER_SYNC_INTERVAL_MINUTES=30              # 持仓同步间隔
```

---

## 1.3 时钟同步与时间一致性

### 1.3.1 现状

```
market_listener.py:811,825 → datetime.now() (本地时间)
market_listener.py:138-172 → is_market_open_now() 使用 datetime.now()
rule_engine.py:76-78 → df.index[-1] / df.index[-2] (依赖数据源时间戳)
无 NTP 同步，无交易所时钟参照
```

### 1.3.2 差距

| 问题 | 后果 |
|---|---|
| 本地时间不准（慢 30 秒） | 收盘前 30 秒还在发单 → 交易所已拒绝 |
| 数据源时间戳不一致 | akshare 返回的是东八区、yfinance 返回 UTC → 回测 bar 对不齐 |
| 不同模块用不同时间源 | listener 用 `datetime.now()`，LLM 分析用另一个时间 → trace_id 链路的时间轴乱序 |

### 1.3.3 实现方案

#### 核心：统一时钟源 `ExchangeClock`

```python
# src/utils/exchange_clock.py
from datetime import datetime, timezone, timedelta
from typing import Optional
import ntplib

# 交易所时区映射
EXCHANGE_TIMEZONES = {
    "cn": timezone(timedelta(hours=8)),     # 上交所/深交所
    "hk": timezone(timedelta(hours=8)),     # 港交所
    "us": timezone(timedelta(hours=-4)),    # 美东夏令时 (EDT)
    "jp": timezone(timedelta(hours=9)),     # 东京
    "kr": timezone(timedelta(hours=9)),     # 首尔
}

class ExchangeClock:
    """
    统一时钟源。
    优先级: NTP → 交易所 API 时间 → 系统时间 (降级)
    所有模块的时间获取必须通过此类。
    """

    _instance: Optional["ExchangeClock"] = None
    _offset_ms: float = 0.0  # NTP 偏差 (local - NTP)
    _last_sync: Optional[datetime] = None

    @classmethod
    def now(cls, market: str = "cn") -> datetime:
        """返回指定交易所的当前时间 (带时区)"""
        utc = datetime.now(timezone.utc)
        tz = EXCHANGE_TIMEZONES.get(market, EXCHANGE_TIMEZONES["cn"])
        return utc.astimezone(tz)

    @classmethod
    def sync(cls) -> bool:
        """NTP 同步 — 启动时和每 60 分钟执行"""
        try:
            client = ntplib.NTPClient()
            response = client.request('pool.ntp.org', version=3, timeout=3)
            cls._offset_ms = response.offset * 1000  # 秒 → 毫秒
            cls._last_sync = datetime.now(timezone.utc)
            return True
        except Exception:
            return False

    @classmethod
    def is_synced(cls) -> bool:
        """NTP 同步状态 — 用于健康检查"""
        if cls._last_sync is None:
            return False
        return (datetime.now(timezone.utc) - cls._last_sync).seconds < 3600
```

#### MarketListener 改动

```python
# market_listener.py 改动

from src.utils.exchange_clock import ExchangeClock

def _tick_market(self, market: str) -> None:
    # [原] now = datetime.now()
    # [改为] 使用交易所时钟
    now = ExchangeClock.now(market)

    open_now = is_market_open_now(market, now)  # market_listener.py:138 已接受 now 参数
    ...

def _should_emit_signal(self, signal: Signal) -> bool:
    # [原] last = self._last_signal_at.get(key)
    # [原] elapsed = (datetime.now() - last).total_seconds()
    # [改为]
    elapsed = (ExchangeClock.now("cn") - last).total_seconds()  # line 813
    ...

def _record_signal(self, signal: Signal) -> None:
    # [原] self._last_signal_at[key] = datetime.now()  # line 825
    # [改为]
    self._last_signal_at[key] = ExchangeClock.now("cn")
```

#### 数据源时间标准化

```python
# data_provider/base.py 新增方法

class DataFetcherManager:
    def get_daily_data(self, stock_code, start_date, end_date, days):
        df, source_name = self._get_daily_data_raw(...)

        # [新增] 时间标准化
        df = self._normalize_timestamps(df, stock_code)
        return df, source_name

    @staticmethod
    def _normalize_timestamps(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        统一所有数据源的 index 为东八区 naive datetime。
        yfinance 返回 UTC → 转东八；akshare 已东八 → 保持。
        去除时区信息，统一为本地 naive datetime。
        """
        if df.index.tz is not None:
            if _is_us_market(stock_code):
                # 美股 → 转美东
                tz = EXCHANGE_TIMEZONES["us"]
            else:
                tz = EXCHANGE_TIMEZONES["cn"]
            df.index = df.index.tz_convert(tz).tz_localize(None)
        return df
```

#### 配置项

```ini
# .env 新增
NTP_SERVER=pool.ntp.org
NTP_SYNC_INTERVAL_SECONDS=3600         # 每 60 分钟同步一次
NTP_MAX_OFFSET_MS_WARN=500             # 偏差超 500ms 告警
NTP_MAX_OFFSET_MS_ERROR=2000           # 偏差超 2000ms 拒绝交易
```

---

# 2. P1 — 上线前必备

## 2.1 WebSocket 行情接入整合 MarketListener

### 2.1.1 现状

```
market_listener.py:696-720  _fetch_latest_prices()
  └── 串行逐股票 HTTP 调用 DataFetcherManager.get_realtime_quote()
  └── 每个 tick 重新拉全部 watched_codes

market_listener.py:308  tick_interval_seconds = 10.0
market_listener.py:435  self._shutdown.wait(timeout=tick_interval_seconds)
```

**问题**：10 秒轮询 → 对日内突破策略意味着最多落后市场 10 秒。串行拉取 N 只股票 = N 次 HTTP 往返。

### 2.1.2 实现方案

#### 架构：双通道模式

```
                    ┌───────────────────────────┐
                    │     MarketListener          │
                    │  (保持现有 tick 循环结构)   │
                    └───────┬───────────┬─────────┘
                            │           │
              ┌─────────────▼──┐   ┌────▼──────────────┐
              │  PollChannel   │   │  WebSocketChannel │ ← 新增
              │  (现有轮询)    │   │                   │
              │  tick=10s      │   │  实时推送 L1 quote│
              │  兜底模式      │   │  (tickflow/       │
              │               │   │   longbridge)     │
              └───────────────┘   └───────────────────┘
                        │                   │
                        └─────────┬─────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │   SharedQuoteCache          │ ← 新增
                    │   (最新价缓存，两种通道     │
                    │    都写入这里)              │
                    └─────────────────────────────┘
```

#### SharedQuoteCache 定义

```python
# paper_trading/quote_cache.py
from threading import RLock
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class CachedQuote:
    price: float
    volume: float
    change_pct: float
    high: float
    low: float
    open: float
    pre_close: float
    timestamp: datetime              # 行情时间戳（不是本地接收时间！）
    source: str                     # "poll_efinance" / "ws_tickflow" / "ws_longbridge"
    received_at: datetime = field(default_factory=datetime.now)

class SharedQuoteCache:
    """线程安全的最新价缓存 — PollChannel 和 WebSocketChannel 共享"""

    def __init__(self, max_age_seconds: float = 5.0):
        self._quotes: Dict[str, CachedQuote] = {}
        self._lock = RLock()
        self._max_age = max_age_seconds

    def update(self, code: str, quote: CachedQuote):
        with self._lock:
            self._quotes[code] = quote

    def get(self, code: str) -> Optional[CachedQuote]:
        with self._lock:
            q = self._quotes.get(code)
            if q is None:
                return None
            if (datetime.now() - q.received_at).total_seconds() > self._max_age:
                return None  # 过期
            return q

    def get_all(self) -> Dict[str, CachedQuote]:
        """MarketListener._fetch_latest_prices 替代品"""
        with self._lock:
            now = datetime.now()
            return {
                code: q
                for code, q in self._quotes.items()
                if (now - q.received_at).total_seconds() <= self._max_age
            }
```

#### WebSocketChannel 定义

```python
# paper_trading/ws_channel.py
import asyncio
import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

class WebSocketChannel:
    """
    WebSocket 实时行情通道。
    设计为 asyncio 协程，在 MarketListener 的线程中通过事件循环桥接。
    """

    def __init__(
        self,
        quote_cache: SharedQuoteCache,
        watched_codes: List[str],
        on_disconnect: Optional[Callable] = None,  # WS 断开回调 → 切 PollChannel
    ):
        self._cache = quote_cache
        self._codes = set(watched_codes)
        self._on_disconnect = on_disconnect
        self._reconnect_backoff = 1.0

    async def connect_tickflow(self):
        """接入 tickflow WebSocket (如果配置了 token)"""
        import websockets
        import json

        while True:
            try:
                async with websockets.connect("wss://api.tickflow.com/v1/ws") as ws:
                    # 订阅代码
                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "codes": list(self._codes),
                    }))
                    # 消费行情推送
                    async for message in ws:
                        data = json.loads(message)
                        for item in data.get("quotes", []):
                            code = item["code"]
                            quote = CachedQuote(
                                price=float(item["price"]),
                                volume=float(item.get("volume", 0)),
                                change_pct=float(item.get("change_pct", 0)),
                                high=float(item.get("high", 0)),
                                low=float(item.get("low", 0)),
                                open=float(item.get("open", 0)),
                                pre_close=float(item.get("pre_close", 0)),
                                timestamp=datetime.fromisoformat(item["time"]),
                                source="ws_tickflow",
                            )
                            self._cache.update(code, quote)
                    # 连接正常关闭
                    self._reconnect_backoff = 1.0  # 重置退避
            except Exception as exc:
                logger.warning("TickFlow WS disconnected: %s, reconnecting in %.1fs", exc, self._reconnect_backoff)
                self._on_disconnect and self._on_disconnect()
                await asyncio.sleep(self._reconnect_backoff)
                self._reconnect_backoff = min(self._reconnect_backoff * 2, 30.0)
```

#### MarketListener 集成改动

```python
# market_listener.py 改动

class MarketListener:
    def __init__(self, ..., use_websocket: bool = False):
        # [新增] QuoteCache
        self._quote_cache = SharedQuoteCache()
        self._use_ws = use_websocket
        self._ws_channel: Optional[WebSocketChannel] = None
        self._ws_fallback_to_poll = False  # WS 断开 → 自动切轮询

    def start(self):
        if self._use_ws:
            self._start_ws_in_background()
        # [现有] 启动 daemon 线程
        super().start()

    def _start_ws_in_background(self):
        import threading
        def _run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._ws_channel = WebSocketChannel(
                self._quote_cache,
                self.config.watched_codes,
                on_disconnect=lambda: setattr(self, '_ws_fallback_to_poll', True),
            )
            loop.run_until_complete(self._ws_channel.connect_tickflow())
        threading.Thread(target=_run_ws, daemon=True, name="ws-channel").start()

    def _fetch_latest_prices(self, codes: List[str]) -> Dict[str, float]:
        """
        [修改] 优先从 QuoteCache 读取（WS 推送），
        过期/缺失的走轮询兜底。
        """
        # 1. 从缓存获取
        cached = self._quote_cache.get_all()
        out: Dict[str, float] = {}
        missing: List[str] = []

        for code in codes:
            q = cached.get(code)
            if q is not None:
                out[code] = q.price
            else:
                missing.append(code)

        # 2. 缺失的走 HTTP 轮询兜底（复用现有逻辑）
        if missing:
            for code in missing:
                try:
                    quote = self.fetcher.get_realtime_quote(code)
                    if quote and quote.price > 0:
                        out[code] = float(quote.price)
                        # 同时写入缓存
                        self._quote_cache.update(code, CachedQuote(
                            price=float(quote.price),
                            volume=float(getattr(quote, "volume", 0)),
                            change_pct=float(getattr(quote, "change_pct", 0)),
                            high=float(getattr(quote, "high", 0)),
                            low=float(getattr(quote, "low", 0)),
                            open=float(getattr(quote, "open", 0)),
                            pre_close=float(getattr(quote, "pre_close", 0)),
                            timestamp=datetime.now(),
                            source=f"poll_{getattr(quote, 'fetcher_name', 'unknown')}",
                        ))
                except Exception as exc:
                    logger.debug("Poll fallback failed for %s: %s", code, exc)
        return out
```

#### 策略评估的 tick 频率调整

```python
# market_listener.py 新增配置项

class MarketListenerConfig:
    # [现有] tick_interval_seconds: float = 10.0
    # [新增]
    tick_interval_seconds: float = 10.0          # 兜底轮询间隔
    ws_tick_interval_seconds: float = 0.5         # WS 模式下策略评估间隔 (500ms)
    enable_websocket: bool = False
    ws_provider: str = "tickflow"                 # "tickflow" / "longbridge"
```

```python
# run_loop 改动
def run_loop(self):
    while not self._shutdown.is_set():
        try:
            self._tick_all_markets()
        except Exception:
            logger.exception("tick error")

        # [修改] WS 模式下用更短的间隔
        interval = (
            self.config.ws_tick_interval_seconds
            if self._use_ws and not self._ws_fallback_to_poll
            else self.config.tick_interval_seconds
        )
        self._shutdown.wait(timeout=interval)
```

#### 配置项

```ini
# .env 新增
PAPER_TRADING_ENABLE_WEBSOCKET=false       # 是否启用 WS 行情
PAPER_TRADING_WS_PROVIDER=tickflow
PAPER_TRADING_WS_TICK_INTERVAL=0.5          # WS 模式下策略评估间隔 (秒)
PAPER_TRADING_WS_FALLBACK_TO_POLL=true      # WS 断开自动切 HTTP 轮询
TICKFLOW_API_KEY=your_key
LONGBRIDGE_APP_KEY=your_key                 # 已有，直接用
```

---

## 2.2 熔断机制

### 2.2.1 现状

```
paper_trading/risk.py:70  max_daily_loss_pct = 0.05  # 日亏损上限 5%
  └── 只在 submit_signal() 时检查 (line 135)
  └── 是"下次交易前检查"，不是"实时监控并主动平仓"
  └── 没有逐笔 PnL 监控，没有自动锁仓
```

### 2.2.2 实现方案

#### 熔断分层

```
Level 1 — 软熔断 (Soft Circuit Breaker)
  └── 触发: 当日累计亏损 > 3% 初始资金
  └── 动作: 禁止新开仓 (reject 所有 buy signal) + 通知
  └── 恢复: 次日自动解除

Level 2 — 硬熔断 (Hard Circuit Breaker)
  └── 触发: 当日累计亏损 > 5% 初始资金
  └── 动作: 禁止所有交易 (reject buy+sell) + 批量挂止损单 + 管理员告警
  └── 恢复: 人工确认后解除

Level 3 — 强制平仓 (Liquidation)
  └── 触发: 当日累计亏损 > 8% 初始资金 或 组合 VaR > 阈值
  └── 动作: 市价平掉所有持仓 → 锁账户 → 需要人工+冷却期才能解锁
  └── 恢复: 人工确认 + 24h 冷却期
```

#### 核心类 `CircuitBreaker`

```python
# paper_trading/circuit_breaker.py
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable, List

class BreakerLevel(str, Enum):
    NORMAL = "normal"       # 正常
    SOFT = "soft"           # 软熔断 (禁止新开仓)
    HARD = "hard"           # 硬熔断 (禁止所有交易)
    LIQUIDATION = "liquidate"  # 强制平仓

@dataclass
class BreakerConfig:
    soft_threshold_pct: float = 0.03       # 3% → 软熔断
    hard_threshold_pct: float = 0.05       # 5% → 硬熔断
    liquidation_threshold_pct: float = 0.08  # 8% → 强制平仓
    var_threshold_pct: float = 0.10        # VaR > 10% → 强制平仓
    cooling_period_hours: int = 24         # 强制平仓后冷却期
    enable_auto_reset_soft: bool = True    # 软熔断次日自动解
    check_interval_seconds: float = 1.0    # 熔断检查间隔

@dataclass
class BreakerState:
    level: BreakerLevel = BreakerLevel.NORMAL
    triggered_at: Optional[datetime] = None
    daily_pnl: float = 0.0
    initial_capital: float = 0.0
    reason: str = ""

class CircuitBreaker:
    """
    独立于 TradingEngine 的熔断守护。
    与 MarketListener 并行运行，持续监控 PnL。
    """

    def __init__(
        self,
        config: BreakerConfig,
        account_id: int,
        on_soft_trigger: Optional[Callable] = None,     # 通知回调
        on_hard_trigger: Optional[Callable] = None,
        on_liquidation: Optional[Callable] = None,       # 平仓执行回调
    ):
        self.config = config
        self.account_id = account_id
        self.state = BreakerState()
        self._callbacks = {
            BreakerLevel.SOFT: on_soft_trigger,
            BreakerLevel.HARD: on_hard_trigger,
            BreakerLevel.LIQUIDATION: on_liquidation,
        }

    def evaluate(self, current_pnl: float, initial_capital: float, current_var: Optional[float] = None) -> BreakerState:
        """
        每次 tick 调用：评估是否需要熔断。
        TradingEngine 调用此方法 — 如果返回非 NORMAL，submit_signal 必须 reject。
        """
        pnl_pct = abs(current_pnl) / initial_capital if initial_capital > 0 else 0.0

        # 检查是否在冷却期
        if self.state.level == BreakerLevel.LIQUIDATION:
            if self.state.triggered_at:
                elapsed = (datetime.now() - self.state.triggered_at).total_seconds() / 3600
                if elapsed < self.config.cooling_period_hours:
                    return self.state  # 仍在冷却期

        # Level 3: 强制平仓
        if pnl_pct >= self.config.liquidation_threshold_pct:
            return self._trigger(BreakerLevel.LIQUIDATION, f"PnL={pnl_pct:.2%} exceeded liquidation limit")
        if current_var and abs(current_var) / initial_capital >= self.config.var_threshold_pct:
            return self._trigger(BreakerLevel.LIQUIDATION, f"VaR exceeded limit")

        # Level 2: 硬熔断
        if pnl_pct >= self.config.hard_threshold_pct:
            return self._trigger(BreakerLevel.HARD, f"PnL={pnl_pct:.2%} exceeded hard limit")

        # Level 1: 软熔断
        if pnl_pct >= self.config.soft_threshold_pct and self.state.level == BreakerLevel.NORMAL:
            return self._trigger(BreakerLevel.SOFT, f"PnL={pnl_pct:.2%} exceeded soft limit")

        return self.state

    def _trigger(self, level: BreakerLevel, reason: str) -> BreakerState:
        self.state.level = level
        self.state.triggered_at = datetime.now()
        self.state.reason = reason
        # 触发回调
        cb = self._callbacks.get(level)
        if cb:
            cb(level, reason)
        return self.state

    def reset_daily(self):
        """每日开盘时重置 (非 LIQUIDATION 级别)"""
        if self.state.level != BreakerLevel.LIQUIDATION:
            self.state = BreakerState()

    def allow_new_position(self) -> bool:
        return self.state.level == BreakerLevel.NORMAL

    def allow_any_trade(self) -> bool:
        return self.state.level in (BreakerLevel.NORMAL, BreakerLevel.SOFT)
```

#### TradingEngine 集成

```python
# trading_engine.py submit_signal() 改动

def submit_signal(self, account_id, signal, ...):
    # [现有] persist signal — 不变

    # [新增] 熔断检查
    if self.circuit_breaker is not None:
        # 先更新 PnL
        account = self.account_mgr.snapshot(account_id)
        current_pnl = account.total_assets - account.initial_capital

        breaker_state = self.circuit_breaker.evaluate(
            current_pnl=current_pnl,
            initial_capital=account.initial_capital,
        )

        # 硬熔断 / 强制平仓 → 拒绝
        if not self.circuit_breaker.allow_any_trade():
            return TradeResult(
                ..., status="rejected",
                reason=f"熔断: {breaker_state.reason}",
            )

        # 软熔断 → 拒绝新开仓 (buy)，允许 sell
        if not self.circuit_breaker.allow_new_position() and side == "buy":
            return TradeResult(
                ..., status="rejected",
                reason=f"软熔断: 禁止新开仓 ({breaker_state.reason})",
            )

    # [现有] 风控 + agent 审查 — 不变
    ...
```

#### Liquidation 执行

```python
# 在 _on_liquidation 回调中

def execute_liquidation(engine: TradingEngine, account_id: int):
    """强制平掉所有持仓"""
    positions = engine.position_mgr.list_positions(account_id)
    for pos in positions:
        # 全部市价卖出
        signal = Signal(
            side="sell",
            code=pos.code,
            name=pos.name,
            trigger_price=pos.current_price or 0.0,
            suggested_quantity=pos.available_quantity,
            strategy_name="circuit_breaker",
            reason=f"强制平仓: 熔断触发",
        )
        engine.submit_signal(account_id, signal, order_type=OrderType.MARKET)
    # 锁账户
    engine.account_mgr.set_status(account_id, "locked")
```

#### 配置项

```ini
# .env 新增
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_SOFT_THRESHOLD_PCT=3.0
CIRCUIT_BREAKER_HARD_THRESHOLD_PCT=5.0
CIRCUIT_BREAKER_LIQUIDATION_THRESHOLD_PCT=8.0
CIRCUIT_BREAKER_VAR_THRESHOLD_PCT=10.0
CIRCUIT_BREAKER_COOLING_HOURS=24
CIRCUIT_BREAKER_CHECK_INTERVAL_SECONDS=1
CIRCUIT_BREAKER_AUTO_RESET_SOFT=true
```

---

## 2.3 实时风控守护进程

### 2.3.1 现状

```
paper_trading/risk.py:RiskChecker — 同步调用，仅在 submit_signal 时触发
paper_trading/agent_risk.py:AgentRiskReviewer — 同上，可选 LLM 审查
```

没有独立的实时风控线程来做：
- 组合 VaR 监控
- 流动性风险预警
- 市场异常检测

### 2.3.2 实现方案

#### 架构

```
                ┌───────────────────────────┐
                │   RiskDaemon              │  ← 新增：独立线程
                │   (paper_trading/         │
                │    risk_daemon.py)        │
                └───────────┬───────────────┘
                            │ 每 1 秒 tick
                ┌───────────▼───────────────┐
                │                           │
        ┌───────▼──────┐  ┌───────▼──────┐  ┌──────▼───────┐
        │ VaR Monitor  │  │ Liquidity    │  │ Market       │
        │ (历史模拟法  │  │ Monitor      │  │ Anomaly      │
        │  + 参数法)   │  │ (换手率/     │  │ Detector     │
        │             │  │  买卖价差)   │  │ (波动率突变) │
        └─────────────┘  └─────────────┘  └──────────────┘
                            │
                ┌───────────▼───────────────┐
                │   RiskAlert               │
                │   → notification /         │
                │   → CircuitBreaker.trigger│
                └───────────────────────────┘
```

#### 核心类

```python
# paper_trading/risk_daemon.py
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
import numpy as np

@dataclass
class VaRResult:
    var_95_pct: float       # 95% 置信 VaR
    var_99_pct: float       # 99% 置信 VaR
    cvar_95_pct: float      # Conditional VaR (Expected Shortfall)
    var_pct_of_capital: float
    is_breach: bool

@dataclass
class LiquidityRisk:
    code: str
    daily_turnover_rate: float    # 换手率
    bid_ask_spread_pct: float     # 买卖价差百分比
    is_illiquid: bool
    days_to_liquidate: float      # 按当前成交量清仓需要多少天

class RiskDaemon:
    """独立的风控守护进程 — 持续监控但不直接干预交易"""

    def __init__(
        self,
        account_id: int,
        circuit_breaker: CircuitBreaker,
        check_interval: float = 1.0,
    ):
        self.account_id = account_id
        self.circuit_breaker = circuit_breaker
        self.check_interval = check_interval
        self._var_monitor = VaRMonitor()
        self._liquidity_monitor = LiquidityMonitor()
        self._anomaly_detector = MarketAnomalyDetector()

    def tick(self, account_snapshot, positions, latest_prices):
        """
        每次 tick 执行全部风控检查。
        返回 RiskAlert 列表 — 调用方决定如何响应。
        """
        alerts = []

        # 1. VaR 检查
        var_result = self._var_monitor.compute(positions, lookback_days=252)
        if var_result.is_breach:
            self.circuit_breaker.evaluate(
                current_pnl=self._current_pnl(account_snapshot),
                initial_capital=account_snapshot.initial_capital,
                current_var=var_result.var_95_pct,
            )
            alerts.append(RiskAlert("var_breach", var_result))

        # 2. 流动性检查
        for pos in positions:
            liq = self._liquidity_monitor.check(pos, latest_prices)
            if liq.is_illiquid:
                alerts.append(RiskAlert("liquidity_warning", liq))

        # 3. 市场异常检测
        anomaly = self._anomaly_detector.detect(latest_prices)
        if anomaly.detected:
            alerts.append(RiskAlert("market_anomaly", anomaly))

        return alerts


class VaRMonitor:
    """组合 VaR 计算 — 历史模拟法 + 参数法"""

    def compute(self, positions, lookback_days: int = 252) -> VaRResult:
        """
        历史模拟法: 计算组合在 lookback_days 内每日 PnL 的分布，
        VaR_95 = PnL 分布的 5% 分位数。
        参数法: 假设正态分布，VaR = μ - 1.645σ。
        取两种方法的较大值（更保守）。
        """
        ...
```

#### 配置项

```ini
# .env 新增
RISK_DAEMON_ENABLED=true
RISK_DAEMON_CHECK_INTERVAL_SECONDS=1
RISK_VAR_CONFIDENCE_LEVEL=0.95
RISK_VAR_LOOKBACK_DAYS=252
RISK_LIQUIDITY_MIN_TURNOVER_RATE=0.005   # 换手率 < 0.5% 视为不流动
RISK_LIQUIDITY_MAX_DAYS_TO_LIQUIDATE=5   # 超过 5 天才能清仓 → 告警
RISK_ANOMALY_VOLATILITY_MULTIPLIER=3.0  # 当前波动率 > 3× 历史均值 → 异常
```

---

## 2.4 系统健康检查与告警

### 2.4.1 现状

```
market_listener.py:420-424  _run_safely() → 捕获异常后仅 log
market_listener.py:410-415  stop() → 有 timeout 但无健康上报
无 listener 存活检测
无数据源成功率监控
无内存/CPU 监控
无任务队列积压告警
```

### 2.4.2 实现方案

#### HealthCheck 守护

```python
# src/services/health_check.py
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
import psutil
import threading
import logging

logger = logging.getLogger(__name__)

@dataclass
class HealthStatus:
    component: str
    healthy: bool
    message: str
    last_checked: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)

class HealthCheckDaemon:
    """独立线程：每 N 秒执行全套健康检查，推送告警"""

    def __init__(
        self,
        on_alert: Callable[[str, str], None],  # (level, message)
        check_interval: float = 30.0,
    ):
        self._checks: List[Callable[[], HealthStatus]] = []
        self._on_alert = on_alert
        self._interval = check_interval
        self._past_failures: Dict[str, int] = {}  # component → consecutive failures
        self._alert_threshold = 3  # 连续 3 次失败才告警
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register(self, check_fn: Callable[[], HealthStatus]):
        self._checks.append(check_fn)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="health-daemon")
        self._thread.start()

    def _loop(self):
        while not self._shutdown.is_set():
            for check in self._checks:
                try:
                    status = check()
                    if not status.healthy:
                        self._past_failures[status.component] = self._past_failures.get(status.component, 0) + 1
                        if self._past_failures[status.component] >= self._alert_threshold:
                            self._on_alert("CRITICAL", f"[{status.component}] {status.message}")
                    else:
                        self._past_failures[status.component] = 0
                except Exception as exc:
                    logger.exception("Health check raised: %s", exc)
            self._shutdown.wait(timeout=self._interval)

    def stop(self):
        self._shutdown.set()
```

#### 具体检查项

```python
# 1. MarketListener 存活检查
def check_listener_alive(listener: MarketListener) -> HealthStatus:
    return HealthStatus(
        component="market_listener",
        healthy=listener.is_running(),
        message="running" if listener.is_running() else "DEAD",
        metadata={"last_tick": getattr(listener, "_last_tick_at", None)},
    )

# 2. 数据源成功率
def check_data_source_health(fetcher: DataFetcherManager) -> HealthStatus:
    """利用已有的 _daily_source_health 数据"""
    from data_provider.base import DataFetcherManager as DFM
    health = DFM._daily_source_health  # dict[fetcher_name, (successes, failures)]
    failures = {}
    for name, (s, f) in health.items():
        total = s + f
        if total > 0 and f / total > 0.3:  # 失败率 > 30%
            failures[name] = f / total
    return HealthStatus(
        component="data_sources",
        healthy=len(failures) == 0,
        message=f"failures: {failures}" if failures else "all healthy",
        metadata={"source_failure_rates": failures},
    )

# 3. 任务队列积压
def check_task_queue() -> HealthStatus:
    from src.services.task_queue import get_task_queue
    q = get_task_queue()
    if q is None:
        return HealthStatus(component="task_queue", healthy=True, message="not initialized")
    pending = len(q.list_pending_tasks())
    stats = q.get_task_stats()
    return HealthStatus(
        component="task_queue",
        healthy=pending < 20,
        message=f"pending={pending} total_stats={stats}",
        metadata={"pending": pending, "stats": stats},
    )

# 4. 系统资源
def check_system_resources() -> HealthStatus:
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=1)
    disk = psutil.disk_usage('/')
    issues = []
    if mem.percent > 85:
        issues.append(f"memory={mem.percent}%")
    if cpu > 90:
        issues.append(f"cpu={cpu}%")
    if disk.percent > 90:
        issues.append(f"disk={disk.percent}%")
    return HealthStatus(
        component="system_resources",
        healthy=len(issues) == 0,
        message="; ".join(issues) if issues else "OK",
        metadata={"memory_pct": mem.percent, "cpu_pct": cpu, "disk_pct": disk.percent},
    )

# 5. NTP 同步状态
def check_ntp_sync() -> HealthStatus:
    from src.utils.exchange_clock import ExchangeClock
    return HealthStatus(
        component="ntp",
        healthy=ExchangeClock.is_synced(),
        message="synced" if ExchangeClock.is_synced() else "NOT SYNCHRONIZED",
        metadata={"offset_ms": ExchangeClock._offset_ms},
    )

# 6. 券商连接
def check_broker_connection(broker: BaseBroker) -> HealthStatus:
    return HealthStatus(
        component="broker",
        healthy=broker.is_connected(),
        message="connected" if broker.is_connected() else "DISCONNECTED",
    )
```

#### 初始化（main.py 或 server.py 启动时）

```python
# main.py 启动时
from src.services.health_check import HealthCheckDaemon
from src.services.notification_diagnostics import send_admin_alert

health_daemon = HealthCheckDaemon(
    on_alert=lambda level, msg: send_admin_alert(level, msg),
    check_interval=30.0,
)
health_daemon.register(lambda: check_listener_alive(listener))
health_daemon.register(lambda: check_data_source_health(fetcher))
health_daemon.register(check_task_queue)
health_daemon.register(check_system_resources)
health_daemon.register(check_ntp_sync)
health_daemon.register(lambda: check_broker_connection(broker))
health_daemon.start()
```

#### 配置项

```ini
# .env 新增
HEALTH_CHECK_ENABLED=true
HEALTH_CHECK_INTERVAL_SECONDS=30
HEALTH_CHECK_ALERT_CONSECUTIVE_FAILURES=3   # 连续 N 次失败才告警
HEALTH_CHECK_MEMORY_WARN_PCT=85
HEALTH_CHECK_CPU_WARN_PCT=90
HEALTH_CHECK_DISK_WARN_PCT=90
HEALTH_CHECK_DATA_SOURCE_FAILURE_RATE_WARN=0.30  # 30% 失败率告警
HEALTH_CHECK_TASK_QUEUE_MAX_PENDING=20
```

---

# 3. P2 — 规模化前提

## 3.1 行情数据质量 Pipeline

### 3.1.1 现状

```
data_provider/base.py:get_realtime_quote / get_daily_data
  └── 多源切换，但无质量校验
  └── 无停牌检测
  └── 无异常价格过滤
  └── 无多源交叉验证
```

### 3.1.2 实现方案

```python
# data_provider/quality.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

@dataclass
class QualityReport:
    code: str
    passed: bool
    checks: List[Dict]                # each check: {name, passed, detail}
    source: str
    timestamp: datetime

class DataQualityPipeline:
    """
    在 DataFetcherManager 返回数据前执行的质量检查流水线。
    对接入口: data_provider/base.py DataFetcherManager.get_realtime_quote / get_daily_data
    """

    def __init__(self):
        self._checks = [
            self._check_not_suspended,        # 停牌检测
            self._check_price_sanity,          # 价格合理性
            self._check_volume_sanity,         # 量合理性
            self._check_timestamp_freshness,   # 时间戳新鲜度
            self._check_no_gaps,               # 日线无缺失
        ]

    def validate_realtime(self, quote: UnifiedRealtimeQuote) -> QualityReport:
        """对实时行情逐字段校验"""
        results = []
        for check in self._checks[:4]:  # 实时行情用前 4 个
            result = check(quote)
            results.append(result)
        passed = all(r["passed"] for r in results)
        return QualityReport(
            code=quote.code, passed=passed, checks=results,
            source=getattr(quote, "fetcher_name", "unknown"),
            timestamp=datetime.now(),
        )

    def validate_daily(self, df: pd.DataFrame, code: str) -> QualityReport:
        """对日线 DataFrame 校验"""
        results = []
        for check in self._checks:
            result = check(df, code)
            results.append(result)
        ...

    @staticmethod
    def _check_not_suspended(quote_or_df, code: str = "") -> Dict:
        """
        停牌检测:
        - 价格连续 N 天不变 (日线)
        - 实时价 = 昨收且量 = 0 (实时)
        - 名字包含 "停牌"
        """
        ...

    @staticmethod
    def _check_price_sanity(quote_or_df) -> Dict:
        """
        价格合理性:
        - 价格 > 0
        - 价格 < 10× 昨收 (防止用港币价标的 A 股)
        - 涨跌幅在合理范围 (A 股 ±10% / 科创 ±20% / 美股无限制)
        """
        ...

    @staticmethod
    def _check_timestamp_freshness(quote) -> Dict:
        """
        时间戳新鲜度:
        - 盘中行情时间戳距今 < 60 秒
        - 盘后行情时间戳是当日
        """
        age = (datetime.now() - quote.timestamp).total_seconds()
        return {
            "name": "timestamp_freshness",
            "passed": age < 60,
            "detail": f"age={age:.1f}s",
        }

    @staticmethod
    def _check_no_gaps(df: pd.DataFrame, code: str) -> Dict:
        """
        日线缺失检测:
        - 日期连续，无跳过 2+ 个交易日
        - 最近一天的数据在 3 个交易日内
        """
        ...


class CrossSourceValidator:
    """多源交叉验证 — 延迟更高，仅用于盘后/非实时场景"""

    def validate(self, code: str, quotes: List[UnifiedRealtimeQuote]) -> QualityReport:
        """
        要求至少 2 个源返回数据:
        - 价格偏差 < 2% → 通过
        - 价格偏差 ≥ 2% → 标记为疑似脏数据，以多数源的价格为准
        """
        if len(quotes) < 2:
            return QualityReport(code=code, passed=False,
                checks=[{"name": "cross_validation", "passed": False, "detail": "only 1 source"}])

        prices = [q.price for q in quotes if q.price > 0]
        if len(prices) < 2:
            return QualityReport(code=code, passed=False,
                checks=[{"name": "cross_validation", "passed": False, "detail": "not enough valid prices"}])

        mean_price = np.mean(prices)
        max_deviation = max(abs(p - mean_price) / mean_price for p in prices)
        return QualityReport(
            code=code,
            passed=max_deviation < 0.02,
            checks=[{"name": "cross_validation", "passed": max_deviation < 0.02,
                     "detail": f"max_deviation={max_deviation:.2%}, sources={[q.source for q in quotes]}"}],
        )
```

#### DataFetcherManager 集成点

```python
# data_provider/base.py DataFetcherManager 改动

class DataFetcherManager:
    def __init__(self, ...):
        # [新增]
        self.quality = DataQualityPipeline()

    def get_realtime_quote(self, stock_code, ...):
        quote = self._get_realtime_quote_raw(...)  # 现有逻辑重命名

        # [新增] 质量检查
        if quote is not None:
            report = self.quality.validate_realtime(quote)
            if not report.passed:
                logger.warning("Quality check failed for %s: %s", stock_code, [
                    c["detail"] for c in report.checks if not c["passed"]
                ])
                # 标记数据来源（用于 prompt 中的 confidence 降级）
                quote.quality_flags = [c for c in report.checks if not c["passed"]]
        return quote
```

#### 配置项

```ini
# .env 新增
DATA_QUALITY_ENABLED=true
DATA_QUALITY_PRICE_SANITY_MAX_CHANGE_PCT=500      # 价格变动 > 500% 视为异常
DATA_QUALITY_TIMESTAMP_MAX_AGE_SECONDS=60          # 盘中行情最大年龄
DATA_QUALITY_CROSS_VALIDATION_ENABLED=false        # 实时场景关闭（太慢）
DATA_QUALITY_CROSS_VALIDATION_MIN_SOURCES=2
DATA_QUALITY_CROSS_VALIDATION_MAX_DEVIATION_PCT=2  # 多源偏差 > 2% → 报警
```

---

## 3.2 行情数据持久化仓库

### 3.2.1 现状

```
market_listener.py:375  _daily_df_cache: Dict[str, Tuple[datetime, pd.DataFrame]]
  └── 内存缓存 5 分钟 (line 309 daily_df_cache_seconds = 300.0)
  └── 没有本地持久化
  └── 每次重启要从远程拉全部日线
```

### 3.2.2 实现方案

```python
# data_provider/local_store.py
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import pandas as pd
import sqlite3
import threading

@dataclass
class StoreConfig:
    db_path: str = "data/market_data.db"
    kline_table: str = "daily_kline"
    max_incremental_days: int = 5       # 增量更新：只拉最近 N 天
    full_refresh_interval_days: int = 30  # 全量刷新：每 30 天重新拉全量
    stale_threshold_hours: int = 24     # 数据过期：超过 24h 未更新就是 stale

class LocalMarketStore:
    """
    本地 SQLite 行情仓库。
    - 所有日线数据落盘
    - 支持增量更新 (INCREMENTAL) 和全量刷新 (FULL_REFRESH)
    - MarketListener 只从缓存读，更新异步进行
    """

    def __init__(self, config: StoreConfig):
        self.config = config
        self._conn = sqlite3.connect(config.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_kline (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL,
                    adjust_factor REAL DEFAULT 1.0,
                    source TEXT,
                    fetched_at TEXT,
                    PRIMARY KEY (code, trade_date)
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_kline_code_date
                ON daily_kline(code, trade_date)
            """)
            self._conn.commit()

    def get(self, code: str, start: date, end: date) -> Optional[pd.DataFrame]:
        """从本地读取日线"""
        query = """
            SELECT trade_date, open, high, low, close, volume, amount, adjust_factor
            FROM daily_kline
            WHERE code = ? AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date ASC
        """
        df = pd.read_sql_query(query, self._conn, params=(code, start.isoformat(), end.isoformat()))
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df.set_index("trade_date", inplace=True)
        return df

    def upsert(self, code: str, df: pd.DataFrame, source: str):
        """增量/全量写入"""
        now = datetime.now().isoformat()
        with self._lock:
            for idx, row in df.iterrows():
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                self._conn.execute("""
                    INSERT OR REPLACE INTO daily_kline
                    (code, trade_date, open, high, low, close, volume, amount, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    code, date_str,
                    float(row.get("open", 0)), float(row.get("high", 0)),
                    float(row.get("low", 0)), float(row.get("close", 0)),
                    float(row.get("volume", 0)), float(row.get("amount", 0)),
                    source, now,
                ))
            self._conn.commit()

    def needs_update(self, code: str) -> bool:
        """检查是否需要更新（最近一条记录超过 stale_threshold）"""
        row = self._conn.execute(
            "SELECT fetched_at FROM daily_kline WHERE code = ? ORDER BY trade_date DESC LIMIT 1",
            (code,)
        ).fetchone()
        if row is None:
            return True
        fetched = datetime.fromisoformat(row[0])
        return (datetime.now() - fetched).total_seconds() / 3600 > self.config.stale_threshold_hours

    def close(self):
        self._conn.close()
```

#### MarketListener 集成

```python
# market_listener.py 改动

class MarketListener:
    def __init__(self, ...):
        # [新增]
        self._local_store: Optional[LocalMarketStore] = None

    def _get_daily_df(self, code: str) -> Optional[pd.DataFrame]:
        """优先从本地存储读，过期才从远程拉"""
        if self._local_store is not None:
            # 检查本地数据新鲜度
            if not self._local_store.needs_update(code):
                # 本地数据够新 → 直接读
                start = date.today() - timedelta(days=365)
                df = self._local_store.get(code, start, date.today())
                if df is not None and len(df) >= 2:
                    return df

        # 去远程拉（现有逻辑）
        df = self._fetch_daily_from_remote(code)

        # [新增] 落盘
        if df is not None and self._local_store is not None:
            self._local_store.upsert(code, df, source="fetcher")

        return df
```

#### 配置项

```ini
# .env 新增
LOCAL_STORE_ENABLED=true
LOCAL_STORE_DB_PATH=data/market_data.db
LOCAL_STORE_STALE_THRESHOLD_HOURS=24
LOCAL_STORE_AUTO_VACUUM_DAYS=7           # 每 7 天自动 VACUUM
```

---

## 3.3 OMS/RMS 生产级分离

### 3.3.1 现状

```
TradingEngine 将 OMS + RMS + 执行 + 结算 耦合在 1671 行单体类中。
```

### 3.3.2 实现方案

```python
# paper_trading/oms.py — Order Management System

class OrderManagementSystem:
    """
    订单生命周期管理 — 从现有 TradingEngine 中拆出。
    职责: 订单创建/路由/状态跟踪/撤单/修改
    不负责: 风控 (交给 RMS), 结算 (交给 Settlement)
    """

    def __init__(self, broker_router: BrokerRouter):
        self.broker_router = broker_router
        self.order_manager = OrderManager()  # 现有

    def submit(self, order_req: OrderRequest, account_id: int) -> TradeResult:
        # 1. 创建本地订单 (OrderManager.create_order)
        order = self.order_manager.create_order(order_req)
        # 2. 路由到券商 (BrokerRouter)
        broker = self.broker_router.resolve(account_id)
        broker_result = broker.submit_order(order_req)
        # 3. 回写 broker_order_id
        ...
        # 4. 返回结果
        return TradeResult(order_id=order.id, broker_order_id=broker_result["broker_order_id"], ...)


# paper_trading/rms.py — Risk Management System

class RiskManagementSystem:
    """
    独立风控 — 从 TradingEngine 拆出。
    职责: 事前风控 (RiskChecker) + 实时风控 (RiskDaemon) + 熔断 (CircuitBreaker)
    """

    def __init__(self):
        self.risk_checker = RiskChecker()         # 现有
        self.circuit_breaker = CircuitBreaker()    # 新增
        self.risk_daemon = RiskDaemon()            # 新增

    def pre_trade_check(self, account_id, code, price, quantity, side) -> RiskDecision:
        decisions = self.risk_checker.check_buy(...) if side == "buy" else self.risk_checker.check_sell(...)
        # 熔断检查
        breaker = self.circuit_breaker.evaluate(...)
        if not breaker.is_normal():
            return RiskDecision(passed=False, reason=f"breaker: {breaker.reason}")
        return self.risk_checker.evaluate(decisions)


# paper_trading/settlement.py — 结算系统

class Settlement:
    """
    日终结算 — 从 TradingEngine.daily_settle 拆出。
    职责: mark-to-market, 手续费计提, 净值曲线计算
    """
    ...
```

#### 重构后的 TradingEngine（薄层）— **把 TradingEngine 降级为编排层**

```
TradingEngine.submit_signal() {
    1. persist signal
    2. RMS.pre_trade_check()        ← 独立风控
    3. AgentRiskReviewer.review()   ← 保持不变
    4. OMS.submit()                 ← 订单管理
    5. Settlement.record_trade()    ← 结算记录
}
```

---

## 3.4 全链路延迟监控

### 3.4.1 实现方案

```python
# src/utils/latency_tracker.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from collections import deque
import time

@dataclass
class SpanEvent:
    """追踪链上的一个时间点"""
    span_name: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)

class LatencySpan:
    """一次完整操作的时间跨度追踪"""

    def __init__(self, operation: str, trace_id: str):
        self.operation = operation
        self.trace_id = trace_id
        self.events: List[SpanEvent] = []
        self.start()

    def start(self):
        self.events.append(SpanEvent(span_name=f"{self.operation}.start"))

    def mark(self, step: str, **metadata):
        self.events.append(SpanEvent(span_name=step, metadata=metadata))

    def finish(self) -> Dict:
        self.events.append(SpanEvent(span_name=f"{self.operation}.end"))
        total_ms = (self.events[-1].timestamp - self.events[0].timestamp).total_seconds() * 1000

        # 计算各阶段耗时
        spans = {}
        for i in range(len(self.events) - 1):
            step_name = self.events[i+1].span_name
            step_ms = (self.events[i+1].timestamp - self.events[i].timestamp).total_seconds() * 1000
            spans[step_name] = round(step_ms, 2)

        return {
            "trace_id": self.trace_id,
            "operation": self.operation,
            "total_ms": round(total_ms, 2),
            "steps": spans,
        }


class LatencyTracker:
    """全局延迟追踪 — 滑动窗口统计"""

    def __init__(self, window_size: int = 1000):
        self._spans: deque = deque(maxlen=window_size)
        self._stats: Dict[str, Dict] = {}  # operation → {p50, p95, p99}

    def record(self, span_result: Dict):
        self._spans.append(span_result)
        self._update_stats(span_result["operation"])

    def get_p95(self, operation: str) -> Optional[float]:
        return self._stats.get(operation, {}).get("p95")

    def report(self) -> List[Dict]:
        return list(self._stats.values())
```

#### 集成到关键路径

```python
# 在 market_listener._tick_market 中打点
def _tick_market(self, market: str):
    span = LatencySpan("tick_market", str(uuid.uuid4()))

    span.mark("fetch_prices_start")
    latest_prices = self._fetch_latest_prices(codes)
    span.mark("fetch_prices_done", codes=len(latest_prices))

    span.mark("match_orders_start")
    matched = self.engine.match_pending_orders(latest_prices)
    span.mark("match_orders_done", matched=len(matched))

    span.mark("evaluate_strategies_start")
    self._evaluate_strategies(codes, latest_prices, market)
    span.mark("evaluate_strategies_done")

    result = span.finish()
    latency_tracker.record(result)

    if result["total_ms"] > 1000:  # tick 耗时 > 1s → 告警
        logger.warning("Slow tick: %s", result)
```

#### 配置项

```ini
LATENCY_TRACKER_ENABLED=true
LATENCY_TRACKER_WINDOW_SIZE=1000
LATENCY_WARN_TICK_MS=1000               # tick 耗时 > 1s 告警
LATENCY_WARN_ORDER_MS=500               # 下单 > 500ms 告警
LATENCY_WARN_DATA_FETCH_MS=2000         # 数据拉取 > 2s 告警
```

---

## 3.5 订单状态机幂等化

### 3.5.1 现状

```
order.py:OrderManager — 使用 DatabaseManager (SQLAlchemy) 但事务边界不明确
fill_order (line 457) — 更新 order.status + position.quantity + account.cash
  └── 三个操作是否在同一事务中？未确认
```

### 3.5.2 实现方案

```python
# paper_trading/order.py OrderManager 改动

# 1. 给 PaperOrder 表加乐观锁字段
"""
ALTER TABLE paper_orders ADD COLUMN version INTEGER DEFAULT 0;
"""

# 2. fill_order 加事务 + 乐观锁

def fill_order(
    self,
    order_id: int,
    fill_price: float,
    fill_quantity: float,
    fill_time: Optional[datetime] = None,
) -> Optional[PaperOrder]:
    """
    [幂等化改造]
    1. 使用 SELECT ... FOR UPDATE (行锁)
    2. 检查 order.version (乐观锁)
    3. 在同一个事务中更新: order + position + account
    4. 如果 version 不匹配，返回 None (调用方重试)
    """
    with self.db.session() as session:
        # 行锁 + 乐观锁
        order = session.query(PaperOrder).filter(
            PaperOrder.id == order_id,
            PaperOrder.version == expected_version,
        ).with_for_update().first()

        if order is None:
            # 版本不匹配 → 可能已被并发 fill
            logger.warning("Order %d version mismatch, skip fill", order_id)
            return None

        if order.status not in (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED):
            # 已经完成/取消 → 幂等跳过
            return order

        # --- 事务内的三步更新 ---
        # 1) 更新订单
        order.filled_quantity += fill_quantity
        order.avg_fill_price = (
            (order.avg_fill_price * (order.filled_quantity - fill_quantity) + fill_price * fill_quantity)
            / order.filled_quantity
        ) if order.filled_quantity > 0 else fill_price

        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        order.version += 1
        order.updated_at = datetime.now()

        # 2) 更新持仓
        position = self._get_or_create_position(session, order.account_id, order.code)
        if order.side == OrderSide.BUY:
            position.quantity += fill_quantity
            position.avg_cost = (
                (position.avg_cost * (position.quantity - fill_quantity) + fill_price * fill_quantity)
                / position.quantity
            ) if position.quantity > 0 else fill_price
            account = session.query(PaperAccount).filter(PaperAccount.id == order.account_id).with_for_update().first()
            account.cash -= fill_price * fill_quantity + fee
        else:  # SELL
            position.quantity -= fill_quantity
            account = session.query(PaperAccount).filter(PaperAccount.id == order.account_id).with_for_update().first()
            account.cash += fill_price * fill_quantity - fee

        # 3) 提交事务
        session.commit()
        return order
```

---

# 4. P3 — 竞争力差异

## 4.1 Level 2 深度行情

### 4.1.1 实现方案

```python
# data_provider/l2_fetcher.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class Level2Quote:
    """十档买卖盘"""
    code: str
    timestamp: datetime
    # 买盘 (从买一到买十)
    bid_prices: List[float] = field(default_factory=list)    # [bid1, bid2, ..., bid10]
    bid_volumes: List[int] = field(default_factory=list)     # [vol1, vol2, ..., vol10]
    # 卖盘 (从卖一到卖十)
    ask_prices: List[float] = field(default_factory=list)
    ask_volumes: List[int] = field(default_factory=list)
    # 衍生指标
    bid_ask_imbalance: float = 0.0     # (总买量 - 总卖量) / (总买量 + 总卖量)
    weighted_bid: float = 0.0           # 加权均价 (买侧)
    weighted_ask: float = 0.0           # 加权均价 (卖侧)
    depth_weighted_spread: float = 0.0  # 深度加权买卖价差

@dataclass
class OrderFlowSignal:
    """基于 L2 的订单流信号"""
    code: str
    large_buy_orders: int      # 大单买入数
    large_sell_orders: int     # 大单卖出数
    net_flow: float            # 净流入
    iceberg_detected: bool     # 检测到冰山订单
    spoofing_detected: bool    # 检测到幌骗 (spoofing)

class L2Fetcher(BaseFetcher):
    """
    L2 数据源适配器 — 继承 BaseFetcher 接口。
    目前仅 tickflow / longbridge 提供部分 L2 数据。
    实现为可选增强：有 L2 时用于增强信号，无 L2 时系统正常运行。
    """

    def get_level2_quote(self, stock_code: str) -> Optional[Level2Quote]:
        ...

    def get_order_flow(self, stock_code: str) -> Optional[OrderFlowSignal]:
        ...
```

#### 配置项

```ini
L2_DATA_ENABLED=false             # 默认关闭，付费功能
L2_DATA_PROVIDER=tickflow
L2_ORDER_FLOW_MIN_THRESHOLD=100   # 大单定义：> 100 手
```

---

## 4.2 信号融合与冲突仲裁

### 4.2.1 实现方案

```python
# paper_trading/signal_fusion.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

class FusionMethod(str, Enum):
    MAJORITY_VOTE = "majority_vote"         # 多数投票
    WEIGHTED_VOTE = "weighted_vote"         # 加权投票 (策略近期 Sharpe 做权重)
    CONFIDENCE_THRESHOLD = "confidence"     # 置信度门槛
    ENSEMBLE = "ensemble"                   # 集成 (各策略独立开仓)

@dataclass
class FusedSignal:
    code: str
    side: str                              # buy / sell / none
    confidence: float                      # 融合后的置信度
    supporting_strategies: List[str]       # 支持该方向的策略
    opposing_strategies: List[str]         # 反对的策略
    weight: float                          # 建议仓位权重 (0-1)
    method: FusionMethod
    details: Dict = field(default_factory=dict)

class SignalFusionEngine:
    """
    接收多个策略对同一股票的信号 → 输出融合后的单一信号。
    """

    def __init__(self, method: FusionMethod = FusionMethod.WEIGHTED_VOTE):
        self.method = method
        self._strategy_weights: Dict[str, float] = {}  # strategy_name → weight

    def update_weights_from_metrics(self, metrics: Dict[str, float]):
        """
        根据策略近期绩效动态更新权重。
        metrics: {strategy_name: sharpe_ratio}
        使用 SoftMax 归一化，Sharpe 越高权重越大。
        """
        ...

    def fuse(self, code: str, signals: List[Signal]) -> Optional[FusedSignal]:
        """
        融合多个策略的信号。
        返回 None 表示无共识 (hold)。
        """
        if not signals:
            return None

        if self.method == FusionMethod.MAJORITY_VOTE:
            return self._majority_vote(code, signals)
        elif self.method == FusionMethod.WEIGHTED_VOTE:
            return self._weighted_vote(code, signals)
        elif self.method == FusionMethod.CONFIDENCE_THRESHOLD:
            return self._confidence_threshold(code, signals)
        else:
            return self._ensemble(code, signals)

    def _weighted_vote(self, code: str, signals: List[Signal]) -> Optional[FusedSignal]:
        buy_weight = 0.0
        sell_weight = 0.0
        buy_strategies = []
        sell_strategies = []

        for s in signals:
            w = self._strategy_weights.get(s.strategy_name, 1.0)
            if s.side == "buy":
                buy_weight += w
                buy_strategies.append(s.strategy_name)
            elif s.side == "sell":
                sell_weight += w
                sell_strategies.append(s.strategy_name)

        total = buy_weight + sell_weight
        if total == 0:
            return None

        # 信号冲突仲裁
        buy_ratio = buy_weight / total
        sell_ratio = sell_weight / total

        # 需要明确优势 (≥60%) 才产生信号，否则 hold
        if buy_ratio >= 0.60:
            return FusedSignal(
                code=code, side="buy",
                confidence=buy_ratio,
                supporting_strategies=buy_strategies,
                opposing_strategies=sell_strategies,
                weight=buy_ratio * 0.5,  # 最大半仓
                method=FusionMethod.WEIGHTED_VOTE,
            )
        elif sell_ratio >= 0.60:
            return FusedSignal(
                code=code, side="sell",
                confidence=sell_ratio,
                supporting_strategies=sell_strategies,
                opposing_strategies=buy_strategies,
                weight=sell_ratio * 0.5,
                method=FusionMethod.WEIGHTED_VOTE,
            )
        else:
            return None  # 无共识，hold
```

#### 集成到 MarketListener._evaluate_strategies

```python
def _evaluate_strategies(self, codes, latest_prices, market):
    fusion = self.signal_fusion_engine   # 新增

    for code in codes:
        # 收集所有策略对该 code 的信号
        all_signals = []
        for strategy in self.strategies:
            signal = self.rule_engine.evaluate(strategy, ...)
            if signal.side in ("buy", "sell"):
                all_signals.append(signal)

        # [新增] 融合信号
        fused = fusion.fuse(code, all_signals)
        if fused is not None and fused.side != "none":
            self.engine.submit_signal(..., signal=fused)
```

#### 配置项

```ini
SIGNAL_FUSION_METHOD=weighted_vote      # majority_vote / weighted_vote / ensemble
SIGNAL_FUSION_CONSENSUS_THRESHOLD=0.60  # 加权共识阈值
```

---

## 4.3 企业事件处理

```python
# data_provider/corporate_actions.py
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional
import json

@dataclass
class CorporateEvent:
    code: str
    event_date: date
    event_type: str            # dividend / split / rights_issue / delist / name_change
    details: Dict              # e.g., {dividend_per_share: 0.5, split_ratio: 2.0}

class CorporateEventCalendar:
    """
    企业事件日历 — 从 akshare/tushare 拉取，本地缓存。
    回测时必须加载，实盘中用于调整持仓数据。
    """

    def __init__(self, cache_path: str = "data/corporate_events.json"):
        self._cache_path = cache_path
        self._events: Dict[str, List[CorporateEvent]] = {}  # code → events
        self._load_cache()

    def update(self, codes: List[str]):
        """拉取代码列表的事件日历"""
        import akshare as ak
        for code in codes:
            try:
                df = ak.stock_dividents_cninfo(symbol=code)
                # 解析分红/拆股事件
                ...
            except Exception:
                continue

    def apply_to_prices(self, code: str, df: pd.DataFrame) -> pd.DataFrame:
        """
        前复权调整: 遍历所有事件，调整历史价格。
        分红: 复权因子 *= (close - dividend) / close
        拆股: 复权因子 *= split_ratio
        """
        events = self._events.get(code, [])
        df = df.copy()
        df["adj_factor"] = 1.0
        for event in sorted(events, key=lambda e: e.event_date, reverse=True):
            mask_before = df.index <= pd.Timestamp(event.event_date)
            if event.event_type == "dividend":
                close_before = df.loc[df.index == pd.Timestamp(event.event_date), "close"].iloc[0]
                factor = (close_before - event.details["dividend_per_share"]) / close_before
                df.loc[mask_before, "adj_factor"] *= factor
            elif event.event_type == "split":
                df.loc[mask_before, "adj_factor"] *= event.details["split_ratio"]
        # 应用复权
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] * df["adj_factor"]
        return df
```

---

## 4.4 特征工程管线

```python
# paper_trading/features/pipeline.py
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional
import pandas as pd
import numpy as np

@dataclass
class FeatureConfig:
    """单特征定义 — 类似 strategy YAML 的声明式定义"""
    name: str
    category: str              # price / volume / momentum / volatility / fundamental / sentiment
    compute_fn: str            # 计算函数名 (注册在 FeatureRegistry 中)
    params: Dict = field(default_factory=dict)
    requires_lookback_days: int = 20

class FeatureRegistry:
    """特征计算函数注册表"""

    _registry: Dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(fn):
            cls._registry[name] = fn
            return fn
        return decorator

# 示例特征
@FeatureRegistry.register("sma_crossover")
def sma_crossover(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    fast_ma = df["close"].rolling(fast).mean()
    slow_ma = df["close"].rolling(slow).mean()
    return ((fast_ma > slow_ma) & (fast_ma.shift(1) <= slow_ma.shift(1))).astype(int)

@FeatureRegistry.register("rsi")
def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

@FeatureRegistry.register("volume_spike")
def volume_spike(df: pd.DataFrame, multiplier: float = 2.0) -> pd.Series:
    avg_vol = df["volume"].rolling(20).mean()
    return (df["volume"] > avg_vol * multiplier).astype(int)

@FeatureRegistry.register("bid_ask_imbalance")
def bid_ask_imbalance(df: pd.DataFrame, l2_data: Dict = None) -> pd.Series:
    """需要 L2 数据"""
    ...


class FeaturePipeline:
    """
    离线特征工程管线。
    使用场景: 每日收盘后运行，为下一个交易日预计算所有特征。
    输出: features/YYYYMMDD.parquet
    """

    def __init__(self, configs: List[FeatureConfig]):
        self.configs = configs
        self.registry = FeatureRegistry._registry

    def run(self, codes: List[str], daily_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        对每个 code 的日线数据计算所有配置的特征。
        返回: MultiIndex DataFrame (code, date) × features
        """
        all_features = []
        for code in codes:
            df = daily_data.get(code)
            if df is None or len(df) < 30:
                continue

            feature_df = pd.DataFrame(index=df.index)
            feature_df["code"] = code

            for cfg in self.configs:
                fn = self.registry.get(cfg.compute_fn)
                if fn:
                    feature_df[cfg.name] = fn(df, **cfg.params)

            all_features.append(feature_df)

        return pd.concat(all_features)

    def save(self, features: pd.DataFrame, as_of: date):
        path = f"data/features/{as_of.strftime('%Y%m%d')}.parquet"
        features.to_parquet(path)
```

#### 配置项

```ini
FEATURE_PIPELINE_ENABLED=true
FEATURE_PIPELINE_SCHEDULE=0 18 * * 1-5   # 工作日 18:00 运行
FEATURE_PIPELINE_OUTPUT_DIR=data/features
```

---

## 4.5 在线学习与模型漂移检测

```python
# paper_trading/drift_detector.py
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
from collections import deque

@dataclass
class DriftReport:
    strategy_name: str
    is_drifting: bool
    rolling_sharpe: List[float]          # 最近 N 日滑动 Sharpe
    sharpe_trend: float                  # Sharpe 趋势 (正 = 改善, 负 = 恶化)
    consecutive_losing_days: int
    recommended_action: str              # "keep" / "reduce_weight" / "pause" / "retire"

class DriftDetector:
    """
    策略漂移检测。
    不依赖 LLM，纯统计方法。
    """

    def __init__(self, window_days: int = 60, min_trades: int = 20):
        self.window_days = window_days
        self.min_trades = min_trades
        self._daily_pnl: Dict[str, deque] = {}   # strategy → rolling daily PnL

    def record_daily_pnl(self, strategy_name: str, daily_pnl: float):
        if strategy_name not in self._daily_pnl:
            self._daily_pnl[strategy_name] = deque(maxlen=self.window_days)
        self._daily_pnl[strategy_name].append(daily_pnl)

    def check(self, strategy_name: str) -> DriftReport:
        pnl = list(self._daily_pnl.get(strategy_name, []))
        if len(pnl) < self.min_trades:
            return DriftReport(strategy_name=strategy_name, is_drifting=False,
                               recommended_action="keep (insufficient data)")

        # 滑动 Sharpe
        rolling_sharpe = self._compute_rolling_sharpe(pnl, window=20)

        # Sharpe 趋势 (线性回归斜率)
        x = np.arange(len(rolling_sharpe))
        sharpe_trend = np.polyfit(x, rolling_sharpe, 1)[0] if len(rolling_sharpe) > 1 else 0

        # 连续亏损天数
        consecutive_losing = 0
        for p in reversed(pnl):
            if p < 0:
                consecutive_losing += 1
            else:
                break

        # 判断漂移
        is_drifting = False
        action = "keep"

        if sharpe_trend < -0.01 and len(rolling_sharpe) >= 30:
            is_drifting = True
            action = "reduce_weight"
        if rolling_sharpe[-1] <= 0.0 and sharpe_trend < -0.02:
            is_drifting = True
            action = "pause"
        if consecutive_losing >= 15:
            is_drifting = True
            action = "retire"

        return DriftReport(
            strategy_name=strategy_name,
            is_drifting=is_drifting,
            rolling_sharpe=rolling_sharpe,
            sharpe_trend=round(float(sharpe_trend), 4),
            consecutive_losing_days=consecutive_losing,
            recommended_action=action,
        )

    @staticmethod
    def _compute_rolling_sharpe(pnl: List[float], window: int) -> List[float]:
        """滑动窗口 Sharpe (年化)"""
        pnl_arr = np.array(pnl)
        result = []
        for i in range(window, len(pnl_arr) + 1):
            window_pnl = pnl_arr[i-window:i]
            mean = np.mean(window_pnl)
            std = np.std(window_pnl)
            sharpe = (mean / std) * np.sqrt(242) if std > 0 else 0.0
            result.append(sharpe)
        return result
```

### 集成到信号融合

```python
# 在 SignalFusionEngine 中

def update_weights_from_drift(self, drift_reports: Dict[str, DriftReport]):
    """
    漂移检测 → 自动降权
    reduce_weight → 权重 × 0.5
    pause → 权重 = 0 (暂停)
    retire → 从活跃策略列表移除
    """
    for name, report in drift_reports.items():
        if report.recommended_action == "reduce_weight":
            self._strategy_weights[name] *= 0.5
        elif report.recommended_action == "pause":
            self._strategy_weights[name] = 0.0
        elif report.recommended_action == "retire":
            self._strategy_weights.pop(name, None)
            # 同时通知人工审查
            ...
```

---

# 5. 其他关键能力

## 5.1 策略生命周期管理

```python
# paper_trading/strategy_lifecycle.py

class StrategyLifecycle:
    """
    策略状态机:
      DRAFT → BACKTEST → PAPER → REVIEW → LIVE → PAUSED → RETIRED
       │                                          │
       └──────────────────────────────────────────┘
                   (任何阶段可回退到 DRAFT)
    """

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.state_machine = {
            "DRAFT": ["BACKTEST"],
            "BACKTEST": ["PAPER", "DRAFT"],
            "PAPER": ["REVIEW", "DRAFT"],
            "REVIEW": ["LIVE", "DRAFT"],
            "LIVE": ["PAUSED", "DRAFT"],
            "PAUSED": ["LIVE", "DRAFT"],
            "RETIRED": ["DRAFT"],  # 退休后只能重新起草
        }

    def transition(self, strategy_name: str, new_state: str, operator: str):
        current = self._get_state(strategy_name)
        if new_state not in self.state_machine.get(current, []):
            raise ValueError(f"Invalid transition: {current} → {new_state}")

        # 记录审批
        self._record_approval(strategy_name, current, new_state, operator)

        # 如果上线 → 分配初始权重 (回测 Sharpe 的 SoftMax)
        if new_state == "LIVE":
            ...

        self._update_state(strategy_name, new_state)
```

---

## 5.2 网络冗余与灾备

| 层面 | 方案 | 具体实现 |
|---|---|---|
| **行情源** | 双路接入 | 主路 WebSocket (tickflow) + 备路 HTTP 轮询 (akshare) — 通过 SharedQuoteCache 自动切换，无需重启 |
| **数据库** | SQLite → WAL + 定期备份 | `PRAGMA journal_mode=WAL`（并发写入安全）；每日 3:00 AM cron 执行 `sqlite3 .backup` 到 `data/backups/` |
| **应用层** | 看门狗 + 自动重启 | `server.py` 最外层被 `systemd` / `supervisor` 包裹；`MarketListener` 线程 `_run_safely` 捕获异常后自动重启（`market_listener.py:420-424` 当前仅 log → 改为重启逻辑） |
| **故障切换** | 双实例热备 | 主实例处理交易 + 备实例只拉行情不交易（`leader_election` 通过数据库中的 `leader_lock` 行实现）。主实例心跳断开 ≥ 10s → 备实例接管 |
| **灾备** | RPO < 1h | 每小时 rsync `data/` 到 NAS（用户已有 fnOS NAS 在 `192.168.88.251`） |

---

## 5.3 AI 推理延迟分离处理

**问题**：AI 分析（LLM ReAct，分钟级）和规则信号（毫秒级）在同一个 tick 中混合执行（`market_listener.py:468-511`）。

**方案**：AI 信号走独立异步管道。

```
                    ┌───────────────────┐
                    │  MarketListener    │
                    │  (规则引擎 tick)   │
                    │  间隔: 0.5~10s    │
                    └─────────┬─────────┘
                              │ rule signals (毫秒级)
                              ▼
                    ┌───────────────────┐
                    │  SignalFusionEngine│
                    │  + TradingEngine   │
                    └───────────────────┘

                    ┌───────────────────┐
                    │  AI Analysis Worker│  ← 独立线程/进程
                    │  (LLM 分析)        │
                    │  间隔: 可配置       │
                    │  (如每小时)         │
                    └─────────┬─────────┘
                              │ AI signals (分钟级)
                              │ push to SignalQueue
                    ┌─────────▼─────────┐
                    │ AIAnalysisSignal  │
                    │ Queue             │  ← 现有 paper_trading_signal_queue.py
                    └─────────┬─────────┘
                              │ listener._consume_ai_signals()
                              ▼
                    ┌───────────────────┐
                    │  TradingEngine     │
                    │  submit_signal()  │
                    └───────────────────┘
```

**MarketListener 改动**：从 tick 循环中移除 AI 分析调用。AI 信号通过独立的 cron 或定时任务产生，写入 `AIAnalysisSignalQueue`，listener 仅消费队列。

```ini
AI_ANALYSIS_SCHEDULE=0 */1 * * *      # 每小时触发一次 AI 分析
AI_ANALYSIS_ASYNC=true                # 独立进程，不阻塞规则引擎
```

---

## 5.4 极端行情应对

```python
# paper_trading/extreme_market.py

class ExtremeMarketDetector:
    """
    检测市场极端状态。
    基于 VIX-like 指标（历史波动率突增）+ 市场宽度异常。
    """

    def __init__(self, volatility_multiplier: float = 3.0):
        self.multiplier = volatility_multiplier

    def detect(self, market: str, index_data: pd.DataFrame) -> Optional[ExtremeMarketAlert]:
        """
        计算最近 20 日波动率 vs 过去 1 年 20 日波动率均值。
        如果当前 > 均值 × multiplier → 触发极端行情警告。
        """
        returns = index_data["close"].pct_change().dropna()
        current_vol = returns.tail(20).std() * np.sqrt(242)
        historical_vol = returns.rolling(20).std().tail(240).mean() * np.sqrt(242)

        if current_vol > historical_vol * self.multiplier:
            return ExtremeMarketAlert(
                market=market,
                current_vol=current_vol,
                historical_vol=historical_vol,
                ratio=current_vol / historical_vol,
                actions=["暂停规则策略", "只执行止损", "禁止市价单开仓"],
            )
        return None


class ExtremeMarketResponse:
    """
    极端行情响应策略:
    1. 暂停所有规则策略的 buy 信号
    2. 禁用市价单 (防止流动性不足滑点)
    3. 仅允许止损单和限价卖单
    4. 提高熔断阈值（极端波动时放宽到日常的 2×）
    5. 将 circuit_breaker 的日亏损阈值临时上调（因为波动大）
    """

    def activate(self, alert: ExtremeMarketAlert):
        logger.critical("EXTREME MARKET: %s", alert)

        # 1) 暂停所有 buy signal
        SignalFusionEngine.force_hold_buy = True

        # 2) 禁用市价单
        TradingEngine.allow_market_orders = False

        # 3) 放宽熔断
        CircuitBreaker.config.soft_threshold_pct *= 2.0
```

#### 配置项

```ini
EXTREME_MARKET_DETECTOR_ENABLED=true
EXTREME_MARKET_VOLATILITY_MULTIPLIER=3.0
EXTREME_MARKET_LOOKBACK_DAYS=252
EXTREME_MARKET_AUTO_RESUME_MINUTES=30   # 极端行情标记 30 分钟后自动重检
```

---

# 6. 实施路线图

```
Phase 0 (当前)
  ├── 规则引擎 + 模拟盘 + 数据源多级 fallback
  └── 单机 HTTP 轮询

Phase 1 — P0 硬前置 (第 1-2 个月)
  ├── 1.1 完整回测框架 (BacktestEngine + Walk-forward)
  ├── 1.2 券商接口适配层 (BrokerRouter + EastMoneyBroker)
  ├── 1.3 时钟同步 (ExchangeClock + NTP)
  └── 5.1 策略生命周期管理

Phase 2 — P1 上线必备 (第 3-4 个月)
  ├── 2.1 WebSocket 行情接入 (SharedQuoteCache + WS→Poll fallback)
  ├── 2.2 熔断机制 (CircuitBreaker 三级)
  ├── 2.3 实时风控守护 (RiskDaemon + VaR + 流动性)
  ├── 2.4 系统健康告警 (HealthCheckDaemon)
  └── 5.4 极端行情应对

Phase 3 — P2 规模化 (第 5-6 个月)
  ├── 3.1 数据质量 Pipeline
  ├── 3.2 行情数据持久化仓库 (LocalMarketStore)
  ├── 3.3 OMS/RMS 分离
  ├── 3.4 全链路延迟监控
  ├── 3.5 订单幂等化
  └── 5.2 网络冗余与灾备

Phase 4 — P3 竞争力 (第 7-12 个月)
  ├── 4.1 Level 2 行情
  ├── 4.2 信号融合与仲裁
  ├── 4.3 企业事件处理
  ├── 4.4 特征工程管线
  ├── 4.5 在线学习与模型漂移
  └── 5.3 AI 推理延迟分离
```

---

*文档生成时间: 2026-08-10 | 基准代码: daily_stock_analysis@ebd9f40 | 作者: Hermes Agent*
