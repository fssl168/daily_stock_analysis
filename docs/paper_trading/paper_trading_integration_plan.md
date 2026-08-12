# 📚 纸面交易深度集成方案与实施计划

> **制定时间**：2026-07-30  
> **基线版本**：commit `0429e94`（main 分支）  
> **目标**：将纸面交易子系统与主分析系统深度绑定，实现信号、风控、数据、通知、Agent、回测七大维度的无缝集成，打造"分析即交易、交易即反馈"的闭环生态。

---

## 一、现状评估

当前纸面交易系统已是一个功能完备的独立子系统，具备完整的事前、事中、事后能力链：

| 模块 | 当前状态 | 与主系统的耦合度 |
|------|---------|-----------------|
| 虚拟账户 | ✅ 支持多账户，初始本金可配 | ⚠️ 独立配置，不与自选股联动 |
| 订单系统 | ✅ 限价/市价单、条件单、批量下单、撤单改单 | ⚠️ 独立 API 端点 |
| 风控前置 | ✅ 资金/持仓检查、集中度、单日亏损 | ⚠️ 参数分散在 config 中，未与 portfolio_risk_* 对齐 |
| 智能止损止盈 | ✅ ATR+Fib+筹码峰三位一体 | ⚠️ 独立 SLTP 计算器，策略引擎不共用指标库 |
| 策略规则引擎 | ✅ 10+ 技术指标，多时间框架，模板策略 | ⚠️ 主分析使用不同策略框架 |
| AI基金经理Agent | ✅ 自主决策工具链，二次确认 | ⚠️ 仅用于纸面场景，不共享给主分析 |
| 复盘反思系统 | ✅ 交易日志持久化，影响后续决策 | ⚠️ 与主分析的历史报告分离 |
| 次日作战卡 | ✅ 三情景预案 + 候选标的 + SLTP三线 | ⚠️ 独立于大盘复盘输出 |
| 通知集成 | ✅ 飞书/DingTalk推送，日报生成 | ⚠️ 独立 webhook，复用度低 |
| 绩效分析 | ✅ 夏普、回撤、胜率等指标 | ⚠️ 未关联回测基准比较 |
| WebUI全景页面 | ✅ 持仓/订单/信号/决策/复盘视图 | ⚠️ 孤立页面，不与分析结果页互通 |

**核心差距**：纸面交易与主分析是两条并行的"管道"，缺乏双向流动的交互机制。

---

## 二、集成维度详解

### 🔴 P0：自选股联动机制

**痛点**：用户需要在 `.env` 中分别设置 `STOCK_LIST`（自选股）和 `PAPER_TRADING_WATCHED_CODES`（纸面观察池），两份列表容易不一致，维护成本高。

#### 实施方案

##### 2.1 新增配置项

```python
# src/config.py - Config class 中添加
paper_trading_sync_stock_list: bool = True  # true 时自动使用 STOCK_LIST 作为 paper_trading 观察池
```

##### 2.2 创建统一获取函数

```python
# paper_trading/__init__.py
from src.config import get_config
from src.services.stock_list_parser import split_stock_list

def get_watched_codes(account_id: int = 0) -> List[str]:
    """获取纸面交易关注的股票代码，优先从 config 联动，其次从 env，最后默认空列表."""
    cfg = get_config()
    
    # 1. 如果启用同步且自有自选股，直接使用
    if cfg.paper_trading_sync_stock_list and cfg.stock_list:
        return [c.upper().strip() for c in cfg.stock_list if c.strip()]
    
    # 2. 否则使用显式配置的 watched_codes
    if cfg.paper_trading_watched_codes:
        return [c.upper().strip() for c in cfg.paper_trading_watched_codes]
    
    # 3. 空兜底
    return []
```

##### 2.3 MarketListener 适配

```python
# paper_trading/market_listener.py - 修改 __init__
def __init__(self, ...):
    # ...原有初始化...
    self.watched_codes = (
        get_watched_codes(account_id) 
        if not self.config.watched_codes 
        else self.config.watched_codes
    )
```

##### 2.4 测试用例

