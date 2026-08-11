# 前后端业务对齐开发实施计划 — 全主动观察系统

**生成日期**: 2026-08-12
**修订**: v2（2026-08-12，按 skill 评审 REV-001~REV-010 修订）
**基线**: HEAD `52ae799`（L1/L2/L3/L4 EventBus 集成已完成）
**前置文档**: `docs/L1_L4_INTEGRATION_IMPLEMENTATION_PLAN.md`、`docs/frontend_quant_alignment_gap_analysis_v2.md`、`docs/frontend_quant_implementation_plan.md`、`docs/FRONTEND_BACKEND_ALIGNMENT_PLAN_REVIEW.md`
**本计划性质**: 规划文档（**不实施**），已通过 skill 评审，待按 Phase 分步执行

---

## 一、背景与现状

### 1.1 已完成的两轮前端对齐（实时量化面板）

前两轮审查（`frontend_quant_alignment_gap_analysis.md` / `_v2.md`）聚焦**实时量化交易面板**，第二轮修复后达到：

| 指标 | 第一轮 | 第二轮修复后 |
|---|---|---|
| 功能完成度 | 72 | 98 |
| 交互还原度 | 65 | 95 |
| 实时性就绪度 | 30 | 90 |
| 状态覆盖度 | 75 | 95 |
| TS 编译 / ESLint | 33 错误 | **0 错误** |

已实现并集成：PaperTradingPage（2200 行）、42 个 paper-trading API 端点、`useWebSocket` 基础设施、QuoteTicker、LatencyPanel、BreakerStatusBadge、RiskAlertToast、EventLogFeed 等 11 个组件。

### 1.2 L1/L2/L3/L4 集成带来的新业务域（核心增量）

`f25d8fd` + `52ae799` 已完成四层 EventBus 全主动观察，但**前端完全没有消费这些新能力**：

| 后端能力（已实现） | 前端现状 | 差距 |
|---|---|---|
| `SystemEventBus` 事件流（24 个 L1/L2 事件 + L3/L4 事件） | **无 API 端点、无前端类型、无页面** | ❌ 完全空白 |
| `MetaCognitiveEngine` 内省报告 / 系统观察 | 无消费 | ❌ 完全空白 |
| `RepairEffectivenessLog` 修复效果日志 | 无消费 | ❌ 完全空白 |
| L3 配置回归观察（L3ConfigObserver） | 无消费 | ❌ 完全空白 |
| EventBus 统计（`get_event_bus_stats`） | 无消费 | ❌ 完全空白 |

**一句话**: 后端已经"看见自己"（全主动观察），但前端还是"瞎的"——L1/L2/L3/L4 的观察数据全部困在后端进程里，没有任何可视化/交互入口。这是本次对齐计划的核心目标。

### 1.3 对齐范围界定

本计划覆盖"所有业务"对齐，分**三个域**：

- **A 域：实时量化交易面板**（复用前两轮成果，标注"已对齐/待增强"）
- **B 域：L1/L2/L3/L4 全主动观察面板**（全新，核心增量）
- **C 域：既有业务域系统核验**（评审新增）——对 Alerts/Portfolio/DecisionSignals/History/Analysis/SystemConfig 六个既有域各做一次 ui-frontend-alignment 六维度走查，识别功能遗漏/交互偏差/状态缺失/契约不一致

> C 域是本计划修订后新增（评审 REV-001）。原计划仅覆盖 A/B 两域，遗漏了其余业务域的对齐核验。C 域用于补齐"对齐所有业务"的承诺。

---

## 二、A 域：实时量化交易面板 — 现状核验与待增强项

前两轮已对齐，本计划核验当前代码状态，仅列出**仍待增强**的项（避免重复劳动）。

### 2.1 已对齐（无需动作）

| 页面/能力 | 状态 | 说明 |
|---|---|---|
| PaperTradingPage 仪表板 | ✅ 已对齐 | 账户/持仓/委托/成交/信号/性能/熔断/健康 |
| 42 个 paper-trading API 端点 | ✅ 已对齐 | 前端 `paperTradingApi` 封装 |
| `useWebSocket` 基础设施 | ✅ 已对齐 | WS-001 已实现 |
| 11 个实时组件集成 | ✅ 已对齐 | QuoteTicker/LatencyPanel/RiskAlertToast 等 |
| TS 类型 / lint | ✅ 已对齐 | 0 错误 |

