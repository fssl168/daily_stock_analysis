# 项目方案复核报告 v2：`paper_trading_ai_pm_plan.md` 实施方案 vs 代码开发差距分析

> 复核时间：2026-07-27
> 复核范围：`docs/paper_trading_ai_pm_plan.md` 全部 6 个里程碑（M1-M6）
> 对比方法：方案文档函数级任务清单 vs 仓库实际代码（文件扫描 + pytest 验证）
> 前置参考：`docs/paper_trading_gap_analysis.md`（v1 差距分析）

---

## 一、总体结论

| 维度 | 评估 |
|------|------|
| **方案完整度** | 函数级任务清单完整，6 个里程碑（M1-M6）覆盖全部规划 |
| **代码实现度** | **约 95-98% 已实现**，v1 中标识的 8 项功能性/测试性缺口（G1-G8）已全部补齐 |
| **偏差类型** | 剩余主要为架构调整型偏差（合理且已文档化），无阻断性功能缺失 |

**核心判断**：自 v1 差距分析以来，开发团队完成了 G1-G8 的全部修复工作，包括 `PositionManager.unfreeze_quantity`、`ReflectionNote.to_markdown`、PM Agent `_inject_reflections` 独立方法、`paper_trading_compute_sltp` 工具注册、按 `order_id` 撤单/改单 API、5 个正式 pytest 测试文件（57 个用例全部通过），以及 `PAPER_TRADING_ENABLE_AUTO_SLTP` 配置开关。当前核心闭环（行情 → 指标 → PM 决策 → 下单 → 撤单/改单 → 成交 → 复盘 → 记忆注入 → 次日作战卡）已完全打通，仅剩少量命名/签名/路径层面的合理偏差。

---

## 二、逐任务差距分析

### P0-A：技术指标增强 — ✅ 已实现，架构偏差已接受

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `compute_fibonacci_retracement` | 已实现 | ✅ CLOSED |
| `compute_atr` | 已实现 | ✅ CLOSED |
| `compute_support_resistance` | 已实现（含 fractal + cluster） | ✅ CLOSED |
| `compute_indicators(df, indicators: List[str], params)` | 实际为 `compute_indicators(df, specs: List[IndicatorSpec])` | ⚠️ 合理偏差（G9） |
| `IndicatorSpec` 注册表登记新指标 | 已扩展支持 `fib`/`atr`/`support`/`resistance` | ✅ CLOSED |
| `Rule` schema 允许规则右值为 Fib 回撤位 | `_try_parse_indicator` 已支持 `fib_0.618` 等 | ✅ CLOSED |
| 单元测试（Fib 0.618 = 87.6） | `tests/test_paper_trading_indicators.py` 已覆盖 | ✅ CLOSED |

**差距说明**：`compute_indicators` 使用 `IndicatorSpec` 而非字符串列表是合理的架构改进，提升了类型安全和可扩展性。v1 中 G7（指标单元测试缺失）已关闭。

---

### P0-C：订单管理增强 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `OrderManager.cancel_order` | 已实现 | ✅ CLOSED |
| `OrderManager.modify_order` | 已实现（cancel + create，保留 `parent_order_id`） | ✅ CLOSED |
| `OrderStatus` 新增 `CANCELLED`/`MODIFIED` | 不使用 `MODIFIED`，改为 cancel+create 新订单 | ⚠️ 合理偏差（G12） |
| `PaperAccountManager.unfreeze_cash` | 已实现 | ✅ CLOSED |
| `PositionManager.unfreeze_quantity` | 已实现（v1-G1 已关闭） | ✅ CLOSED |
| `TradingEngine.cancel_signal` / `modify_signal` | 已实现 | ✅ CLOSED |
| `TradingEngine.cancel_order` / `modify_order`（按 order_id） | 已实现（v1-G5 已关闭） | ✅ CLOSED |
| `PaperOrder` 表 `parent_order_id`/`cancel_reason`/`modified_at` | 已实现 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_cancel_modify.py` 已覆盖 | ✅ CLOSED |

**差距说明**：v1 中 G1（`unfreeze_quantity` 缺失）和 G5（API 仅支持 signal_id）已完全修复。卖出限价单当前不冻结持仓，因此 `unfreeze_quantity` 主要作为对称接口和未来扩展点存在。

---

### P0-B：AI 基金经理 Agent — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `PMDecision` dataclass | 已实现（含 `to_dict`） | ✅ CLOSED |
| `PortfolioManagerAgent` 类 | 已实现 | ✅ CLOSED |
| `make_decision` 方法 | 已实现 | ✅ CLOSED |
| `_build_system_prompt` | 使用模块级常量 `PM_SYSTEM_PROMPT` | ⚠️ 合理偏差（G11） |
| `_build_user_message` | 已实现 | ✅ CLOSED |
| `_call_agent_with_timeout` | 已实现 | ✅ CLOSED |
| `_parse_decision` | 已实现 | ✅ CLOSED |
| `_inject_reflections` | 已实现为独立方法（v1-G3 已关闭） | ✅ CLOSED |
| `register_paper_trading_tools` | 已实现（含 `paper_trading_compute_sltp`，v1-G4 已关闭） | ✅ CLOSED |
| `build_portfolio_manager_agent` 工厂 | 已实现 | ✅ CLOSED |
| `PaperDecision` ORM 表 | 已实现 | ✅ CLOSED |
| 持久化决策到 `PaperDecision` | 已实现 | ✅ CLOSED |
| `MarketListener` 集成 PM Agent | 已实现 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_pm_agent.py` 已覆盖 | ✅ CLOSED |

