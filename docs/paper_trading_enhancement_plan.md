# 模拟交易功能增强实施计划

> 制定时间：2026-07-27  
> 基线版本：commit `0429e94`（已合并 origin/main 后的 main 分支）  
> 目标：在当前 95-98% 已实现的基础上，补齐四类增强能力，形成可独立 PR 的最小单元。

---

## 一、总体目标

基于 `docs/paper_trading_gap_analysis_v2.md` 当前结论，模拟交易核心闭环已打通。本计划聚焦四类增强：

1. **API 与订单能力补全**：批量下单、条件单、订单查询筛选。
2. **风控与绩效分析**：新增绩效分析模块、持仓集中度实时监控、回撤/夏普等关键指标。
3. **策略规则引擎增强**：扩展技术指标、支持多时间框架、策略模板与回测联动。
4. **WebUI 集成与测试**：把新增能力接入前端页面，并补充 Playwright 自动化测试覆盖。

所有改动遵循 AGENTS.md 的目录边界与验证矩阵，commit message 使用英文，默认不触发 tag（需要时由 PR 标题添加 `#minor`/`#patch`）。

---

## 二、实施原则

- 每个 Phase 可独立成 PR，避免单次 diff 过大。
- 先写 schema / 类型 / 接口契约，再写实现，最后补测试。
- 后端新增 Python 模块优先放在 `paper_trading/`；新增 API 放在 `api/v1/endpoints/paper_trading.py`。
- WebUI 改动在 `apps/dsa-web/`，新增组件优先复用现有 `Card`、`Button`、`Table` 等基础组件。
- 涉及配置项必须同步更新 `.env.example` 和相关文档。
- 每个 Phase 完成后执行：
  - 后端：`python -m py_compile <changed_files>` + `pytest tests/test_paper_trading_*.py`
  - 前端：`npm run lint && npm run build && npm run test:e2e`

---

## 三、Phase 1：API 与订单能力补全 ✅ 已完成

### 3.1 目标

补齐订单侧的高级能力：批量下单、条件单（止损/止盈/OCO）、订单列表筛选，同时保持现有状态机不变。

### 3.2 新增/修改文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `api/v1/schemas/paper_trading.py` | 新增 | `BatchOrderCreateRequest`、`BatchOrderResponse`、`ConditionalOrderCreateRequest`、`ConditionalOrderItem`、`OrderListFilterParams` |
| `paper_trading/order.py` | 修改 | `OrderType` 增加 `stop_loss`/`take_profit`/`oco`；`OrderRequest` 增加 `trigger_price`、`linked_order_id`；`OrderManager` 增加 `create_conditional_order`、`match_conditional_orders` |
| `paper_trading/trading_engine.py` | 修改 | `_execute_*` 支持条件单触发；`tick_market_price` 或 listener 调用 `match_conditional_orders` |
| `api/v1/endpoints/paper_trading.py` | 新增端点 | `POST /orders/batch`、`POST /orders/conditional`、`GET /orders` 支持筛选参数 |
| `tests/test_paper_trading_orders_advanced.py` | 新增 | 覆盖批量、条件单触发、OCO 联动取消 |

### 3.3 关键函数/任务清单

1. `OrderType` 扩展枚举（market / limit / stop_loss / take_profit / oco_primary / oco_secondary）。
2. `OrderRequest` 增加可选字段：`trigger_price: Optional[float]`、`linked_order_id: Optional[int]`、`parent_order_id: Optional[int]`。
3. `OrderManager.create_conditional_order(account_id, req)`：创建条件单，状态为 `conditional`，不冻结资金/持仓（或按规则选择性冻结）。
4. `OrderManager.match_conditional_orders(account_id, code, price, session)`：遍历未触发条件单，按触发价转为 market/limit 单或执行。
5. `OrderManager.create_batch_orders(account_id, requests)`：事务批量创建订单，返回每个子订单结果。
6. `TradingEngine._match_conditional_orders(self, account_id, code, price)`：在 listener 推送价格时调用。
7. API 端点：
   - `POST /api/v1/paper-trading/orders/batch`
   - `POST /api/v1/paper-trading/orders/conditional`
   - `GET /api/v1/paper-trading/accounts/{id}/orders?status=&side=&code=&from=&to=&limit=&offset=`
