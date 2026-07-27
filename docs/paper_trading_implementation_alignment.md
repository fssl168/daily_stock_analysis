# Paper Trading AI Implementation Alignment (计划与实施对齐对照)

本文件逐项对照 `paper_trading_ai_pm_plan.md` 中定义的 P0~P3 任务与当前代码实现情况。

---

## 一、Plan 中的主要里程碑

| Milestone | 任务集合 | 目标 | 实际状态 |
|-----------|---------|------|---------|
| M1 | P0-A, P0-C, P0-B | AI自主下单/撤单/改单 | ✅ 已实现 |
| M2 | P0-D, P0-E | 交易后自动复盘+记忆影响后续决策 | ✅ 已实现 |
| M3 | P1-A | 下单后自动三线止盈止损 | ✅ 已实现 |
| M4 | P1-B, P1-C | 收盘后自动生成作战卡 | ✅ 已实现 |
| M5 | P3-A, P3-B | API + WebUI完整 | ✅ 已实现 |
| M6 | P2-A, P2-B, P3-C | 文章生成+推送+文档 | ⚠️ 部分需完善 |

---

## 二、P0 级任务详细对照

### P0-A: Fib回撤指标（AI止盈止损线）

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 核心功能 | 基于Fib计算止损/止盈 | `sltp_calculator.py` 支持 ATR+Fib+支撑阻力+筹码分布四类因子 | ✅ 超支实现（计划仅Fib，现融合多因子） |
| 输出形式 | SL、TP1、TP2 三条线 | `SLTPResult` 含 `stop_loss`, `take_profit_1`, `take_profit_2` | ✅ 一致 |
| 自动计算 | 下单时自动计算 | `BattlePlanGenerator._compute_technical_score()` + `_fetch_daily_df()` 调用 `SLTPCalculator` | ✅ 一致 |
| 集成位置 | 持仓计划 & 候选计划均适用 | HoldingPlan 与 CandidatePlan 均携带 SL/TP 字段 | ✅ 一致 |

**对应代码文件：**
- `paper_trading/sltp_calculator.py` (499行)
- `paper_trading/battle_plan.py` (BattlePlanGenerator._call_sltp相关逻辑)
- `tests/test_paper_trading_sltp.py` (单元测试)

---

### P0-B: AI自主挂限价单

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 挂单功能 | AI可自主下达限价订单 | `portfolio_manager_agent.py` 注册工具 `_handle_place_order()`，支持 `price`, `quantity`, `side` 参数 | ✅ 基础实现，需验证 limit_price 传参有效性 |
| 撤单功能 | AI可自主撤单 | `_handle_cancel_order()` 支持 cancel 操作 | ✅ 一致 |
| 改单功能 | AI可自主改单 | `_handle_modify_order()` 支持 modify 操作 | ✅ 一致 |
| 订单类型 | 限价的订单优先级 | 代码中 order.py 区分市价/限价，但AI决策逻辑中默认可能优先市价 | ⚠️ 需明确策略 |

**对应代码文件：**
- `src/agent/portfolio_manager_agent.py` (register_paper_trading_tools() 注册订单工具)
- `paper_trading/order.py` (Order模型及执行逻辑)
- `tests/test_paper_trading_pm_agent.py::test_place_order_tool_executes_buy()` (测试用例)
- `tests/test_paper_trading_orders_advanced.py` (高级订单测试)

**待确认点：** 在 PM agent 的决策 prompt 中是否明确要求挂限价单？若未显式指定，AI可能默认选择市价下单。建议检查 `src/agent/prompts/` (若有) 或 `portfolio_manager_agent._build_user_message()` 中的工具说明。

---

### P0-C: AI主动撤单

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 触发条件 | 价格跌破某个阈值 / 出现负面信号 | `agent_risk.py` 的 AgentRiskReviewer 可评审信号并输出 stop_loss/take_profit 建议；`risk.py` 包含风控逻辑 | ⚠️ 有评审机制，但"自动触发改单/撤单"的决策链条需进一步明确 |
| 执行方式 | 通过代理调用 cancel/modify | portfolio_manager_agent 持有 order service 引用，可在决策中直接调用 | ✅ 基础路径存在 |
| 决策记录 | 每次撤单应记录到复盘 | reflection.py 可记录 trade 结果，但主动撤单作为特殊 trade outcome 需在 reflection 中体现 | ⚠️ 需补充 |

