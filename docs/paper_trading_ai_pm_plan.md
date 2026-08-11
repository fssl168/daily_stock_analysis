# AI 基金经理 Agent 二次开发规划方案

> **文档版本**: v1.0
> **创建日期**: 2026-07-26
> **目标**: 在现有 paper_trading 模块基础上，二次开发实现"AI 自主决策下单 + 撤单改单 + 复盘反思 + 策略迭代"完整闭环，达到并超越公众号参考文章的功能。
> **参考文章**: `https://mp.weixin.qq.com/s/8pw40M5bOM2mvfjsRXC4ig`

---

## 一、参考文章功能与业务管线总结

### 1.1 文章核心场景

原文展示了一个 AI 自主交易的最小闭环：
1. 给 AI 1000 美元模拟资金
2. AI 用现有指标和策略制定计划并下单（限价单 $4008）
3. **AI 主动撤单**（反思后认为价位不合理）
4. **AI 进行复盘反思**（生成"基金经理笔记"，记录"太贪了"等教训）
5. 基于复盘结论**重新挂单**到更合理价位（给价格 $7 吸筹空间）
6. 等待成交验证

### 1.2 文章已实现的功能

| # | 功能 | 文章体现 |
|---|------|----------|
| F1 | AI 自主决策下单 | AI 主动挂限价单 $4008 |
| F2 | AI 主动撤单 | 取消第一单 |
| F3 | AI 复盘反思 | "基金经理笔记"记录教训 |
| F4 | 基于复盘的策略迭代 | 撤单后用更合理价位重挂 |
| F5 | 技术指标体系 | Fib 38.2% / 61.8% 回撤 |
| F6 | 止损止盈硬约束 | 止损放基地下沿 $3,998 |
| F7 | 情景预案 | 给价格 $7 吸筹空间 |
| F8 | 不追涨纪律 | 没追涨，挂限价等回踩 |

### 1.3 文章业务管线

```
行情接入 → 指标计算(Fib/MA/支撑阻力) → 信号生成 → AI 决策(挂限价单)
    ↓                                            ↓
订单管理 ← 撤单 ← AI 复盘反思(基金经理笔记) ← 订单状态监控
    ↓
策略迭代(调整入场价/止损/仓位) → 重新挂单 → 等待成交 → 收盘复盘
```

### 1.4 与本项目当前能力对比

| 能力 | 项目已有 | 缺口 |
|------|---------|------|
| 账户/订单/持仓/信号/净值持久化 | ✅ Phase 1 | — |
| 程序化策略规则引擎 | ✅ Phase 2 | — |
| 费用模型 + 6 项风控检查 | ✅ Phase 3 | — |
| TradingEngine 全流程 | ✅ Phase 3 | — |
| Agent 风控二次确认 | ✅ Phase 4 | — |
| MarketListener 实时触发 | ✅ Phase 5 | — |
| **AI 自主决策下单** | ❌（当前 AI 仅做确认） | **核心缺口** |
| **AI 主动撤单/改单** | ❌ | **核心缺口** |
| **AI 复盘反思 + 笔记** | ❌ | **核心缺口** |
| **基于复盘的策略迭代** | ❌ | **核心缺口** |
| **Fibonacci 回撤指标** | ❌ | 中等缺口 |
| **ATR 指标 + 动态止损** | ❌ | 中等缺口 |
| **支撑阻力位识别** | ❌ | 中等缺口 |
| **情景预案 + 次日作战卡** | ❌ | 中等缺口 |
| API + WebUI | ❌ | 已规划 |

---

## 二、规划方案设计

### 2.1 设计理念

**核心创新点：把 AI 从"风控确认者"升级为"基金经理 Agent"**

文章的精髓不是程序化规则触发，而是 **AI 自主决策 + 复盘反思 + 策略迭代**的闭环。本项目要超越文章，必须：

1. **AI 自主发起交易决策**：不只是规则触发后确认，AI 能主动调用工具分析并生成交易计划（含入场价/止损/止盈/仓位）
2. **AI 主动撤单/改单能力**：订单不是"挂了就等"，AI 能根据后续行情反思并调整
3. **AI 复盘反思系统**：每笔交易完成后自动复盘 + 每日收盘生成基金经理笔记
4. **复盘记忆影响后续决策**：复盘结论进入 Agent 上下文，下次决策时引用
5. **多维度技术指标增强**：Fib + ATR + 筹码峰 + 支撑阻力四位一体
6. **情景预案 + 次日作战卡**：自动生成可执行的次日作战手册

### 2.2 任务优先级

按"业务价值 × 实现可行性 × 与文章契合度"排序：

- **P0（必须做，核心创新）**：AI 基金经理 Agent、AI 复盘反思系统、技术指标增强
- **P1（重要，超越文章）**：智能止损止盈、情景预案 + 作战卡、复盘记忆系统
- **P2（锦上添花）**：复盘文章自动生成、飞书/钉钉推送
- **P3（配套基础设施）**：API 端点、WebUI、配置测试文档

### 2.3 交付节奏

| 批次 | 任务 | 目标 |
|------|------|------|
| 批次 1（核心创新） | P0-A → P0-C → P0-B → P0-D → P0-E | AI 自主决策 + 撤单 + 复盘 + 记忆完整闭环 |
| 批次 2（智能增强） | P1-A → P1-B → P1-C | 智能止损止盈 + 情景预案 + 作战卡 |
| 批次 3（对外能力） | P3-A → P3-B | API + WebUI 暴露所有能力 |
| 批次 4（锦上添花） | P2-A → P2-B → P3-C | 内容沉淀 + 推送 + 配置测试文档 |

---

## 三、函数级任务清单

### P0-A：技术指标增强

**目标**: 为 AI 决策和止损止盈计算提供 Fib/ATR/支撑阻力三类核心指标。

**文件**: `strategies_v2/indicators.py`

#### 函数清单