```python
# tests/test_paper_trading_sync.py
def test_sync_with_stock_list(monkeypatch):
    """验证 paper_trading_sync_stock_list=True 时使用 STOCK_LIST."""
    def mock_get_config():
        cfg = MagicMock()
        cfg.paper_trading_sync_stock_list = True
        cfg.stock_list = ["600519", "300750"]
        cfg.paper_trading_watched_codes = []
        return cfg
    
    monkeypatch.setattr("src.config.get_config", mock_get_config)
    from paper_trading import get_watched_codes
    assert get_watched_codes() == ["600519", "300750"]

def test_sync_disabled_uses_explicit_codes(monkeypatch):
    """验证关闭同步后使用显式 watched_codes."""
    def mock_get_config():
        cfg = MagicMock()
        cfg.paper_trading_sync_stock_list = False
        cfg.stock_list = ["600519"]
        cfg.paper_trading_watched_codes = ["000001", "000002"]
        return cfg
    
    monkeypatch.setattr("src.config.get_config", mock_get_config)
    from paper_trading import get_watched_codes
    assert get_watched_codes() == ["000001", "000002"]
```

**文件变更**：`src/config.py`, `paper_trading/__init__.py`, `paper_trading/market_listener.py`, 新增 `tests/test_paper_trading_sync.py`

**预估工时**：0.5 天

---

### 🟠 P1：AI 分析报告 → 纸面交易信号源共享

**痛点**：AI 分析给出的买卖建议需要用户手动复制到纸面交易界面，效率低且容易遗漏；策略规则引擎与 AI 信号源割裂。

#### 架构设计

```
┌─────────────┐      ┌──────────────┐      ┌─────────────┐      ┌────────────┐
│   AI 分析    │───▶│  Signal Queue │───▶│ PaperTrading│───▶│ TradingEngine│
│   (main.py)  │      │ (in-memory)  │      │   Listener  │      │   Engine     │
└─────────────┘      └──────────────┘      └─────────────┘      └────────────┘
         │                                                   ▲
         │                                               风险审核 + PM Agent
         ▼                                                   │
   分析报告                                                  │
                                                         订单执行
```

##### 2.5 新增信号队列模块 `src/paper_trading_signal_queue.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from queue import Queue, Full, Empty
from threading import Lock, RLock
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

@dataclass
class AIAnalysisSignal:
    """AI 分析产生的交易信号模型."""
    code: str
    side: str  # "buy" or "sell"
    name: str
    trigger_price: float
    suggested_quantity: Optional[float] = None
    reason: str = ""
    strategy_name: str = "ai_analysis_signal"
    confidence: float = 1.0  # AI 置信度 (0-1)
    timestamp: datetime = field(default_factory=datetime.now)

class AIAnalysisSignalQueue:
    """线程安全的内存信号队列，供 main.py 的分析结果推送给 paper_trading listener.
    
    特点：
    - 有界队列（maxsize=1000），满时丢弃旧条目（drop oldest first）
    - pop_all() 一次性消费所有待处理信号，避免单次循环开销
    - thread-safe，适合多线程并发写入/读取
    """
    
    def __init__(self, maxsize: int = 1000):
        self._queue: Queue[AIAnalysisSignal] = Queue(maxsize=maxsize)
        self._lock = RLock()
        self._stopped = False
    
    def push(self, signal: AIAnalysisSignal) -> bool:
        """
        推送一个信号到队列。
        
        Returns:
            True if pushed successfully, False if dropped due to full queue.
        """
        try:
            self._queue.put_nowait(signal)
            return True
        except Full:
            with self._lock:
                logger.warning("AI analysis signal queue full (max=%d), dropping oldest", self._queue.maxsize)
            # Drop one old item to make room for new one
            try:
                self._queue.get_nowait()
                # Try again after removing old item
                self._queue.put_nowait(signal)
                return True
            except Exception:
                # Queue was empty, still can't put
                return False
    
    def pop_all(self) -> List[AIAnalysisSignal]:
        """一次性拉取所有待处理信号，原子操作."""
        signals = []
        while not self._queue.empty():
            try:
                signals.append(self._queue.get_nowait())
            except Empty:
                break
        return signals
    
    def empty(self) -> bool:
        return self._queue.empty()
    
    def size(self) -> int:
        with self._lock:
            return self._queue.qsize()
    
    def close(self):
        """关闭队列，阻止新入队."""
        with self._lock:
            self._stopped = True