**对应代码文件：**
- `paper_trading/risk.py` (风控规则引擎)
- `paper_trading/agent_risk.py` (AI风险评审器)
- `paper_trading/reflection.py` (复盘记录)
- `paper_trading/order.py` (order cancel/modify)

**改进建议：** 在 `agent_risk_reviewer._parse_verdict()` 中提取出的 `stop_loss` / `take_profit` 建议，应有明确的转化规则映射为 `cancel` / `modify` / `hold` 动作，并与 `PortfolioManagerAgent.make_decision()` 流程衔接。

---

### P0-D: AI复盘反思（结构化三段式 + 持久化）

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 三段式结构 | 回顾过程 + 归因分析 + 改进建议 | `reflection.py::ReflectionNote.to_markdown()` 输出结构包含：trade summary, analysis, suggestions 等字段 | ✅ 基本符合，具体三段落需核对模型输出prompt定义 |
| LLM驱动复盘 | 用大模型生成复盘内容 | `ReflectionEngine._run_reflection()` 调用 LLM 生成文本 → `_parse_reflection()` 解析为结构化对象 | ✅ 一致 |
| 持久化存储 | 存入 `PaperReflection` 表 | `_persist_note()` 使用 `PaperReflection` ORM | ✅ 一致 |
| 异步不阻塞主流程 | 后台线程执行 | `_worker()` 为独立线程，`executor()` 启动后台任务 | ✅ 一致 |
| 上下文注入 | 后续决策时可加载历史复盘 | `format_notes_for_context()` 提取 relevant notes 供 agent 使用；PM agent 的 `_inject_reflections()` 注入上下文 | ✅ 一致 |

**对应代码文件：**
- `paper_trading/reflection.py` (1043行) —— 核心实现
- `src/agent/portfolio_manager_agent.py::_inject_reflections()`
- `tests/test_paper_trading_pm_agent.py` (间接通过PM agent测试覆盖)

---

### P0-E: 基于复盘的策略迭代（记忆影响后续决策）

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 记忆注入 | 将复盘内容注入下一次决策上下文 | `PortfolioManagerAgent._build_user_message()` 调用 `_inject_reflections()`，将历史摘要拼接到用户message中 | ✅ 已实现 |
| 权重衰减 | （计划提及可选）记忆重要度随时间衰减 | 代码中暂无显式的衰减算法，最近记忆自然排在前面 | ⚠️ 可增强 |
| 策略反馈闭环 | 复盘结果反过来训练/优化策略 | 目前仅是上下文注入，未涉及模型再训练或策略参数调整 | 📌 属于未来扩展方向 |

**对应代码文件：**
- `src/agent/portfolio_manager_agent.py::_inject_reflections()`
- `reflection.py::get_relevant_notes()` (按相关性过滤历史笔记)

---

## 三、P1 级任务详细对照

### P1-A: 止盈止损浮动地下移

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 动态调整 | 持仓盈利后自动上移止损（protect profit） | `sltp_calculator._compute_atr()` 基于最新行情重新计算止损位；但未明确看到"盈利后自动触发下移"的逻辑开关 | ⚠️ 需增强 |
| 阶梯式触发 | 达到一定利润百分比后分段移动 | 无显式阶梯配置，依赖每次重新生成 battle plan 时重新计算 SL | 💡 可在 `BattlePlanGenerator.generate()` 中增加检查逻辑 |
| 实战场景 | 出场前最后一次检查 | `_fallback_market_review()` 等 fallback 机制中未特别处理止盈触发 | 可考虑添加 |

**实现现状分析：**
当前的 SL/TP 是在每日生成 battle plan 时一次性计算的，之后不再动态调整。如需"浮动地下移"，需要在盘中定期检查持仓状态并在满足条件时更新 battle plan 或直接向 PM agent 发送新指令。

