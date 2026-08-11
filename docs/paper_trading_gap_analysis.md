# 项目方案复核报告：`paper_trading_ai_pm_plan.md` 实施方案 vs 代码开发差距分析

> 复核时间：2026-07-26
> 复核范围：`docs/paper_trading_ai_pm_plan.md` 全部 6 个里程碑（M1-M6）
> 对比方法：方案文档函数级任务清单 vs 仓库实际代码（tree/analyze/shell 扫描）

---

## 一、总体结论

| 维度 | 评估 |
|------|------|
| **方案完整度** | ✅ 函数级任务清单完整，6 个里程碑（M1-M6）覆盖全部规划 |
| **代码实现度** | ⚠️ **约 85% 已实现**，核心闭环全部落地，但存在若干偏差与缺口 |
| **偏差类型** | 架构调整型偏差（合理）、功能缺失型缺口（需补齐）、测试/文档薄弱 |

**核心判断**：P0-A ~ P0-E、P1-A ~ P1-C、P2-A ~ P2-B、P3-A ~ P3-B 的**主体功能均已实现**，方案规划的 6 个里程碑整体可达。主要差距集中在 **P0-E 记忆注入实现方式偏差**、**PositionManager.unfreeze_quantity 缺失**、**集成测试薄弱**、**部分函数签名与方案不一致** 四个方面。

---

## 二、逐任务差距分析

### P0-A：技术指标增强 — ✅ 已实现，轻微偏差

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `compute_fibonacci_retracement` | ✅ 已实现 | ✅ |
| `compute_atr` | ✅ 已实现 | ✅ |
| `compute_support_resistance` | ✅ 已实现（含 fractal + cluster 两种方法） | ✅ |
| `compute_indicators` 调度函数签名 | ⚠️ 方案：`compute_indicators(df, indicators: List[str], params)` | ⚠️ 偏差 |
| | 实际：`compute_indicators(df, specs: List[IndicatorSpec])` | |
| `IndicatorSpec` 注册表登记新指标 | ✅ 已在 `indicators.py` 中扩展支持 `fib`/`atr`/`support`/`resistance` | ✅ |
| `Rule` schema 允许规则右值为 Fib 回撤位 | ✅ `schema.py` 的 `_try_parse_indicator` 已集成 `IndicatorSpec`，支持 `fib_0.618` 等 | ✅ |
| 单元测试 | ❌ `tests/` 下无 Fibonacci/ATR/support_resistance 专项单元测试 | ❌ |

**差距说明**：
1. `compute_indicators` 的签名从方案设计的 `(df, indicators: List[str], params)` 改为 `(df, specs: List[IndicatorSpec])`，这是**合理的架构改进**——`IndicatorSpec` 提供了更强的类型安全和可扩展性，但与方案文档不一致。
2. 方案验收标准要求"合成数据验证 Fib 0.618 = 87.6"的单元测试**不存在**，仅有冒烟测试间接覆盖。

---

### P0-C：订单管理增强 — ✅ 已实现，1 个缺口

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `OrderManager.cancel_order` | ✅ 已实现 | ✅ |
| `OrderManager.modify_order` | ✅ 已实现（cancel + create，保留 `parent_order_id`） | ✅ |
| `OrderStatus` 新增 `CANCELLED`/`MODIFIED` | ⚠️ 方案要求新增 `MODIFIED` 状态；实际**不使用** `MODIFIED` 状态，改为 cancel+create 新订单 | ⚠️ 合理偏差 |
| `PaperAccountManager.unfreeze_cash` | ✅ 已实现 | ✅ |
| `PositionManager.unfreeze_quantity` | ❌ **未实现** | ❌ |
| `TradingEngine.cancel_signal` | ✅ 已实现 | ✅ |
| `TradingEngine.modify_signal` | ✅ 已实现 | ✅ |
| `PaperOrder` 表 `parent_order_id`/`cancel_reason`/`modified_at` | ✅ 已实现 | ✅ |
| 单元测试 | ⚠️ 仅有 `_smoke_p3c_cancel_modify.py` 冒烟测试，无正式 `tests/test_*.py` | ⚠️ |

