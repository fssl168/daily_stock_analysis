# 项目方案复核报告 v3：`paper_trading_ai_pm_plan.md` 实施方案 vs 代码开发差距分析

> 复核时间：2026-07-27  
> 复核范围：`docs/paper_trading_ai_pm_plan.md` 全部 6 个里程碑（M1-M6）  
> 基线版本：`0429e94`（已合并 origin/main 后的 main 分支）  
> 前置参考：`docs/paper_trading_gap_analysis_v2.md`  
> 对比方法：方案文档函数级任务清单 vs 仓库实际代码（文件扫描 + pytest + lint + Playwright 验证）

---

## 一、总体结论

| 维度 | 评估 |
|------|------|
| **方案完整度** | 函数级任务清单完整，6 个里程碑覆盖全部规划 |
| **代码实现度** | **约 97-99% 已实现**，v2 中标识的 9 项功能性/测试性缺口（G1-G8、G13）已全部关闭 |
| **偏差类型** | 剩余为架构/实现细节层面的合理偏差，无阻断性功能缺失 |
| **验证状态** | 后端 64 个 pytest 用例全部通过；前端 lint 通过；Playwright 模拟交易 6/6 通过 |

**核心判断**：模拟交易核心闭环（行情 → 指标 → PM 决策 → 下单 → 撤单/改单 → 成交 → SLTP → 复盘 → 记忆注入 → 次日作战卡 → 通知推送）已完全打通。当前基线已推送到 `https://github.com/fssl168/daily_stock_analysis.git` 的 main 分支，与远程保持一致。

---

## 二、验证执行记录

### 2.1 后端验证

```bash
python -m pytest tests/test_paper_trading_sltp.py tests/test_paper_trading_pm_agent.py tests/test_paper_trading_indicators.py tests/test_paper_trading_e2e.py tests/test_paper_trading_config_aliases.py tests/test_paper_trading_cancel_modify.py tests/test_paper_trading_battle_plan.py -q
```

结果：**64 passed, 1 warning in 16.16s**

- `test_paper_trading_sltp.py`: 9 passed
- `test_paper_trading_pm_agent.py`: 8 passed
- `test_paper_trading_indicators.py`: 16 passed
- `test_paper_trading_e2e.py`: 1 passed
- `test_paper_trading_config_aliases.py`: 6 passed
- `test_paper_trading_cancel_modify.py`: 11 passed
- `test_paper_trading_battle_plan.py`: 13 passed

### 2.2 前端验证

```bash
cd apps/dsa-web
npm run lint        # 通过
npm run test:e2e    # 6 passed, 12 skipped, 0 failed
```

- `paper-trading.spec.ts`: 6/6 通过
- 其他 12 个为 smoke / report-markdown 测试，因需要后端服务与 `DSA_WEB_SMOKE_PASSWORD` 环境变量而跳过

### 2.3 代码扫描

- `paper_trading/` 目录无 `TODO` / `FIXME` / `NotImplementedError` 残留。
- 方案中列出的关键类/函数均已实现：
  - `compute_fibonacci_retracement`、`compute_atr`、`compute_support_resistance`
  - `PortfolioManagerAgent`、`PMDecision`、`build_portfolio_manager_agent`
  - `register_paper_trading_tools`、`paper_trading_compute_sltp`
  - `ReflectionEngine`、`ReflectionNote.to_markdown`
  - `SLTPCalculator`、`BattlePlanGenerator`、`ContentGenerator`、`PaperTradingNotifier`
  - `OrderManager.cancel_order` / `modify_order`、`TradingEngine.cancel_order` / `modify_order`
  - 全部 ORM 模型：`PaperAccount`、`PaperPosition`、`PaperOrder`、`PaperTrade`、`PaperSignal`、`PaperNetValue`、`PaperDecision`、`PaperReflection`、`PaperBattlePlan`

---

## 三、逐任务差距分析（v3 复核）

### P0-A：技术指标增强 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `compute_fibonacci_retracement` | 已实现 | ✅ CLOSED |
| `compute_atr` | 已实现 | ✅ CLOSED |
| `compute_support_resistance` | 已实现（fractal + cluster） | ✅ CLOSED |
| `compute_indicators(df, indicators: List[str], params)` | 实际为 `compute_indicators(df, specs: List[IndicatorSpec])` | ⚠️ 已接受偏差 |
| `IndicatorSpec` 登记新指标 | 已支持 `fib`/`atr`/`support`/`resistance` | ✅ CLOSED |
| `Rule` schema 允许规则右值为 Fib 回撤位 | 已支持 `fib_0.618` 等 | ✅ CLOSED |
| 单元测试（Fib 0.618 = 87.6） | `tests/test_paper_trading_indicators.py` 已覆盖 | ✅ CLOSED |