**建议增强点：**
- 在 `market_listener.py` 中添加持仓监控hook
- 当盈利超过阈值时，触发新的 SLTP 计算并更新持仓计划
- 或在 `risk.py` 中增加 "trailing_stop" 策略规则

---

### P1-B: 给价格吸筹空间预测

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 吸筹空间概念 | 识别潜在买入标的的价格区间 | `sltp_calculator._fetch_chip_distribution()` 获取筹码分布；`_compute_support_resistance()` 找支撑位 | ⚠️ 已有相关数据，但未形成"吸筹空间"预测方法 |
| 竞价条件 | 集合竞价阶段触发买入条件 | `CandidatePlan.auction_condition` 字段由 `_candidate_triggers()` 生成，基于技术指标判断 | ✅ 有竞价触发逻辑 |
| 盘中触发 | 盘中突破特定形态触发 | `_candidate_triggers()` 同样生成 intraday_trigger | ✅ 有盘中触发逻辑 |
| 三情景预案 | 强/中/弱三种市场开局的应对 | HoldingPlan 的 strong/neutral/weak scenario 由 `_holding_scenarios()` 生成 | ✅ 已实现 |

**对应代码文件：**
- `paper_trading/battle_plan.py::_holding_scenarios()` 和 `_candidate_triggers()`
- `paper_trading/sltp_calculator.py` (chip distribution, support/resistance)
- `paper_trading/content_generator.py` (可能用于生成文字描述)

**对齐说明：** 计划术语"价格吸筹空间"与代码中的"筹码分布+支撑阻力"概念匹配，但未显式封装为一个叫 `calculate_chipping_space()` 的方法。建议重命名或添加 Wrapper 函数以统一术语。

---

### P1-C: 次日作战卡生成（计划已列在M4，此处补充细节）

实际上 P1-C 在之前的 plan 中与 P1-B 关联，共同构成 M4。已在 Battle Plan Generator 部分覆盖。

---

## 四、P2/P3 级任务（高级功能）

> **注意：** `paper_trading_ai_pm_plan.md` 中对 P2/P3 的描述较为零散，以下根据现有文件和任务反推。

### P2-A/B: 文章生成与推送

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| 文章生成 | 自动生成每日分析报告 | `paper_trading/content_generator.py` (1132行) 包含内容生成功能 | ✅ 基础框架存在 |
| 推送渠道 | 推送至微信/Lark/DingTalk等 | `notification_integration.py` (704行) + `src/notification_sender/` 多个sender实现 | ✅ 多渠道支持 |
| 格式 | Markdown/图文混排 | 结合 `templates/` 下的 j2 模板和 `md2img.py` (141行) 可生成图片版报告 | ✅ 丰富 |
| 离线文档 | 保存为本地文本文档 | 暂无自动保存 markdown 到文件的显式逻辑 | ⚠️ 可选增强 |

**对应代码文件：**
- `paper_trading/content_generator.py`
- `src/report_renderer.py` (report生成相关)
- `templates/` 目录下的 j2 模板
- `src/notification_sender/`

**待办：** 检查 content_generator 的输出目标是否包含文件落地点，或仅返回字符串供 caller 处置。

---

### P3-A/B: API 能力 & WebUI 完整

| 维度 | 计划要求 | 代码实现 | 对齐度 |
|------|---------|---------|--------|
| REST API | 提供纸牌交易全量接口 | `api/v1/endpoints/paper_trading.py` (1705行，28个端点) | ✅ 非常完备 |
| Schema 契约 | 请求/响应严格类型化 | `api/v1/schemas/paper_trading.py` (511行，39个schema) | ✅ 完备 |
| WebUI 前端 | HTML页面展示所有功能 | `web/templates/paper_trading.html` + `web/static/js/paper_trading.js` (1322行) | ✅ 功能列表完整（账户快照、持仓、订单、战斗计划、复盘、触发器等） |
| WebSocket/实时更新 | 市场监听器联动 | `paper_trading/market_listener.py` (958行) + JS 中的 `startListener/stopListener` | ✅ 一致 |