**差距说明**：
1. **`PositionManager.unfreeze_quantity` 缺失**——方案明确要求卖出冻结时释放冻结数量，但 `position.py` 中无此方法。当前卖出撤单的资金解冻通过 `unfreeze_cash` 完成，但如果存在卖出限价单冻结持仓的场景，撤单后持仓解冻可能不完整。需确认卖出限价单是否冻结持仓——如果卖出限价单不冻结持仓（仅记录待卖），则此缺口影响不大；否则为功能性缺陷。
2. `MODIFIED` 状态的设计偏差是**合理的**——cancel+create 比状态机更简洁，审计链通过 `parent_order_id` 保留。

---

### P0-B：AI 基金经理 Agent — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `PMDecision` dataclass | ✅ 已实现（含 `to_dict`） | ✅ |
| `PortfolioManagerAgent` 类 | ✅ 已实现 | ✅ |
| `make_decision` 方法 | ✅ 已实现 | ✅ |
| `_build_system_prompt` | ⚠️ 方案要求独立方法；实际使用模块级常量 `PM_SYSTEM_PROMPT` | ⚠️ 轻微偏差 |
| `_build_user_message` | ✅ 已实现 | ✅ |
| `_call_agent_with_timeout` | ✅ 已实现 | ✅ |
| `_parse_decision` | ✅ 已实现 | ✅ |
| `_inject_reflections` | ⚠️ 方案要求独立方法；实际合并到 `_build_user_message` 中通过 `_fetch_reflections_summary` 内联注入 | ⚠️ 实现方式偏差 |
| `register_paper_trading_tools` | ✅ 已实现（8 个工具全部注册） | ✅ |
| `build_portfolio_manager_agent` 工厂 | ✅ 已实现 | ✅ |
| `PaperDecision` ORM 表 | ✅ 已实现 | ✅ |
| 持久化决策到 `PaperDecision` | ✅ `_persist_decision` 已实现 | ✅ |
| `MarketListener` 集成 PM Agent | ✅ `_maybe_trigger_pm_decision` 已实现 | ✅ |
| 冒烟测试 | ✅ `_smoke_p3c_pm_agent.py` 存在 | ✅ |

**差距说明**：
1. **`_inject_reflections` 未作为独立方法实现**——方案 P0-E 明确要求此方法。实际实现中，反射记忆注入通过 `_fetch_reflections_summary` 在 `_build_user_message` 中内联完成，功能等价但结构与方案不一致。这是 P0-E 的主要偏差。
2. **`_build_system_prompt` 未作为方法实现**——改用模块级常量 `PM_SYSTEM_PROMPT`，功能等价，但不支持运行时动态构建。

---

### P0-D：AI 复盘反思系统 — ✅ 已实现，1 个缺口

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ReflectionNote` dataclass | ✅ 已实现（含 `to_dict`） | ✅ |
| `ReflectionNote.to_markdown` | ❌ **未实现** | ❌ |
| `ReflectionEngine` 类 | ✅ 已实现 | ✅ |
| `reflect_on_trade` | ✅ 已实现 | ✅ |
| `reflect_on_daily` | ✅ 已实现 | ✅ |
| `_reflect_sync` | ⚠️ 方案要求 `_reflect_sync`；实际为 `_run_reflection` | ⚠️ 命名偏差 |
| `_build_trade_reflection_prompt` | ✅ 已实现 | ✅ |
| `_build_daily_reflection_prompt` | ✅ 已实现 | ✅ |
| `_parse_reflection` | ✅ 已实现 | ✅ |
| `_persist_note` | ✅ 已实现 | ✅ |
| `get_recent_notes` | ✅ 已实现 | ✅ |
| 异步 daemon 线程 + timeout | ✅ 已实现 | ✅ |
| `PaperReflection` ORM 表 | ✅ 已实现（含全部字段） | ✅ |
| 触发时机集成（TradingEngine 回调） | ✅ `on_trade_executed` 回调已实现 | ✅ |

**差距说明**：
1. **`ReflectionNote.to_markdown` 未实现**——方案明确要求此方法，用于生成"🧠 基金经理笔记"Markdown。虽然 `PaperTradingNotifier._render_reflection_markdown` 有类似的渲染逻辑，但 `ReflectionNote` 自身缺少 `to_markdown`，影响直接展示和 API 返回。
2. 方法命名 `_reflect_sync` → `_run_reflection` 属轻微偏差，不影响功能。

---

### P0-E：复盘记忆系统 — ⚠️ 已实现，结构性偏差

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ReflectionEngine.get_relevant_notes` | ✅ 已实现 | ✅ |
| `format_notes_for_context` | ✅ 已实现（模块级函数） | ✅ |
| `PortfolioManagerAgent._inject_reflections` | ❌ **未作为独立方法实现** | ❌ |
| `_build_user_message` 加入复盘记忆段落 | ✅ 通过 `_fetch_reflections_summary` 内联实现 | ✅ |
| PM system prompt 加入复盘记忆提示 | ✅ `PM_SYSTEM_PROMPT` 中已包含 | ✅ |