### P0-B：AI 基金经理 Agent — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `PMDecision` dataclass | 已实现 | ✅ CLOSED |
| `PortfolioManagerAgent` 类 | 已实现 | ✅ CLOSED |
| `make_decision` | 已实现 | ✅ CLOSED |
| `_build_system_prompt` | 模块级常量 `PM_SYSTEM_PROMPT` | ⚠️ 已接受偏差 |
| `_build_user_message` | 已实现 | ✅ CLOSED |
| `_call_agent_with_timeout` | 已实现 | ✅ CLOSED |
| `_parse_decision` | 已实现 | ✅ CLOSED |
| `_inject_reflections` | 已实现为独立方法 | ✅ CLOSED |
| `register_paper_trading_tools` | 已实现 | ✅ CLOSED |
| `build_portfolio_manager_agent` | 已实现 | ✅ CLOSED |
| `PaperDecision` ORM 表 | 已实现 | ✅ CLOSED |
| 持久化决策 | 已实现 | ✅ CLOSED |
| `MarketListener` 集成 PM Agent | 已实现 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_pm_agent.py` 已覆盖 | ✅ CLOSED |

### P0-C：订单管理增强 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `OrderManager.cancel_order` | 已实现 | ✅ CLOSED |
| `OrderManager.modify_order` | 已实现（cancel + create） | ✅ CLOSED |
| `OrderStatus` 新增 `CANCELLED`/`MODIFIED` | 未使用 `MODIFIED`，改单通过 cancel+create 实现 | ⚠️ 已接受偏差 |
| `PaperAccountManager.unfreeze_cash` | 已实现 | ✅ CLOSED |
| `PositionManager.unfreeze_quantity` | 已实现 | ✅ CLOSED |
| `TradingEngine.cancel_signal` / `modify_signal` | 已实现 | ✅ CLOSED |
| `TradingEngine.cancel_order` / `modify_order`（按 order_id） | 已实现 | ✅ CLOSED |
| `PaperOrder` 相关字段 | 已实现 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_cancel_modify.py` 已覆盖 | ✅ CLOSED |

### P0-D：AI 复盘反思系统 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ReflectionNote` dataclass | 已实现 | ✅ CLOSED |
| `ReflectionNote.to_markdown` | 已实现 | ✅ CLOSED |
| `ReflectionEngine` 类 | 已实现 | ✅ CLOSED |
| `reflect_on_trade` / `reflect_on_daily` | 已实现 | ✅ CLOSED |
| `_reflect_sync` | 实际为 `_run_reflection` | ⚠️ 已接受偏差 |
| 其他方法 | 已实现 | ✅ CLOSED |

### P0-E：复盘记忆系统 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `get_relevant_notes` | 已实现 | ✅ CLOSED |
| `format_notes_for_context` | 已实现 | ✅ CLOSED |
| `_inject_reflections` | 已实现 | ✅ CLOSED |
| 上下文注入 | 已实现 | ✅ CLOSED |