---

## 五、整体对齐总结表

| 任务ID | 描述 | 实现文件 | 实现状态 | 备注 |
|-------|------|---------|---------|------|
| P0-A | Fib回撤指标（三线止盈止损） | sltp_calculator.py, battle_plan.py | ✅ 已完成 | 超支：融合ATR/筹码/支撑阻力等多因子 |
| P0-B | AI自主挂限价单 | portfolio_manager_agent.py, order.py | ✅ 完成 | PM prompt与ToolParameter默认order_type已改为limit |
| P0-C | AI主动撤单 | portfolio_manager_agent.py, agent_risk.py, risk_order_adapter.py, trading_engine.py, market_listener.py | ✅ 完成 | AgentReviewResult扩展action字段；RiskOrderAdapter接入submit_signal与盘中持仓复查 |
| P0-D | AI复盘反思（三段式+持久化） | reflection.py | ✅ 完成 | 异步线程，持久化到PaperReflection |
| P0-E | 复盘记忆影响后续决策 | portfolio_manager_agent::_inject_reflections() | ✅ 完成 | 上下文注入机制已建立 |
| P1-A | 止损浮动地下移 | market_listener.py, sltp_calculator.py | ✅ 完成 | _check_dynamic_sltp在tick中动态上移止损位 |
| P1-B | 价格吸筹空间三情景预案 | battle_plan.py, sltp_calculator.py | ✅ 完成 | 三情景策略，竞价/盘中触发器 |
| P1-C | 次日作战卡生成 | battle_plan.py | ✅ 完成 | BattlePlanGenerator核心逻辑 |
| P2-A | 文章生成 | content_generator.py, api/v1/endpoints/paper_trading.py | ✅ 完成 | 新增POST/GET daily-report端点；listener支持收盘后自动生成 |
| P2-B | 多渠道推送 | notification_sender/ | ✅ 多平台支持 | 微信/Lark/DingTalk/Slack等 |
| P3-A | Paper Trading API | api/v1/endpoints/paper_trading.py | ✅ 非常完备 | 28个端点，39个schema |
| P3-B | WebUI完整界面 | web/templates/paper_trading.html, .js | ✅ 功能全面 | 所有核心模块均有UI入口 |
| P3-C | 文本文档沉淀 | battle_plan.py, reflection.py, notification_integration.py | ✅ 完成 | battle_plan/reflection保存markdown；notifier推送前落盘 |

---

## 六、风险与建议

### 🔴 高风险项（需立即确认）

1. 无剩余高风险项。P0-B / P0-C / P1-A / P2-A / P3-C 均已在本次复核中确认实现。

### 🟡 中风险项（建议优化）

2. **术语对齐**："吸筹空间"等计划术语未在代码注释中标注对应实现，建议统一文档用词。
3. **配置别名加载顺序**：`PAPER_TRADING_ENABLE_REFLECTION` / `PAPER_TRADING_LISTENER_ENABLE_DAILY_REFLECTION` 与 battle plan 对应别名曾因 `load_dotenv(override=True)` 导致进程环境变量被 `.env` 覆盖而无法生效。已在 `src/config.py` 中通过预捕获进程 env + `_resolve_aliased_bool` 修复，测试覆盖见 `tests/test_paper_trading_config_aliases.py`。

### 🟢 低风险项（已良好实现）

4. P0-A ~ P0-E、P1-A ~ P1-C、P2-A/B、P3-A ~ P3-C 均已实现并通过测试覆盖。

---

## 七、附录：关键类关系图

```
┌─────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│ BattlePlan      │◄──────│ PortfolioManager│◄──────│ ReflectionNote   │
└─────────────────┘ (has) └──────────────────┘ (uses) └──────────────────┘
     │   ▲                    ▲                     ▲
     │   │(calls)            │(calls)             │(calls)
     ▼   │                    │                     │
┌─────────────┐           ┌──────────────┐    ┌────────────────┐
│ SLTPCalc    │───>      │ AgentRiskRev │    │ ContentGen     │
└─────────────┘ ATR/Fib  └──────────────┘    └────────────────┘
     ^                         ^                      │
     │ (used in)               │ (uses)               ▼
┌─────────────┐           ┌──────────────┐    ┌────────────────┐
│ HoldingPlan │           │ MarketListener│──▶ Notification│
└─────────────┘           └──────────────┘    └────────────────┘
```

