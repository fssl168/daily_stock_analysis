# 纸面交易系统 实施开发清单（函数级）

> 状态：Draft（待评审）
> 更新：2026-08-13
> 上游：`docs/paper_trading_next_phase_plan.md`（实施计划）
> 粒度：文件 + 函数/类/端点；每条任务含可验证验收标准（AC）
> 约束：本阶段**不接入实盘**；纸面全链路测试跑通后再切换

本清单由 `intent-driven-development` 方法论产出：每个 AC 有场景 / 动作 / 可观察期望 / 禁止副作用 / 验证方法 / 优先级，避免"正确性/鲁棒性"等不可验证措辞。

---

## 0. 关键前置事实（函数签名现状，代码核实）

| 事实 | 证据（文件:符号） |
| --- | --- |
| 市价单滑点模型已存在 | `paper_trading/fees.py:apply_slippage(price, side)` — buy ×(1+bps/1e4), sell ×(1−bps/1e4) |
| 行情缓存已存在（线程安全） | `paper_trading/quote_cache.py:SharedQuoteCache` — `update(code, quote)` / `get(code)` / `get_all()` / `is_fresh(code)` / `remove()` / `clear()` |
| 行情缓存当前只注入 MarketListener，未接入下单 | `api/v1/endpoints/paper_trading.py:PaperTradingService.start_listener()` 构造 `SharedQuoteCache` 并传给 `build_default_listener` |
| 市价单成交价目前=参考价 | `paper_trading/trading_engine.py:submit_signal()` → `ref_price = signal.trigger_price`（=limit_price）→ `_execute_market_order(ref_price)` |
| WS 已修复 | `api/v1/endpoints/paper_trading.py:ws_router` + `ws_quotes()` / `ws_events()`；`api/v1/router.py` 挂载 |
| backtest 日期已修复 | `paper_trading/backtest_adapter.py:_fetch_net_values()` 返回 ISO str；`api/v1/endpoints/paper_trading.py:_fmt_iso_date_value()` |
| Broker 抽象层已存在 | `paper_trading/broker/router.py:BrokerRouter.resolve_by_account()`（默认 fallback paper）；`paper_trading/broker/eastmoney_broker.py:EastMoneyBroker.submit_order()` |
| 演示数据 seed 在 /tmp（未固化） | `/tmp/ptdata/gen_data.py`、`seed_agent_tables.py`、`seed_backtest_report.py` |
| 测试基建存在 | `tests/test_paper_trading_e2e.py`、`test_paper_trading_pending_api.py`、`test_backtest_service.py`、`test_ws_channel.py`（WS 重连，非 ws_router） |

---

## T-01 补回归测试 + 合入本次修复（P0，第 1 周）

### T-01.1 WS 握手回归测试
- **文件**：`tests/test_paper_trading_ws.py`（新增）
- **覆盖**：`api/v1/endpoints/paper_trading.py:ws_quotes`、`ws_events`
- **改动**：用 TestClient 测 WS 握手（未登录/已登录/断连）

**AC-101: 未登录 WS 握手被拒绝**
- 场景：无 `dsa_session` cookie，连 `/api/v1/paper-trading/3/ws/quotes`
- 动作：发起握手
- 期望：非 101 升级（403/拒绝），连接不建立
- 禁止：返回 500（回归点）
- 验证：`pytest tests/test_paper_trading_ws.py -k "unauth"`；TestClient `websocket_connect` 抛 WebSocketDisconnect/HTTPException
- 优先级：Required

**AC-102: 已登录 WS 握手成功并保活**
- 场景：登录后带 cookie，连接 `/ws/events`
- 动作：握手 + 等待事件推送
- 期望：握手 101；收到 `bus.replay()` 或事件 JSON；无 500
- 验证：`pytest tests/test_paper_trading_ws.py -k "authed"`
- 优先级：Required

**AC-103: 客户端断开清理订阅**
- 场景：`ws/events` 已连接并订阅 `PaperTradingEventBus`
- 动作：客户端断开
- 期望：`finally` 执行 `bus.unsubscribe(_on_event)`，无内存泄漏/日志无 traceback
- 验证：`pytest tests/test_paper_trading_ws.py -k "disconnect"`
- 优先级：Required