```

##### 2.6 修改分析流程推送信号

```python
# analyzer.py - 在 analyze_stock 方法中添加
from src.paper_trading_signal_queue import AIAnalysisSignal, init_signal_queue
from src.config import get_config

def analyze_stock(self, stock_code: str, ...) -> AnalyzeResult:
    # ...原有分析逻辑...
    
    result = self._do_analysis(...)
    
    # 如果启用了纸面交易且 AI 给出了明确交易建议，推送信号
    cfg = get_config()
    if cfg.paper_trading_enabled and cfg.paper_trading_enable_ai_signal_source:
        if result.ai_decision and result.ai_decision.is_clear_trade_signal():
            # 只推送置信度超过阈值的信号
            if result.ai_decision.confidence >= cfg.paper_trading_ai_signal_min_confidence:
                signal = AIAnalysisSignal(
                    code=stock_code,
                    side=result.ai_decision.side,  # "buy"/"sell"
                    name=stock_name,
                    trigger_price=current_price,
                    suggested_quantity=result.ai_decision.suggested_qty,
                    reason=f"AI分析: {result.ai_decision.reason}",
                    strategy_name="ai_analysis_signal",
                    confidence=result.ai_decision.confidence
                )
                init_signal_queue().push(signal)
    
    return result
```

需要先在 `main.py` 或全局初始化中调用 `init_signal_queue()`。

##### 2.7 MarketListener 消费两类信号源

```python
# paper_trading/market_listener.py - 修改 _collect_signals
def _collect_signals(self, latest_prices: Dict[str, float]) -> List[Signal]:
    signals = []
    
    # 传统策略规则信号（已有代码）
    signals.extend(self._evaluate_strategy_rules(latest_prices))
    
    # AI分析信号（新功能）
    from src.paper_trading_signal_queue import AIAnalysisSignal, init_signal_queue as get_signal_queue
    signal_q = get_signal_queue()
    if signal_q and not signal_q.empty():
        for ai_signal in signal_q.pop_all():
            # 过滤掉置信度过低的信号
            cfg = get_config()
            if ai_signal.confidence < cfg.paper_trading_ai_signal_min_confidence:
                continue
                
            # 转换为内部 Signal 对象
            signal = Signal(
                side=ai_signal.side,
                code=ai_signal.code,
                name=ai_signal.name,
                strategy_name=ai_signal.strategy_name,
                rule_name="ai_analysis_signal",
                trigger_price=ai_signal.trigger_price,
                suggested_quantity=ai_signal.suggested_quantity,
                reason=f"AI分析置信度={ai_signal.confidence:.2f}: {ai_signal.reason}",
            )
            signals.append(signal)
    
    return signals
```

##### 2.8 配置项增强

```python
# src/config.py - Config class 中添加（paper_trading section 下方）
paper_trading_enable_ai_signal_source: bool = True          # 是否将 AI 分析结果推送为信号
paper_trading_ai_signal_min_confidence: float = 0.7          # AI 最低置信度才推送信号
paper_trading_ai_signal_cooldown_seconds: float = 30.0       # 同一股票的最小冷却时间
```

**文件变更**：新增 `src/paper_trading_signal_queue.py`，修改 `analyzer.py`/`main.py`，修改 `paper_trading/market_listener.py`，`src/config.py`

**预估工时**：2-3 天

---

### 🟠 P1：风控策略参数对齐

**痛点**：纸面交易有自己的 RiskConfig（max_pct_per_stock=0.3, max_open_positions=8 等），而主系统在 portfolio_risk_alerts 中有另一套相似但不同的配置，用户需要在两个地方维护相同的风控阈值。

#### 实施方案

##### 2.9 创建配置适配层 `paper_trading/risk_config_adapter.py`

```python
# paper_trading/risk_config_adapter.py
from typing import Optional
from src.config import get_config
from paper_trading.risk import RiskConfig
from paper_trading.performance import PerformanceConfig
import logging

logger = logging.getLogger(__name__)