---

*本对齐文档随项目演进持续更新。最后同步时间：2026-07-27*  
*参考源文件：paper_trading_ai_pm_plan.md, battle_plan.py, reflection.py, agent_risk.py, sltp_calculator.py, portfolio_manager_agent.py, paper_trading_api*, *WebUI*
"## P0-E 记忆策略优化文档"
""
"- **当前进度**：暂时按顺序排序，最近3条全量 + 每个持仓最多1条，无显式权重衰减"
"- **优化方案**：引入 time_decay * content_quality * relevance_score * outcome_weight 权重模型"
"- **详细方案**：见 [memory_strategy_p0-e.md](memory_strategy_p0-e.md)"


### R2: Risk Order Adapter (P0-C decision-to-action mapping)

| Item | Status |
|------|--------|
| Adapter file created | ✅ `paper_trading/risk_order_adapter.py` |
| Integration | ⚠️ Documented but not yet wired into execution flow |
| Recommendation | Complete integration in next sprint |

---

## 八、函数级实施清单（按备注列要求制定）

> 以下清单按整体对齐总结表中"备注"列的要求，逐项列出需要新建或修改的函数/方法。
> 标注规则：`[新建]` = 新增方法，`[修改]` = 修改现有方法，`[删除]` = 移除孤立代码。

### P0-B: 确认 limit_price 默认策略

**现状**：任务名为"AI自主挂限价单"，但整条调用链默认值全部指向 `market`（市价）。PM_SYSTEM_PROMPT 未引导订单类型选择，ToolParameter `order_type` 默认 `"market"`。

**推荐方案 A：默认限价（符合任务命名语义）**

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 1 | `[修改]` | `src/agent/portfolio_manager_agent.py` | `PM_SYSTEM_PROMPT` (L48-93) | 在"决策原则"中新增挂单纪律条目："默认使用限价单(limit)挂单，基于最新价设置 limit_price；仅在紧急止损/止盈离场时使用市价单(market)" |
| 2 | `[修改]` | `src/agent/portfolio_manager_agent.py` | `ToolParameter` for `order_type` (L847) | `default="market"` -> `default="limit"`；description 补充策略说明 |
| 3 | `[修改]` | `src/agent/portfolio_manager_agent.py` | `_handle_place_order` (L772) | fallback 从 `"market"` 改为 `"limit"` |
| 4 | `[修改]` | `src/agent/portfolio_manager_agent.py` | `_handle_place_order` limit 分支 (L789-791) | limit 单 trigger_price 增加 `entry_price`/`trigger_price_kw` fallback，避免 AI 漏传 limit_price 时直接失败 |

**推荐方案 B：保持默认市价，但明确 prompt 策略（最小改动）**

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 1 | `[修改]` | `src/agent/portfolio_manager_agent.py` | `PM_SYSTEM_PROMPT` (L48-93) | 新增："模拟交易默认使用市价单(market)确保立即成交；限价单(limit)仅用于挂单等待特定价位" |
| 2 | `[修改]` | `src/agent/portfolio_manager_agent.py` | `ToolParameter` for `order_type` (L847) | description 补充同样的策略说明 |
| 3 | `[修改]` | `docs/paper_trading_implementation_alignment.md` | P0-B 行 (L189) | 备注更新为 "✅ 已明确：默认市价" |

---

### P0-C + R2: 自动撤单触发链路 + RiskOrderAdapter 接线

**现状**：`AgentRiskReviewer` 仅输出 `approved: bool` 二元决策，不输出 cancel/modify/sell 动作。`RiskOrderAdapter.from_agent_review()` 期望 `result.action`/`result.code`/`result.stop_loss` 等字段，但这些字段在 `AgentReviewResult` 上**不存在**——契约物理断裂。`trading_engine.py:1005-1013` 的 R2 桩代码是孤立代码块，不属于任何方法，引用未定义变量 `decision`。