### 2.2 仍待增强（前两轮遗留）

| 编号 | 待增强项 | 现状 | 目标 |
|---|---|---|---|
| A-01 | **CandlestickChart 挂载** | 已创建未挂载；后端 `getDailyBars` 需返回真实 OHLC | 持仓行点击展开 K 线 |
| A-02 | **useLivePositions 挂载** | 已创建未挂载；依赖 WS quotes 推送 | 替换静态 PnL 为实时 |
| A-03 | **操作结果乐观更新** | submit_signal 后依赖手动刷新 | 乐观更新 + 回滚 |
| A-04 | **策略生命周期完整状态机** | 仅 Listener start/stop | DRAFT→BACKTEST→PAPER→REVIEW→LIVE→PAUSED→RETIRED |
| A-05 | **L2 深度行情订单簿** | 无 | 十档买卖盘可视化 |

> A-01/A-02 已在 v2 报告标注为"前端就绪、后端待交付"；本计划将其归入 B 域后端 API 补齐的依赖项。

### 2.3 A-04 策略生命周期状态流转表（评审修订 REV-010）

A-04 的实现必须先定义合法状态流转，避免非法跳转：

```
DRAFT ──▶ BACKTEST ──▶ PAPER ──▶ REVIEW ──▶ LIVE
  │          │           │          │          │
  │          ▼           ▼          ▼          ▼
  └───────── RETIRED ◀──┘ ◀─────── PAUSED ◀───┘
```

| 当前状态 | 允许流转 | 触发条件 | 前置校验 |
|---|---|---|---|
| DRAFT | BACKTEST / RETIRED | 提交回测 | 策略参数完整 |
| BACKTEST | PAPER / DRAFT / RETIRED | 回测通过 | Sharpe/回撤阈值 |
| PAPER | REVIEW / PAUSED / RETIRED | 模拟盘运行达标 | 模拟盘≥N 天 |
| REVIEW | LIVE / PAPER / RETIRED | 人工审批通过 | 审批人授权 |
| LIVE | PAUSED / RETIRED | 暂停/下线 | 有持仓时需确认 |
| PAUSED | LIVE / RETIRED | 恢复/下线 | 恢复需重新校验 |
| RETIRED | （终态） | - | - |

前端实现须：只允许合法流转按钮、非法流转禁用并提示原因、状态变更调用后端 API（不做前端本地假流转）。

---

## 三、B 域：L1/L2/L3/L4 全主动观察面板（核心增量）

### 3.1 后端新 API 端点设计

在现有 `api/v1/endpoints/` 下新增 **`observability.py`**，挂载到 `api/v1/router.py`（prefix=`/observability`）。按 L1/L2/L3/L4 分资源：

> **统一契约（评审修订 REV-002/003/005/006）**：
> - **鉴权**：全部端点复用现有 `auth` 依赖注入鉴权（与 `paper_trading.py` 一致），未登录返回 401；`get_observability_service` 依赖注入获取共享实例。
> - **分页**：统一 `page`/`page_size`（默认 `page=1, page_size=20`，`page_size` 范围 1-100），与 `alerts.py`/`decision_signals.py` 现有约定一致；响应含 `total`/`page`/`page_size` 元数据。
> - **错误响应**：复用 `api/v1/errors.py` 错误体系（`ApiError`/统一错误结构），不新造错误格式。
> - **payload 脱敏**：`SystemEventOut` 对事件 payload 裁剪敏感字段（agent tool arguments 截断、notification 渠道详情隐藏内部端点），保留审计所需最小集。

#### B-API-1: 事件流查询（L1/L2/L3/L4 统一）

| 方法 | 端点 | 说明 | 后端调用 |
|---|---|---|---|
| GET | `/observability/events` | 最近事件（分页/过滤） | `SystemEventBus.get_recent_events` + `get_event_count` |
| GET | `/observability/events/stats` | 事件统计（类型/来源/严重度分布） | `SystemEventBus.stats` + `get_event_bus_stats` |
| GET | `/observability/events/correlation/{cid}` | 按 correlation_id 追踪事件链 | `get_events_by_correlation` |

