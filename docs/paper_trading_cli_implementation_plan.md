# Paper Trading CLI — 实时量化交易系统命令行实施计划

> **目标**：为实时量化交易系统开发完整的 CLI 命令行接口，覆盖从账户管理、策略评估、回测分析、实时监听、风险监控到订单执行的**全部量化交易操作**。
> **实现原则**：复用现有 `paper_trading/` 模块和 `build_default_listener` 工厂函数，最小化新代码引入；所有 CLI 命令通过 `argparse` 子命令扩展 `main.py` 入口。

---

## Phase 0：基础架构层（1 day）

### Step 0-1：CLI 入口框架

**上下文**：当前 `main.py` 已支持 `--backtest`、`--market-review` 等离散模式，但 paper_trading 没有专门的 CLI 入口。需要在 `parse_arguments()` 中新增 `paper-trading` 子命令组。

**任务清单**：

- [ ] 在 `parse_arguments()` 中新增 `subparsers`，创建 `paper-trading` 子命令命名空间
- [ ] 子命令分组：`account`（账户管理）、`strategy`（策略）、`backtest`（回测）、`listen`（实时监听）、`risk`（风控）、`order`（交易信号）
- [ ] 实现 `run_paper_trading_cli(args)` 分发函数，根据子命令路由到对应处理器

**验证**：`python main.py paper-trading --help` 输出完整子命令树

---

## Phase 1：核心交易操作层（3 days）

### Step 1-1：账户管理 CLI

**上下文**：`paper_trading/account.py` 提供 `PaperAccountManager`，`paper_trading/broker/router.py` 提供 `BrokerRouter`。

**CLI 接口**：

```bash
# 创建虚拟账户
python main.py paper-trading account create --name=my-account --capital=100000
# 列出所有账户
python main.py paper-trading account list
# 查看账户详情
python main.py paper-trading account show --account-id=1
# 查看账户持仓
python main.py paper-trading account positions --account-id=1
# 查看账户订单
python main.py paper-trading account orders --account-id=1
# 查看账户成交记录
python main.py paper-trading account trades --account-id=1
# 查看账户净值曲线
python main.py paper-trading account net-value --account-id=1 --days=90
# 删除账户
python main.py paper-trading account delete --account-id=1
```

**任务清单**：

- [ ] 实现 `AccountCreateAction`、`AccountListAction`、`AccountShowAction`、`AccountPositionsAction`
- [ ] 实现 `AccountOrdersAction`、`AccountTradesAction`、`AccountNetValueAction`、`AccountDeleteAction`
- [ ] 提供 `--output-format=json|table|color` 参数控制格式化输出
- [ ] 颜色化终端输出：买入绿、卖出红、持仓蓝

**依赖**：Step 0-1

---

### Step 1-2：策略管理 CLI

**上下文**：`paper_trading/strategies/` 提供 15 个 YAML 策略模板和 `RuleEngine` 评估器。

**CLI 接口**：

```bash
# 列出所有策略
python main.py paper-trading strategy list
# 查看策略详情（规则、指标、参数）
python main.py paper-trading strategy show --name=ma_golden_cross
# 导入策略（自定义 YAML）
python main.py paper-trading strategy import --file=./my_strategy.yaml
# 创建策略模板
python main.py paper-trading strategy scaffold --name=my_strategy
# 评估策略（单股单策略，快照判断）
python main.py paper-trading strategy evaluate --name=ma_golden_cross --code=600519
# 查看策略状态转换
python main.py paper-trading strategy lifecycle --list --account-id=1
```

**任务清单**：

- [ ] 实现 `StrategyListAction`（读取 `paper_trading/strategies/configs/` YAML 目录）
- [ ] 实现 `StrategyShowAction`（YAML 渲染 + 规则/指标树形展示）
- [ ] 实现 `StrategyImportAction` / `StrategyScaffoldAction`
- [ ] 实现 `StrategyEvaluateAction`（调用 `RuleEngine.evaluate` 对指定股票快照判断）
- [ ] 实现 `StrategyLifecycleAction`（展示七阶段状态机）

**依赖**：Step 0-1

---

### Step 1-3：回测 CLI

**上下文**：`paper_trading/backtest/engine.py` 提供完整的 `BacktestEngine`（逐 bar 模拟 + 滑点 + 手续费 + 涨跌停），`paper_trading/backtest/walkforward.py` 提供 `WalkforwardOptimizer`。

**CLI 接口**：