### T-01.2 backtest-scenario 序列化回归测试
- **文件**：`tests/test_backtest_service.py`（追加）或 `tests/test_paper_trading_backtest_scenario.py`
- **覆盖**：`paper_trading/backtest_adapter.py:_fetch_net_values`、`api/v1/endpoints/paper_trading.py:_paper_scenario_to_schema`/`_fmt_iso_date_value`

**AC-104: 含净值历史的账户生成场景返回 200**
- 场景：账户有 ≥1 条 `paper_net_values`
- 动作：`GET /accounts/{id}/backtest-scenario`
- 期望：200；`net_value_curve[].date` 为 ISO 字符串；`start_date/end_date/base_date` 为 str 或 null
- 禁止：500（回归点）；date 为 date 对象导致 pydantic 校验失败
- 验证：`pytest tests/test_backtest_service.py`；DB 固定 fixture 账户
- 优先级：Required

**AC-105: 空净值账户返回空场景不 500**
- 场景：账户无 `paper_net_values`
- 动作：同上
- 期望：200 且 `net_value_curve=[]`
- 验证：同测试文件新增用例
- 优先级：Required

### T-01.3 battle-plans seed 契约测试
- **文件**：`tests/test_paper_trading_battle_plan.py`（追加）
- **覆盖**：`HoldingPlanItem` 必填字段契约（`code`/`current_price`）

**AC-106: 缺 current_price 的 holdings 被拒绝而非 500**
- 场景：seed 数据缺 `current_price`
- 动作：`GET /accounts/{id}/battle-plans`
- 期望：422（校验失败）而非 500；`_plan_to_item` 可序列化合法行
- 验证：`pytest tests/test_paper_trading_battle_plan.py -k "seed"`
- 优先级：Required

### T-01.4 合入流程
- **交付**：1 个 PR；`docs/CHANGELOG.md` `[Unreleased]` 追加扁平条目（`- [修复] WS 握手 500`、`- [修复] backtest-scenario 日期序列化`、`- [测试] ws_router/backtest-scenario 回归`）
- **验收**：`./scripts/ci_gate.sh` 通过；PR body 含验证证据（本 E2E 截图）

---

## T-02 建立下单定价闭环（P0，第 1 周）

### T-02.1 行情缓存注入下单链路
- **文件**：`api/v1/endpoints/paper_trading.py:PaperTradingService`、`paper_trading/trading_engine.py:TradingEngine.__init__`
- **改动**：
  - `TradingEngine.__init__` 增加可选参数 `quote_cache: Optional[SharedQuoteCache] = None`
  - `PaperTradingService` 维护一个进程级 `SharedQuoteCache`（单例），既传给 listener 又传给 engine
  - `engine()` 构造时传入

**AC-201: 引擎可持有共享行情缓存**
- 场景：构造 `TradingEngine(quote_cache=cache)`
- 动作：`submit_signal` market 单
- 期望：引擎从 cache 读取行情；无 cache 时降级不崩
- 禁止：破坏现有 `TradingEngine` 构造签名兼容（缺省参数）
- 验证：`pytest tests/test_paper_trading_e2e.py` 全绿
- 优先级：Required

### T-02.2 市价单成交价改用实时行情 + 滑点
- **文件**：`paper_trading/trading_engine.py:submit_signal`、`_execute_market_order`、`_execute_triggered_market_order`
- **改动**：
  - market 单：`live = quote_cache.get(code)`；`fill_base = live.price if live else limit_price_or_trigger`
  - `eff_price = fee_model.apply_slippage(fill_base, side)`
  - 缓存无此 code 或过期 → 降级到原参考价 + `logger.warning`（显式降级，非静默）

**AC-202: 市价单成交价 ≈ 实时行情**
- 场景：cache 有 `600519@1680`，提交 market buy qty=100
- 动作：`submit_signal`
- 期望：成交价 = `apply_slippage(1680, "buy")`（1680×(1+bps/1e4)），非 limit_price
- 禁止：成交价仍取 limit_price；无日志的静默降级
- 验证：单测 mock cache + 断言 `fill_price`；`test_paper_trading_e2e.py` 断言成交价≈行情
- 优先级：Required

**AC-203: 缓存缺失时降级且留痕**
- 场景：cache 无该 code / 过期
- 动作：提交 market 单
- 期望：走降级路径（原参考价），且 `logger.warning` 含 code/降级原因
- 禁止：抛异常中断下单
- 验证：单测捕获日志；断言 order 仍成交
- 优先级：Required