**查询参数**: `event_type`（过滤）、`source`、`min_severity`、`page`、`page_size`（默认 20，范围 1-100）
**响应 Schema**: `SystemEventOut`（event_id/event_type/severity/source/timestamp/**payload_redacted**/correlation_id）+ 分页元数据 `total`/`page`/`page_size`

> **payload_redacted**：对 `agent_tool_call`/`agent_tool_result` 的 arguments/result 截断到 200 字符；`notification_*` 事件隐藏渠道内部配置；`llm_usage` 只保留模型名与 token 数，不暴露密钥/端点。

#### B-API-2: L4 元认知

| 方法 | 端点 | 说明 | 后端调用 |
|---|---|---|---|
| GET | `/observability/meta/observations` | 系统观察历史 | `MetaCognitiveEngine.get_system_observations` |
| GET | `/observability/meta/introspection` | 最新内省报告 | `get_latest_introspection` |
| GET | `/observability/meta/stats` | 元认知统计 | `MetaCognitiveEngine.stats` |
| POST | `/observability/meta/reflect` | 触发一次反思（**dry_run，仅产出报告**） | `force_reflection` |

#### B-API-3: L3 修复效果

| 方法 | 端点 | 说明 | 后端调用 |
|---|---|---|---|
| GET | `/observability/repairs` | 修复记录列表 | `RepairEffectivenessLog.get_entries_by_target` / `stats` |
| GET | `/observability/repairs/effectiveness` | 修复效果分析报告 | `RepairEffectivenessLog.analyze_effectiveness` |
| GET | `/observability/regressions` | 配置回归观察记录 | `L3ConfigObserver.stats` / 事件流过滤 |

> **共享实例（评审修订 REV-008）**：`RepairEffectivenessLog` 不能每次 new（会重新从磁盘加载、内存新记录丢失）。须在 `bootstrap_event_bus()` 装配层注册单例（`persist_path=Path("data/repair_effectiveness.json")`），端点通过 `get_observability_service` 依赖注入复用同一实例。`L3ConfigObserver` 同理从 bootstrap 模块级引用获取。

#### B-API-4: 健康趋势（L2→L3 桥）

| 方法 | 端点 | 说明 | 后端调用 |
|---|---|---|---|
| GET | `/observability/health/trend` | 健康检查历史趋势 | 从事件流过滤 `HEALTH_CHECK_COMPLETED` 聚合 |

### 3.2 前端新增

#### 类型（`apps/dsa-web/src/types/observability.ts`）

```typescript
export type SystemEventType =
  | 'module_restarted' | 'module_restart_failed' | 'module_health_changed'
  | 'config_snapshot_created' | 'config_rollback_executed' | 'config_regression_detected'
  | 'degradation_transition' | 'capability_disabled' | 'capability_restored'
  | 'reflection_completed' | 'bias_detected' | 'circularity_detected' | 'outcome_deviation'
  | 'system_startup' | 'system_shutdown' | 'health_check_completed'
  | 'data_source_fallback' | 'data_fetch_failed' | 'data_quality_alert'
  | 'circuit_open' | 'circuit_closed' | 'config_changed' | 'clock_degraded'
  | 'latency_summary' | 'llm_backend_switched' | 'llm_usage' | 'storage_error'
  | 'pipeline_started' | 'pipeline_completed' | 'pipeline_failed'
  | 'market_review_completed' | 'backtest_started' | 'backtest_completed'
  | 'agent_tool_call' | 'agent_tool_result' | 'agent_loop_detected'
  | 'no_trade_decision' | 'notification_sent' | 'notification_failed' | 'service_error';

export interface SystemEvent { /* ... */ }
export interface IntrospectionReport { /* ... */ }
export interface RepairEffectivenessEntry { /* ... */ }
export interface ObservabilityStats { /* ... */ }
```

#### API 客户端（`apps/dsa-web/src/api/observability.ts`）

```typescript
import apiClient from './index';  // 复用现有 axios 实例（含 401 拦截）

export const observabilityApi = {
  getEvents: (params) => apiClient.get('/observability/events', { params }),
  getEventStats: () => apiClient.get('/observability/events/stats'),
  getMetaObservations: () => apiClient.get('/observability/meta/observations'),
  getIntrospection: () => apiClient.get('/observability/meta/introspection'),
  triggerReflect: () => apiClient.post('/observability/meta/reflect'),
  getRepairs: () => apiClient.get('/observability/repairs'),
  getRepairEffectiveness: () => apiClient.get('/observability/repairs/effectiveness'),
  getRegressions: () => apiClient.get('/observability/regressions'),
  getHealthTrend: () => apiClient.get('/observability/health/trend'),
};
```