8. `PaperOrder` ORM 若缺少 `trigger_price`/`linked_order_id` 字段则新增 migration 或 schema 更新（当前仓库使用 SQLAlchemy create_all，可直接加字段）。
9. 测试：
   - 批量 3 个买单全部成交
   - 止损单在价格跌破触发价后转为 market 单成交
   - OCO 一对：一个触发后另一个自动取消

### 3.4 验收标准

- ✅ `pytest tests/test_paper_trading_orders_advanced.py` 全部通过（6/6）。
- ✅ paper_trading 相关测试集全部通过（70/70）。
- ✅ API schema 能通过 FastAPI `/docs` 自动渲染。
- ✅ 不影响现有 `test_paper_trading_cancel_modify.py` 用例。
- ✅ `python -m py_compile` 对新增/修改文件无编译错误。

> 实施备注：条件单触发后执行市价卖出时，会按 T+1 规则检查 `available_quantity`。因此止损/止盈测试在买入后调用 `daily_roll_available()` 使持仓可用，符合 A 股真实交易 semantics。

---

## 四、Phase 2：风控与绩效分析 ✅ 已完成

### 4.1 目标

新增独立的绩效分析模块，提供账户级别和持仓级别的风险指标，并暴露 API / WebUI。

### 4.2 新增/修改文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `paper_trading/performance.py` | 新增 | `PerformanceAnalyzer`、`PerformanceMetrics`、`DrawdownRecord` |
| `paper_trading/risk.py` | 修改 | 增加实时集中度检查 API、单日亏损上限检查 |
| `api/v1/schemas/paper_trading.py` | 新增 | `PerformanceMetricsResponse`、`DrawdownItem`、`RiskMetricsResponse` |
| `api/v1/endpoints/paper_trading.py` | 新增端点 | `GET /accounts/{id}/performance`、`GET /accounts/{id}/risk-metrics` |
| `tests/test_paper_trading_performance.py` | 新增 | 覆盖绩效指标计算 |

### 4.3 关键函数/任务清单

1. `PerformanceMetrics` dataclass：
   - `total_return_pct`
   - `annualized_return_pct`
   - `sharpe_ratio`
   - `max_drawdown_pct`
   - `max_drawdown_start_date` / `end_date`
   - `volatility_annualized`
   - `win_rate`
   - `profit_factor`
   - `avg_win` / `avg_loss`
   - `calmar_ratio`
2. `PerformanceAnalyzer.__init__(db_manager)`。
3. `PerformanceAnalyzer.calculate(account_id, start_date, end_date) -> PerformanceMetrics`：
   - 从 `PaperNetValue` 读取净值序列计算收益/回撤/波动。
   - 从 `PaperTrade` 读取成交记录计算胜率、盈亏比。
4. `PerformanceAnalyzer.get_drawdown_curve(account_id) -> list[DrawdownRecord]`。
5. `RiskChecker` 新增：
   - `_check_daily_loss_limit(account_id, price, quantity)`：基于当日已实现亏损限制。
   - `_check_sector_concentration(...)`：若持仓带行业标签，则限制行业集中度（可选，先占位）。
6. API 端点：
   - `GET /api/v1/paper-trading/accounts/{id}/performance?start_date=&end_date=`
   - `GET /api/v1/paper-trading/accounts/{id}/risk-metrics`
7. WebUI（Phase 4 接入）：在账户摘要旁新增 "绩效" 卡片，展示夏普、最大回撤、胜率。
8. 测试：
   - 给定已知净值序列，验证最大回撤计算正确。
   - 给定 3 盈 2 亏交易，验证胜率、盈亏比。