**差距说明**：
1. **`_inject_reflections` 方法缺失**是 P0-E 的核心偏差。方案设计为两步：先 `_inject_reflections` 将笔记注入 `market_context` dict，再 `_build_user_message` 从 context 中读取。实际实现合并为一步，直接在 `_build_user_message` 中调用 `_fetch_reflections_summary`。功能等价但：
   - 无法在 `make_decision` 开头统一注入上下文
   - 不支持 `code_reflections`（按股票代码分类的历史教训）维度——方案要求"每个 watched code 的最近 3 条笔记"，实际仅注入账户级别的通用摘要

---

### P1-A：智能止损止盈 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `SLTPResult` dataclass | ✅ 已实现 | ✅ |
| `SLTPCalculator` 类 | ✅ 已实现 | ✅ |
| `compute` 方法 | ✅ 已实现 | ✅ |
| ATR + Fib + 支撑阻力三位一体 | ✅ 已实现 | ✅ |
| 筹码峰集成 | ✅ `_fetch_chip_distribution` 已实现（可通过配置开关） | ✅ |
| `TradingEngine` 集成 `sltp_calculator` | ✅ `__init__` 已接受参数 | ✅ |
| `_execute_market_order` 后自动计算三线 | ✅ BUY fill 后自动调用 | ✅ |
| PM Agent 可调用 `compute_sltp` 工具 | ⚠️ 方案要求注册 `paper_trading_compute_sltp` 工具；实际 `register_paper_trading_tools` 中**未注册此工具** | ⚠️ |
| 单元测试 | ❌ 无专项测试 | ❌ |

**差距说明**：
1. **`paper_trading_compute_sltp` 工具未注册**——方案要求 PM Agent 可调用此工具预览三线，但 `register_paper_trading_tools` 注册的 8 个工具中不包含 SLTP 计算工具。
2. 三线计算的子方法 `_compute_stop_loss`/`_compute_take_profit` 在实际实现中改为更细粒度的 `_nearest_below`/`_nearest_above`/`_compute_atr`/`_compute_fib` 等，属合理重构。

---

### P1-B：情景预案 + 次日作战卡 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `HoldingPlan`/`CandidatePlan`/`BattlePlan` dataclass | ✅ 全部已实现 | ✅ |
| `BattlePlan.to_markdown` | ✅ 已实现 | ✅ |
| `BattlePlanGenerator` 类 | ✅ 已实现 | ✅ |
| `generate` 方法 | ✅ 已实现 | ✅ |
| 三情景预案 + 候选标的 + 三线 | ✅ 全部已实现 | ✅ |
| PM Agent 生成市场综述 | ✅ `_call_pm_for_review` 已实现 | ✅ |
| `PaperBattlePlan` ORM 表 | ✅ 已实现 | ✅ |
| `MarketListener` 触发生成 | ✅ `_maybe_generate_battle_plan` 已实现 | ✅ |
| 规则降级 fallback | ✅ `_fallback_market_review` 等已实现 | ✅ |

**差距说明**：无显著差距，此任务实现度最高。

---