#### 页面/组件（`apps/dsa-web/src/pages/ObservabilityPage.tsx` + 组件）

| 组件 | 功能 | 数据源 |
|---|---|---|
| `EventStreamPanel` | 实时事件流（分页/过滤/严重度着色） | GET events + **WS 推送**（复用 useWebSocket） |
| `EventStatsOverview` | 事件类型/来源/严重度分布图表 | GET events/stats |
| `MetaIntrospectionPanel` | 最新内省报告 + 触发反思按钮（dry_run） | GET meta/introspection + POST meta/reflect |
| `MetaObservationsPanel` | 系统观察列表（degradation/rollback/restart） | GET meta/observations |
| `RepairEffectivenessPanel` | 修复记录 + 效果分析（成功率/平均延迟） | GET repairs + effectiveness |
| `RegressionPanel` | 配置回归记录 | GET regressions |
| `HealthTrendPanel` | 健康检查历史趋势（Sparkline） | GET health/trend |

#### 组件状态矩阵（评审修订 REV-007）

每个组件必须覆盖四态，实施时按此矩阵验收：

| 组件 | 加载中 | 空数据 | 错误 | 成功 | 实时更新 |
|---|---|---|---|---|---|
| EventStreamPanel | Skeleton | "暂无事件" | Error toast + 重试 | 事件流列表 | ✅ WS |
| EventStatsOverview | Spinner | "暂无统计" | ErrorBoundary | 图表 | 刷新周期 |
| MetaIntrospectionPanel | Spinner | "尚无内省报告" | Error toast | 报告全文 | 手动刷新 |
| MetaObservationsPanel | Skeleton | "暂无系统观察" | ErrorBoundary | 观察列表 | 刷新周期 |
| RepairEffectivenessPanel | Spinner | "暂无修复记录" | Error toast | 成功率 + 明细 | 刷新周期 |
| RegressionPanel | Skeleton | "无配置回归" | ErrorBoundary | 回归列表 | 刷新周期 |
| HealthTrendPanel | Spinner | "无健康数据" | ErrorBoundary | Sparkline | 刷新周期 |

#### 触发反思交互流程（评审修订 REV-005）

`MetaIntrospectionPanel` 的"触发反思"按钮交互：
1. 点击 → 按钮进入 `loading` 态（禁用防重复提交），提示"正在生成内省报告（dry-run）..."
2. 请求 `POST /observability/meta/reflect`（后端执行 `force_reflection`，**仅产出报告不干预**）
3. 成功 → 展示新报告，按钮恢复可用，toast 提示"反思完成（观察模式）"
4. 失败 → 按钮恢复，Error toast，保留上一次报告
5. 超时（>10s）→ 按钮恢复，提示"反思超时，请稍后重试"
6. 并发保护：反射进行中禁用按钮，防止重复触发

### 3.3 WS 实时推送（评审修订：从"可选"提升为 P0）

既有 `useWebSocket` 已支持通用 WS。B 域事件流**必须**复用 WS：后端新增 WS 端点 `/ws/events`（复用 EventBus `on_batch` 回调），前端 `EventStreamPanel` 通过 `useWebSocket` 实时接收新事件，**REST 轮询仅作降级兜底**。

> **为何提升 P0（评审 REV-009）**：A 域 QuoteTicker/RiskAlertToast/EventLogFeed 已用 WS 实时推送，若 B 域观察面板用轮询会造成同一系统内"实时 vs 滞后"的体验割裂。且事件流（熔断、降级、修复）属于高时效信息，轮询 5-30s 会延迟风险感知。**第一版即实现 WS 推送，REST 分页查询仅用于历史浏览。**

---

## 三-C、C 域：既有业务域系统核验（评审新增 REV-001）

原计划仅覆盖 A/B 两域，遗漏其余业务域。C 域用 `ui-frontend-alignment` 六维度（功能完整性/交互逻辑/状态覆盖/数据字段/边界异常/文案提示）对每个既有业务域走查，识别未对齐项。