### 4.4 验收标准

- ✅ `pytest tests/test_paper_trading_performance.py` 全部通过（11/11）。
- ✅ 绩效指标计算与手动预期结果一致（误差 < 1e-6）。
- ✅ paper_trading 相关测试集全部通过（29/29，含 Phase 1 订单与 E2E）。
- ✅ `python -m py_compile` 对新增/修改文件无编译错误。

> 实施备注：
> - `PerformanceAnalyzer` 查询 `PaperNetValue`/`PaperTrade` 后在会话内提取为普通 tuple/dict，避免 ORM detached instance 跨 session 访问。
> - 测试中使用 `PositionManager.apply_buy(acc_id, code, qty, price, name=...)` 正确签名，并更新集中度期望以匹配 `apply_buy` 不扣减现金的子系统语义。
> - 单日亏损限制基于 `PerformanceAnalyzer` 当日已实现亏损 + 本次卖出预估亏损，配置项 `max_daily_loss_pct` 已在 `RiskConfig` / `PaperTradingService` 中生效。

---

## 五、Phase 3：策略规则引擎增强 ✅ 已完成

### 5.1 目标

在现有 `strategies_v2/` 基础上扩展技术指标、支持多时间框架、提供常用策略模板，并与回测模块联动。

### 5.2 新增/修改文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `strategies_v2/indicators.py` | 修改 | 新增 OBV、Stochastic、CCI、Williams %R、Volume MA、VWAP；修复 `IndicatorSpec.parse` 缺省周期与复合指标名识别 |
| `strategies_v2/schema.py` | 修改 | `RuleStrategy` 增加 `timeframes: List[str]`、`template: Optional[str]`；构造时自动纳入规则引用的指标 |
| `strategies_v2/templates.py` | 新增 | 预置策略模板：`golden_cross`、`rsi_reversal`、`boll_breakout`、`macd_momentum` |
| `strategies_v2/rule_engine.py` | 修改 | 新增 `RuleEngine.evaluate_multi_timeframe`，所有周期信号一致才触发（AND 语义） |
| `strategies_v2/__init__.py` | 修改 | 导出 `TEMPLATES`、`get_template` |
| `paper_trading/market_listener.py` | 修改 | listener 支持日线数据重采样为周线/月线，按策略 `timeframes` 喂给引擎 |
| `src/config.py` / `.env.example` | 修改 | 新增 `PAPER_TRADING_STRATEGY_TIMEFRAMES` 配置项 |
| `tests/test_strategies_v2_phase3.py` | 新增 | 覆盖 Phase 3 指标解析、计算、模板、多周期评估与 MarketListener 数据链路 |

### 5.3 关键函数/任务清单

1. 指标计算：
   - `compute_obv(df) -> pd.Series`
   - `compute_stochastic(df, k_period=14, d_period=3) -> Dict[str, pd.Series]`（%K、%D）
   - `compute_cci(df, period=20) -> pd.Series`
   - `compute_williams_r(df, period=14) -> pd.Series`
   - `compute_volume_ma(df, period=20) -> pd.Series`
   - `compute_vwap(df) -> pd.Series`
2. `IndicatorSpec.parse` 扩展：`obv`、`sto`、`sto_k`、`sto_d`、`cci20`、`wr14`、`vma20`、`vwap`、`boll_upper`/`boll_lower`/`macd_hist` 等复合输出名。
3. `RuleStrategy` dataclass 增加：
   - `timeframes: List[str] = field(default_factory=lambda: ["1d"])`
   - `template: Optional[str] = None`
4. `strategies_v2/templates.py`：
   - `golden_cross_template()` -> RuleStrategy
   - `rsi_reversal_template()`
   - `boll_breakout_template()`
   - `macd_momentum_template()`