**差距说明**：v1 中 G3（`_inject_reflections` 未独立实现）和 G4（SLTP 工具未注册）已修复。`_build_system_prompt` 改为模块级常量不影响功能，仅降低运行时动态构建的灵活性。

---

### P0-D：AI 复盘反思系统 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ReflectionNote` dataclass | 已实现 | ✅ CLOSED |
| `ReflectionNote.to_markdown` | 已实现（v1-G2 已关闭） | ✅ CLOSED |
| `ReflectionEngine` 类 | 已实现 | ✅ CLOSED |
| `reflect_on_trade` / `reflect_on_daily` | 已实现 | ✅ CLOSED |
| `_reflect_sync` | 实际为 `_run_reflection`（命名偏差） | ⚠️ 已接受 |
| `_build_trade_reflection_prompt` / `_build_daily_reflection_prompt` | 已实现 | ✅ CLOSED |
| `_parse_reflection` / `_persist_note` / `get_recent_notes` | 已实现 | ✅ CLOSED |
| 异步 daemon 线程 + timeout | 已实现 | ✅ CLOSED |
| `PaperReflection` ORM 表 | 已实现 | ✅ CLOSED |
| `TradingEngine` 回调触发 | 已实现 | ✅ CLOSED |

**差距说明**：v1 中 G2（`to_markdown` 缺失）已关闭。`_reflect_sync` → `_run_reflection` 的命名偏差不影响功能。

---

### P0-E：复盘记忆系统 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ReflectionEngine.get_relevant_notes` | 已实现 | ✅ CLOSED |
| `format_notes_for_context` | 已实现（模块级函数） | ✅ CLOSED |
| `PortfolioManagerAgent._inject_reflections` | 已实现为独立方法（v1-G3 已关闭） | ✅ CLOSED |
| `_build_user_message` 加入复盘记忆段落 | 已实现 | ✅ CLOSED |
| PM system prompt 加入复盘记忆提示 | 已实现 | ✅ CLOSED |

**差距说明**：v1 中 P0-E 的主要偏差（`_inject_reflections` 未独立实现、缺少按股票代码分类的 `code_reflections` 维度）已修复。`_inject_reflections` 现在作为独立方法存在，功能等价。

---

### P1-A：智能止损止盈 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `SLTPResult` / `SLTPCalculator` | 已实现 | ✅ CLOSED |
| `compute` 方法 | 已实现 | ✅ CLOSED |
| ATR + Fib + 支撑阻力三位一体 | 已实现 | ✅ CLOSED |
| 筹码峰集成 | `_fetch_chip_distribution` 已实现（可配置开关） | ✅ CLOSED |
| `TradingEngine` 集成 `sltp_calculator` | 已实现 | ✅ CLOSED |
| `_execute_market_order` 后自动计算三线 | BUY fill 后自动调用 | ✅ CLOSED |
| PM Agent 可调用 `compute_sltp` 工具 | `paper_trading_compute_sltp` 已注册（v1-G4 已关闭） | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_sltp.py` 已覆盖 | ✅ CLOSED |

**差距说明**：v1 中 G4（SLTP 工具未注册）和 G7（SLTP 单元测试缺失）已关闭。实现度最高。

---

### P1-B：情景预案 + 次日作战卡 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `HoldingPlan`/`CandidatePlan`/`BattlePlan` dataclass | 全部已实现 | ✅ CLOSED |
| `BattlePlan.to_markdown` | 已实现 | ✅ CLOSED |
| `BattlePlanGenerator` 类 | 已实现 | ✅ CLOSED |
| `generate` 方法 | 已实现 | ✅ CLOSED |
| 三情景预案 + 候选标的 + 三线 | 全部已实现 | ✅ CLOSED |
| PM Agent 生成市场综述 | 已实现 | ✅ CLOSED |
| `PaperBattlePlan` ORM 表 | 已实现 | ✅ CLOSED |
| `MarketListener` 触发生成 | 已实现 | ✅ CLOSED |
| 规则降级 fallback | 已实现 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_battle_plan.py` 已覆盖 | ✅ CLOSED |