### P1-A：智能止损止盈 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `SLTPResult` / `SLTPCalculator` | 已实现 | ✅ CLOSED |
| `compute` | 已实现 | ✅ CLOSED |
| ATR + Fib + 支撑阻力三位一体 | 已实现 | ✅ CLOSED |
| 筹码峰集成 | 已实现（可配置开关） | ✅ CLOSED |
| `TradingEngine` 集成 | 已实现 | ✅ CLOSED |
| PM Agent 可调用 `compute_sltp` | `paper_trading_compute_sltp` 已注册 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_sltp.py` 已覆盖 | ✅ CLOSED |

### P1-B：情景预案 + 次日作战卡 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `HoldingPlan`/`CandidatePlan`/`BattlePlan` | 全部已实现 | ✅ CLOSED |
| `BattlePlan.to_markdown` | 已实现 | ✅ CLOSED |
| `BattlePlanGenerator` | 已实现 | ✅ CLOSED |
| `generate` | 已实现 | ✅ CLOSED |
| 三情景预案 + 候选标的 + 三线 | 全部已实现 | ✅ CLOSED |
| `PaperBattlePlan` ORM 表 | 已实现 | ✅ CLOSED |
| `MarketListener` 触发 | 已实现 | ✅ CLOSED |
| 单元测试 | `tests/test_paper_trading_battle_plan.py` 已覆盖 | ✅ CLOSED |

### P1-C：MarketListener 集成 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `MarketListenerConfig` 引擎字段 | 已实现 | ✅ CLOSED |
| `pm_decision_interval_seconds` | 已实现 | ✅ CLOSED |
| `_tick_market` 触发 PM 决策 | 已实现 | ✅ CLOSED |
| `_maybe_daily_settle` 触发复盘 + 作战卡 | 已实现 | ✅ CLOSED |
| `TradingEngine` 回调机制 | 已实现 | ✅ CLOSED |

### P2-A：复盘文章自动生成 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `ContentGenerator` | 已实现 | ✅ CLOSED |
| `generate_daily_report` / `generate_voice_script` | 已实现 | ✅ CLOSED |
| `_collect_daily_data` / `_save_to_file` | 已实现 | ✅ CLOSED |
| LLM 调用 + fallback | 已实现 | ✅ CLOSED |

### P2-B：飞书/钉钉推送 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `PaperTradingNotifier` | 已实现 | ✅ CLOSED |
| `push_battle_plan` / `push_reflection` / `push_daily_summary` | 已实现 | ✅ CLOSED |
| `_send_lark` / `_send_dingtalk` | 已实现 | ✅ CLOSED |
| 钉钉 URL 签名 / 消息分块 | 已实现 | ✅ CLOSED |

### P3-A：API 端点 + Pydantic schema — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| 端点文件位置 | 方案：`src/api/paper_trading_routes.py`；实际：`api/v1/endpoints/paper_trading.py` | ⚠️ 已接受偏差 |
| schema 文件位置 | 方案：`src/api/schemas/paper_trading.py`；实际：`api/v1/schemas/paper_trading.py` | ⚠️ 已接受偏差 |
| 手动下单 `POST /orders` | 已实现 | ✅ CLOSED |
| 按 signal_id 撤单/改单 | 已实现 | ✅ CLOSED |
| 按 order_id 撤单/改单 | 已实现 | ✅ CLOSED |
| AI 决策日志 `GET /decisions` | 已实现 | ✅ CLOSED |
| 复盘笔记列表/单条/手动触发 | 已实现 | ✅ CLOSED |
| 作战卡列表/单日/手动生成 | 已实现 | ✅ CLOSED |
| 触发 PM 决策 | 已实现 | ✅ CLOSED |
| Pydantic schemas | 30 个 schema 类全部实现 | ✅ CLOSED |
| Listener 控制 API | 额外实现 status/start/stop | ✅ CLOSED |

### P3-B：WebUI 页面 — ✅ 已实现，存在一处实现偏差

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| PaperTradingPage 账户概览 | 已实现 | ✅ CLOSED |
| AI 决策时间线 | 已实现 | ✅ CLOSED |
| 复盘笔记流 | 已实现 | ✅ CLOSED |
| 作战卡视图 | 已实现 | ✅ CLOSED |
| 净值曲线图 | 方案要求 ECharts；实际使用原生 SVG Sparkline | ⚠️ 偏差 |
| 前端函数清单 | 全部实现 | ✅ CLOSED |

**偏差说明**：前端项目未引入 echarts/recharts 等图表库，当前使用原生 SVG 实现净值曲线。功能等效，但可视化能力和交互性弱于 ECharts。若后续需要更复杂的图表（缩放、tooltip、多指标叠加），建议引入轻量图表库。

### P3-C：配置 + 测试 + 文档 — ✅ 已实现

| 方案要求 | 实际实现 | 状态 |
|---------|---------|------|
| `.env.example` 新增配置项 | 全部覆盖 | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_PM_AGENT` | 已实现 | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_REFLECTION` | 已实现（兼容旧命名） | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_BATTLE_PLAN` | 已实现（兼容旧命名） | ✅ CLOSED |
| `PAPER_TRADING_ENABLE_AUTO_SLTP` | 已实现 | ✅ CLOSED |
| 集成测试 | 64 个用例全部通过 | ✅ CLOSED |
| 跨模块端到端测试 | `tests/test_paper_trading_e2e.py` 已覆盖 | ✅ CLOSED |
| `README.md` / `CHANGELOG.md` 更新 | 已更新 | ✅ CLOSED |

---

## 四、差距汇总矩阵（v3）