### P1-C：MarketListener 集成 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `MarketListenerConfig` 新增引擎字段 | ✅ `pm_agent`/`reflection_engine`/`battle_plan_generator` | ✅ |
| `pm_decision_interval_seconds` 配置 | ✅ 已实现 | ✅ |
| `_tick_market` 触发 PM 决策 | ✅ `_maybe_trigger_pm_decision` | ✅ |
| `_maybe_daily_settle` 触发复盘 + 作战卡 | ✅ 拆分为 `_maybe_run_daily_reflection` + `_maybe_generate_battle_plan` | ✅ |
| `TradingEngine` 回调机制 | ✅ `on_trade_executed`/`on_signal_rejected` 已实现 | ✅ |
| 回调注册成交后触发复盘 | ✅ `build_default_listener` 中已接线 | ✅ |

**差距说明**：无显著差距。

---

### P2-A：复盘文章自动生成 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ContentGenerator` 类 | ✅ 已实现 | ✅ |
| `generate_daily_report` | ✅ 已实现 | ✅ |
| `generate_voice_script` | ✅ 已实现 | ✅ |
| `_collect_daily_data` | ✅ 已实现 | ✅ |
| `_save_to_file` | ✅ 已实现 | ✅ |
| LLM 调用 + fallback | ✅ `_call_llm_for_narrative` + `_fallback_narrative` | ✅ |

**差距说明**：无显著差距。

---

### P2-B：飞书/钉钉推送 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `PaperTradingNotifier` 类 | ✅ 已实现 | ✅ |
| `push_battle_plan` | ✅ 已实现 | ✅ |
| `push_reflection` | ✅ 已实现 | ✅ |
| `push_daily_summary` | ✅ 已实现 | ✅ |
| `_send_lark` / `_send_dingtalk` | ✅ 已实现 | ✅ |
| DingTalk URL 签名 | ✅ `_sign_dingtalk_url` 已实现 | ✅ |
| 消息分块（chunking） | ✅ `_chunk_text` 已实现（超出方案要求） | ✅ |

**差距说明**：无显著差距，实现超越方案要求。

---

### P3-A：API 端点 + Pydantic schema — ✅ 已实现，路径偏差

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| 端点文件位置 | ⚠️ 方案：`src/api/paper_trading_routes.py`；实际：`api/v1/endpoints/paper_trading.py` | ⚠️ 路径偏差 |
| schema 文件位置 | ⚠️ 方案：`src/api/schemas/paper_trading.py`；实际：`api/v1/schemas/paper_trading.py` | ⚠️ 路径偏差 |
| 手动下单 `POST /orders` | ✅ `submit_manual_order` | ✅ |
| 手动撤单 `POST /orders/{id}/cancel` | ⚠️ 实际为 `POST /signals/{signal_id}/cancel` | ⚠️ 路径偏差 |
| 手动改单 `POST /orders/{id}/modify` | ⚠️ 实际为 `POST /signals/{signal_id}/modify` | ⚠️ 路径偏差 |
| AI 决策日志 `GET /decisions` | ✅ `GET /accounts/{id}/pm-decisions` | ✅ |
| 复盘笔记列表 `GET /reflections` | ✅ 已实现 | ✅ |
| 手动触发复盘 `POST /reflect` | ✅ `POST /accounts/{id}/reflections/daily` | ✅ |
| 作战卡列表 `GET /battle-plans` | ✅ 已实现 | ✅ |
| 手动生成作战卡 `POST /generate-battle-plan` | ✅ 已实现 | ✅ |
| 触发 PM 决策 `POST /pm-decision` | ✅ `POST /accounts/{id}/pm-decisions/trigger` | ✅ |
| Pydantic schemas | ✅ 30 个 schema 类全部实现 | ✅ |
| Listener 控制 API | ✅ 额外实现 status/start/stop（超出方案） | ✅ |

