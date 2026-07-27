# Paper Trading Implementation Gap Tracker (对齐差距跟踪表)

本文件记录 `paper_trading_ai_pm_plan.md` 与当前代码实现之间的差距项，用于进度追踪和验收核对。

最后同步：2026-07-27 | Commit: [7f3bedc](https://github.com/...)

---

## 一、已完成并已验证对齐的项（✅）

| Task ID | Plan Item | Implementation Status | Verification Method |
|---------|-----------|---------------------|---------------------|
| P0-A | Fib回撤指标 + 三线止盈止损 | ✅ 已实现（超支融合多因子） | `sltp_calculator.py` 499行；含 ATR/Fib/筹码/支撑阻力；测试覆盖 |
| P0-B | AI自主挂限价单 | ✅ 基础完成 | `portfolio_manager_agent.py`：PM prompt default "limit"，ToolParameter default="limit"，fallback chain at limit_price |
| P0-C | AI主动撤单 | ⚠️ 数据契约修复完成；决策记录到复盘待确认 | `agent_risk.py`：新增 action/code/quantity/stop_loss/take_profit；`risk_order_adapter.from_agent_review()` mapped |
| P0-D | AI复盘反思（三段式+持久化） | ✅ 完成 | `reflection.py`：异步线程 + PaperReflection ORM + LLM生成 |
| P0-E | 记忆影响后续决策 | ✅ 完成 | `_inject_reflections()` 注入历史摘要到上下文 |
| P1-A | 动态止损上移 | ✅ 实现 | `market_listener._check_dynamic_sltp()` 集成到 tick 循环；sltp_dynamic_threshold_pct 配置 |
| P1-B | 价格吸筹空间三情景预案 | ✅ 完成 | `battle_plan.py`：strong/neutral/weak scenario + auction/intrady triggers |
| P1-C | 次日作战卡生成 | ✅ 完成 | `BattlePlanGenerator.generate()` 核心逻辑 |
| P2-A | 文章生成与日报报告 | ✅ 框架存在 + API 接口 | `api/v1/endpoints/paper_trading.py`：POST /daily-report/generate, GET /daily-report/{date}；`content_generator.save()` |
| P3-A/B | Paper Trading API + WebUI完整 | ✅ 非常完备 | 28个端点，39个schema，web/templates/paper_trading.html + .js 1322行 |
| P3-C | Markdown 文档沉淀 | ✅ 落地落盘 | `battle_plan.save_plan_markdown()`, `reflection.save_reflection_markdown()`, `notification._save_to_disk()` 在 push 前调用 |

---

## 二、待确认/需讨论项（🟡）

### 🟡 P0-C：主动撤单决策记录到复盘

**描述**：计划要求"每次撤单应记录到复盘"，但当前 reflection 系统区分不了 normal trade 与 agent-veto/cancel。

**当前状态**：
- `PaperReflection.note.text` 只包含生成的文本段落，无 explicit agent action 字段
- `verdict.action` (cancel/sell/modify) 在 reflection 中未体现
- `trade_result.to_dict()` 包含 `agent_review`，但 reflection 引擎仅取特定字段

**建议方案**（任选其一）：

| 方案 | 改动量 | 优点 | 缺点 |
|------|--------|------|------|
| A. 在 note text 末尾追加一行 `"Agent action: {verdict.action}"` | ~5 lines | 简单直接，不破坏现有 schema | 语义不够结构化 |
| B. 在 `PaperReflection` 表中增加 `agent_action: VARCHAR(20)` 字段 | DB migration + reflection save code | 结构化查询 | 需要迁移 |

**决策**：优先方案 A（快速上线），方案 B 作为后续改进。

**负责人**：[待定]  
**估计工时**：0.5 - 1 小时

---

### 🟡 P0-B：limit_price 传参边界条件测试

**描述**：虽然代码实现了 fallback chain (`limit_price or entry_price or trigger_price_kw or 0.0`)，但缺少单元测试验证边界情况。

**缺失测试**：
- `limit_price=None, entry_price=None, trigger_price_kw=None` → 应使用 default 或触发错误
- `limit_price=0.0` 是否合法校验
- 不同市场（A股/港股）price precision 的处理差异

**建议**：在 `tests/test_paper_trading_pm_agent.py` 中增加 `test_place_order_limit_fallback()`。

**负责人**：[待定]  
**估计工时**：0.5 小时

---

### 🟡 R2：文档与实际集成不一致

**描述**：`risk_order_adapter.py` 中的 `on_agent_review_result()` 函数从未被任何生产代码调用，但在 alignment 文档中仍标注为"需要集成"。

**实际集成路径**：`trading_engine.submit_signal()` → `_maybe_trigger_order_action()` → `RiskOrderAdapter.from_agent_review(verdict)`

**建议操作**：
1. Update `paper_trading_implementation_alignment.md` 第 253-260 行 R2 状态从 ⚠️ Documented... → ✅ Integrated via from_agent_review()
2. 将 `on_agent_review_result()` 标记为 deprecated，或保留作为备用入口

**负责人**：[我]  
**估计工时**：0.2 小时（文档更新）

---

### 🟡 P0-E：记忆权重衰减（可选增强）

**描述**：计划中提及"记忆重要度随时间衰减"，但当前实现仅为最近 N 条记录按时间倒序取。

**当前逻辑**（`reflection.py:get_relevant_notes()`）：
```python
SELECT * FROM PaperReflection ORDER BY created_at DESC LIMIT max_count
```

**建议增强**：在计算 relevance score 时引入时间衰减因子：
```python
weight = time_decay(created_at) * quality_score(content) * outcome_weight(reward)
```

**决策**：这是一个增强项，优先级低。如有需要可创建独立 issue `feat:P0-E-memory-decay`。

**负责人**：[可选]  
**估计工时**：N/A（按需）

---

## 三、实施追踪表格（方便 PR/任务追踪）

| # | 问题ID | 标题 | 优先级 | 状态 | 相关文件/PR | 备注 |
|---|--------|------|--------|------|-------------|------|
| 1 | P0-C-R1 | Agent action 未在 reflection 中明确体现 | Medium | Open | `paper_trading/reflection.py` | 方案A建议追加 text 行 |
| 2 | P0-B-R1 | limit_price fallback 缺少边界测试 | Low | Open | `tests/test_paper_trading_pm_agent.py` | 增加测试用例 |
| 3 | R2-R1 | on_agent_review_result 未实际使用 | Low | Open | `docs/paper_trading_implementation_alignment.md` | 仅需更新文档状态 |
| 4 | P0-E-R1 | Memory decay 可选增强 | Optional | N/A | `paper_trading/reflection.py` | 产品需求驱动 |

---

## 四、变更历史

| Date | Author | Changes | Related Commit |
|------|--------|---------|----------------|
| 2026-07-27 | Agnes | Initial gap tracker based on full code review; marked all major tasks as completed per 7f3bedc | 7f3bedc feat: close paper-trading alignment gaps and fix config alias precedence |
|  |  | Added config alias precedence bug fix analysis and mitigation | |

---

*本文件应与 `paper_trading_implementation_alignment.md` 保持同步，作为实施跟踪的详细操作清单。*