def create_risk_config_from_main() -> RiskConfig:
    """根据主系统 config 创建纸面交易 RiskConfig，实现参数对齐.
    
    映射关系：
    - portfolio_risk_concentration_alert_pct → max_pct_per_stock (实际使用时折半留余地)
    - portfolio_max_open_positions → max_open_positions (如存在，否则用默认值 8)
    - portfolio_risk_max_cash_per_buy_pct → max_pct_cash_per_buy
    - paper_trading_max_daily_loss_pct → max_daily_loss_pct (直接保留)
    """
    cfg = get_config()
    
    # concentration: 主系统 alert 用 35%，纸面交易限制更保守取 30%
    concentration_limit = min(cfg.portfolio_risk_concentration_alert_pct / 100.0, 0.30)
    
    # max positions: 优先用 portfolio_max_open_positions，不存在则用默认值 8
    max_pos = getattr(cfg, 'portfolio_max_open_positions', None)
    if max_pos is None or max_pos <= 0:
        max_pos = 8
    
    # cash per buy: 使用 portfolio_risk_max_cash_per_buy_pct，默认 50%
    cash_pct = getattr(cfg, 'portfolio_risk_max_cash_per_buy_pct', None)
    if cash_pct is None or cash_pct <= 0:
        cash_pct = 0.50
    else:
        cash_pct = min(cash_pct / 100.0, 0.50)  # 上限 50%
    
    daily_loss = cfg.paper_trading_max_daily_loss_pct
    
    return RiskConfig(
        max_pct_per_stock=concentration_limit,
        max_open_positions=int(max_pos),
        max_pct_cash_per_buy=cash_pct,
        max_daily_loss_pct=daily_loss,
    )

def create_performance_config_from_main() -> PerformanceConfig:
    """创建 PerformanceConfig，可选从主系统获取 risk_free_rate."""
    cfg = get_config()
    rfr = getattr(cfg, 'paper_trading_risk_free_rate', None)
    if rfr is None or rfr < 0:
        rfr = 0.03  # 默认 3%
    return PerformanceConfig(risk_free_rate_annual=rfr)
```

##### 2.10 修改 trading_engine 初始化使用适配器

```python
# paper_trading/trading_engine.py - 修改 build_default_trading_engine

def build_default_trading_engine(
    db_manager=None,
    account_manager=None,
    order_manager=None,
    position_manager=None,
    fee_model=None,
    sltp_calculator=None,
    enable_auto_sltp: bool = True,
    agent_reviewer=None,
) -> TradingEngine:
    """构建带默认配置的 TradingEngine，使用来自主系统的风险配置."""
    from .risk_config_adapter import create_risk_config_from_main, create_performance_config_from_main
    
    # 关键变化：使用 adapter 创建的 config，而不是硬编码的 RiskConfig()
    risk_cfg = create_risk_config_from_main()
    
    risk_checker = RiskChecker(
        db_manager=db_manager,
        account_manager=account_manager,
        position_manager=position_manager,
        fee_model=fee_model,
        config=risk_cfg,  # ← 传入适配后的 config
    )
    
    # ...其余初始化保持不变...
```

##### 2.11 配置项迁移脚本（向后兼容）

```python
# scripts/migrate_paper_risk_config.py
"""向后兼容脚本：当用户设置了 paper_trading_max_daily_loss_pct 但未设置
portfolio_risk_* 相关配置时，自动用前者填充后者，并发出 DeprecationWarning."""

import warnings
from src.config import get_config

def migrate_risk_config_if_needed():
    cfg = get_config()
    
    # 检测是否需要迁移
    needs_migration = False
    if hasattr(cfg, 'paper_trading_max_daily_loss_pct') and cfg.paper_trading_max_daily_loss_pct > 0:
        # 检查是否有显式设置的 portfolio_risk 配置
        if not hasattr(cfg, 'portfolio_risk_max_daily_loss_pct') or cfg.portfolio_risk_max_daily_loss_pct <= 0:
            needs_migration = True
    
    if needs_migration:
        warnings.warn(
            "Deprecated: paper_trading_max_daily_loss_pct is being used as the source of truth for daily loss limit. "
            "Please set portfolio_risk_max_daily_loss_pct instead; this warning will be removed in v2.0.",
            DeprecationWarning,
            stacklevel=2
        )