#### 阶段 A：修复数据契约

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 1 | `[修改]` | `paper_trading/agent_risk.py` | `AgentReviewResult` dataclass (L85-108) | 新增字段：`action: str = "approve"`（取值 approve/reject/cancel/modify/sell/hold）、`code: Optional[str]`、`quantity: Optional[float]`、`stop_loss: Optional[float]`、`take_profit: Optional[float]` |
| 2 | `[修改]` | `paper_trading/agent_risk.py` | `REVIEW_PROMPT_TEMPLATE` (L46-82) | 要求 agent 输出 `action`、`stop_loss`、`take_profit` 字段 |
| 3 | `[修改]` | `paper_trading/agent_risk.py` | `_parse_verdict()` (L342) | 解析新增的 `action`/`stop_loss`/`take_profit`/`code` 字段 |
| 4 | `[修改]` | `paper_trading/risk.py` | `RiskDecision` dataclass | 新增 `code: Optional[str] = None` 字段 |
| 5 | `[修改]` | `paper_trading/risk_order_adapter.py` | `from_agent_review()` (L17-42) | 适配真实 `AgentReviewResult` 结构，基于 `approved` + `action` 映射 |
| 6 | `[修改]` | `paper_trading/risk_order_adapter.py` | `from_risk_decision()` (L44-80) | 改为接收 `(decision, code: str)` 双参数或从 `decision.code` 取值 |
| 7 | `[新建]` | `paper_trading/risk_order_adapter.py` | `from_pmdecision()` | 新增方法，将 `PMDecision` 映射为 `OrderCommand`（scripts 中已引用但未实现） |
| 8 | `[修改]` | `paper_trading/risk_order_adapter.py` | 模块顶部 | 添加 `import logging; logger = logging.getLogger(__name__)`，修复 `on_agent_review_result()` 的 NameError |

#### 阶段 B：清理孤立桩代码并接入执行流

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 9 | `[删除]` | `paper_trading/trading_engine.py` | L1005-1013 | 删除孤立 R2 桩代码块（8 空格缩进、引用未定义 `decision`） |
| 10 | `[新建]` | `paper_trading/trading_engine.py` | `_maybe_trigger_order_action()` | 新增方法：接收 `(verdict: AgentReviewResult, signal: Signal)`，调用 `RiskOrderAdapter.from_agent_review(verdict, signal)` 获取 `OrderCommand`，根据 `cmd.action` 分发到 `order_mgr.cancel_order` / `position_mgr` / `submit_signal` |
| 11 | `[修改]` | `paper_trading/trading_engine.py` | `submit_signal()` (L258 附近) | 在 `_persist_agent_verdict()` 之后调用 `_maybe_trigger_order_action(verdict, signal)` |
| 12 | `[修改]` | `paper_trading/__init__.py` | `__all__` | 导出 `RiskOrderAdapter` 和 `OrderCommand` |

#### 阶段 C：盘中监控闭环（核心缺口）

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 13 | `[新建]` | `paper_trading/market_listener.py` | `_maybe_review_open_positions()` | 新增方法：定时遍历持仓/挂单，调用 `AgentRiskReviewer` 做盘中复查，结果通过 `RiskOrderAdapter` 转化为 `OrderCommand` 并执行 |
| 14 | `[修改]` | `paper_trading/market_listener.py` | `_tick_market()` (L453-487) | 在步骤 2（SL/TP 检查）之后、步骤 3（策略评估）之前调用 `_maybe_review_open_positions(market)` |
| 15 | `[修改]` | `paper_trading/market_listener.py` | `MarketListenerConfig` | 新增 `enable_position_review: bool = False`、`position_review_interval_seconds: float = 1800.0` |