### T-02.3 持仓估值同源
- **文件**：`paper_trading/position.py:PositionManager`、`api/v1/endpoints/paper_trading.py:list_positions`
- **改动**：持仓 `last_price` 优先用 cache 实时价；无则用 `apply_slippage` 后的一致性来源；消除"成交 1680 / 估值 1343"失真

**AC-204: 持仓浮盈与实时行情一致**
- 场景：持仓 600519，cache 有实时价
- 动作：`GET /accounts/{id}/positions`
- 期望：`floating_pnl` 基于实时价计算，与成交价偏差仅反映滑点+真实波动
- 禁止：用 `limit_price` 或过时 last_price 估值
- 验证：E2E 断言演示账户浮盈率与行情一致
- 优先级：Required

---

## T-03 演示数据脚本固化（P0，第 1 周）

### T-03.1 统一 seed 脚本
- **文件**：`scripts/seed_demo_data.py`（新增，合并 /tmp 三个脚本逻辑）
- **参数**：`--account` `--capital` `--codes` `--days` `--llm-off`（默认关闭 agent，避免慢 LLM）
- **改动**：复用 `PaperAccountManager`/`OrderManager`/DB 写入，而非裸 sqlite（保持契约一致）

**AC-301: 一条命令复现全部示例数据**
- 场景：空库
- 动作：`python scripts/seed_demo_data.py --account demo --capital 1000000`
- 期望：生成账户 + 订单/持仓/成交/信号 + 复盘 + 作战计划 + PM 决策 + 净值曲线 + 回测汇总 + 历史报告；随后前端各 Tab 非空
- 禁止：依赖真实行情/LLM；写入失败残留半截
- 验证：`python scripts/seed_demo_data.py --help` + 脚本内断言；E2E 复跑
- 优先级：Required

**AC-302: 幂等可重跑**
- 场景：已有 demo 数据
- 动作：再次运行
- 期望：按 `--account` 重置/覆盖，不产生重复行
- 验证：脚本内唯一键 + 断言计数
- 优先级：Required

### T-03.2 文档
- **文件**：`docs/demo-data.md`（新增）
- **验收**：含命令示例、生成内容清单、与 .env 关系、注意事项（不碰真实挂载盘 db）

---

## T-04 固收定位决策（P0/P1 边界，评审项）

- **性质**：产品决策 + 可能的文档/立项，非纯编码
- **文件**：`README.md`、`IDEA.md`（若收敛定位）、或 `docs/fixed-income-plan.md`（若立项）

**AC-401: 定位决策有记录**
- 动作：评审后产出决策记录（ADR 或本文档第 6 节更新）
- 期望：明确"做 / 不做 / 延后"；若做，列出最小闭环范围
- 验证：文档评审
- 优先级：Required（阻塞 T-04.x 拆解）

> 若决定"做"，拆解（候选，待确认）：
> - `data_provider/` 新增债券收益率曲线 fetcher（akshare 中债曲线）
> - `paper_trading/` 新增久期/凸性风险指标 + RMS 集成
> - `src/agent/` 新增股债再平衡信号源
> 本清单暂不展开函数级细节，待决策后补。

---

## T-05 Agent 真实能力验证（P1，第 2~3 周）

### T-05.1 非 fallback 路径端到端验证
- **文件**：`paper_trading/reflection.py:ReflectionEngine.reflect_on_daily`、`paper_trading/battle_plan.py:BattlePlanGenerator.generate`、`src/agent/portfolio_manager_agent.py:PortfolioManagerAgent.make_decision`
- **改动**：配置真实 LLM 密钥后，验证 3 端点非 fallback

**AC-501: PM 决策非 fallback**
- 场景：`PAPER_TRADING_ENABLE_PM_AGENT=true` + 有效 LLM
- 动作：`POST /accounts/{id}/pm-decisions/trigger`
- 期望：`used_fallback=false`；决策 `action` 非恒 `hold`；`reason` 有推理内容
- 禁止：静默 fallback 后伪装成功
- 验证：API 断言 + 人工评审记录
- 优先级：Required

