# 系统可观测性与 L4 干预模式

> 本文档介绍股票智能分析系统的 **L1/L2/L3/L4 全主动观察** 能力：事件总线、可观测性面板、元认知内省，以及 **门控干预模式**（让系统自我认知真正影响 Agent 行为）。
>
> 相关：`docs/L1_L4_INTEGRATION_IMPLEMENTATION_PLAN.md`（集成实施计划）、`docs/L1_L2_RETROSPECTIVE.md`（四层架构定义）

---

## 一、四层架构与全主动观察

系统采用四层架构，通过统一的 **SystemEventBus** 双向互通：

```
┌─────────────────────────────────────────────────────────────┐
│  L4 元认知层   MetaCognitiveEngine                            │
│  理解"为什么这么做"、检测认知偏差、生成内省报告                 │
├─────────────────────────────────────────────────────────────┤
│  L3 操作级自修复层  ModuleAutoRestarter / ConfigAutoRollback   │
│  优雅降级 / CodeAwareRepairAgent / RepairEffectivenessLog      │
├─────────────────────────────────────────────────────────────┤
│  L2 业务执行与分析层  Pipeline / Agent / Notification / 回测    │
├─────────────────────────────────────────────────────────────┤
│  L1 基础数据与设施层  data_provider / Storage / Config / Clock  │
└─────────────────────────────────────────────────────────────┘
          ▲                  SystemEventBus                  ▼
     各层发布事件  ◄─────►  订阅 / 落盘 / 统计 / WS 推送
```

**全主动观察**：每一层在关键动作发生时主动发布事件（数据源降级、管线完成、Agent 工具调用、配置回归、模块重启、反思完成等），事件进入 EventBus 并落盘审计，L4 元认知订阅全部事件形成系统自我认知。

---

## 二、可观测性面板（Web `/observability`）

打开 Web 界面侧边栏「可观测性」，即可看到 L1/L2/L3/L4 的实时运行全貌：

### 2.1 系统事件流（EventStreamPanel）

- **实时事件流**：通过 WebSocket（`/api/v1/observability/ws/events`）实时推送系统事件，覆盖 L1/L2/L3/L4 全部事件类型
- **历史浏览**：REST 分页查询（`page`/`page_size`，默认 20），可按事件类型过滤
- **降级兜底**：WS 不可用时自动切换 5s 轮询
- 事件按严重度着色（info/warning/error/critical）

**事件类型覆盖**：

| 层级 | 事件示例 |
|---|---|
| L1 | 数据源降级、取数失败、配置变更、时钟降级、LLM 渠道切换、熔断开关 |
| L2 | 管线启动/完成/失败、Agent 工具调用/结果、通知发送/失败、无交易决策 |
| L3 | 模块重启、配置回归、降级切换 |
| L4 | 反思完成、检测到偏差、思维循环 |

### 2.2 事件统计（EventStatsOverview）

- 事件总数 / 类型数 / 配置回归数 三卡片概览
- TOP 事件类型分布列表

### 2.3 L4 内省报告（MetaIntrospectionPanel）

- 展示 `MetaCognitiveEngine` 最新内省报告（摘要、生成时间、偏差发现）
- **触发反思**按钮：手动触发一次深度反思（`POST /observability/meta/reflect`）
- 反思后自动生成**调整提案**（见第三章）

### 2.4 其他面板

| 面板 | 内容 |
|---|---|
| L4 系统观察 | 降级/回滚/重启等事件的元认知观察记录 |
| L3 修复效果 | RepairEffectivenessLog 修复记录与 24h 效果分析 |
| 配置回归 | L3 观察到的配置回归记录（observe-only，不自动回滚） |
| 健康趋势 | HealthCheckDaemon 历史检查趋势（Sparkline，异常红点） |

---

## 三、L4 干预模式（门控 · 安全）

**干预模式**让 L4 内省建议真正影响 Agent 行为，但**严格门控**：

### 3.1 安全边界（执行红线）