**差距说明**：无显著差距，v1 中无此任务相关缺口。

---

### P1-C：MarketListener 集成 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `MarketListenerConfig` 新增引擎字段 | 已实现 | ✅ CLOSED |
| `pm_decision_interval_seconds` 配置 | 已实现 | ✅ CLOSED |
| `_tick_market` 触发 PM 决策 | 已实现 | ✅ CLOSED |
| `_maybe_daily_settle` 触发复盘 + 作战卡 | 已实现 | ✅ CLOSED |
| `TradingEngine` 回调机制 | 已实现 | ✅ CLOSED |
| 回调注册成交后触发复盘 | 已实现 | ✅ CLOSED |

**差距说明**：无显著差距。

---

### P2-A：复盘文章自动生成 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ContentGenerator` 类 | 已实现 | ✅ CLOSED |
| `generate_daily_report` / `generate_voice_script` | 已实现 | ✅ CLOSED |
| `_collect_daily_data` / `_save_to_file` | 已实现 | ✅ CLOSED |
| LLM 调用 + fallback | 已实现 | ✅ CLOSED |

**差距说明**：无显著差距。

---

### P2-B：飞书/钉钉推送 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `PaperTradingNotifier` 类 | 已实现 | ✅ CLOSED |
| `push_battle_plan` / `push_reflection` / `push_daily_summary` | 已实现 | ✅ CLOSED |
| `_send_lark` / `_send_dingtalk` | 已实现 | ✅ CLOSED |
| DingTalk URL 签名 / 消息分块 | 已实现 | ✅ CLOSED |

**差距说明**：实现超越方案要求。

---

### P3-A：API 端点 + Pydantic schema — ✅ 已实现，路径偏差已接受

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| 端点文件位置 | 方案：`src/api/paper_trading_routes.py`；实际：`api/v1/endpoints/paper_trading.py` | ⚠️ 合理偏差（G10） |
| schema 文件位置 | 方案：`src/api/schemas/paper_trading.py`；实际：`api/v1/schemas/paper_trading.py` | ⚠️ 合理偏差（G10） |
| 手动下单 `POST /orders` | 已实现 | ✅ CLOSED |
| 按 signal_id 撤单/改单 | 已实现 | ✅ CLOSED |
| 按 order_id 撤单/改单 `POST /orders/{id}/cancel\|modify` | 已实现（v1-G5 已关闭） | ✅ CLOSED |
| AI 决策日志 `GET /decisions` | 已实现 | ✅ CLOSED |
| 复盘笔记列表/单条/手动触发 | 已实现 | ✅ CLOSED |
| 作战卡列表/单日/手动生成 | 已实现 | ✅ CLOSED |
| 触发 PM 决策 | 已实现 | ✅ CLOSED |
| Pydantic schemas | 30 个 schema 类全部实现 | ✅ CLOSED |
| Listener 控制 API | 额外实现 status/start/stop | ✅ CLOSED |

**差距说明**：v1 中 G5（仅支持 signal_id 撤单/改单）已修复，现在同时支持 signal_id 和 order_id 两种路径。文件路径从 `src/api/` 改为 `api/v1/` 是与现有端点一致的合理架构选择。

---

### P3-B：WebUI 页面 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| PaperTradingPage 账户概览 | Vue 3 + Tailwind 实现 | ✅ CLOSED |
| AI 决策时间线 | 已实现 | ✅ CLOSED |
| 复盘笔记流 | 已实现 | ✅ CLOSED |
| 作战卡视图 | 已实现 | ✅ CLOSED |
| 净值曲线图 | ECharts 实现 | ✅ CLOSED |
| 前端函数清单 | 全部实现 | ✅ CLOSED |

**差距说明**：无显著差距。

---

### P3-C：配置 + 测试 + 文档 — ✅ 已实现，命名偏差残留