| 编号 | 差距描述 | 严重度 | 任务 | 类型 | v3 状态 |
|------|---------|--------|------|------|---------|
| G1 | `PositionManager.unfreeze_quantity` 未实现 | 🔴 高 | P0-C | 功能缺失 | ✅ **CLOSED** |
| G2 | `ReflectionNote.to_markdown` 未实现 | 🟡 中 | P0-D | 功能缺失 | ✅ **CLOSED** |
| G3 | `_inject_reflections` 未作为独立方法实现 | 🟡 中 | P0-E | 功能偏差 | ✅ **CLOSED** |
| G4 | `paper_trading_compute_sltp` 工具未注册 | 🟡 中 | P1-A | 功能缺失 | ✅ **CLOSED** |
| G5 | API 不支持按 `order_id` 撤单/改单 | 🟡 中 | P3-A | 功能缺失 | ✅ **CLOSED** |
| G6 | `tests/` 下无 paper_trading 正式测试 | 🔴 高 | P3-C | 测试缺口 | ✅ **CLOSED** |
| G7 | Fibonacci/ATR/SLTP 专项单元测试缺失 | 🟡 中 | P0-A/P1-A | 测试缺口 | ✅ **CLOSED** |
| G8 | `PAPER_TRADING_ENABLE_AUTO_SLTP` 配置开关未实现 | 🟢 低 | P3-C | 配置缺口 | ✅ **CLOSED** |
| G9 | `compute_indicators` 签名与方案不一致 | 🟢 低 | P0-A | 合理偏差 | ⚠️ 已接受 |
| G10 | API/schema 文件路径从 `src/api/` 改为 `api/v1/` | 🟢 低 | P3-A | 合理偏差 | ⚠️ 已接受 |
| G11 | `_build_system_prompt` 改为模块级常量 | 🟢 低 | P0-B | 合理偏差 | ⚠️ 已接受 |
| G12 | `OrderStatus.MODIFIED` 未使用 | 🟢 低 | P0-C | 合理偏差 | ⚠️ 已接受 |
| G13 | 配置命名与方案不一致 | 🟢 低 | P3-C | 命名偏差 | ✅ **CLOSED** |
| G14 | WebUI 净值曲线使用 SVG 而非 ECharts | 🟢 低 | P3-B | 实现偏差 | ⚠️ 已接受 |

**v3 状态统计**：
- 已关闭（CLOSED）：10 项（G1-G8、G13、新增验证）
- 已接受合理偏差：5 项（G9-G12、G14）
- 残留 OPEN 缺口：**0 项**

---

## 五、建议行动清单

### 5.1 已完成（无需进一步 action）

1. **G1-G8、G13 全部关闭**：功能性缺口、核心测试缺口与命名偏差已补齐。
2. **端到端验证通过**：后端 64 个 pytest 用例、前端 lint、Playwright 模拟交易测试均通过。
3. **代码已推送**：当前基线 `0429e94` 已推送至 `https://github.com/fssl168/daily_stock_analysis.git` main 分支。

### 5.2 可选优化（非阻断）

1. **G9/G11 - 文档对齐**
   - 在 `docs/paper_trading_ai_pm_plan.md` 中标注实际签名：
     - `compute_indicators` 使用 `List[IndicatorSpec]`
     - `_build_system_prompt` 为模块级常量 `PM_SYSTEM_PROMPT`
     - `OrderStatus.MODIFIED` 未使用，改单通过 cancel+create 实现

2. **G14 - WebUI 净值曲线增强**
   - 当前 SVG Sparkline 满足基本需求。
   - 若后续需要交互式图表（tooltip、缩放、多指标叠加），建议引入 `recharts` 或 `echarts`。

3. **性能与监控增强**
   - 当前 `paper_trading/` 无独立性能分析模块，可参见 `docs/paper_trading_enhancement_plan.md` Phase 2。
   - 条件单、批量下单、更多技术指标等增强需求已整理在同一文档中。

---

## 六、后续方向

若需继续迭代，建议参考 `docs/paper_trading_enhancement_plan.md` 中的五阶段增强计划：

1. **Phase 1**：批量下单、条件单（止损/止盈/OCO）、订单筛选
2. **Phase 2**：绩效分析模块（夏普、最大回撤、胜率、盈亏比）
3. **Phase 3**：策略规则引擎增强（OBV/Stochastic/CCI/WR/VWAP、多时间框架、策略模板）
4. **Phase 4**：WebUI 集成与 Playwright 测试
5. **Phase 5**：文档与配置同步

当前核心闭环已可交付，上述增强属于"超越文章"的进一步能力建设。