5. `RuleEngine.evaluate_multi_timeframe(strategy, data: Dict[str, pd.DataFrame], code, name)`：要求所有周期的 entry/exit 规则同时满足才触发信号（AND 语义）。
6. `MarketListener._get_strategy_data(code, timeframes)` 拉取日线并按配置重采样为周线/月线，喂给多周期 evaluate。
7. 配置项：`.env.example` 增加 `PAPER_TRADING_STRATEGY_TIMEFRAMES=1d,1w`。
8. 测试：
   - 每个模板可序列化/反序列化且 `template` 字段保留。
   - 多周期策略仅在两个周期都满足时才产生信号；任一周期反向或无信号时不触发。

### 5.4 验收标准

- ✅ `pytest tests/test_strategies_v2_phase3.py` 全部通过（34/34）。
- ✅ paper_trading 相关测试集全部通过（81/81，含 Phase 1/2 用例）。
- ✅ 不影响现有 `test_paper_trading_indicators.py` 用例。
- ✅ `python -m py_compile` 对新增/修改文件无编译错误。

> 实施备注：
> - `compute_stochastic` 在滚动窗口未满时返回 `NaN`，避免 warm-up 段被误判为 50.0 中性信号。
> - `RuleStrategy` 直接构造（如单元测试）时自动把规则引用的指标加入 `indicators`，保证 `RuleEngine.evaluate` 有数据可用。
> - `MarketListener` 通过 `pandas.resample` 将日线聚合为周线/月线，对 unsupported timeframe 返回 `None` 并跳过该策略。

---

## 六、Phase 4：WebUI 集成与 Playwright 测试 ✅ 已完成

### 4.1 目标

把 Phase 1-3 的新能力接入前端，并补充端到端测试。

### 4.2 新增/修改文件

| 文件 | 动作 | 说明 |
|------|------|------|
| `apps/dsa-web/src/types/paperTrading.ts` | 修改 | 新增批量下单、条件单、绩效指标、风险指标类型 |
| `apps/dsa-web/src/api/paperTrading.ts` | 修改 | 新增批量下单、条件单、绩效/风险 API 调用 |
| `apps/dsa-web/src/pages/PaperTradingPage.tsx` | 修改 | 订单表单增加 single/batch/conditional 三种模式；新增 "绩效" 卡片；订单表格增加筛选 |
| `apps/dsa-web/e2e/paper-trading.spec.ts` | 修改 | 扩展 mock 路由与测试用例，覆盖绩效卡片、条件单、批量下单、订单筛选 |

### 4.3 关键函数/任务清单

1. 类型扩展：
   - `BatchOrderCreateRequest`、`BatchOrderResponse`
   - `ConditionalOrderCreateRequest`、`ConditionalOrderItem`
   - `PerformanceMetricsResponse`、`RiskMetricsResponse`
2. API 客户端：
   - `paperTradingApi.submitBatchOrders(params)`
   - `paperTradingApi.createConditionalOrder(params)`
   - `paperTradingApi.getPerformanceMetrics(accountId, params)`
   - `paperTradingApi.getRiskMetrics(accountId)`
3. `PaperTradingPage`：
   - 在账户摘要与净值曲线下方新增 `PerformanceCard`（数据来自 Phase 2 API），展示总收益率、夏普、最大回撤、胜率及集中度/回撤风险指标。
   - `OrderForm` 增加 single/batch/conditional 三种模式切换。
   - `OrdersTable` 顶部增加 status / side / code 筛选栏，实时显示匹配数量。
4. Playwright 测试：
   - 验证绩效卡片展示夏普比率、最大回撤、胜率。
   - 提交止损条件单并验证 "CONDITIONAL CREATED" 反馈。
   - 提交两笔批量订单并验证每笔执行结果。
   - 验证订单按 status、side、code 筛选后表格行数变化。
5. mock 数据：在 `e2e/paper-trading.spec.ts` 中统一 mock Phase 1-3 的新端点。

### 4.4 验收标准

- ✅ `npm run lint` 通过。
- ✅ `npm run build` 通过。
- ✅ `npm run test:e2e` 全部通过（paper-trading 10/10，其余 12 个因未启动后端而 skip）。