```

**文件变更**：新增 `paper_trading/risk_config_adapter.py`，修改 `trading_engine.py`，`src/config.py` 新增 portfolio_risk 相关字段

**预估工时**：1 天

---

### 🟡 P2：行情数据源统一降级

**痛点**：主系统有复杂的多数据源优先级+降级链（tickflow > akshare > tushare > yfinance），而 paper_trading 的 listener 硬编码使用单一数据源或简单 fallback，导致行情不一致、失败率高。

#### 实施方案

##### 2.12 抽取公共数据获取工具 `src/data_fetcher.py`

```python
# src/data_fetcher.py
from typing import Optional, List, Tuple, Callable
import pandas as pd
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

class MultiSourceDataFetcher:
    """统一的多数据源行情获取器，支持按优先级列表自动降级.
    
    使用示例:
        fetcher = MultiSourceDataFetcher(source_priority=["tickflow", "tushare", "yfinance"])
        df = fetcher.get_daily_historical("600519", days=120)
    """
    
    DEFAULT_PRIORITY = ["tickflow", "tushare", "yfinance", "akshare"]
    
    def __init__(self, source_priority: Optional[List[str]] = None):
        self.source_priority = source_priority or self.DEFAULT_PRIORITY
        self._sources_cache = {}  # 懒加载各数据源适配器
        self._fetch_cache = {}    # 结果缓存，避免重复请求同一数据源
    
    @lru_cache(maxsize=1000)
    def _get_source_adapter(self, source_name: str) -> Optional[Any]:
        """获取指定名称的数据源适配器实例，单例缓存."""
        if source_name in self._sources_cache:
            return self._sources_cache[source_name]
        
        try:
            if source_name == "tickflow":
                from data_provider.tickflow_fetcher import TickFlowFetcher
                self._sources_cache[source_name] = TickFlowFetcher()
            elif source_name == "tushare":
                from data_provider.tushare_fetcher import TushareFetcher
                self._sources_cache[source_name] = TushareFetcher()
            elif source_name == "yfinance":
                from data_provider.yfinance_adapter import YFinanceFetcher
                self._sources_cache[source_name] = YFinanceFetcher()
            elif source_name == "akshare":
                from data_provider.akshare_fetcher import AkShareFetcher
                self._sources_cache[source_name] = AkShareFetcher()
            else:
                logger.warning("Unknown data source: %s", source_name)
                return None
            
            return self._sources_cache[source_name]
        except Exception as e:
            logger.debug("Failed to instantiate data source '%s': %s", source_name, e)
            self._sources_cache[source_name] = None  # 标记失败，不再重试
            return None
    
    def get_daily_historical(self, code: str, days: int = 120) -> Optional[pd.DataFrame]:
        """按优先级尝试多个数据源获取日 K 线数据，返回第一个成功的 DataFrame."""
        for source_name in self.source_priority:
            adapter = self._get_source_adapter(source_name)
            if adapter is None:
                continue
            
            try:
                df = adapter.get_daily_historical(code, days)
                if df is not None and not df.empty and len(df) >= min(10, days):
                    logger.info("Daily historical data for %s fetched from %s", code, source_name)
                    return df
            except Exception as e:
                logger.debug("Source '%s' failed for %s (days=%d): %s", source_name, code, days, e)
                continue
        
        logger.warning("All data sources failed for %s (days=%d)", code, days)
        return None
    
    def get_realtime_quote(self, code: str) -> Optional[Dict]:
        """获取实时行情报价，同样支持优先级降级."""
        for source_name in self.source_priority:
            adapter = self._get_source_adapter(source_name)
            if adapter is None:
                continue
            
            try:
                quote = adapter.get_realtime_quote(code)
                if quote is not None and 'price' in quote:
                    logger.info("Realtime quote for %s from %s", code, source_name)
                    return quote
            except Exception as e:
                logger.debug("Source '%s' realtime failed for %s: %s", source_name, code, e)
                continue
        
        return None
    
    def get_kline_with_source(self, code: str, period: str = "1d") -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """返回获取到的数据框和实际使用的数据源名称，便于调试."""
        for source_name in self.source_priority:
            adapter = self._get_source_adapter(source_name)
            if adapter is None:
                continue
            
            try:
                df = adapter.get_kline(code, period)
                if df is not None and not df.empty:
                    return df, source_name
            except Exception as e:
                logger.debug("Source '%s' kline failed for %s: %s", source_name, code, e)
                continue
        return None, None