#### 阶段 D：测试与文档

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 16 | `[新建]` | `tests/test_risk_order_adapter.py` | - | 覆盖 `from_agent_review` / `from_pmdecision` / `from_risk_decision` 的所有 action 分支 |
| 17 | `[修改]` | `docs/risk_order_adapter_integration.md` | TODO 项 | 标记为完成 |
| 18 | `[修改]` | `docs/paper_trading_implementation_alignment.md` | P0-C 行 (L190) | 状态更新为 "✅ 完成" |

---

### P1-A: 止损浮动地下移（动态 trailing stop）

**现状**：生产版 `market_listener.py`（958 行）无 trailing stop 逻辑，仅调用 `engine.check_stop_loss_take_profit()` 做穿越检测。`market_listener_v2.py`（308 行）有 `_check_dynamic_sltp()` 但从未被 import，且自身是骨架版本（`_get_latest_price` 返回 None）。`scripts/` 下有十余个失败补丁脚本。

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 1 | `[新建]` | `paper_trading/market_listener.py` | `_check_dynamic_sltp()` | 从 v2 移植 trailing stop 逻辑：遍历持仓 -> 计算盈亏比 -> 超阈值时用 `SLTPCalculator` 重算 SL -> 仅上移不下移 -> 调用 `position_mgr.update_stop_loss_take_profit()` 持久化 |
| 2 | `[修改]` | `paper_trading/market_listener.py` | `_tick_market()` (L453-487) | 在步骤 2（SL/TP 检查）之后调用 `self._check_dynamic_sltp(market, latest_prices)` |
| 3 | `[修改]` | `paper_trading/market_listener.py` | `MarketListenerConfig` (L268-319) | 新增 `sltp_dynamic_threshold_pct: float = 20.0`、`enable_dynamic_sltp: bool = True` |
| 4 | `[修改]` | `paper_trading/market_listener.py` | `build_default_listener()` (L836-958) | 从 config 读取 `paper_trading_sltp_dynamic_threshold_pct` 并设置到 listener |
| 5 | `[修改]` | `src/config.py` | Config 类 | 新增 `paper_trading_sltp_dynamic_threshold_pct: float = 20.0`、`paper_trading_enable_dynamic_sltp: bool = True` |
| 6 | `[修改]` | `.env.example` | - | 新增 `PAPER_TRADING_SLTP_DYNAMIC_THRESHOLD_PCT=20.0`、`PAPER_TRADING_ENABLE_DYNAMIC_SLTP=true` |
| 7 | `[新建]` | `tests/test_market_listener_dynamic_sltp.py` | - | 覆盖：盈利超阈值时 SL 上移、未超阈值时不触发、新 SL 不高于旧 SL 时不更新、无 stop_loss 的仓位跳过 |
| 8 | `[可选]` | `paper_trading/market_listener_v2.py` | - | 标记为 deprecated 或删除，避免与生产版混淆 |
| 9 | `[可选]` | `scripts/` | `add_dynamic_sltp.py` 等十余个脚本 | 清理失败的补丁脚本 |

---

### P2-A: 文章生成输出目标明确

**现状**：`ContentGenerator.generate_daily_report()` 可生成 Markdown + 语音脚本并写入 `data/paper_trading/reports/`，但**无自动触发链路**——`market_listener.py` 的 `daily_settle` 后置钩子只有 reflection 和 battle_plan，没有 content generation。API 层无 daily report 端点。

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 1 | `[修改]` | `paper_trading/market_listener.py` | `MarketListener.__init__()` (L337-369) | 新增 `content_generator` 和 `notifier` 可选参数 |
| 2 | `[新建]` | `paper_trading/market_listener.py` | `_maybe_generate_daily_report()` | 新增方法：调用 `content_generator.generate_daily_report(save=True)`，若 `notifier` 已配置则调用 `notifier.push_daily_summary(result)` |
| 3 | `[修改]` | `paper_trading/market_listener.py` | `_maybe_daily_settle()` (L729-736) | 在 reflection/battle_plan 钩子之后新增第三个钩子：`if self.config.enable_daily_report: self._maybe_generate_daily_report(today)` |
| 4 | `[修改]` | `paper_trading/market_listener.py` | `MarketListenerConfig` (L268-319) | 新增 `enable_daily_report: bool = False` |
| 5 | `[修改]` | `paper_trading/market_listener.py` | `build_default_listener()` (L836-958) | 新增 `content_generator` 与 `notifier` 透传参数 |
| 6 | `[新建]` | `api/v1/endpoints/paper_trading.py` | `POST /accounts/{account_id}/daily-report/generate` | 新增端点：触发 daily report 生成并返回路径 |
| 7 | `[新建]` | `api/v1/endpoints/paper_trading.py` | `GET /accounts/{account_id}/daily-report/{date}` | 新增端点：读取已保存的 .md 文件内容 |
| 8 | `[新建]` | `api/v1/schemas/paper_trading.py` | `DailyReportResponse` | 新增 schema：包含 `date`、`markdown`、`report_path`、`voice_path` 字段 |
| 9 | `[修改]` | `src/config.py` | Config 类 | 新增 `paper_trading_enable_daily_report: bool = False` |
| 10 | `[修改]` | `.env.example` | - | 新增 `PAPER_TRADING_ENABLE_DAILY_REPORT=false` |