### C-1 核验范围（6 个既有域）

| 域 | 前端页面 | 后端端点 | 核验重点 |
|---|---|---|---|
| 告警 | `AlertsPage.tsx` | `alerts.py`（9 端点） | 告警规则 CRUD、触发状态、分页（page/page_size） |
| 组合 | `PortfolioPage.tsx` | `portfolio.py`（20 端点） | 持仓/资产/收益、风险指标、导入导出 |
| 决策信号 | `DecisionSignalsPage.tsx` | `decision_signals.py`（11 端点） | 信号列表/详情、评估、Reassess |
| 历史 | `HomePage/History` | `history.py`（9 端点） | 历史报告列表/对比/删除 |
| 分析 | `ChatPage/HomePage` | `analysis.py`（6 端点）+ `agent.py`（10 端点） | 个股分析、Agent 对话、上下文 |
| 系统配置 | `SettingsPage.tsx` | `system_config.py`（15 端点） | 配置读写/校验、脱敏、重启通知 |

### C-2 核验产出

每个域输出一份 `docs/frontend_alignment_c_{domain}.md` 走查报告，含：六维度逐项判定、功能遗漏清单、契约偏差（分页/字段/错误码）、状态缺失、优先级修复清单。

### C-3 核验方法

1. 读后端端点定义（`api/v1/endpoints/{domain}.py`）提取契约
2. 读前端页面 + 对应 `api/{domain}.ts` 客户端
3. 按 ui-frontend-alignment 六维度逐项比对
4. 输出走查报告 + 修复任务清单（文件/函数级）

### C-4 验收

- 6 份走查报告全部产出
- 每份报告含 P0/P1/P2 修复清单
- 契约偏差（分页/字段/错误码）逐项标注代码定位

---

## 四、分阶段实施计划

### Phase A：后端 API 暴露（2 天）

| 步骤 | 内容 | 验证 |
|---|---|---|
| A-1 | 新增 `api/v1/endpoints/observability.py`（B-API-1~4 + 鉴权/脱敏/分页） | `py_compile` + 手动 curl |
| A-2 | 挂载到 `api/v1/router.py`（prefix=`/observability`） | 启动 server 冒烟 |
| A-3 | 新增 Pydantic Schema（SystemEventOut/IntrospectionOut/RepairOut + 脱敏逻辑） | 类型校验 |
| A-4 | 新增 `/ws/events` WS 端点（复用 EventBus `on_batch`） | WS 客户端冒烟 |
| A-5 | 单测：observability 端点 + WS 测试 | pytest |

### Phase B：前端类型 + API 客户端（1 天）

| 步骤 | 内容 | 验证 |
|---|---|---|
| B-1 | 新增 `types/observability.ts`（含分页/脱敏类型） | tsc |
| B-2 | 新增 `api/observability.ts`（9 个方法 + WS 连接） | tsc |
| B-3 | 挂载到 `api/index.ts` barrel | tsc |

### Phase C：观察面板页面 + 组件（3 天）

| 步骤 | 内容 | 验证 |
|---|---|---|
| C-1 | 新增 `ObservabilityPage.tsx`（路由 `/observability`） | tsc + lint |
| C-2 | 7 个组件（EventStreamPanel 等），按状态矩阵实现四态 | tsc + lint |
| C-3 | 导航栏加入口 | lint |
| C-4 | WS 事件流接入 EventStreamPanel（REST 降级兜底） | 手工走查 |
| C-5 | 触发反思交互流程（loading/成功/失败/超时） | 手工走查 |

### Phase D：A 域待增强项（2 天）

| 步骤 | 内容 | 依赖 |
|---|---|---|
| D-1 | A-01 CandlestickChart 挂载 + 后端 getDailyBars 真实数据 | 后端补齐 |
| D-2 | A-02 useLivePositions 挂载 | WS quotes 推送 |
| D-3 | A-03 操作乐观更新 | 前端 |
| D-4 | A-04 策略生命周期完整状态机（按 §2.3 流转表） | 前端 + 后端 API |
| D-5 | A-05 L2 订单簿 | 后端 L2 数据 |

### Phase E：C 域既有业务域核验（2 天）