```python
def compute_fibonacci_retracement(
    df: pd.DataFrame,
    lookback: int = 60,
    price_col: str = "close",
    high_col: str = "high",
    low_col: str = "low",
) -> Dict[str, float]:
    """Compute Fibonacci retracement levels over the lookback window.

    Args:
        df: Daily-bar DataFrame indexed by date ascending, must contain
            high/low/close columns.
        lookback: Number of bars to consider (from the end of df).
        price_col / high_col / low_col: Column names.

    Returns:
        Dict with keys "0.236", "0.382", "0.5", "0.618", "0.786" mapping
        to retracement price levels. Computed as:
            swing_high = max(df[high_col][-lookback:])
            swing_low  = min(df[low_col][-lookback:])
            diff = swing_high - swing_low
            level = swing_high - ratio * diff  (for upward trend)
        Direction auto-detected: if last close >= first close in window,
        treat as up-trend (retrace from high); else down-trend (retrace
        from low).
    """


def compute_atr(
    df: pd.DataFrame,
    period: int = 14,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.Series:
    """Compute Average True Range (ATR) as an EMA of True Range.

    True Range = max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )

    Args:
        df: Daily-bar DataFrame.
        period: EMA window. Default 14.
        high_col / low_col / close_col: Column names.

    Returns:
        pd.Series indexed like df, ATR values. First `period` rows may be
        NaN. Use `.iloc[-1]` to get the latest ATR.
    """


def compute_support_resistance(
    df: pd.DataFrame,
    window: int = 20,
    method: str = "fractal",
    high_col: str = "high",
    low_col: str = "low",
) -> Dict[str, List[float]]:
    """Identify support and resistance levels.

    Args:
        df: Daily-bar DataFrame.
        window: Lookback window for fractal detection (each side).
        method: "fractal" (default) or "cluster".
            - fractal: a high is a resistance if it's the max within
              [i-window, i+window]; similarly for lows.
            - cluster: round prices to bins, find density peaks.
        high_col / low_col: Column names.

    Returns:
        {"supports": List[float], "resistances": List[float]}
        Sorted ascending. Limited to top 5 each by recency/strength.
    """


def compute_indicators(
    df: pd.DataFrame,
    indicators: List[str],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dispatch function — extend existing compute_indicators to support
    new indicator names: "fibonacci", "atr", "support_resistance".

    Args:
        df: Daily-bar DataFrame.
        indicators: List of indicator names (existing + new).
        params: Optional params dict, e.g. {"atr_period": 14}.

    Returns:
        Dict mapping indicator name to its computed value.
        - "fibonacci" -> Dict[str, float] (5 levels)
        - "atr" -> float (latest ATR value)
        - "support_resistance" -> Dict[str, List[float]]
    """
```

#### 子任务

1. 在 `strategies_v2/indicators.py` 新增上述 3 个函数
2. 扩展现有 `compute_indicators` 调度函数，注册新指标名 `fibonacci` / `atr` / `support_resistance`
3. 在 `IndicatorSpec` 注册表（`strategies_v2/schema.py`）登记新指标
4. 扩展 `Rule` schema 允许规则右值为 Fib 回撤位（如 `close <= fib_0.618`）
5. 单元测试：用合成数据验证 Fib 回撤位计算正确性

**验收标准**:
- `from strategies_v2 import compute_indicators` 能返回包含新指标的字典
- `py_compile` 通过
- 单元测试通过：合成数据（已知 swing_high=100, swing_low=80）Fib 0.618 = 100 - 0.618*20 = 87.6

---

### P0-C：订单管理增强

**目标**: 支持 AI 主动撤单/改单，补全订单生命周期。

**文件**: `paper_trading/order.py`, `paper_trading/trading_engine.py`, `src/storage.py`

#### 函数清单

**`paper_trading/order.py` - OrderManager 类扩展**:

```python
def cancel_order(
    self,
    order_id: int,
    reason: Optional[str] = None,
) -> Order:
    """Cancel a pending order.

    Transitions order status PENDING -> CANCELLED. For buy orders,
    releases frozen cash via account_manager.unfreeze_cash.
    For sell orders, releases frozen quantity via position_manager.

    Args:
        order_id: ID of the order to cancel.
        reason: Cancellation reason (e.g. "AI reflection: price too high").

    Returns:
        The updated Order row.

    Raises:
        ValueError: If order is not in PENDING status (already filled
            or already cancelled).
    """


def modify_order(
    self,
    order_id: int,
    new_price: Optional[float] = None,
    new_quantity: Optional[float] = None,
    reason: Optional[str] = None,
) -> Order:
    """Modify a pending limit order.

    Implemented as: cancel old order + create new order with same
    account_id/code/side/signal_id but new price/quantity. Preserves
    audit chain via parent_order_id.

    Args:
        order_id: ID of the order to modify.
        new_price: New limit price (None = keep original).
        new_quantity: New quantity (None = keep original).
        reason: Modification reason.

    Returns:
        The new Order row (with parent_order_id pointing to old order).

    Raises:
        ValueError: If order is not PENDING, or both new_price and
            new_quantity are None.
    """
```

**`paper_trading/trading_engine.py` - TradingEngine 类扩展**:

```python
def cancel_signal(
    self,
    signal_id: int,
    reason: str,
) -> TradeResult:
    """Cancel all pending orders associated with a signal.

    Updates signal status to "cancelled". For each pending order linked
    to this signal, calls order_manager.cancel_order. Does NOT affect
    already-filled orders (those are closed positions, use sell instead).

    Args:
        signal_id: ID of the signal whose orders should be cancelled.
        reason: Cancellation reason.

    Returns:
        TradeResult with status="cancelled".
    """


def modify_signal(
    self,
    signal_id: int,
    new_price: Optional[float] = None,
    new_quantity: Optional[float] = None,
    reason: str = "",
) -> TradeResult:
    """Modify the order parameters of a signal.

    Cancels existing pending orders for this signal and creates a new
    order with updated price/quantity. The signal row is updated with
    new params and a note about the modification.

    Args:
        signal_id: ID of the signal to modify.
        new_price: New limit price.
        new_quantity: New quantity.
        reason: Modification reason.

    Returns:
        TradeResult with the new order_id.
    """
```

#### 子任务

1. 在 `OrderStatus` 枚举新增 `CANCELLED` / `MODIFIED`（如未有）
2. 实现 `OrderManager.cancel_order` 和 `modify_order`
3. 在 `PaperAccountManager` 新增 `unfreeze_cash(account_id, amount)` 方法
4. 在 `PositionManager` 新增 `unfreeze_quantity(account_id, code, quantity)` 方法（如卖出冻结已实现）
5. 在 `TradingEngine` 实现 `cancel_signal` 和 `modify_signal`
6. `PaperOrder` 表新增字段：`parent_order_id` (Optional[int])、`cancel_reason` (Optional[str])、`modified_at` (Optional[datetime])
7. 单元测试：撤单后资金解冻正确、改单后 audit 链完整

**验收标准**:
- `engine.cancel_signal(...)` 和 `engine.modify_signal(...)` 工作正常
- 订单状态机覆盖挂单→撤单→改单→成交全路径
- 撤单后账户可用现金恢复

---

### P0-B：AI 基金经理 Agent

**目标**: AI 从"风控确认者"升级为"自主决策的基金经理"，能调用工具分析并生成完整交易计划。

**文件**: `src/agent/portfolio_manager_agent.py` (新建), `src/agent/tools/registry.py`, `src/agent/factory.py`

#### 函数清单

**`src/agent/portfolio_manager_agent.py` (新建)**:

```python
@dataclass
class PMDecision:
    """Parsed decision from Portfolio Manager Agent."""
    action: str                          # "buy" | "sell" | "cancel" | "modify" | "hold"
    code: str
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    quantity: Optional[float] = None
    order_type: str = "limit"            # "limit" | "market"
    reason: str = ""
    confidence: float = 0.0
    alternative_plan: Optional[Dict[str, Any]] = None
    raw_response: Optional[str] = None
    used_fallback: bool = False
    error: Optional[str] = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for API response / persistence."""


class PortfolioManagerAgent:
    """AI agent that autonomously decides trading actions.

    Wraps AgentExecutor with a PM-specific system prompt and a set of
    paper-trading tools (place_order, cancel_order, modify_order, etc.).
    On each invocation, returns a structured PMDecision.
    """

    def __init__(
        self,
        executor: Optional[AgentExecutor] = None,
        config: Optional[Any] = None,
        account_id: Optional[int] = None,
        reflection_engine: Optional[Any] = None,
        timeout_seconds: float = 180.0,
        fallback_action: str = "hold",
        max_retries: int = 0,
    ):
        """Initialize PM Agent.

        Args:
            executor: Pre-built AgentExecutor. If None, built via
                build_portfolio_manager_agent factory.
            config: Application config.
            account_id: Paper trading account this PM manages.
            reflection_engine: For injecting historical reflections
                into decision context.
            timeout_seconds: Hard timeout for LLM calls.
            fallback_action: Action to return if LLM fails ("hold" | "buy" | "sell").
            max_retries: Number of retry attempts on LLM failure.
        """

    def make_decision(
        self,
        market_context: Dict[str, Any],
        trigger_reason: str = "scheduled",
    ) -> PMDecision:
        """Make a trading decision based on current market context.

        Args:
            market_context: Dict containing:
                - watched_codes: List[str]
                - latest_prices: Dict[str, float]
                - account_snapshot: AccountSnapshot
                - positions: List[Position]
                - open_orders: List[Order]
                - recent_reflections: List[ReflectionNote]
                - daily_dfs: Dict[str, pd.DataFrame]
            trigger_reason: Why this decision was triggered
                ("scheduled" | "signal" | "manual" | "reflection").

        Returns:
            PMDecision with action and parameters.
        """

    def _build_system_prompt(self) -> str:
        """Build the PM system prompt.

        Includes:
        - Role definition: "你是基金经理，管理虚拟账户"
        - Trading philosophy: 不追涨、严进策略、趋势交易
        - Tool descriptions
        - Output format: JSON with action/entry_price/stop_loss/...
        """

    def _build_user_message(
        self,
        market_context: Dict[str, Any],
        trigger_reason: str,
    ) -> str:
        """Build the user message with current market state.

        Formats account snapshot, positions, open orders, recent
        reflections, and latest prices into a structured prompt.
        """

    def _call_agent_with_timeout(
        self,
        prompt: str,
        session_id: str,
    ) -> str:
        """Call executor.chat() in a daemon thread with hard timeout.

        Same pattern as AgentRiskReviewer._call_agent_with_timeout.
        """

    def _parse_decision(
        self,
        raw_text: str,
    ) -> PMDecision:
        """Parse LLM response into PMDecision.

        Parsing priority:
        1. Strict JSON parse
        2. json_repair fallback
        3. Keyword extraction (buy/sell/cancel/modify/hold)
        4. Empty response -> fallback_action
        """

    def _inject_reflections(
        self,
        market_context: Dict[str, Any],
    ) -> None:
        """Prepend recent reflection notes to the context.

        Called before _build_user_message. Adds:
        - Last 5 reflections (any code)
        - Last 3 reflections for each watched code
        """
```

**`src/agent/tools/registry.py` - 新增工具注册**:

```python
def register_paper_trading_tools(
    registry: ToolRegistry,
    engine: TradingEngine,
    account_id: int,
    reflection_engine: Optional[Any] = None,
) -> None:
    """Register paper-trading tools into the agent's tool registry.

    Tools registered:
    - paper_trading_place_order(account_id, code, side, order_type,
        price, quantity) -> Dict
    - paper_trading_cancel_order(order_id, reason) -> Dict
    - paper_trading_modify_order(order_id, new_price, new_quantity) -> Dict
    - paper_trading_get_account_snapshot(account_id) -> Dict
    - paper_trading_get_open_orders(account_id) -> List[Dict]
    - paper_trading_get_positions(account_id) -> List[Dict]
    - paper_trading_get_recent_reflections(account_id, limit=5) -> List[Dict]
    - paper_trading_compute_sltp(code, entry_price, side) -> Dict
        (calls SLTPCalculator from P1-A, available after P1-A done;
         before that, returns a stub or None)
    """
```

**`src/agent/factory.py` - 新增工厂函数**:

```python
def build_portfolio_manager_agent(
    config: Any,
    account_id: int,
    reflection_engine: Optional[Any] = None,
    trading_engine: Optional[TradingEngine] = None,
) -> PortfolioManagerAgent:
    """Build a PortfolioManagerAgent wired to project defaults.

    Args:
        config: Application config.
        account_id: Paper trading account ID.
        reflection_engine: For memory injection.
        trading_engine: Pre-built engine. If None, builds default.

    Returns:
        PortfolioManagerAgent ready to call make_decision().
    """
```

#### 子任务

1. 新建 `src/agent/portfolio_manager_agent.py`，实现 `PMDecision` dataclass 和 `PortfolioManagerAgent` 类
2. 在 `src/agent/tools/registry.py` 实现 `register_paper_trading_tools`
3. 在 `src/agent/factory.py` 新增 `build_portfolio_manager_agent` 工厂函数
4. 定义 PM 专属 system prompt（强调基金经理角色 + 不追涨纪律 + JSON 输出格式）
5. 定义 `PaperDecision` ORM 表（account_id, action, code, params_json, reason, confidence, raw_response, created_at）
6. `PortfolioManagerAgent.make_decision` 末尾持久化决策到 `PaperDecision`
7. 集成到 `MarketListener`：可配置 `pm_agent`，按 `pm_decision_interval_seconds` 周期触发
8. 冒烟测试：PM Agent 能在测试账户上自主调用工具下单、撤单、改单

**验收标准**:
- PM Agent 能自主调用工具下单、撤单、改单
- 决策日志可查（PaperDecision 表）
- 与文章"先挂单→撤单→重挂"的闭环等价

---

### P0-D：AI 复盘反思系统

**目标**: 每笔交易完成后自动触发 AI 复盘，生成"基金经理笔记"。

**文件**: `paper_trading/reflection.py` (新建), `src/storage.py`

#### 函数清单

**`paper_trading/reflection.py` (新建)**:

```python
@dataclass
class ReflectionNote:
    """A single reflection note (基金经理笔记)."""
    note_id: Optional[int] = None
    account_id: int
    signal_id: Optional[int] = None
    trade_id: Optional[int] = None
    code: str
    action: str                                    # "buy" | "sell" | "reject" | "daily"
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    lesson: str = ""                               # 核心教训
    what_went_right: str = ""                      # 做得对的
    what_went_wrong: str = ""                      # 做得错的
    what_to_do_next_time: str = ""                 # 下次怎么做
    confidence: float = 0.0
    raw_response: Optional[str] = None
    used_fallback: bool = False
    error: Optional[str] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""

    def to_markdown(self) -> str:
        """Format as Markdown for display.

        Format:
        ## 🧠 基金经理笔记 - {code} {date}

        **操作**: {action}
        **入场**: {entry_price}  **出场**: {exit_price}  **盈亏**: {pnl}

        ### 做得对的
        {what_went_right}

        ### 做得错的
        {what_went_wrong}

        ### 下次怎么做
        {what_to_do_next_time}

        ### 核心教训
        > {lesson}
        """


class ReflectionEngine:
    """Generates AI reflection notes after trades and at end-of-day.

    Runs asynchronously (daemon thread) to avoid blocking the trading
    pipeline. Persists notes to PaperReflection table for memory injection.
    """

    def __init__(
        self,
        executor: Optional[AgentExecutor] = None,
        config: Optional[Any] = None,
        db_manager: Optional[DatabaseManager] = None,
        timeout_seconds: float = 180.0,
        fallback_on_failure: bool = True,
        max_retries: int = 0,
    ):
        """Initialize ReflectionEngine.

        Args:
            executor: Pre-built AgentExecutor for LLM calls.
            config: Application config.
            db_manager: Database manager for persistence.
            timeout_seconds: Hard timeout for LLM calls.
            fallback_on_failure: If True, returns a stub note on failure.
            max_retries: Number of retry attempts.
        """

    def reflect_on_trade(
        self,
        trade_result: TradeResult,
        account_id: int,
        trigger: str = "trade",
    ) -> None:
        """Async: trigger reflection after a trade completes.

        Spawns a daemon thread that calls _reflect_sync. Returns
        immediately to avoid blocking the trading pipeline.

        Args:
            trade_result: The TradeResult from TradingEngine.
            account_id: Paper trading account.
            trigger: "trade" | "reject" (for rejected signals).
        """

    def reflect_on_daily(
        self,
        account_id: int,
        target_date: date,
    ) -> None:
        """Async: trigger end-of-day comprehensive reflection.

        Reviews all trades for the day, generates a daily summary note
        covering: best/worst decisions, lessons learned, tomorrow's plan.
        """

    def _reflect_sync(
        self,
        trade_result: TradeResult,
        account_id: int,
        trigger: str,
    ) -> Optional[ReflectionNote]:
        """Synchronous reflection logic. Called by daemon thread.

        1. Build reflection prompt
        2. Call LLM via executor
        3. Parse response
        4. Persist to PaperReflection table
        """

    def _build_trade_reflection_prompt(
        self,
        trade_result: TradeResult,
        account_id: int,
    ) -> str:
        """Build prompt for per-trade reflection.

        Includes:
        - Trade details (action, price, quantity, fee)
        - Account snapshot before/after (if available)
        - Recent market context (price action, indicators)
        - Recent reflections (to avoid repetition)

        Output format: 3-section (what_went_right / wrong / next_time).
        """

    def _build_daily_reflection_prompt(
        self,
        account_id: int,
        target_date: date,
    ) -> str:
        """Build prompt for end-of-day reflection.

        Includes:
        - All trades today
        - Net P&L
        - Win/loss rate
        - Best/worst decisions
        - Market overview
        """

    def _parse_reflection(
        self,
        raw_text: str,
        context: Dict[str, Any],
    ) -> ReflectionNote:
        """Parse LLM response into ReflectionNote.

        Parsing priority:
        1. Strict JSON
        2. json_repair
        3. Markdown section extraction (### 做得对的 / ### 做得错的 / ### 下次)
        4. Plain text -> put entire content in `lesson` field
        """

    def _persist_note(
        self,
        note: ReflectionNote,
    ) -> int:
        """Persist note to PaperReflection table. Returns note_id."""

    def get_recent_notes(
        self,
        account_id: int,
        limit: int = 10,
        code: Optional[str] = None,
    ) -> List[ReflectionNote]:
        """Retrieve recent reflection notes for memory injection.

        Args:
            account_id: Paper trading account.
            limit: Max notes to return.
            code: If provided, filter by stock code.

        Returns:
            List of ReflectionNote, most recent first.
        """
```

**`src/storage.py` - PaperReflection ORM 表**:

```python
class PaperReflection(Base):
    """Reflection notes (基金经理笔记) for paper trading.

    Persisted by ReflectionEngine, read by PortfolioManagerAgent for
    memory injection.
    """
    __tablename__ = "paper_reflections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("paper_accounts.id"), nullable=False, index=True)
    signal_id = Column(Integer, ForeignKey("paper_signals.id"), nullable=True)
    trade_id = Column(Integer, ForeignKey("paper_trades.id"), nullable=True)
    code = Column(String(32), nullable=False, index=True)
    action = Column(String(16), nullable=False)               # buy/sell/reject/daily
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    lesson = Column(Text, nullable=False, default="")
    what_went_right = Column(Text, nullable=False, default="")
    what_went_wrong = Column(Text, nullable=False, default="")
    what_to_do_next_time = Column(Text, nullable=False, default="")
    confidence = Column(Float, nullable=False, default=0.0)
    raw_response = Column(Text, nullable=True)
    used_fallback = Column(Boolean, nullable=False, default=False)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
```

#### 子任务

1. 新建 `paper_trading/reflection.py`，实现 `ReflectionNote` dataclass 和 `ReflectionEngine` 类
2. 在 `src/storage.py` 新增 `PaperReflection` ORM 表
3. 复盘 prompt 模板设计（参考文章话术："复盘教训：xxx 设在了 xxx 位之下 $8 — 太贪了"）
4. 复盘触发时机集成：
   - `TradingEngine._execute_market_order` 末尾调用 `reflection_engine.reflect_on_trade`
   - `MarketListener._maybe_daily_settle` 末尾调用 `reflection_engine.reflect_on_daily`
5. 异步执行机制：daemon 线程 + timeout
6. 持久化到 `PaperReflection` 表
7. 冒烟测试：合成 TradeResult，验证复盘笔记生成

**验收标准**:
- 每笔交易后能生成结构化复盘笔记
- 笔记内容包含"教训/改进/下次怎么做"三段
- 与文章风格一致

---

### P0-E：复盘记忆系统

**目标**: 复盘结论进入 Agent 上下文，影响后续决策。

**文件**: `src/agent/portfolio_manager_agent.py`, `paper_trading/reflection.py`

#### 函数清单

**`paper_trading/reflection.py` - ReflectionEngine 扩展**:

```python
def get_relevant_notes(
    self,
    account_id: int,
    code: Optional[str] = None,
    limit: int = 3,
    days_back: int = 30,
) -> List[ReflectionNote]:
    """Retrieve reflections relevant to a code for memory injection.

    Args:
        account_id: Paper trading account.
        code: Stock code to filter by. If None, returns general notes.
        limit: Max notes to return.
        days_back: Only consider notes from last N days.

    Returns:
        List of ReflectionNote, most recent first.
    """


def format_notes_for_context(
    notes: List[ReflectionNote],
    max_chars: int = 2000,
) -> str:
    """Format reflection notes into a context string for LLM prompt.

    Format:
    ## 最近的复盘教训（请避免重复犯错）

    ### 笔记 1 - {code} {date}
    - 操作: {action}
    - 教训: {lesson}
    - 下次: {what_to_do_next_time}

    ### 笔记 2 - ...

    Truncates to max_chars to avoid context overflow.
    """
```

**`src/agent/portfolio_manager_agent.py` - PortfolioManagerAgent 扩展**:

```python
def _inject_reflections(
    self,
    market_context: Dict[str, Any],
) -> None:
    """Inject recent reflection notes into market_context.

    Called at the start of make_decision(). Adds:
    - market_context["recent_reflections"]: last 5 notes (any code)
    - market_context["code_reflections"]: Dict[code, List[note]] for
      each watched code

    If reflection_engine is None, skips silently.
    """


def _build_user_message(
    self,
    market_context: Dict[str, Any],
    trigger_reason: str,
) -> str:
    """Extended to include reflection context.

    Adds a section at the top:
    "## 最近的复盘教训
    {format_notes_for_context(market_context['recent_reflections'])}

    ## 针对关注股票的历史教训
    {per-code formatted notes}
    "
    """
```

#### 子任务

1. 实现 `ReflectionEngine.get_relevant_notes`
2. 实现 `format_notes_for_context` 辅助函数
3. 在 `PortfolioManagerAgent._inject_reflections` 中调用上述方法
4. 在 `_build_user_message` 中加入复盘记忆段落
5. PM system prompt 加入："以下是最近的复盘教训，请在决策时参考避免重复犯错"
6. 冒烟测试：注入历史笔记后，PM Agent 决策时能引用过往教训

**验收标准**:
- AI 决策时能引用过往教训
- 决策内容能体现"上次在 xxx 设止损太贪，这次按复盘建议调整"
- 与文章"撤单后用更合理价位重挂"的行为等价

---

### P1-A：智能止损止盈

**目标**: 基于 ATR + 筹码峰 + Fib 三位一体自动计算三线。

**文件**: `paper_trading/sltp_calculator.py` (新建), `paper_trading/trading_engine.py`

#### 函数清单

**`paper_trading/sltp_calculator.py` (新建)**:

```python
@dataclass
class SLTPResult:
    """Stop-loss / take-profit calculation result."""
    stop_loss: float
    take_profit_1: float           # 第一止盈位（触及减半）
    take_profit_2: float           # 第二止盈位（触及清仓）
    reasoning: str                 # 人类可读的计算依据
    components: Dict[str, Any]     # 各指标计算明细


class SLTPCalculator:
    """Computes stop-loss and take-profit levels from multiple indicators.

    Combines ATR (volatility), chip distribution (support/resistance from
    cost peaks), and Fibonacci retracement to produce three lines:
    - stop_loss: max loss tolerance
    - take_profit_1: first target (partial exit)
    - take_profit_2: final target (full exit)
    """

    def __init__(
        self,
        data_fetcher: Optional[Any] = None,
        atr_period: int = 14,
        fib_lookback: int = 60,
        support_window: int = 20,
    ):
        """Initialize calculator."""

    def compute(
        self,
        entry_price: float,
        side: str,                            # "buy" | "sell"
        code: str,
        df: Optional[pd.DataFrame] = None,
        chip_distribution: Optional[Dict] = None,
    ) -> SLTPResult:
        """Compute three lines for a trade.

        For buy:
            R = entry_price - stop_loss   (risk per share)
            take_profit_1 = entry_price + 1*R  (or Fib 0.618)
            take_profit_2 = entry_price + 2*R  (or nearest resistance)

        Stop loss candidates (take the tightest):
            - entry_price - 1.5 * ATR
            - nearest support below entry (from compute_support_resistance)
            - nearest chip peak below entry (if chip_distribution provided)

        Args:
            entry_price: Planned entry price.
            side: "buy" or "sell".
            code: Stock code (for fetching data if df is None).
            df: Pre-fetched daily bars. If None, fetches via data_fetcher.
            chip_distribution: Chip distribution dict from get_chip_distribution tool.

        Returns:
            SLTPResult with three lines and reasoning.
        """

    def _fetch_data(
        self,
        code: str,
    ) -> Optional[pd.DataFrame]:
        """Fetch daily bars for code via data_fetcher."""

    def _fetch_chip_distribution(
        self,
        code: str,
    ) -> Optional[Dict]:
        """Fetch chip distribution via data_fetcher."""

    def _compute_stop_loss(
        self,
        entry_price: float,
        side: str,
        atr: float,
        supports: List[float],
        chip_peaks_below: List[float],
    ) -> Tuple[float, str]:
        """Compute stop loss from candidates. Returns (price, reasoning)."""

    def _compute_take_profit(
        self,
        entry_price: float,
        stop_loss: float,
        side: str,
        resistances: List[float],
        fib_levels: Dict[str, float],
        index: int,                   # 1 or 2
    ) -> Tuple[float, str]:
        """Compute take profit 1 or 2. Returns (price, reasoning)."""
```

**`paper_trading/trading_engine.py` - 集成**:

```python
class TradingEngine:
    def __init__(
        self,
        ...
        sltp_calculator: Optional[SLTPCalculator] = None,  # NEW
    ):
        ...

    def _execute_market_order(
        self,
        ...
    ) -> TradeResult:
        """Extended: after applying buy, if sltp_calculator is configured,
        compute three lines and write to PaperPosition.stop_loss /
        take_profit. Override any previously set values.
        """
```

#### 子任务

1. 新建 `paper_trading/sltp_calculator.py`，实现 `SLTPResult` 和 `SLTPCalculator`
2. 三线计算逻辑：止损（min 候选）、一止（entry+1R 或 Fib 0.618）、二止（entry+2R 或阻力位）
3. 筹码峰集成：调用 `get_chip_distribution` 工具，提取主要筹码峰
4. `TradingEngine.__init__` 新增 `sltp_calculator` 可选参数
5. `TradingEngine._execute_market_order` 在 `apply_buy` 后调用 `SLTPCalculator.compute` 自动写入 `PaperPosition.stop_loss / take_profit`
6. PM Agent 决策时可调用 `compute_sltp` 工具预览三线
7. 单元测试：合成数据验证三线计算合理性