- **只调整非交易软参数**：分析深度（`AGENT_MAX_STEPS`）、上下文压缩档位（`AGENT_CONTEXT_COMPRESSION_PROFILE`）
- **绝不触及**订单/仓位/风控/熔断路径（白名单外参数一律 400 拒绝）
- 所有动作写入 `ADJUSTMENT_PROPOSED/APPLIED/REJECTED` 事件，全程审计

### 3.2 工作流程

```
L4 内省检测偏差
      │  improvement_hints
      ▼
AdjustmentEngine.derive_commands()  ← 映射到白名单参数
      │  ADJUSTMENT_PROPOSED 事件
      ▼
┌─ 人工确认（默认）──────────────┐
│  Web 面板点「应用」/「忽略」      │
└──────────────────────────────┘
      │  apply / reject
      ▼
运行时 Config 更新 + 写入 .env（重启保留）
      │  ADJUSTMENT_APPLIED/REJECTED 事件
      ▼
Agent 下次分析采用新参数
```

### 3.3 调整参数说明

| 参数 | 语义 | 生效方式 |
|---|---|---|
| `AGENT_MAX_STEPS` | Agent 最大执行步数（分析深度） | 运行时立即生效 |
| `AGENT_CONTEXT_COMPRESSION_PROFILE` | 上下文压缩档位 aggressive/balanced/conservative | 运行时立即生效 |
| `AGENT_SKILLS` | 激活技能集 | 需重启生效（持久化到 .env） |

### 3.4 自动应用（可选）

默认人工确认。如需自动应用，在 `.env` 设置：

```bash
ADJUSTMENT_AUTO_APPLY=true
```

> ⚠️ 自动应用仍受白名单限制，只改上述软参数，不碰交易路径。

### 3.5 调整历史

Web 面板内省报告下方展示调整历史（参数、值、状态：已应用/已拒绝/待确认）。

---

## 四、API 参考

### 可观测性

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/api/v1/observability/events` | 事件流（分页/过滤） |
| GET | `/api/v1/observability/events/stats` | 事件统计 |
| GET | `/api/v1/observability/events/correlation/{cid}` | 按 correlation_id 追踪事件链 |
| GET | `/api/v1/observability/meta/observations` | L4 系统观察历史 |
| GET | `/api/v1/observability/meta/introspection` | 最新内省报告 |
| GET | `/api/v1/observability/meta/stats` | 元认知统计 |
| POST | `/api/v1/observability/meta/reflect` | 触发反思（产出内省 + 调整提案） |
| GET | `/api/v1/observability/repairs` | 修复记录 |
| GET | `/api/v1/observability/repairs/effectiveness` | 修复效果分析 |
| GET | `/api/v1/observability/regressions` | 配置回归记录 |
| GET | `/api/v1/observability/health/trend` | 健康趋势 |
| WS | `/api/v1/observability/ws/events` | 事件实时推送 |

### L4 干预

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | `/api/v1/observability/adjustments` | 调整历史 |
| POST | `/api/v1/observability/adjustments/apply` | 应用调整（白名单校验 + 类型校验） |
| POST | `/api/v1/observability/adjustments/reject` | 拒绝调整 |

### 安全机制

- **鉴权**：`/api/v1/*` 受全局 auth 中间件保护（启用 `ADMIN_AUTH_ENABLED` 时）
- **脱敏**：事件 payload 自动脱敏（Agent 工具参数截断、通知渠道隐藏、LLM 密钥不暴露）
- **白名单**：调整参数白名单校验，非法参数 400

---

## 五、相关文档

- [L1/L2 架构回望](L1_L2_RETROSPECTIVE.md) — 四层架构定义与边界
- [L3 架构审计](L3_ARCHITECTURE_AUDIT.md) — 操作级 vs 架构级自修复
- [L1-L4 集成实施计划](L1_L4_INTEGRATION_IMPLEMENTATION_PLAN.md) — EventBus 双向互通设计
- [前后端对齐计划](FRONTEND_BACKEND_ALIGNMENT_PLAN.md) — 观察面板实施