```bash
# 运行回测
python main.py paper-trading backtest run \
  --strategy=ma_golden_cross --codes=600519,000001 \
  --start=2023-01-01 --end=2024-12-31 --capital=100000
# 展示回测结果（指标 + 每日 snapshot）
python main.py paper-trading backtest result --result-id=1
# 列出历史回测
python main.py paper-trading backtest list
# Walk-forward 优化
python main.py paper-trading backtest walk-forward \
  --strategy=ma_golden_cross --code=600519 \
  --train-days=504 --test-days=126 --step-days=63
# 参数扫射
python main.py paper-trading backtest grid-search \
  --strategy=ma_golden_cross --code=600519 \
  --fast=5,10,20 --slow=20,30,50
# 回测 vs 纸面对比
python main.py paper-trading backtest compare --account-id=1
```

**任务清单**：

- [ ] 实现 `BacktestRunAction`（构建 BacktestEngine，拉数据，跑回测，输出绩效报告）
- [ ] 实现 `BacktestResultAction` / `BacktestListAction`
- [ ] 实现 `WalkForwardAction`（调用 `WalkforwardOptimizer.run`）
- [ ] 实现 `GridSearchAction`（参数组合遍历）
- [ ] 格式化的终端绩效报告：Sharpe / MaxDD / Calmar / 胜率 / 收益曲线 ASCII 图

**依赖**：Step 0-1

---

## Phase 2：实时交易执行层（3 days）

### Step 2-1：实时监听 CLI

**上下文**：`paper_trading/market_listener.py` 的 `build_default_listener()` 封装了完整的监听器构造。需要 CLI 包装启动/停止/状态查询。

**CLI 接口**：

```bash
# 启动实时监听（前台，Ctrl+C 退出）
python main.py paper-trading listen start --account-id=1
# 后台启动（daemon 模式）
python main.py paper-trading listen start --account-id=1 --daemon
# 查看监听状态
python main.py paper-trading listen status --account-id=1
# 停止监听
python main.py paper-trading listen stop --account-id=1
```

**任务清单**：

- [ ] 实现 `ListenStartAction`：复用 `build_default_listener()`，注册信号处理退出
- [ ] 前台模式：实时仪表板输出（每 tick 刷新行情/持仓/信号/熔断/延迟）
- [ ] 后台模式：写入 PID 文件，通过 `listen stop` 发信号退出
- [ ] 实现 `ListenStatusAction`：查询运行/停止/上次 settle/代码数/策略数
- [ ] 实现 `ListenStopAction`：通过 PID 文件杀进程

**验证**：`python main.py paper-trading listen start` 启动后，终端每 500ms 刷新行情+信号+风控状态，Ctrl+C 优雅退出

**依赖**：Step 0-1

---

### Step 2-2：策略模式监听（Watch Mode）

**上下文**：在监听器运行的同时，支持**交互式操作**：调参、暂停策略、手动信号。

**CLI 接口**：

```bash
# 启动监听并进入交互 shell
python main.py paper-trading listen start --account-id=1 --interactive
# 交互模式下可用命令：
#   > status          # 打印当前状态
#   > pause ma_golden  # 暂停策略
#   > resume ma_golden # 恢复策略
#   > signal buy 600519 --price=18.50 --qty=100 --reason="手动入场"
#   > breaker         # 查看熔断状态
#   > latency         # 查看延迟统计
#   > positions       # 查看持仓
#   > quit             # 退出
```

**任务清单**：

- [ ] 在 `run_loop` 线程中启动一个独立 stdin 读取线程
- [ ] 实现 `CMDLLoop`：readline 风格，支持 TAB 补全
- [ ] 实现 pause/resume/signal/breaker/latency/positions 命令

**依赖**：Step 2-1

---

### Step 2-3：执行快照和即时决策

**上下文**：无需启动长监听器，立即获取当前市场快照并做出判断。

**CLI 接口**：

```bash
# 快速查询当前行情
python main.py paper-trading quote --codes=600519,300750
# 对指定股票立即跑策略评估（只判断，不执行）
python main.py paper-trading scan --codes=600519,300750 --strategy=ma_golden_cross
# 立即提交一个信号（手动下单）
python main.py paper-trading order submit \
  --account-id=1 --side=buy --code=600519 \
  --price=18.50 --quantity=100 --order-type=market
# 查看挂单
python main.py paper-trading order list --account-id=1
# 撤单
python main.py paper-trading order cancel --order-id=42
```

**任务清单**：

- [ ] 实现 `QuoteAction`（调用 `DataFetcherManager.get_realtime_quote`）
- [ ] 实现 `ScanAction`（调用 `RuleEngine.evaluate` 对所有 code×strategy 组合）
- [ ] 实现 `OrderSubmitAction` / `OrderListAction` / `OrderCancelAction`（调用 OMS）
- [ ] 格式化输出订单确认和成交回执

**依赖**：Step 0-1