**差距说明**：
1. 文件路径从 `src/api/` 改为 `api/v1/`，这是**项目架构统一**的结果（与现有 `stocks.py`/`analysis.py` 等端点保持一致），属合理偏差。
2. 撤单/改单从 `orders/{id}/cancel` 改为 `signals/{signal_id}/cancel`——实际实现走 signal 维度而非 order 维度，与 P0-C 的 `TradingEngine.cancel_signal`/`modify_signal` 一致。但**方案还要求 `POST /orders/{id}/cancel` 直接按订单 ID 撤单**，当前 API 不支持按 order_id 撤单，只能按 signal_id 撤单。

---

### P3-B：WebUI 页面 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| PaperTradingPage 账户概览 | ✅ Vue 3 + Tailwind 实现 | ✅ |
| AI 决策时间线 | ✅ `decisions` ref + 时间线样式 | ✅ |
| 复盘笔记流 | ✅ `reflections` ref + `.reflection-card` 样式 | ✅ |
| 作战卡视图 | ✅ `battlePlan` ref | ✅ |
| 净值曲线图 | ✅ ECharts 实现 | ✅ |
| 前端函数清单 | ✅ 全部实现（1320 行 JS） | ✅ |
| 文件位置 | ✅ 与方案一致（`web/templates/` + `web/static/js/`） | ✅ |
| 技术栈 | ✅ Vue 3 + ECharts + Tailwind | ✅ |

**差距说明**：无显著差距，WebUI 实现完整。

---

### P3-C：配置 + 测试 + 文档 — ⚠️ 部分实现

#### 配置项

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `.env.example` 新增配置项 | ✅ 全部覆盖（30+ 项配置） | ✅ |
| `PAPER_TRADING_ENABLE_PM_AGENT` | ✅ 已实现 | ✅ |
| `PAPER_TRADING_ENABLE_REFLECTION` | ⚠️ 方案要求此开关；实际用 `LISTENER_ENABLE_DAILY_REFLECTION` 替代 | ⚠️ 命名偏差 |
| `PAPER_TRADING_ENABLE_BATTLE_PLAN` | ⚠️ 同上，实际用 `LISTENER_ENABLE_BATTLE_PLAN` | ⚠️ 命名偏差 |
| `PAPER_TRADING_ENABLE_AUTO_SLTP` | ❌ **未实现**独立开关；SLTP 通过 `sltp_calculator` 注入控制 | ❌ |

#### 集成测试

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `test_full_pm_agent_loop` | ⚠️ `_smoke_p3c_pm_agent.py` 部分覆盖，但不是正式 `tests/test_*.py` | ⚠️ |
| `test_cancel_modify_flow` | ⚠️ `_smoke_p3c_cancel_modify.py` 部分覆盖 | ⚠️ |
| `test_battle_plan_generation` | ⚠️ `_smoke_p3c_battle_plan.py` 部分覆盖 | ⚠️ |
| 正式 pytest 集成测试 | ❌ `tests/` 目录下无 paper_trading 相关测试 | ❌ |
| 指标单元测试（Fib 0.618=87.6） | ❌ 不存在 | ❌ |

#### 文档更新

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `README.md` 更新 | ✅ 已添加 paper trading 功能说明 | ✅ |
| `docs/CHANGELOG.md` 更新 | ✅ 已详细记录全部功能 | ✅ |

**差距说明**：
1. **测试是最大的系统性缺口**——5 个冒烟测试存在但都是 `_smoke_*.py` 脚本而非正式 `tests/test_*.py`，无法被 `pytest` 自动发现和执行，也无法纳入 CI。方案要求的 3 个集成测试场景均未正式实现。
2. **指标单元测试完全缺失**——方案验收标准明确要求"Fib 0.618 = 87.6"的合成数据验证，但不存在。
3. 3 个配置项命名与方案不一致，属轻微偏差。

---

## 三、差距汇总矩阵

