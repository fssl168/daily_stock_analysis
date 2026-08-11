# 前后端业务对齐计划 — Skill 评审报告

**评审日期**: 2026-08-12
**评审对象**: `docs/FRONTEND_BACKEND_ALIGNMENT_PLAN.md`
**评审方法**: `ui-frontend-alignment` + `backend-pipeline-alignment` 双 skill 框架
**评审性质**: 计划评审（尚未实施），重点验证计划本身的完整性、契约正确性与可落地性

---

## 一、整体评估

| 维度 | 评分 | 说明 |
|---|---|---|
| 业务覆盖度 | 70 / 100 | 核心增量（EventBus 观察域）识别准确，但**遗漏了部分既有业务域的未对齐项** |
| 契约一致性 | 85 / 100 | 后端方法名全部与真实代码一致；但存在**认证/鉴权、错误码、分页约定**未定义 |
| 交互还原度 | 60 / 100 | 页面/组件结构清晰，但**缺少交互细节**（状态联动、乐观更新、WS 降级） |
| 状态覆盖度 | 55 / 100 | 验收提到 loading/empty/error，但**未落实到每个组件的状态矩阵** |
| 可落地性 | 80 / 100 | 分阶段清晰、工作量合理，但**缺少具体文件/函数级任务拆解** |

**主要问题概述**: 计划准确定位了"前端看不见后端自我观察"这一核心差距，API 设计方向正确。但作为一份"对齐所有业务"的计划，它**只覆盖了实时量化 + EventBus 观察两个域，遗漏了告警(Alerts)、组合(Portfolio)、决策信号(DecisionSignals)、历史(History)、分析(Analysis)、系统配置(SystemConfig) 这些既有业务域的系统性对齐核验**；同时后端新 API 的**鉴权、错误码、分页契约**未定义，存在契约一致性风险。

---

## 二、逐项问题明细

### 2.1 业务覆盖遗漏（功能完整性维度）

| 编号 | 维度 | 阻断类型 | 判定 | 对应计划章节 | 预期行为 | 实际表现 | 影响范围 | 修复建议 |
|------|------|----------|------|------------|----------|----------|----------|----------|
| R-01 | 业务遗漏 | 功能可用性阻断 | 未实现 | §一 对齐范围 | "对齐所有业务"应覆盖全部 12 个页面/14 个 API 域 | 计划只覆盖 A 域(实时量化) + B 域(EventBus)，未纳入 Alerts/Portfolio/DecisionSignals/History/Analysis/SystemConfig 的对齐核验 | 其余业务域的对齐缺口仍是盲区 | 增加 C 域"既有业务域核验"，用 ui-frontend-alignment 六维度对每个页面走查 |
| R-02 | 契约遗漏 | 契约一致性阻断 | 存在偏差 | §3.1 后端 API 设计 | 新端点应定义鉴权方式 | 未说明 `/observability` 端点如何鉴权（现有端点走 `get_paper_trading_service` 依赖注入，observability 应明确走 auth 依赖） | 新端点可能无鉴权或鉴权不一致 | 明确 observability 端点鉴权方案（复用 auth 依赖或公开只读） |
| R-03 | 契约遗漏 | 契约一致性阻断 | 存在偏差 | §3.1 分页参数 | 分页契约应与现有约定一致 | 计划用 `limit`/`offset`，但未核对现有端点分页约定（是否用 page/size） | 前端分页组件可能解析失败 | 核查现有端点分页约定并统一 |
| R-04 | 契约遗漏 | 契约一致性阻断 | 存在偏差 | §3.1 错误响应 | 错误码体系应统一 | 计划未定义 `/observability` 错误响应格式 | 前端错误处理不一致 | 复用现有 `api/v1/errors.py` 错误体系 |
| R-05 | 交互遗漏 | 功能可用性阻断 | 未实现 | §3.2 触发反思 | `POST /meta/reflect` 触发反思应可视化结果 | 计划有按钮但未定义点击后的交互流程（loading/成功/报告展示） | 用户触发后无反馈 | 补充 reflect 交互流程 |
| R-06 | 实时性设计缺口 | 功能可用性阻断 | 存在偏差 | §3.3 WS 可选 | 事件流实时推送列为可选 | 但 A 域 QuoteTicker 等已用 WS，EventBus 事件流用轮询会造成观察面板与实时面板体验割裂 | 观察面板滞后 | 评估 EventBus WS 推送优先级，建议提升为 P0 |

### 2.2 计划合理性（backend-pipeline-alignment 维度）

| 编号 | 维度 | 阻断类型 | 判定 | 预期 | 实际 | 建议 |
|------|------|----------|------|------|------|------|
| R-07 | 数据流 | 数据正确性阻断 | 有缺陷 | 事件流 payload 含敏感信息（agent tool 参数/notification 渠道详情） | 计划未定义 `SystemEventOut` 字段裁剪/脱敏 | 前端可能暴露内部 payload | SystemEventOut Schema 明确裁剪字段、脱敏规则 |
| R-08 | 数据源 | 功能可用性阻断 | 有缺陷 | repair_effectiveness_log 需要单例/共享实例 | 计划未说明如何获取共享实例（每次 new 会从磁盘加载、但记录可能丢） | 多次实例化导致数据不一致 | 在 bootstrap_event_bus 装配 repair log 单例，端点复用 |
| R-09 | 性能 | 功能可用性阻断 | 有缺陷 | 事件流查询高频 | 计划有分页但未定义默认 limit 与超时 | 大事件量下 API 慢 | 明确默认 limit、加缓存 |
| R-10 | 状态机 | 业务规则阻断 | 存在偏差 | A-04 策略生命周期 | 计划只列状态名，未定义状态流转合法路径/校验 | 实施时可能状态跳转混乱 | 补充策略状态机流转表 |