---

## Phase 3：风控与监控层（2 days）

### Step 3-1：风控查询 CLI

**上下文**：`paper_trading/circuit_breaker.py`、`paper_trading/risk.py`、`paper_trading/risk_daemon.py` 完成全部风控模块。

**CLI 接口**：

```bash
# 查看熔断状态
python main.py paper-trading risk breaker --account-id=1
# 手动重置熔断
python main.py paper-trading risk breaker-reset --account-id=1
# 查看 VaR 报告
python main.py paper-trading risk var --account-id=1
# 查看流动性风险
python main.py paper-trading risk liquidity --account-id=1
# 查看市场异常状态
python main.py paper-trading risk anomaly --account-id=1
# 查看极端行情状态
python main.py paper-trading risk extreme-market
# 查看全链路延迟
python main.py paper-trading risk latency --account-id=1
```

**任务清单**：

- [ ] 实现 `BreakerStatusAction`（读取 `CircuitBreakerState`，展示四级状态+理由+触发时间）
- [ ] 实现 `BreakerResetAction`
- [ ] 实现 `VaRAction`、`LiquidityAction`、`AnomalyAction`（调用 `RiskDaemon` 方法）
- [ ] 实现 `ExtremeMarketAction`
- [ ] 实现 `LatencyAction`

**依赖**：Step 0-1

---

### Step 3-2：性能分析 CLI

**上下文**：`paper_trading/performance.py` 和 API 性能端点。

**CLI 接口**：

```bash
# 查看绩效指标
python main.py paper-trading performance --account-id=1
# 查看回撤曲线（ASCII chart）
python main.py paper-trading performance drawdown --account-id=1
# 查看策略性能对比
python main.py paper-trading performance leaderboard --account-id=1
# 查看漂移检测
python main.py paper-trading performance drift --account-id=1
# 查看特征工程
python main.py paper-trading performance features --account-id=1
```

**任务清单**：

- [ ] 实现 `PerformanceMetricsAction`（Sharpe/MaxDD/Calmar/胜率/盈亏比表格）
- [ ] 实现 `DrawdownAction`（ASCII 回撤曲线 + 最大回撤区间标注）
- [ ] 实现 `LeaderboardAction`（多策略性能排名表）
- [ ] 实现 `DriftAction`、`FeaturesAction`

**依赖**：Step 0-1

---

### Step 3-3：健康检查 CLI

**上下文**：`src/services/health_check.py` 的 `HealthCheckDaemon`。

**CLI 接口**：

```bash
# 运行健康检查
python main.py paper-trading health
# JSON 格式输出
python main.py paper-trading health --format=json
```

**任务清单**：

- [ ] 触发所有注册的检查项，输出表格化结果
- [ ] 支持 `--format=json` 输出（用于自动化脚本/CI）

**依赖**：Step 0-1

---

## Phase 4：固定收益扩展层（3 days）

> **目标**：扩展现有股票量化系统，支持固定收益品种（国债/可转债/债券 ETF）的分析与交易。

### Step 4-1：固定收益数据接入

**上下文**：现有 `data_provider/` 多源适配器架构支持新增 Fetcher。

**CLI 接口**：

```bash
# 查询国债收益率曲线
python main.py paper-trading bond yield-curve
# 查询可转债列表及指标
python main.py paper-trading bond convertible-list
# 查询债券 ETF 行情
python main.py paper-trading bond etf-quote --codes=511010,511260
```

**任务清单**：

- [ ] 新增 `data_provider/bond_fetcher.py`（akshare `bond_zh_us_rate` + `bond_cb_jsl` 等数据源）
- [ ] 实现 `BondYieldCurveAction`（终端 ASCII 收益率曲线图）
- [ ] 实现 `BondConvertibleListAction`（可转债转股溢价率/纯债价值/期权价值表）
- [ ] 实现 `BondETFQuoteAction`

**依赖**：Step 0-1

---

### Step 4-2：固定收益策略引擎

**上下文**：复用 `paper_trading/strategies/engine/rule_engine.py` 的规则评估框架。

**CLI 接口**：

```bash
# 评估债券策略
python main.py paper-trading bond strategy-evaluate --name=bond_spread --code=511010
# 固定收益回测
python main.py paper-trading bond backtest --code=511010 --start=2023-01-01
```

**任务清单**：

- [ ] 新增固定收益策略模板（`bond_spread.yaml`：久期/凸性/信用利差）
- [ ] 复用 `BacktestEngine` 跑固收回测（需要现金流处理而非 K 线）
- [ ] 实现 `BondStrategyEvaluateAction` / `BondBacktestAction`

**依赖**：Step 4-1

---

### Step 4-3：组合优化 CLI