```

##### 2.13 修改各数据源适配器的统一接口

需要确保各个数据源类（TickFlowFetcher、TushareFetcher 等）提供以下方法：
- `get_daily_historical(code: str, days: int) -> Optional[pd.DataFrame]`
- `get_realtime_quote(code: str) -> Optional[Dict]` （包含 'code', 'price', 'volume' 等字段）

##### 2.14 MarketListener 替换为 MultiSourceDataFetcher

```python
# paper_trading/market_listener.py - 修改 __init__ 和数据获取方法

def __init__(self, ...):
    from src.data_fetcher import MultiSourceDataFetcher
    from src.config import get_config
    
    cfg = get_config()
    priority_list = [p.strip() for p in cfg.realtime_source_priority.split(',') if p.strip()]
    self.data_fetcher = MultiSourceDataFetcher(source_priority=priority_list)

def _fetch_daily_df(self, code: str, days: int = 120) -> Optional[pd.DataFrame]:
    """使用统一的 MultiSourceDataFetcher 获取日线数据."""
    df = self.data_fetcher.get_daily_historical(code, days)
    if df is None or df.empty:
        return None
    
    if 'date' in df.columns:
        df = df.set_index('date')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df

def _get_latest_price(self, code: str) -> Optional[float]:
    """从 MultiSourceDataFetcher 获取最新价格."""
    quote = self.data_fetcher.get_realtime_quote(code)
    if quote and 'price' in quote:
        return float(quote['price'])
    return None
```

##### 2.15 增加缓存与防降级风暴

```python
def __init__(self, source_priority=None, cache_ttl: int = 60):
    self.cache_ttl = cache_ttl
    self._cache = {}
    self._cache_timestamps = {}
    
    def _is_cache_valid(self, key: str) -> bool:
        now = time.time()
        return key in self._cache and (now - self._cache_timestamps[key]) < self.cache_ttl
    
    def _set_cache(self, key: Any, value: Any):
        self._cache[key] = value
        self._cache_timestamps[key] = time.time()
```

**文件变更**：新增 `src/data_fetcher.py`，修改 `paper_trading/market_listener.py`，`src/config.py`

**预估工时**：1 天

---

### 🟡 P2：通知渠道统一

**痛点**：纸面交易日报、作战卡、复盘消息使用独立的钉钉/飞书 webhook URL，与主系统配置不集中，管理分散。

#### 实施方案

##### 2.16 重写 PaperTradingNotifier 使用通用 NotificationService

```python
# paper_trading/notification_integration.py - 修改版
from typing import Optional
from src.notification import NotificationService, NotificationSeverity, ChannelType
from src.config import get_config

class PaperTradingNotifier:
    """使用主系统统一的 NotificationService 进行通知推送."""
    
    def __init__(self, notification_svc: Optional[NotificationService] = None):
        self.notification_svc = notification_svc or NotificationService.getInstance()
        self._cfg = get_config()
    
    def push_battle_plan(self, markdown: str, account_id: int) -> PushResult:
        title = "📋 次日作战卡"
        return self._push_to_channels(markdown, title, NotificationSeverity.INFO, account_id)
    
    def push_reflection(self, markdown: str, trade_id: int, account_id: int) -> PushResult:
        title = "💭 交易复盘"
        return self._push_to_channels(markdown, title, NotificationSeverity.WARNING, account_id)
    
    def push_daily_report(self, markdown: str, date_str: str, account_id: int) -> PushResult:
        title = f"📊 纸面交易日报 ({date_str})"
        return self._push_to_channels(markdown, title, NotificationSeverity.INFO, account_id)
    
    def _push_to_channels(self, content: str, title: str, severity: NotificationSeverity, account_id: int) -> PushResult:
        result = PushResult(success=False, sent_channels=[], error="")
        
        try:
            message = f"{title}\n\n{content}"
            
            channel_names = []
            if hasattr(self._cfg, 'paper_trading_notification_channels') and self._cfg.paper_trading_notification_channels:
                channel_names = [c.strip() for c in self._cfg.paper_trading_notification_channels.split(',')]
            else:
                channel_names = self._get_active_notification_channels()
            
            self.notification_svc.send(
                recipient="paper_trading",
                title=title,
                content=message,
                severity=severity,
                channels=channel_names,
                extra={"account_id": account_id},
            )
            
            result.success = True
            result.sent_channels = channel_names
        except Exception as e:
            result.error = str(e)
        
        return result
    
    def _get_active_notification_channels(self) -> List[str]:
        channels = []
        cfg = self._cfg
        if getattr(cfg, 'wechat_webhook_url', None): channels.append("wechat")
        if getattr(cfg, 'feishu_webhook_url', None): channels.append("feishu")
        if getattr(cfg, 'telegram_bot_token', None) and getattr(cfg, 'telegram_chat_id', None): channels.append("telegram")
        if getattr(cfg, 'email_sender', None) and getattr(cfg, 'email_password', None): channels.append("email")
        if getattr(cfg, 'dingtalk_webhook_url', None): channels.append("dingtalk")
        return channels