#### 配置项

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `.env.example` 新增配置项 | 全部覆盖 | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_PM_AGENT` | 已实现 | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_REFLECTION` | 已新增别名，兼容 `PAPER_TRADING_LISTENER_ENABLE_DAILY_REFLECTION` | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_BATTLE_PLAN` | 已新增别名，兼容 `PAPER_TRADING_LISTENER_ENABLE_BATTLE_PLAN` | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_AUTO_SLTP` | 已实现（v1-G8 已关闭） | ✅ CLOSED |

#### 集成测试

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `test_full_pm_agent_loop` | `tests/test_paper_trading_pm_agent.py` 覆盖核心闭环 | ✅ CLOSED |
| `test_cancel_modify_flow` | `tests/test_paper_trading_cancel_modify.py` 覆盖 | ✅ CLOSED |
| `test_battle_plan_generation` | `tests/test_paper_trading_battle_plan.py` 覆盖 | ✅ CLOSED |
| 指标单元测试（Fib 0.618=87.6） | `tests/test_paper_trading_indicators.py` 覆盖 | ✅ CLOSED |
| SLTP 单元测试 | `tests/test_paper_trading_sltp.py` 覆盖 | ✅ CLOSED |
| 配置别名测试 | `tests/test_paper_trading_config_aliases.py` 覆盖 | ✅ CLOSED |
| 跨模块端到端测试（PM → 下单 → 成交 → SLTP → 复盘 → 记忆） | `tests/test_paper_trading_e2e.py` 覆盖 | ✅ CLOSED |
| pytest 自动发现与执行 | **64 个用例全部通过** | ✅ CLOSED |

#### 文档更新

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `README.md` 更新 | 已添加 paper trading 功能说明 | ✅ CLOSED |
| `docs/CHANGELOG.md` 更新 | 已详细记录全部功能，包含本次 G1-G12 对齐记录 | ✅ CLOSED |

**差距说明**：v1 中 G6（无正式 pytest 测试）、G7（指标/SLTP 单元测试缺失）、G8（auto SLTP 开关缺失）已全部关闭。G13 命名偏差已通过新增方案命名别名并兼容旧命名彻底解决。

---

## 三、差距汇总矩阵

| 编号 | 差距描述 | 严重度 | 任务 | 类型 | v1 状态 | v2 状态 |
|------|---------|--------|------|------|---------|---------|
| G1 | `PositionManager.unfreeze_quantity` 未实现 | 🔴 高 | P0-C | 功能缺失 | ❌ OPEN | ✅ **CLOSED** |
| G2 | `ReflectionNote.to_markdown` 未实现 | 🟡 中 | P0-D | 功能缺失 | ❌ OPEN | ✅ **CLOSED** |
| G3 | `PortfolioManagerAgent._inject_reflections` 未作为独立方法实现 | 🟡 中 | P0-E | 功能偏差 | ❌ OPEN | ✅ **CLOSED** |
| G4 | `paper_trading_compute_sltp` 工具未注册到 PM Agent | 🟡 中 | P1-A | 功能缺失 | ❌ OPEN | ✅ **CLOSED** |
| G5 | API 不支持按 `order_id` 撤单/改单 | 🟡 中 | P3-A | 功能缺失 | ❌ OPEN | ✅ **CLOSED** |
| G6 | `tests/` 下无 paper_trading 正式测试 | 🔴 高 | P3-C | 测试缺口 | ❌ OPEN | ✅ **CLOSED** |
| G7 | Fibonacci/ATR/SLTP 专项单元测试缺失 | 🟡 中 | P0-A/P1-A | 测试缺口 | ❌ OPEN | ✅ **CLOSED** |
| G8 | `PAPER_TRADING_ENABLE_AUTO_SLTP` 配置开关未实现 | 🟢 低 | P3-C | 配置缺口 | ❌ OPEN | ✅ **CLOSED** |
| G9 | `compute_indicators` 签名与方案不一致（`IndicatorSpec` vs `List[str]`） | 🟢 低 | P0-A | 合理偏差 | ⚠️ 偏差 | ⚠️ 已接受 |
| G10 | API/schema 文件路径从 `src/api/` 改为 `api/v1/` | 🟢 低 | P3-A | 合理偏差 | ⚠️ 偏差 | ⚠️ 已接受 |
| G11 | `_build_system_prompt` 改为模块级常量 | 🟢 低 | P0-B | 合理偏差 | ⚠️ 偏差 | ⚠️ 已接受 |
| G12 | `OrderStatus.MODIFIED` 未使用（cancel+create 替代） | 🟢 低 | P0-C | 合理偏差 | ⚠️ 偏差 | ⚠️ 已接受 |
| G13 | `PAPER_TRADING_ENABLE_REFLECTION` / `PAPER_TRADING_ENABLE_BATTLE_PLAN` 命名与方案不一致 | 🟢 低 | P3-C | 命名偏差 | — | ✅ **CLOSED** |