| 步骤 | 内容 | 验证 |
|---|---|---|
| E-1 | 6 域走查（Alerts/Portfolio/DecisionSignals/History/Analysis/SystemConfig） | 产出 6 份报告 |
| E-2 | 按报告修复 P0/P1 项 | tsc + lint + pytest |
| E-3 | 契约偏差（分页/字段/错误码）统一 | 前后端对齐 |

### Phase F：端到端验证（1 天）

| 验证项 | 命令 |
|---|---|
| 后端回归 | `python -m pytest -m "not network"` |
| 前端编译 | `cd apps/dsa-web && npx tsc -b --noEmit` |
| 前端 lint | `npm run lint` |
| 前端 build | `npm run build` |
| 端到端冒烟 | 启动 server + 前端，走查全部页面（A/B/C 三域） |

---

## 五、工作量与依赖

| 项 | 依赖 | 可并行 |
|---|---|---|
| Phase A（后端 API + WS） | 无 | ✅ 可立即启动 |
| Phase B（前端类型/API） | Phase A 的 Schema | 与 A 并行（按约定契约） |
| Phase C（观察面板） | Phase B | 与 D 并行 |
| Phase D（A 域增强） | 后端 getDailyBars / WS quotes | 与 C 并行 |
| Phase E（C 域核验） | 无（只读走查） | 与 A/B/C 并行 |
| Phase F（端到端） | 全部 | 最后 |

**总工作量**: 约 9-11 天（含 C 域核验与端到端验证）。

---

## 六、风险与回滚

| 风险 | 缓解 |
|---|---|
| EventBus 事件量过大导致 API 响应慢 | `get_recent_events` 已有 limit；分页 + 过滤；事件面板 WS 推送 + 历史浏览分页 |
| 内省报告体积大 | IntrospectionOut Schema 裁剪字段；只返回最新一份 |
| **事件 payload 泄露内部细节（评审 REV-003）** | SystemEventOut 统一脱敏（tool arguments 截断 / 通知渠道隐藏 / LLM 密钥不暴露） |
| **RepairEffectivenessLog 多实例数据不一致（评审 REV-008）** | bootstrap 装配单例，端点依赖注入复用 |
| 新增端点破坏现有 API 契约 | 只新增不修改；router 挂载独立 prefix；分页/错误码复用现有约定 |
| C 域核验发现大量未对齐项 | 分域推进，每域独立报告独立修复，不阻塞 B 域 |
| 前端页面过多 | ObservabilityPage 单页聚合 7 组件，避免多路由膨胀 |
| 回滚 | 后端删 router 挂载行；前端删路由 + 导航项，无侵入 |

---

## 七、验收标准

| 验收项 | 判定 |
|---|---|
| 事件流可视 | 打开 `/observability` 能看到 L1/L2/L3/L4 全部事件类型（WS 实时 + REST 分页历史） |
| 内省报告可读 | 能看到 MetaCognitiveEngine 最新内省报告，可触发 dry_run 反思（含交互反馈） |
| 修复效果可见 | 能看到 RepairEffectivenessLog 记录与成功率分析（共享实例） |
| 健康趋势可见 | 能看到 HealthCheckDaemon 历史趋势 |
| payload 已脱敏 | 事件流/内省报告无内部密钥、完整 tool arguments、渠道内部配置泄露 |
| 鉴权生效 | `/observability` 端点未登录返回 401，与现有端点一致 |
| C 域核验完成 | 6 份走查报告产出，P0/P1 修复闭环 |
| 后端无回归 | `pytest -m "not network"` 全绿 |
| 前端无回归 | `tsc --noEmit` + `eslint` 零错误，`npm run build` 成功 |
| 状态覆盖 | 观察面板全组件按状态矩阵覆盖 loading/empty/error/success |

---

## 八、一句话总结

> 后端已经通过 EventBus 让 L1/L2/L3/L4 **看见自己**了，本计划让前端**看见后端看见的一切**：事件流、内省报告、修复效果、健康趋势（B 域），同时**系统性核验全部既有业务域**（C 域），把实时量化面板的遗留增强补完（A 域）——全主动观察系统第一次把"系统的自我认知"交到使用者眼前，且所有反思/修复动作保持 dry_run，观测与干预严格分离，契约（鉴权/分页/脱敏/错误码）钉死再实施。