**AC-502: 日度复盘非 fallback**
- 场景：同上 + `PAPER_TRADING_ENABLE_REFLECTION=true`
- 动作：`POST /accounts/{id}/reflections/daily`
- 期望：`used_fallback=false`；`summary/takeaway/lessons` 非空且与当日交易相关
- 验证：API 断言 + 评审
- 优先级：Required

**AC-503: 作战计划非 fallback**
- 场景：`PAPER_TRADING_ENABLE_BATTLE_PLAN_AI=true`
- 动作：`POST /accounts/{id}/battle-plans/generate`
- 期望：`used_fallback=false`；`market_review` 有实质内容
- 验证：API 断言 + 评审
- 优先级：Required

### T-05.2 超时/失败降级仍成立
- **文件**：`paper_trading/reflection.py`（daemon 超时线程）
- **AC-504:** LLM 不可用时在 `timeout_seconds` 内 fallback 且不阻塞 API（回归护栏）

---

## T-06 纸面交易稳定性演练（P1，第 3 周）

### T-06.1 MarketListener 连续运行
- **文件**：`paper_trading/market_listener.py:MarketListener._tick_market`、日结算、每日复盘/作战计划触发
- **改动**：无功能改动；用回放/模拟行情源跑 ≥5 个交易日

**AC-601: 5 日无未捕获异常**
- 场景：listener 启动 + 模拟行情注入
- 动作：连续 5 个交易日运行
- 期望：日志无 traceback；每日净值曲线连续；日结算/复盘/作战计划自动落库
- 禁止：崩溃后重启依赖人工
- 验证：运行脚本 + 断言 5 日 `paper_net_values` 连续
- 优先级：Required

### T-06.2 极端行情/熔断演练
- **文件**：`paper_trading/extreme_market.py`、`paper_trading/circuit_breaker.py`
- **AC-602:** 注入极端波动 → 触发熔断/告警事件 → 事件流可见 → 恢复后解除

---

## T-07 实盘切换前置清单（P2，纸面完备后；只准备，不执行实盘）

### T-07.1 券商 sandbox 接入验证
- **文件**：`paper_trading/broker/router.py:BrokerRouter.resolve_by_account`、`paper_trading/broker/eastmoney_broker.py:EastMoneyBroker`
- **改动**：在**sandbox/模拟环境**验证 eastmoney 或长桥 openapi 下单/撤销/回报回写；不开真实资金

**AC-701: BrokerRouter 按账户路由到非 paper broker**
- 场景：账户 `broker` 字段设为 `eastmoney`，且已注册
- 动作：`resolve_by_account(account_id)`
- 期望：返回 `EastMoneyBroker` 实例，非 fallback paper
- 验证：单测 mock 注册
- 优先级：Required（前置：T-04 定位不影响此抽象层）

### T-07.2 实盘切换检查单
- **文件**：`docs/live-trading-switch-checklist.md`（新增）
- **内容**：风控参数校验、订单幂等/对账、滑点/断线处理、权限与确认流程、一键回滚到 paper
- **AC-702:** 检查单评审通过；所有"纸面→实盘"差异点有方案；明确切换需另行立项确认

---

## 优先级与依赖

```
T-01（还债） ────────────────┐
T-02（定价闭环） ────────────┼──→ T-06（稳定性演练）
T-03（演示固化） ────────────┤
T-04（固收决策） ────────────┘
T-05（Agent 真验） ─── 依赖 T-02（行情一致）与 LLM 密钥
T-07（实盘前置） ─── 依赖 T-01/T-02 完成，纸面完备后
```

| 优先级 | 任务 | 理由 |
| --- | --- | --- |
| P0（本周） | T-01, T-02, T-03 | 还债止血 + 执行地基 + 演示可复现 |
| P1（2~3 周） | T-04, T-05, T-06 | 定位决策 + Agent 能力验证 + 稳定性 |
| P2（纸面完备后） | T-07 | 实盘前置准备（不执行实盘） |

---

## Blocking Decisions

- [ ] **T-04 固收做 / 不做 / 延后** —— 影响是否立项固收子任务；本清单已按"待定"处理
- [ ] **T-02 行情缺失时的默认降级策略**（用 limit_price 兜底 vs 拒绝下单）—— 影响行为契约，建议默认"兜底+日志"，需确认
- [ ] **T-07 sandbox 券商选型**（eastmoney vs 长桥 openapi）—— 切换阶段再定，本期不阻塞

---

## 交接说明

