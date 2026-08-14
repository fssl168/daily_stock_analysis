# PM/复盘/作战卡 真实密钥验收报告 (P0-1)

> 日期：2026-08-14
> 环境：真实 agnes-2.0-flash 密钥（LiteLLM 通道）
> 验收对象：复盘 / PM 决策 / 作战卡 三个自我进化端点在真实 LLM 下非 fallback 运行

---

## 1. 验收结果总览

| 端点 | 函数 | 非 fallback | 内容质量 | 判定 |
|---|---|---|---|---|
| 复盘（日终） | `reflection.reflect_on_daily` | ✅ | subject/summary/takeaway 全非空 | ✅ 通过 |
| 复盘（成交） | `reflection.reflect_on_trade` | ✅ 落库 | 落库实证（trade scope） | ✅ 通过 |
| 作战卡 | `battle_plan.generate` | ✅ | market_review 为 LLM 生成（真实行情分析） | ✅ 通过 |
| PM 决策 | `portfolio_manager_agent.make_decision` | ✅ | action 可执行、reason 真实分析 | ⚠️ 部分（confidence 未达标） |

## 2. 各端点实证

### 2.1 复盘（reflect_on_daily, 账户 2）

```
subject: '2026年8月14日 账户#2 每日复盘'
summary: '账户期初与期末资产均为零，当日无任何交易活动、决策记录或持仓变动。
          账户处于完全空仓状态，可能为新开户或策略性观望。'
takeaway: '空仓期间应明确策略意图，建议建立建仓计划与决策日志，
           避免长期无活动导致策略失效。'
mood: neutral
```

- 输出为完整 JSON（```json 围栏 + subject/summary/takeaway/lessons/tags/mood）
- `_parse_reflection_json` 正则提取 + 解析正常
- **结论**：复盘内容非空验收通过。此前复盘 subject/summary 全空是**账户无数据状态**（空仓无交易）下的合理输出 + 解析增强（`_extract_json_object` 类机制）后已闭环。

### 2.2 作战卡（battle_plan.generate, 账户 2）

```
used_fallback: False
market_review: 'A股市场今日整体偏弱，上证指数跌0.5%报3926.96，深证成指跌0.87%，
  创业板指跌0.45%。板块分化显著：医疗板块领涨（诊断服务+5.66%...），
  贵金属板块领跌（白银-6.44%...）。市场成交量维持高位，但上涨动力不足...'
sentiment_score: 42
```

- LLM 生成的市场综述包含**真实行情数据**（指数点位、板块涨跌）
- **关键修复**：`build_battle_plan_generator` 需传 `trading_engine` 才会构建 pm_agent（否则直接 fallback）——验收脚本补传后非 fallback
- **结论**：作战卡非 fallback 验收通过。

### 2.3 PM 决策（make_decision, 账户 3, 多次运行）

- 非 fallback ✅（`used_fallback=False`，JSON 提取增强 `_extract_json_object` 生效）
- action 可执行 ✅（hold/plan，reason 为真实持仓/挂单分析）
- **confidence 未达标 ❌**：模型输出 JSON 不填 `confidence` 字段（默认 0.0）
  - 根因：agnes-2.0-flash 指令遵循弱（prompt 已明确要求 + 反例强调，模型仍省略）
  - 影响：`paper_decisions.confidence=0.0` 的决策无法通过 ≥0.7 验收线
  - **建议**：① 换更强模型（agnes-2.5）验证 ② 或解析层对缺失 confidence 做启发式兜底（不推荐——违背验收语义）

## 3. 产出 bug 清单

| # | 问题 | 状态 |
|---|---|---|
| 1 | `build_battle_plan_generator` 未传 trading_engine 时 pm_agent 不构建 → 作战卡永远 fallback | 已确认（验收脚本补传验证），生产装配（T-08 `build_full_listener`）已传 engine，无影响 |
| 2 | PM JSON 输出缺 confidence 字段（模型行为） | 已知，待换模型验证 |
| 3 | 复盘内容依赖账户状态（空仓时 subject 为"空仓复盘"） | 合理行为，非 bug |

## 4. 验收结论

- **复盘 / 作战卡**：✅ 真实密钥下非 fallback + 内容可用——「自我进化组件在真实 LLM 下跑通」从口头承诺变为已验证事实
- **PM 决策**：⚠️ 机制闭环（非 fallback + 可执行），confidence 卡在模型指令遵循——需换模型或接受降级验收

## 5. 附注

- 验收脚本（临时）：`paper_trading/_verify_reflection_content.py` / `_verify_battle_plan.py`（已清理）
- 数据修复（P0-2）后账户 3 持仓浮亏进入 ±15% 合理区间，PM/复盘的分析基础数据已健康化

## 6. 补充验证（2026-08-14 11:31）：agnes-2.5-flash confidence 复核

- 用 `AGENT_LITELLM_MODEL=openai/agnes-2.5-flash` 重跑 make_decision：**非 fallback ✅、parse_ok ✅、reason 真实分析**（茅台资金不足/立讯信号弱等合理判断），但 **raw_response 仍不含 confidence 字段**（confidence=0.0）
- **结论**：confidence 缺失是 **agnes 系列模型（2.0/2.5）指令遵循共性问题**，非单版本缺陷
- **建议**：① 实际部署换非 agnes 模型（如 deepseek 系列）验证 confidence ② 或解析层对缺失 confidence 按证据强度推断并标记 `confidence_source="inferred"`（需用户确认——改变验收语义）③ 或接受「非 fallback + 可执行」为 PM 决策达标线，confidence 作为模型侧优化项