**验收标准**:
- 下单后持仓自动有止损止盈
- 三线基于多指标综合计算
- 超越文章"止损放基地下沿"的单一维度

---

### P1-B：情景预案 + 次日作战卡

**目标**: 每日收盘后生成强势/中性/弱势三情景 + 可执行的次日作战卡。

**文件**: `paper_trading/battle_plan.py` (新建), `src/storage.py`

#### 函数清单

**`paper_trading/battle_plan.py` (新建)**:

```python
@dataclass
class HoldingPlan:
    """Plan for an existing holding."""
    code: str
    name: str
    current_price: float
    strong_scenario: str             # 强势情景应对
    neutral_scenario: str            # 中性情景应对
    weak_scenario: str               # 弱势情景应对
    action_conditions: List[str]     # 触发条件清单
    stop_loss: float
    take_profit_1: float
    take_profit_2: float


@dataclass
class CandidatePlan:
    """Plan for a candidate stock to buy."""
    code: str
    name: str
    auction_condition: str           # 集合竞价条件
    intraday_trigger: str            # 盘中触发条件
    position_ratio: float            # 建议仓位比例
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    technical_score: float


@dataclass
class BattlePlan:
    """Daily battle plan (次日作战卡)."""
    plan_id: Optional[int] = None
    account_id: int
    date: date
    holdings_plans: List[HoldingPlan] = field(default_factory=list)
    candidates: List[CandidatePlan] = field(default_factory=list)
    market_review: str = ""           # 市场综述（AI 生成）
    sentiment_score: int = 50         # 0-100, 情绪评级
    main_theme: str = ""              # 主线逻辑
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""

    def to_markdown(self) -> str:
        """Format as Markdown作战卡 for display/push.

        Format:
        # 📋 次日作战卡 - {date}

        ## 市场综述
        {market_review}

        **情绪评级**: {sentiment_score}/100  |  **主线**: {main_theme}

        ## 持仓应对（三情景）
        ### {code} {name}
        - 当前价: {current_price}
        - 强势: {strong_scenario}
        - 中性: {neutral_scenario}
        - 弱势: {weak_scenario}
        - 止损: {stop_loss} | 一止: {take_profit_1} | 二止: {take_profit_2}

        ## 候选标的
        ### {code} {name} (评分: {technical_score})
        - 集合竞价: {auction_condition}
        - 盘中触发: {intraday_trigger}
        - 建议仓位: {position_ratio}%
        - 三线: SL={stop_loss} TP1={take_profit_1} TP2={take_profit_2}
        """


class BattlePlanGenerator:
    """Generates daily battle plans using AI + technical analysis."""

    def __init__(
        self,
        pm_agent: Optional[PortfolioManagerAgent] = None,
        sltp_calculator: Optional[SLTPCalculator] = None,
        data_fetcher: Optional[Any] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):
        """Initialize generator."""

    def generate(
        self,
        account_id: int,
        target_date: date,
        watched_codes: Optional[List[str]] = None,
    ) -> BattlePlan:
        """Generate battle plan for the next trading day.

        Steps:
        1. Fetch account snapshot (holdings, cash)
        2. For each holding: compute three-scenario plan + SLTP
        3. For each watched code (not held): evaluate as candidate
        4. Call PM Agent to generate market_review
        5. Persist to PaperBattlePlan table

        Args:
            account_id: Paper trading account.
            target_date: Date for the plan (usually tomorrow).
            watched_codes: Candidate codes. If None, uses config.stock_list.

        Returns:
            BattlePlan object.
        """

    def _generate_holding_plan(
        self,
        account_id: int,
        position: Any,
        df: pd.DataFrame,
    ) -> HoldingPlan:
        """Generate three-scenario plan for a holding."""

    def _generate_candidate_plan(
        self,
        code: str,
        df: pd.DataFrame,
        cash_available: float,
    ) -> CandidatePlan:
        """Generate candidate plan for a non-held stock."""

    def _generate_market_review(
        self,
        account_id: int,
        target_date: date,
    ) -> Tuple[str, int, str]:
        """Call PM Agent for market review.

        Returns:
            (market_review_text, sentiment_score, main_theme)
        """

    def _persist_plan(
        self,
        plan: BattlePlan,
    ) -> int:
        """Persist to PaperBattlePlan table. Returns plan_id."""
```

**`src/storage.py` - PaperBattlePlan ORM 表**:

```python
class PaperBattlePlan(Base):
    """Daily battle plans (次日作战卡)."""
    __tablename__ = "paper_battle_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("paper_accounts.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    holdings_plans_json = Column(Text, nullable=False, default="[]")   # JSON-serialized
    candidates_json = Column(Text, nullable=False, default="[]")
    market_review = Column(Text, nullable=False, default="")
    sentiment_score = Column(Integer, nullable=False, default=50)
    main_theme = Column(String(200), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_battle_plan_account_date"),
    )
```

#### 子任务

1. 新建 `paper_trading/battle_plan.py`，实现 dataclass 和 `BattlePlanGenerator` 类
2. 在 `src/storage.py` 新增 `PaperBattlePlan` ORM 表
3. 生成逻辑：三情景预案 + 候选标的 + 三线（调用 P1-A 的 `SLTPCalculator`）
4. PM Agent 生成"市场综述"段落
5. 持久化到 `PaperBattlePlan` 表
6. `MarketListener._maybe_daily_settle` 末尾触发 `BattlePlanGenerator.generate`
7. `to_markdown` 输出可推送的作战卡

**验收标准**:
- 每日收盘后自动生成结构化作战卡
- 包含三情景 + 候选标的 + 三线
- 超越文章"给价格 $7 吸筹空间"的单一预案

---

### P1-C：MarketListener 集成复盘触发

**目标**: 把 P0-D / P1-B 的触发点接入 MarketListener。

**文件**: `paper_trading/market_listener.py`, `paper_trading/trading_engine.py`

#### 函数清单

**`paper_trading/market_listener.py` - MarketListenerConfig 扩展**:

```python
@dataclass
class MarketListenerConfig:
    # ... existing fields ...

    # NEW: optional engines for AI-driven features
    reflection_engine: Optional[Any] = None
    battle_plan_generator: Optional[Any] = None
    pm_agent: Optional[Any] = None

    # NEW: PM Agent decision interval (seconds)
    pm_decision_interval_seconds: float = 60.0

    # NEW: enable auto reflection on trade execution
    enable_auto_reflection: bool = True
```

**`paper_trading/market_listener.py` - MarketListener 扩展**:

```python
class MarketListener:
    def __init__(self, ...):
        # ... existing init ...
        self._last_pm_decision_at: Optional[datetime] = None

    def _tick_market(
        self,
        market: str,
    ) -> None:
        """Extended: after existing tick logic, optionally trigger PM Agent.

        PM Agent is triggered every pm_decision_interval_seconds, not
        every tick (to limit LLM cost).
        """

    def _maybe_trigger_pm_decision(
        self,
        market: str,
        latest_prices: Dict[str, float],
    ) -> None:
        """Trigger PM Agent if interval elapsed.

        Builds market_context dict and calls pm_agent.make_decision.
        Persists decision via PM Agent itself.
        """

    def _maybe_daily_settle(
        self,
        market: str,
    ) -> None:
        """Extended: after daily_settle, trigger:
        1. reflection_engine.reflect_on_daily
        2. battle_plan_generator.generate (for next trading day)
        """
```

**`paper_trading/trading_engine.py` - 事件回调机制**:

```python
class TradingEngine:
    def __init__(
        self,
        ...
        on_trade_executed: Optional[Callable[[TradeResult, int], None]] = None,  # NEW
        on_signal_rejected: Optional[Callable[[TradeResult, int], None]] = None,  # NEW
    ):
        # Store callbacks
        self._on_trade_executed = on_trade_executed
        self._on_signal_rejected = on_signal_rejected

    def _execute_market_order(
        self,
        ...
    ) -> TradeResult:
        """Extended: after successful fill, call on_trade_executed callback."""

    def submit_signal(
        self,
        ...
    ) -> TradeResult:
        """Extended: after rejection, call on_signal_rejected callback."""
```

#### 子任务

1. `MarketListenerConfig` 新增 `reflection_engine` / `battle_plan_generator` / `pm_agent` 字段
2. `MarketListener._tick_market` 末尾调用 `_maybe_trigger_pm_decision`
3. `MarketListener._maybe_daily_settle` 末尾触发 `reflect_on_daily` 和 `battle_plan_generator.generate`
4. `TradingEngine` 新增 `on_trade_executed` / `on_signal_rejected` 回调
5. `MarketListener` 在初始化时注册回调：成交后触发 `reflection_engine.reflect_on_trade`
6. PM Agent 决策周期可配置（默认 60s）

**验收标准**:
- 完整闭环：行情 → PM 决策 → 下单 → 成交 → 复盘 → 记忆 → 次日作战卡 → 影响 PM 决策
- 复盘异步执行不阻塞交易流程

---

### P2-A：复盘文章自动生成

**目标**: 把当日复盘数据转为公众号/雪球风格文章。

**文件**: `paper_trading/content_generator.py` (新建)

#### 函数清单

```python
class ContentGenerator:
    """Generates publishable content from trading data."""

    def __init__(
        self,
        pm_agent: Optional[PortfolioManagerAgent] = None,
        reflection_engine: Optional[ReflectionEngine] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):

    def generate_daily_report(
        self,
        account_id: int,
        target_date: date,
    ) -> str:
        """Generate Markdown daily report.

        Sections:
        1. 今日市场回顾
        2. 主线逻辑
        3. 持仓操作反思
        4. 明日策略展望

        Returns:
            Markdown string.
        """

    def generate_voice_script(
        self,
        account_id: int,
        target_date: date,
    ) -> str:
        """Generate voice-over script for video/audio.

        Returns:
            Plain text script (300-500 chars).
        """

    def _collect_daily_data(
        self,
        account_id: int,
        target_date: date,
    ) -> Dict[str, Any]:
        """Collect all data needed for report generation.

        Returns dict with:
        - trades: List[TradeResult]
        - reflections: List[ReflectionNote]
        - battle_plan: BattlePlan
        - net_value: float
        - pnl_today: float
        """

    def _save_to_file(
        self,
        content: str,
        account_id: int,
        target_date: date,
        kind: str,                  # "report" | "script"
    ) -> str:
        """Save content to data/reports/{date}_{kind}.md. Returns path."""
```

---

### P2-B：飞书/钉钉推送工作流

**目标**: 作战卡 + 复盘笔记 + AI 决策日志自动推送。

**文件**: `paper_trading/notification_integration.py` (新建)

#### 函数清单

```python
class PaperTradingNotifier:
    """Pushes paper-trading updates to Lark/DingTalk."""

    def __init__(
        self,
        config: Any,
        reflection_engine: Optional[ReflectionEngine] = None,
        battle_plan_generator: Optional[BattlePlanGenerator] = None,
    ):

    def push_battle_plan(
        self,
        plan: BattlePlan,
    ) -> bool:
        """Push battle plan as Markdown card."""

    def push_reflection(
        self,
        note: ReflectionNote,
    ) -> bool:
        """Push a reflection note (optional, can be noisy)."""

    def push_daily_summary(
        self,
        account_id: int,
        target_date: date,
    ) -> bool:
        """Push daily summary: net value, P&L, key decisions."""

    def _send_lark(
        self,
        title: str,
        content: str,
    ) -> bool:
        """Send to Lark via existing notification module."""

    def _send_dingtalk(
        self,
        title: str,
        content: str,
    ) -> bool:
        """Send to DingTalk via existing notification module."""
```

---

### P3-A：API 端点 + Pydantic schema

**目标**: 暴露所有新能力。

**文件**: `src/api/paper_trading_routes.py` (新建), `src/api/schemas/paper_trading.py` (新建)

#### 端点清单

```python
# Account / Position / Order / Signal (原有)
GET  /api/v1/paper-trading/accounts/{id}/snapshot
GET  /api/v1/paper-trading/accounts/{id}/positions
GET  /api/v1/paper-trading/accounts/{id}/orders
GET  /api/v1/paper-trading/accounts/{id}/signals
GET  /api/v1/paper-trading/accounts/{id}/net-values

# Order actions (P0-C)
POST /api/v1/paper-trading/accounts/{id}/orders                # 手动下单
POST /api/v1/paper-trading/orders/{id}/cancel                  # 手动撤单
POST /api/v1/paper-trading/orders/{id}/modify                  # 手动改单

# AI 决策日志 (P0-B)
GET  /api/v1/paper-trading/accounts/{id}/decisions             # PM Agent 决策列表

# 复盘笔记 (P0-D)
GET  /api/v1/paper-trading/accounts/{id}/reflections           # 复盘笔记列表
GET  /api/v1/paper-trading/accounts/{id}/reflections/{note_id} # 单条笔记
POST /api/v1/paper-trading/accounts/{id}/reflect               # 手动触发复盘

# 作战卡 (P1-B)
GET  /api/v1/paper-trading/accounts/{id}/battle-plans          # 作战卡列表
GET  /api/v1/paper-trading/accounts/{id}/battle-plans/{date}   # 单日作战卡
POST /api/v1/paper-trading/accounts/{id}/generate-battle-plan  # 手动触发生成

# PM Agent 控制 (P0-B)
POST /api/v1/paper-trading/accounts/{id}/pm-decision           # 触发 PM 决策
```

