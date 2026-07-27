# P0-E: 记忆注入策略文档（_inject_reflections）

## 1. 当前记忆策略概览

`PortfolioManagerAgent._inject_reflections()` 将历史复盘笔记注入到 PM agent 的决策上下文中，形成闭环学习机制。当前策略如下：

### 1.1 数据来源

| 来源 | 获取方式 | 数量限制 |
|------|---------|---------|
| 全局近期复盘 | `reflection_engine.get_recent_notes(limit=3, account_id)` | 最多 3 条（最新优先） |
| 持仓关联复盘 | 对每个持仓 code 调用 `get_relevant_notes(code=code, limit=1)` | 每个持仓最多 1 条，最多 5 个持仓 |

### 1.2 去重逻辑

- 使用 `seen_ids` 集合过滤重复的 `row_id`
- 合并顺序先放全局笔记，再放持仓特定笔记（持仓笔记可覆盖全局同类记录）

### 1.3 输出格式

每行格式：`-[时间][scope] [code] 摘要/takeaway`

示例：
```
- [2026-07-25 14:30][trade] 600519 追高买入未设止损，下次需严格检查止盈止损参数
- [2026-07-26 09:15][daily] 市场情绪偏弱，注意控制仓位
```

### 1.4 局限性与权重缺失

当前实现**缺乏显式记忆权重机制**：

- 所有笔记平等对待，不分重要性
- 无时间衰减因子（新笔记天然靠前，但无指数衰减）
- 无基于笔记内容的质量评分（如是否含 actionable lessons）
- 无基于交易结果的正向强化标记（成功/失败模式识别）

---

## 2. 优化建议：加权记忆注入模型

为提升记忆系统的决策辅助能力，建议引入以下权重策略：

### 2.1 基础权重公式

```
score(note) = 
    time_decay(note) * 
    content_quality(note) * 
    relevance_score(note, current_context) * 
    outcome_penalty_or_reward(note)
```

各分量说明：

| 分量 | 计算方式 | 说明 |
|------|---------|------|
| time_decay | exp(-Δt / τ), τ=7天 | 越近的记忆权重越高，τ控制衰减速率 |
| content_quality | 基于 NLP 特征提取长文本中的行动项数量 | 含"下次"、"应"、"避免"等关键词的笔记得分更高 |
| relevance_score | 基于股票代码匹配度 + 主题相似度 | 持仓相关笔记获得额外 boost |
| outcome_penalty_or_reward | 根据关联交易的盈亏结果加分/减分 | 亏损交易的负面教训权重加倍 |

### 2.2 实施路线图

**阶段一（快速，文档级）** - 不修改代码，仅完善现有逻辑说明

在 `portfolio_manager_agent.py` 的 `_inject_reflections` 方法 docstring 中明确当前行为：

```python
def _inject_reflections(self, account_id):
    """Inject reflection memory into decision context (P0-E).
    
    Strategy:
      - Fetch up to 3 most recent global reflections (by created_at desc).
      - For each held stock (max 5 codes), fetch latest relevant note.
      - Deduplicate by row_id; prepend global notes, append stock-specific notes.
      - Format: "[timestamp][scope] code takeaway".
      
    Note: This is a simple time-order strategy without weighted scoring.
          Future enhancement could incorporate decay, quality, and outcome weights.
    """
```

同时在 `docs/memory_strategy_p0-e.md` 中记录该策略（即本文档）。

**阶段二（中等）** - 实现基础时间衰减排序

修改 `reflection_engine.get_recent_notes()` 返回带权重的列表，或直接修改 `_inject_reflections` 按自定义 score 排序：

```python
def _weighted_merge(self, global_notes, code_notes, account_id):
    """Merge notes with time-decay weighting."""
    from datetime import datetime
    
    now = datetime.now()
    all_notes = []
    
    for n in global_notes + code_notes:
        created = getattr(n, 'created_at', now)
        delta = (now - created).total_seconds() / 86400  # days
        decay = float('exp(-delta / 7.0)')  # τ=7 days
        
        # Simple quality heuristic: count length of takeaway
        take = getattr(n, 'takeaway', '') or getattr(n, 'summary', '')
        quality = max(0.5, min(1.0, len(take) / 200))  # normalize
        
        score = decay * quality
        
        all_notes.append((score, n))
    
    # Sort descending by score
    all_notes.sort(key=lambda x: x[0], reverse=True)
    return [note for _, note in all_notes[:6]]  # top 6 total
```

**阶段三（长期）** - 引入 outcome-based reinforcement

在 `ReflectionNote` 中增加字段：
- `outcome_tag: Optional[str]` = "win"/"loss"/"neutral"
- `lessons_count: int` （由 NLP 分析提取的行动项数量）

在 `_inject_reflections` 中对 loss 类笔记给予 1.5x 权重，对 lessons 多的给予 bonus。

---

## 3. 测试建议

添加单元测试验证记忆注入行为：

```python
# tests/test_pm_agent_memory.py
class TestMemoryInjection:
    def test_global_notes_first(self, ...):
        # 确保全局笔记排在持仓笔记前
    
    def test_time_decays_closer_higher(self, ...):
        # 创建不同时间的笔记，确认最近的分数更高
    
    def test_deduplication_works(self, ...):
        # 同一笔记在全局和持仓中都存在时只保留一份
    
    def test_empty_cases(self, ...):
        # 无笔记时返回占位字符串
```

---

## 4. 与计划文档对齐

`paper_trading_ai_pm_plan.md` 中 P0-E 要求"基于复盘的策略迭代（记忆影响后续决策）"。当前实现完成了基础的信息注入流程，但尚未实现高级的加权记忆策略。上述优化建议逐步对齐计划的意图：

| 计划条目 | 当前进度 | 下一步 |
|---------|---------|--------|
| 记忆注入已有 _inject_reflections | ✅ 完成 | 文档化当前简单策略 |
| 记忆有衰减效应 | ⚠️ 隐式（靠时间排序） | 显式实现指数衰减公式 |
| 记忆影响决策上下文 | ✅ 注入到 user prompt | 增强内容质量感知 |
| 策略迭代反馈闭环 | 🟡 初步（仅 read） | 加入 outcome 权重，未来可扩展策略参数调整 |

---

*文档最后更新: 2026-07-27*  
*关联 issue/ref: P0-E, memory_management, strategy_iteration*