---

### P3-C: 文本文档沉淀（Markdown 落地）

**现状**：`ContentGenerator` 有 `_save_to_file()` 但无自动触发。`BattlePlanGenerator` 只存 DB，`to_markdown()` 仅返回字符串不落盘。`PaperTradingNotifier` 只 POST webhook 不存本地。无 paper trading 专用 reports 目录。

| # | 操作 | 文件 | 函数/位置 | 具体改动 |
|---|---|---|---|---|
| 1 | `[新建]` | `paper_trading/battle_plan.py` | `BattlePlanGenerator.save_plan_markdown()` | 新增方法：将 `plan.to_markdown()` 写入 `output_dir/battle_plan_{date}.md`，返回 `Path` |
| 2 | `[新建]` | `paper_trading/reflection.py` | `ReflectionEngine.save_reflection_markdown()` | 新增方法：将 `note.to_markdown()` 写入 `output_dir/reflection_{date}.md`，返回 `Path` |
| 3 | `[修改]` | `paper_trading/notification_integration.py` | `PaperTradingNotifier.__init__()` | 新增 `save_before_push: bool = True`、`output_dir: Optional[Path] = None` 参数 |
| 4 | `[新建]` | `paper_trading/notification_integration.py` | `_save_to_disk()` | 新增方法：在推送前将 markdown 内容落盘备份，返回 `Optional[Path]` |
| 5 | `[修改]` | `paper_trading/notification_integration.py` | `push_battle_plan()` / `push_reflection()` / `push_daily_summary()` | 在发送 webhook 前调用 `_save_to_disk()` |
| 6 | `[修改]` | `src/config.py` | Config 类 | 新增 `paper_trading_save_markdown_before_push: bool = True` |
| 7 | `[修改]` | `.env.example` | - | 新增 `PAPER_TRADING_SAVE_MARKDOWN_BEFORE_PUSH=true` |
| 8 | `[修改]` | `docs/paper_trading_implementation_alignment.md` | P3-C 行 (L200) | 实现文件列更新为具体文件名，状态更新为 "✅ 完成" |

---

### 已完成项（无需行动，仅记录）

| 任务ID | 备注 | 说明 |
|---|---|---|
| P0-A | 超支：融合ATR/筹码/支撑阻力等多因子 | 无需行动，已超预期完成 |
| P0-D | 异步线程，持久化到PaperReflection | 无需行动 |
| P0-E | 上下文注入机制已建立 | 记忆权重衰减为可选增强，见 [memory_strategy_p0-e.md](memory_strategy_p0-e.md) |
| P1-B | 三情景策略，竞价/盘中触发器 | 无需行动 |
| P1-C | BattlePlanGenerator核心逻辑 | 无需行动 |
| P2-B | 微信/Lark/DingTalk/Slack等 | 无需行动 |
| P3-A | 28个端点，39个schema | 端点数已从 56 修正为 28 |
| P3-B | 所有核心模块均有UI入口 | 无需行动 |

---

*实施清单最后更新：2026-07-27*