#### Pydantic schema 文件

```python
# src/api/schemas/paper_trading.py
class AccountSnapshotSchema(BaseModel): ...
class PositionSchema(BaseModel): ...
class OrderSchema(BaseModel): ...
class SignalSchema(BaseModel): ...
class TradeResultSchema(BaseModel): ...
class PMDecisionSchema(BaseModel): ...
class ReflectionNoteSchema(BaseModel): ...
class BattlePlanSchema(BaseModel): ...
class PlaceOrderRequest(BaseModel): ...
class ModifyOrderRequest(BaseModel): ...
class CancelOrderRequest(BaseModel): ...
```

---

### P3-B：WebUI 页面

**目标**: 可视化展示所有能力。

**文件**: `web/static/js/paper_trading.js` (新建), `web/templates/paper_trading.html` (新建)

#### 页面模块

1. **PaperTradingPage**: 账户概览 + 持仓 + 订单 + 净值曲线
2. **AI 决策时间线**: PM Agent 决策日志按时间倒序展示（含 reason / confidence / action）
3. **复盘笔记墙**: 基金经理笔记卡片流（参考文章"🧠 基金经理笔记"样式）
4. **作战卡视图**: 当日作战卡可视化（三情景 + 候选标的 + 三线）
5. **净值曲线图**: 用 ECharts/Recharts 绘制

#### 函数清单（前端）

```javascript
// 加载账户快照
async function loadAccountSnapshot(accountId) { ... }
// 加载持仓
async function loadPositions(accountId) { ... }
// 加载订单
async function loadOrders(accountId) { ... }
// 加载 AI 决策日志
async function loadDecisions(accountId) { ... }
// 加载复盘笔记
async function loadReflections(accountId) { ... }
// 加载作战卡
async function loadBattlePlan(accountId, date) { ... }
// 加载净值曲线
async function loadNetValues(accountId) { ... }
// 渲染净值曲线（ECharts）
function renderNetValueChart(data) { ... }
// 触发 PM 决策
async function triggerPMDecision(accountId) { ... }
// 手动下单
async function placeOrder(accountId, params) { ... }
// 手动撤单
async function cancelOrder(orderId) { ... }
```

---

### P3-C：配置 + 测试 + 文档

**目标**: 完成配置项、集成测试、文档更新。

#### .env.example 新增配置项

```bash
# Paper Trading
PAPER_TRADING_ENABLED=true
PAPER_TRADING_INITIAL_CAPITAL=1000
PAPER_TRADING_DEFAULT_ACCOUNT_ID=1
PAPER_TRADING_WATCHED_CODES=600519,000001
PAPER_TRADING_STRATEGY_DIR=paper_trading/strategies
PAPER_TRADING_TICK_INTERVAL_SECONDS=10
PAPER_TRADING_MARKETS=cn

# Agent Risk Review (Phase 4)
PAPER_TRADING_ENABLE_AGENT_REVIEW=false

# Portfolio Manager Agent (P0-B)
PAPER_TRADING_ENABLE_PM_AGENT=true
PAPER_TRADING_PM_DECISION_INTERVAL_SECONDS=60

# Reflection Engine (P0-D)
PAPER_TRADING_ENABLE_REFLECTION=true

# Battle Plan Generator (P1-B)
PAPER_TRADING_ENABLE_BATTLE_PLAN=true

# Auto SLTP Calculator (P1-A)
PAPER_TRADING_ENABLE_AUTO_SLTP=true

# Content & Push (P2)
PAPER_TRADING_PUSH_LARK=false
PAPER_TRADING_PUSH_DINGTALK=false
```

#### 集成测试场景

```python
def test_full_pm_agent_loop():
    """Test full loop: PM decision -> order -> fill -> reflection -> memory.

    1. Create test account with 1000 yuan
    2. Trigger PM Agent decision
    3. Verify order created
    4. Simulate fill
    5. Verify reflection note generated
    6. Trigger second PM decision
    7. Verify reflection memory injected into context
    """


def test_cancel_modify_flow():
    """Test order cancel and modify flow.

    1. Place limit order
    2. Cancel order
    3. Verify cash unfrozen
    4. Place another limit order
    5. Modify order (change price)
    6. Verify audit chain (parent_order_id)
    """


def test_battle_plan_generation():
    """Test daily battle plan generation.

    1. Set up account with positions
    2. Trigger battle plan generation
    3. Verify three scenarios + candidates + SLTP
    4. Verify persistence to PaperBattlePlan table
    """
```

---

## 四、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| PM Agent 调用 LLM 成本高 | 可配置决策周期（默认 60s），避免每 tick 都调用 |
| 复盘触发可能阻塞交易 | ReflectionEngine 用 daemon 线程异步执行，不阻塞主流程 |
| Agent 决策不一致 | 通过 system prompt 强约束 + 记忆系统保持一致性 |
| 数据源依赖 | Fib/ATR 依赖日线数据，MarketListener 已有 DataFrame 缓存机制 |
| LLM 不可用时降级 | PM Agent 失败时 fallback 到规则引擎（已有 strategies_v2），不阻塞交易 |

---

## 五、与文章功能对标矩阵

| 文章功能 | 对应任务 | 是否超越 |
|---------|---------|---------|
| AI 自主挂限价单 | P0-B | ✅ 超越（不止 Fib，还能综合多指标） |
| AI 主动撤单 | P0-C | ✅ 超越（支持改单，文章只撤不改） |
| AI 复盘反思 | P0-D | ✅ 超越（结构化三段式 + 持久化） |
| 基于复盘的策略迭代 | P0-E | ✅ 超越（记忆系统自动注入上下文） |
| Fib 回撤指标 | P0-A | ✅ 超越（+ATR + 筹码峰 + 支撑阻力） |
| 止损放基地下沿 | P1-A | ✅ 超越（三位一体自动计算） |
| 给价格吸筹空间 | P1-B | ✅ 超越（三情景预案覆盖） |
| 等待成交验证 | MarketListener | ✅ 已有 |

---

## 六、交付里程碑

| 里程碑 | 任务 | 验收 |
|--------|------|------|
| M1: AI 自主决策闭环 | P0-A, P0-C, P0-B | AI 能自主下单/撤单/改单 |
| M2: 复盘反思系统 | P0-D, P0-E | 交易后自动复盘 + 记忆影响后续决策 |
| M3: 智能止损止盈 | P1-A | 下单后自动三线 |
| M4: 次日作战卡 | P1-B, P1-C | 收盘后自动生成作战卡 |
| M5: 对外能力 | P3-A, P3-B | API + WebUI 完整 |
| M6: 内容沉淀 | P2-A, P2-B, P3-C | 文章生成 + 推送 + 文档 |

---

**文档结束。** 任务清单已详细到函数级，可作为开发依据。确认后按批次 1（M1+M2）顺序开工。