---

## 三、API 对齐检查清单（新增）

| 接口路径 | HTTP | 计划状态 | 鉴权 | 参数 | 响应 | 遗漏 |
|----------|------|---------|------|------|------|------|
| /observability/events | GET | ✅ 定义 | ❌ 未定义 | limit/offset 待确认 | SystemEventOut 待脱敏 | 分页契约 |
| /observability/events/stats | GET | ✅ 定义 | ❌ 未定义 | - | 统计结构待定 | - |
| /observability/events/correlation/{cid} | GET | ✅ 定义 | ❌ 未定义 | cid | 事件链 | - |
| /observability/meta/observations | GET | ✅ 定义 | ❌ 未定义 | - | 观察列表 | - |
| /observability/meta/introspection | GET | ✅ 定义 | ❌ 未定义 | - | 报告裁剪 | - |
| /observability/meta/reflect | POST | ✅ 定义 | ❌ 未定义 | - | 报告 | 交互流程(R-05) |
| /observability/repairs | GET | ✅ 定义 | ❌ 未定义 | - | 修复记录 | 共享实例(R-08) |
| /observability/repairs/effectiveness | GET | ✅ 定义 | ❌ 未定义 | window | 效果报告 | - |
| /observability/regressions | GET | ✅ 定义 | ❌ 未定义 | - | 回归记录 | - |
| /observability/health/trend | GET | ✅ 定义 | ❌ 未定义 | - | 趋势 | - |

---

## 四、状态覆盖检查清单（B 域组件）

| 组件 | 加载中 | 空数据 | 错误 | 成功 | 实时更新 | 评审结论 |
|------|--------|--------|------|------|----------|----------|
| EventStreamPanel | 计划未明确 | 计划未明确 | 计划未明确 | ✅ | 轮询(计划) | 需补充状态矩阵 |
| EventStatsOverview | ⚠️ | ⚠️ | ⚠️ | ✅ | - | 需补充 |
| MetaIntrospectionPanel | ⚠️ | ⚠️ | ⚠️ | ✅ | - | 需补充 |
| MetaObservationsPanel | ⚠️ | ⚠️ | ⚠️ | ✅ | - | 需补充 |
| RepairEffectivenessPanel | ⚠️ | ⚠️ | ⚠️ | ✅ | - | 需补充 |
| RegressionPanel | ⚠️ | ⚠️ | ⚠️ | ✅ | - | 需补充 |
| HealthTrendPanel | ⚠️ | ⚠️ | ⚠️ | ✅ | - | 需补充 |

---

## 五、阻断类型统计

| 阻断类型 | 数量 | P0 | P1 | 说明 |
|----------|------|-----|-----|------|
| 数据正确性阻断 | 1 | 1 | 0 | R-07 payload 脱敏 |
| 功能可用性阻断 | 4 | 2 | 2 | R-01 业务遗漏、R-06 WS 优先级、R-05 reflect 交互、R-08 共享实例 |
| 业务规则阻断 | 1 | 0 | 1 | R-10 策略状态机 |
| 契约一致性阻断 | 3 | 1 | 2 | R-02 鉴权、R-03 分页、R-04 错误码 |

---

## 六、优先级修复清单（计划需修订项）

### P0（修订计划时必须纳入）
- [ ] **REV-001** 补充 C 域"既有业务域核验"：对 Alerts/Portfolio/DecisionSignals/History/Analysis/SystemConfig 六域各做一次六维度走查（复用 ui-frontend-alignment）
- [ ] **REV-002** 定义 `/observability` 端点鉴权方案（复用现有 auth 依赖）
- [ ] **REV-003** SystemEventOut Schema 定义字段裁剪与脱敏规则
- [ ] **REV-004** 明确 repair_effectiveness_log 共享实例装配（bootstrap 里注册单例）

### P1（本迭代）
- [ ] **REV-005** 统一分页契约（核对现有端点 page/size vs limit/offset）
- [ ] **REV-006** 复用 `api/v1/errors.py` 错误码体系
- [ ] **REV-007** 补充每个 B 域组件状态矩阵（loading/empty/error/success）
- [ ] **REV-008** 补充 `POST /meta/reflect` 交互流程
- [ ] **REV-009** 评估 EventBus WS 推送，建议提升为 P0
- [ ] **REV-010** 补充策略生命周期状态流转表

---

## 七、评审结论

**计划方向正确、核心增量识别精准，但作为"对齐所有业务"的计划覆盖不完整。** 按双 skill 框架审查，发现 10 项需修订（4 P0 / 6 P1）。

**建议**: 先按 REV-001~REV-010 修订计划，补齐 C 域核验、契约定义（鉴权/分页/错误码/脱敏）、状态矩阵、交互流程，再进入实施。修订后的计划才满足"对齐所有业务"的目标。

**一句话**: 计划把"后端看见自己"的能力接给前端是对的，但还差"看全所有业务"和"把契约钉死"两步——先补这两步再动手。