| 编号 | 差距描述 | 严重度 | 任务 | 类型 |
|------|---------|--------|------|------|
| G1 | `PositionManager.unfreeze_quantity` 未实现 | 🔴 高 | P0-C | 功能缺失 |
| G2 | `ReflectionNote.to_markdown` 未实现 | 🟡 中 | P0-D | 功能缺失 |
| G3 | `PortfolioManagerAgent._inject_reflections` 未作为独立方法实现，`code_reflections` 维度缺失 | 🟡 中 | P0-E | 功能偏差 |
| G4 | `paper_trading_compute_sltp` 工具未注册到 PM Agent | 🟡 中 | P1-A | 功能缺失 |
| G5 | API 不支持按 `order_id` 撤单/改单（仅支持 `signal_id`） | 🟡 中 | P3-A | 功能缺失 |
| G6 | `tests/` 下无 paper_trading 正式测试（仅冒烟脚本） | 🔴 高 | P3-C | 测试缺口 |
| G7 | Fibonacci/ATR/SLTP 专项单元测试缺失 | 🟡 中 | P0-A/P1-A | 测试缺口 |
| G8 | `PAPER_TRADING_ENABLE_AUTO_SLTP` 配置开关未实现 | 🟢 低 | P3-C | 配置缺口 |
| G9 | `compute_indicators` 签名与方案不一致（`IndicatorSpec` vs `List[str]`） | 🟢 低 | P0-A | 合理偏差 |
| G10 | API/schema 文件路径从 `src/api/` 改为 `api/v1/` | 🟢 低 | P3-A | 合理偏差 |
| G11 | `_build_system_prompt` 改为模块级常量 | 🟢 低 | P0-B | 合理偏差 |
| G12 | `OrderStatus.MODIFIED` 未使用（cancel+create 替代） | 🟢 低 | P0-C | 合理偏差 |

---

## 四、建议行动清单

### 必须修复（P1 优先级）

1. **G1 - 实现 `PositionManager.unfreeze_quantity`**
   - 如果卖出限价单会冻结持仓，撤单时必须解冻。需检查 `OrderManager.cancel_order` 中卖出撤单路径是否调用了持仓解冻。

2. **G6 - 补充正式 pytest 测试**
   - 将 5 个 `_smoke_*.py` 脚本重构为 `tests/test_paper_trading_*.py`，纳入 pytest 体系
   - 至少覆盖：PM Agent 闭环、撤单改单流程、作战卡生成、SLTP 计算、指标计算

### 建议修复（P2 优先级）

3. **G2 - 实现 `ReflectionNote.to_markdown`**
   - 方案明确要求，且 API/通知场景需要直接调用

4. **G3 - 补充 `_inject_reflections` + `code_reflections` 维度**
   - 当前仅注入账户级通用摘要，缺少按股票代码分类的历史教训注入

5. **G4 - 注册 `paper_trading_compute_sltp` 工具**
   - PM Agent 应能预览三线，辅助决策

6. **G5 - API 补充按 `order_id` 撤单/改单端点**
   - 方案明确要求 `POST /orders/{id}/cancel` 和 `POST /orders/{id}/modify`

7. **G7 - 补充指标单元测试**
   - 合成数据验证 Fib/ATR/支撑阻力计算正确性

### 可选修复（P3 优先级）

8. **G8 - 添加 `PAPER_TRADING_ENABLE_AUTO_SLTP` 独立开关**
9. 更新方案文档，标注已实现的合理偏差（G9-G12），保持文档与代码一致

---

## 五、里程碑达成评估

| 里程碑 | 方案任务 | 实现状态 | 达成度 |
|--------|---------|---------|--------|
| M1: AI 自主决策闭环 | P0-A, P0-C, P0-B | ✅ 核心闭环已实现 | 95% |
| M2: 复盘反思系统 | P0-D, P0-E | ✅ 主体已实现，记忆注入有偏差 | 85% |
| M3: 智能止损止盈 | P1-A | ✅ 已实现，工具注册缺失 | 90% |
| M4: 次日作战卡 | P1-B, P1-C | ✅ 完整实现 | 100% |
| M5: 对外能力 | P3-A, P3-B | ✅ API + WebUI 完整 | 90% |
| M6: 内容沉淀 | P2-A, P2-B, P3-C | ⚠️ 内容+推送完整，测试薄弱 | 75% |

**总体实现度：约 85-90%**，核心创新闭环（AI 自主决策 → 撤单改单 → 复盘反思 → 记忆迭代）已全部打通，剩余差距主要是测试覆盖、个别方法缺失和配置细节。