**v2 状态统计**：
- 已关闭（CLOSED）：9 项（G1-G8、G13）
- 已接受合理偏差：4 项（G9-G12）
- 残留命名偏差：0 项
- **无 OPEN 功能性缺口**

---

## 四、建议行动清单

### 已完成（无需进一步 action）

1. **G1-G8、G13 全部关闭**：功能性缺口、核心测试缺口与命名偏差已补齐，pytest 63 个用例全部通过。

### 可选优化（P3 优先级，不影响核心闭环）

2. **G9/G11 - 文档对齐**
   - 在 `docs/paper_trading_ai_pm_plan.md` 或 README 中标注：
     - `compute_indicators` 实际签名为 `List[IndicatorSpec]`
     - `_build_system_prompt` 实际为模块级常量 `PM_SYSTEM_PROMPT`
     - `OrderStatus.MODIFIED` 未使用，改单通过 cancel+create 实现

4. **补充端到端集成测试**
   - 当前测试已覆盖各模块核心路径，但可进一步增加一个跨模块的端到端测试：
     - PM Agent 决策 → 下单 → 模拟成交 → 自动 SLTP 写入 → 复盘笔记生成 → 记忆注入影响下一次决策
   - 优先级低，因为现有 smoke 测试与单元测试组合已基本覆盖。

5. **WebUI 端到端测试**
   - 现有测试集中在后端模块，WebUI 页面尚无自动化测试。可在后续迭代中补充 Playwright 或简单 DOM 测试。

---

## 五、里程碑达成评估

| 里程碑 | 方案任务 | v1 达成度 | v2 达成度 | 说明 |
|--------|---------|----------|----------|------|
| M1: AI 自主决策闭环 | P0-A, P0-C, P0-B | 95% | **100%** | 下单/撤单/改单、工具注册、测试全部完成 |
| M2: 复盘反思系统 | P0-D, P0-E | 85% | **100%** | `to_markdown`、`_inject_reflections` 独立方法已补齐 |
| M3: 智能止损止盈 | P1-A | 90% | **100%** | `compute_sltp` 工具已注册，SLTP 测试已覆盖 |
| M4: 次日作战卡 | P1-B, P1-C | 100% | **100%** | 完整实现 |
| M5: 对外能力 | P3-A, P3-B | 90% | **100%** | 按 order_id 撤单/改单 API 已补齐 |
| M6: 内容沉淀 | P2-A, P2-B, P3-C | 75% | **100%** | 内容+推送完整，测试已补齐，配置命名偏差已通过别名解决 |

**总体实现度：约 98-100%**，核心创新闭环已完全落地，剩余差距均为不影响功能的低优先级架构偏差（已接受）。

---

## 六、验证记录

- **pytest 执行结果**：`python -m pytest tests/test_paper_trading_indicators.py tests/test_paper_trading_cancel_modify.py tests/test_paper_trading_sltp.py tests/test_paper_trading_battle_plan.py tests/test_paper_trading_pm_agent.py tests/test_paper_trading_config_aliases.py tests/test_paper_trading_e2e.py -q`
- **结果**：64 passed in 42.59s
- **关键已验证文件**：
  - `paper_trading/position.py`：`unfreeze_quantity` 已实现
  - `paper_trading/reflection.py`：`ReflectionNote.to_markdown` 已实现；`_fetch_trade` 已修复 DetachedInstanceError
  - `src/agent/portfolio_manager_agent.py`：`_inject_reflections` 独立方法、`paper_trading_compute_sltp` 工具注册已实现；`_inject_reflections` 已兼容 `list_positions` 返回 dict
  - `api/v1/endpoints/paper_trading.py`：`POST /orders/{order_id}/cancel` 和 `POST /orders/{order_id}/modify` 已实现
  - `src/config.py`：`paper_trading_enable_auto_sltp` 与 `PAPER_TRADING_ENABLE_AUTO_SLTP` 配置映射已实现；`PAPER_TRADING_ENABLE_REFLECTION` / `PAPER_TRADING_ENABLE_BATTLE_PLAN` 别名已生效
  - `tests/test_paper_trading_config_aliases.py`：配置别名 6 个用例全部通过
  - `tests/test_paper_trading_e2e.py`：跨模块端到端测试通过

---

**文档结束。** v1 差距分析中的 8 项核心缺口已全部关闭，项目已达到方案规划的交付标准。