**上下文**：为股票+固定收益混合组合提供均值-方差优化和风险平价的基础功能。

**CLI 接口**：

```bash
# 均值-方差优化
python main.py paper-trading portfolio optimize \
  --codes=600519,511010,511260 --method=mean-variance --target-return=8
# 风险平价
python main.py paper-trading portfolio optimize \
  --codes=600519,511010,511260 --method=risk-parity
# 有效前沿
python main.py paper-trading portfolio efficient-frontier \
  --codes=600519,511010,511260 --points=20
```

**任务清单**：

- [ ] 新增 `paper_trading/portfolio/` 子包
- [ ] 实现均值-方差优化（`numpy.linalg.solve` + 协方差矩阵）
- [ ] 实现风险平价（`risk_parity_weights`）
- [ ] 实现有效前沿计算
- [ ] ASCII 图渲染有效前沿曲线

**依赖**：Step 4-1

---

## 全量任务汇总

| Phase | ID | 内容 | 工作量 | 依赖 |
|-------|-----|------|--------|------|
| P0 | CLI-001 | argparse 子命令框架 | S | 无 |
| P1 | CLI-002 | 账户管理 CLI | M | CLI-001 |
| P1 | CLI-003 | 策略管理 CLI | M | CLI-001 |
| P1 | CLI-004 | 回测 CLI | L | CLI-001 |
| P2 | CLI-005 | 实时监听 CLI (start/stop/status) | L | CLI-001 |
| P2 | CLI-006 | 交互式监听 (watch mode) | M | CLI-005 |
| P2 | CLI-007 | 执行快照 & 即时决策 | M | CLI-001 |
| P3 | CLI-008 | 风控查询 CLI | M | CLI-001 |
| P3 | CLI-009 | 性能分析 CLI | M | CLI-001 |
| P3 | CLI-010 | 健康检查 CLI | S | CLI-001 |
| P4 | CLI-011 | 固定收益数据接入 | M | CLI-001 |
| P4 | CLI-012 | 固定收益策略引擎 | M | CLI-011 |
| P4 | CLI-013 | 组合优化 CLI | M | CLI-011 |

**总计**：12 个工作日（单人），8-10 个工作日（双人可并行）。

**并行可选项**（无文件冲突）：
- Phase 1 完成后：CLI-002 / CLI-003 / CLI-004 可并行
- Phase 2 完成后：CLI-008 / CLI-009 / CLI-010 可并行
- Phase 4 完成后：CLI-012 / CLI-013 可并行

**关键路径**：CLI-001 → CLI-002 → CLI-005 → CLI-006（前后依赖，不可并行）

---

## 毫秒级执行路径（CLI 视角）

```
CLI 用户输入指令
  → argparse 解析子命令
  → 调用对应的 Action（复用现有模块）
  → 如果是实时监听的启动命令：
      → build_default_listener() 构造 MarketListener
      → CircuitBreaker + RiskDaemon + SignalFusion 自动注入
      → MarketListener.start() 启动 daemon 线程
      → 前台模式：实时刷新终端仪表板
         行情 → RuleEngine → SignalFusion → CircuitBreaker → OMS
      → 后台模式：PID 文件 + 日志落盘
  → 如果是快照/扫描/回测：
      → 直接调用对应模块，格式化输出到终端
      → 单次执行结果即时返回
```

---

## 验证矩阵

| Phase | 验证项 | 方式 | 通过标准 |
|-------|--------|------|---------|
| P0 | CLI 入口 | `python main.py paper-trading --help` | 完整子命令树无报错 |
| P1 | 账户与策略操作 | `paper-trading account list` / `strategy list` | 数据正确展示 |
| P1 | 回测完整链路 | `backtest run --strategy=ma_golden --codes=600519` | 输出 Sharpe/MaxDD/Calmar/胜率 |
| P2 | 实时监听 | `listen start --account-id=1` 前台 | 仪表板每 500ms 刷新，Ctrl+C 退出 |
| P2 | 交互式监听 | `listen start --interactive` 暂停/恢复 | pause/resume 生效 |
| P2 | 即时决策 | `order submit` / `scan` | 信号提交后返回确认 |
| P3 | 风控可查询 | `risk breaker` / `risk var` | 四级状态展示 |
| P3 | 性能可量化 | `performance` / `drawdown` | 指标表格 + ASCII 图 |
| P4 | 固收数据可获取 | `bond yield-curve` | 收益率曲线正常渲染 |
| P4 | 组合优化可执行 | `portfolio optimize` | 权重向量输出，和为 1.0 |

---

*计划生成时间：2026-08-11 | 目标：股票+固定收益实时量化交易执行系统 CLI*