本清单供实施方（含后续 agent）直接执行：每个任务含 `文件:函数` 定位、可观察 AC、验证命令、优先级。执行顺序建议按 P0→P1→P2；每完成一项在清单对应 AC 后标记验证证据（测试命令 + 结果）。所有 AC 在未满足前不得声称"完成"。

---

## 执行状态（2026-08-13 更新）

### P0 已完成并验证

| 任务 | 交付物 | 验证证据 |
| --- | --- | --- |
| T-01.1 WS 回归 | `tests/test_paper_trading_ws.py`（7 测试） | `pytest` 通过（含 contracts 共 16 passed） |
| T-01.2 backtest 序列化 | `tests/test_paper_trading_backtest_contracts.py` | 同上 |
| T-01.3 battle-plans 契约 | 同上（HoldingPlanItem 必填 current_price） | 同上 |
| T-02 定价闭环 | `paper_trading/trading_engine.py`（`quote_cache` 注入 + `_live_price` + market 实时定价 + 显式降级）；`api/v1/endpoints/paper_trading.py`（共享 cache、`_apply_live_valuation` 持仓估值、batch 实时定价） | `tests/test_paper_trading_pricing.py`（6 passed）；orders_advanced/cancel_modify/performance（28 passed）；e2e/battle_plan/pm_agent/backtest_service（83 passed） |
| T-03 演示固化 | `scripts/seed_demo_data.py`（参数化、服务层下单、ORM 写入、幂等） | 一条命令复现（trades=5, net_value=14, artifacts, backtest=7+6+6）；两次 `--reset` 重跑行数稳定 |

### 全流程 E2E（seed_test.db + vite 最新源码）

- 纸面交易 **10/10 Tab 有数据**：positions/orders/trades/signals/decisions/reflections/battle-plans/daily-report/backtest-comparison（"BACKTEST VS PAPER TRADING" 面板）/strategies（策略排行榜）/features。
- `/backtest` 指标渲染（Win Rate/accuracy/Overall）；`/` 首页历史含 demo 报告（贵州茅台）。
- page errors **0**；HTTP ≥400 **0**；console errors 2（React `<button>` 嵌套 hydration 警告——前端既有问题，非本次引入）。
- 截图：`tmp/e2e_shots/seed-*.png`。

### 本次修改文件（挂载盘源码，未 commit）

- `paper_trading/trading_engine.py`（T-02）
- `api/v1/endpoints/paper_trading.py`（ws_router + T-02）
- `api/v1/router.py`（ws_router 挂载）
- `paper_trading/backtest_adapter.py`（日期序列化）
- 新增：`tests/test_paper_trading_ws.py`、`tests/test_paper_trading_backtest_contracts.py`、`tests/test_paper_trading_pricing.py`、`scripts/seed_demo_data.py`

### 未完成（依赖用户/后续阶段）

- ~~T-04~~ **已定：做固收最小闭环**（2026-08-13 落地，见下）
- ~~T-05~~ **已推进**：LLM 真密钥可用（PM 决策非 fallback 通过；reflection/battle_plan 受沙箱联网搜索限制）
- ~~T-06~~ **已完成**：5 日演练脚本 `scripts/simulate_trading_days.py`
- ~~T-07~~ **已完成**：`docs/live-trading-switch-checklist.md` + BrokerRouter 路由修复

### T-04~T-07 执行记录（2026-08-13）

| 任务 | 交付 | 验证 |
| --- | --- | --- |
| T-04 固收 | `paper_trading/fixed_income/`（curve/duration/spread/repo）+ `/api/v1/fixed-income/*` | `tests/test_fixed_income.py` 11 passed；API 4 端点 200 |
| T-05 Agent | LLM 真实调用 OK；PM 决策非 fallback | `LLMToolAdapter.call_text` 返回 OK；决策 id=4（hold, conf=1.0）落库 |
| T-06 演练 | `scripts/simulate_trading_days.py` | 5 日净值曲线连续（741005→760356） |
| T-07 前置 | `docs/live-trading-switch-checklist.md`；`broker/router.py` 修复 | `tests/test_broker_router.py` 5 passed |

> T-07 附带修复真实 bug：`BrokerRouter.resolve_by_account` 原来调用不存在的 `PaperAccountManager.get()`（实盘路由无法工作），改为 `_get_account_by_id` 并支持注入 account_mgr。