```

##### 2.17 弃用旧的 paper_trading_ 前缀 webhook 配置

在 `.env.example` 中注释掉 `paper_trading_lark_webhook_url`、`paper_trading_dingtalk_webhook_url` 等旧配置项。

**文件变更**：修改 `paper_trading/notification_integration.py`，`.env.example` 更新

**预估工时**：0.5-1 天

---

### 🟢 P3：AI Agent 角色复用与增强

**痛点**：PM Agent 仅服务于纸面交易场景，常规分析报告没有用到相同的 Agent 决策能力；纸面交易的复盘记录无法反过来优化 Agent 的策略偏好。

#### 实施方案

##### 2.19 增强 PM Agent 上下文——注入历史绩效

```python
# src/portfolio_manager_agent.py - 扩展 make_decision 方法

def make_decision(self, account_id: int, extra_context: Dict) -> PMDecision:
    perf_analyzer = PerformanceAnalyzer()
    try:
        metrics = perf_analyzer.calculate(account_id)
        perf_summary = {
            "total_return_pct": round(metrics.total_return_pct, 2),
            "annualized_return_pct": round(metrics.annualized_return_pct, 2) if metrics.annualized_return_pct else None,
            "sharpe_ratio": round(metrics.sharpe_ratio, 2) if metrics.sharpe_ratio else None,
            "max_drawdown_pct": round(metrics.max_drawdown_pct, 2),
            "win_rate": round(metrics.win_rate, 2),
            "trade_count": metrics.trade_count,
        }
    except Exception as e:
        perf_summary = {"error": str(e)}
    
    enhanced_context = extra_context.copy()
    enhanced_context.setdefault("paper_trading_analysis", {})["performance_summary"] = perf_summary
    
    return self._call_agent_with_decisions(enhanced_context)
```

##### 2.20 在 AI 分析 Prompt 中嵌入 PM 建议格式

```python
# src/report_generator.py - 构建个股分析 Prompt 时

pm_suggestion = ""
if cfg.paper_trading_enable_pm_agent:
    pm_dec = get_cached_pm_decision(stock_info['code'])
    if pm_dec and pm_dec.action in ('buy', 'sell'):
        pm_suggestion = f"""
【AI基金经理建议】强烈 {pm_dec.action.upper()} {stock_info['name']}({stock_info['code']})
- 建议仓位: {pm_dec.params.get('position_ratio', 'unknown'):.1%}
- 止损位: {pm_dec.params.get('stop_loss', 'N/A'):.2f}
...
"""
else:
    pm_suggestion = "[AI基金经理未配置]"

prompt = f"...{pm_suggestion}..."
```

##### 2.21 WebUI 新增"PM 决策分析"面板

在分析历史页面中增加筛选器，显示每条分析报告是否触发了 PM 决策，以及决策结果与原分析的对比。

**文件变更**：`portfolio_manager_agent.py`，`report_generator.py`，WebUI 改造

**预估工时**：1-2 天

---

### 🟢 P3：回测-实盘一体化闭环

**痛点**：纸面交易的真实行为数据没有被用于验证或增强回测引擎。

#### 实施方案

##### 2.23 新增适配器 `paper_trading/backtest_adapter.py`

```python
# paper_trading/backtest_adapter.py
class PaperTradingToBacktestAdapter:
    def __init__(self, account_id: int):
        self.account_id = account_id
    
    def generate_backtest_scenario(self, strategy_name: str, base_date: date = None) -> BacktestInput:
        # 收集 trades, positions, net value history
        # 构造 BacktestInput
        pass
    
    def evaluate_strategy_vs_paper(self, backtest_result: BacktestResult, strategy_name: str) -> Dict[str, Any]:
        # 比较回测结果与纸面表现，生成差异分析报告
        pass