> 实施备注：
> - 绩效/风险指标中的胜率、集中度、当前回撤为纯比例数值，前端直接格式化为 `xx.xx%`，不使用 `formatPct` 的 `+` 号前缀，避免收益率语义误导。
> - 修复了构建阶段发现的 TypeScript 类型问题：`BatchOrderResponse` 未导入、`batchResult.results.map` 参数隐式 `any`。
> - mock 数据 `win_rate` 与后端保持一致，使用百分比数值（55 而非 0.55）。

---

## 七、Phase 5：文档与配置同步 ✅ 已完成

### 5.1 目标

每完成一个 Phase 后同步更新文档，避免规则漂移。

### 5.2 关键任务

1. `.env.example`：
   - `PAPER_TRADING_STRATEGY_TIMEFRAMES=1d`：已存在，策略未声明 timeframes 时的默认周期。
   - `PAPER_TRADING_DAILY_LOSS_LIMIT_PCT=0.05`：已存在，单日最大已实现亏损占初始本金比例。
   - `PAPER_TRADING_ENABLE_CONDITIONAL_ORDERS=true`：**未引入**。代码中条件单能力默认随纸面交易子系统启用，无独立开关，故不增加无实际消费方的配置项。
2. `docs/CHANGELOG.md`：
   - `[Unreleased]` 段已扁平追加 Phase 1-4 全部条目，并新增一条 `[测试] 扩展 apps/dsa-web/e2e/paper-trading.spec.ts` 记录 Playwright 覆盖。
3. `docs/paper_trading_enhancement_plan.md`：
   - 各 Phase 进度已同步更新，Phase 1-5 全部标记完成。

### 5.3 验收标准

- ✅ `.env.example` 中所有实际被 `src/config.py` 读取的 `PAPER_TRADING_*` 变量均有说明或默认值。
- ✅ CHANGELOG `[Unreleased]` 段保持扁平格式，无小标题，类型取值符合规范。
- ✅ 增强计划与当前代码状态一致，无未关闭 Phase。

---

## 八、依赖与风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 条件单触发依赖 MarketListener 实时价格，测试环境可能无实时数据 | 测试不稳定 | 在测试中使用 `TradingEngine.tick_market_price` 手动触发 |
| 多时间框架需要不同周期历史数据，数据源可能不支持 | Phase 3 进度 | 先实现日线 + 周线，其他周期用日线重采样 fallback |
| 前端新增组件可能破坏现有响应式布局 | WebUI | 每个组件单独测试，Playwright 覆盖关键路径 |
| 绩效计算涉及浮点精度 | Phase 2 测试 | 使用 `pytest.approx` 设置合理容差 |

---

## 九、建议执行顺序

1. **Phase 1** 与 **Phase 2** 可并行开发（文件交集小）。
2. **Phase 3** 依赖 Phase 1 的订单增强不多，可并行，但建议在其后合入，避免同时改 listener。
3. **Phase 4** 必须等 Phase 1-3 API 稳定后才开始。
4. **Phase 5** 在每个 Phase 合入时同步完成。

---

## 十、下一步行动

选择以下任一选项继续：

1. ~~**开始 Phase 1**：批量下单与条件单已实现并验证。~~
2. ~~**开始 Phase 2**：绩效分析模块、风险 API 与测试已实现并验证。~~
3. ~~**开始 Phase 3**：扩展策略规则引擎指标与模板已实现并验证。~~
4. ~~**开始 Phase 4**：WebUI 集成与 Playwright 测试已实现并验证。~~
5. ~~**开始 Phase 5**：文档与配置同步（`.env.example`、CHANGELOG 最终整理）已实现并验证。~~
6. **调整计划**：你指定优先级或增删功能，我更新本文档。

当前推荐：**按 PR 节奏进入 review / commit**，或拆分/合并本次纸面交易增强分支。