def update_paper_trading_from_backtest(backtest_result: BacktestResult, strategy_name: str, account_id: int) -> None:
    from paper_trading.reflection import ReflectionEngine, ReflectionNote
    reflection = ReflectionEngine().reflect_on_trade(
        subject=f"策略回测评估: {strategy_name}",
        summary=f"回测总收益: {backtest_result.total_return_pct:.2f}%, 夏普: {backtest_result.sharpe_ratio:.2f}...",
        lessons=[...],
        tags="backtest,performance-analysis"
    )
    reflection.save(account_id=account_id)
```

##### 2.24 修改 backtest_engine 支持"实盘验证模式"

```python
def run_with_paper_validation(self, strategy, test_period, paper_account_id: int = 1, backtest_only: bool = False) -> Dict[str, Any]:
    backtest_input = self._build_backtest_input(strategy, test_period)
    backtest_result = self._execute_backtest(backtest_input)
    
    if backtest_only:
        return {'backtest': backtest_result, 'paper_comparisons': None}
    
    adapter = PaperTradingToBacktestAdapter(paper_account_id)
    scenario = adapter.generate_backtest_scenario(...)
    paper_comparison = adapter.evaluate_strategy_vs_backtest(backtest_result, scenario)
    
    return {'backtest': backtest_result, 'paper_scenario': scenario.to_dict(), 'paper_comparison': paper_comparison}
```

##### 2.25 WebUI 新增"回测-实盘对比看板"页面

**文件变更**：新增 `paper_trading/backtest_adapter.py`，修改 `backtest_engine.py`，WebUI 新增页面

**预估工时**：3-5 天

---

## 三、实施路线图

| 阶段 | 优先级 | 集成方向 | 预估工时 | 依赖 |
|------|--------|----------|----------|------|
| Phase 1 | 🔴 P0 | 自选股同步 | 0.5 天 | 无 |
| Phase 2 | 🟠 P1 | AI信号共享 + 风险对齐 | 3-4 天 | Phase 1 |
| Phase 3 | 🟡 P2 | 数据源统一 + 通知统一 | 1.5-2 天 | Phase 1-2 |
| Phase 4 | 🟢 P3 | Agent 复用 + 回测闭环 | 4-7 天 | Phase 2-3 |

---

## 四、验收标准

1. **自选股同步**：设置 `paper_trading_sync_stock_list=true` + `STOCK_LIST` 后，MarketListener 自动关注这些股票。
2. **AI 信号共享**：AI 分析给出置信度≥0.7 的买卖建议时，信号被推送到纸面交易队列并被 listener 捕获。
3. **风控对齐**：修改 `portfolio_risk_concentration_alert_pct=40` 后，纸面交易单股限制自动更新为 30%。
4. **数据源统一**：tickflow 不可用时自动降级到 tushare/yfinance，日志正确记录。
5. **通知统一**：删除 `paper_trading_lark_webhook_url` 后，仍可通过 `FEISHU_WEBHOOK_URL` 收到推送。
6. **Agent 复用**：启用 PM Agent 后，个股分析报告显示"【AI基金经理建议】"区块。
7. **回测闭环**：回测-实盘对比页面可查看曲线对比和差异分析报告。

---

## 五、附录：关键文件清单

- `src/paper_trading_signal_queue.py` — AI 信号队列（新增）
- `paper_trading/risk_config_adapter.py` — 风控参数适配（新增）
- `src/data_fetcher.py` — 多数据源获取器（新增）
- `paper_trading/notification_integration.py` — 统一通知推送（修改）
- `paper_trading/backtest_adapter.py` — 回测-实盘转换（新增）
