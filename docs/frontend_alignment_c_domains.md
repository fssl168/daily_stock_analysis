# C 域既有业务域核验走查报告

**生成日期**: 2026-08-12
**核验方法**: ui-frontend-alignment 六维度（功能完整性/交互逻辑/状态覆盖/数据字段/边界异常/文案提示）
**核验范围**: 6 个既有业务域（Alerts/Portfolio/DecisionSignals/History/Analysis/SystemConfig）
**核验执行**: Explore agent 全量契约比对 + 人工确认关键缺口

---

## 一、整体评估

| 域 | 契约一致度 | 分页/字段 | 状态覆盖 | 主要缺口 |
|---|---|---|---|---|
| 告警 Alerts | 高 | ✅ page/page_size + toCamelCase | ✅ 齐全 | **缺 updateRule 前端封装 + 编辑入口** |
| 组合 Portfolio | 高 | ✅ | ✅ 齐全 | **缺 updateAccount 前端封装 + 编辑入口** |
| 决策信号 DecisionSignals | 高 | ✅ | ✅ 完整 | 无 |
| 历史 History | 高 | ✅（page/limit 特殊） | ✅ 完整 | 轻微：loadStockBar 错误被静默吞 |
| 分析 Analysis | 中 | ✅ analysis 走 camelCase；**agent 走 snake_case 不一致** | 未专门页面 | **agent.ts 未封装 /models /research /strategies** |
| 系统配置 SystemConfig | 高 | 无分页 | ✅ 齐全 | 无 |

**总体结论**: 6 个域契约整体高度对齐，未发现前端调用后端不存在端点。发现 **3 处"后端有而前端无"的端点缺口** + 1 处命名风格不一致。

---

## 二、逐项缺口明细

| 编号 | 域 | 缺口类型 | 严重度 | 代码定位 | 说明 | 修复建议 |
|---|---|---|---|---|---|---|
| C-01 | 告警 | 功能遗漏 | P1 | 后端 `api/v1/endpoints/alerts.py:122` PATCH `/rules/{rule_id}`；前端 `apps/dsa-web/src/api/alerts.ts` | 后端有 `update_rule`，前端 `alertsApi` 未封装，页面无编辑规则入口 | 前端补 `updateRule` 方法 + 规则编辑弹窗 |
| C-02 | 组合 | 功能遗漏 | P1 | 后端 `api/v1/endpoints/portfolio.py` PUT `/accounts/{account_id}`；前端 `apps/dsa-web/src/api/portfolio.ts` | 后端有 `update_account`，前端未封装，页面只有创建/删除无编辑 | 前端补 `updateAccount` 方法 + 账户编辑入口 |
| C-03 | 分析 | 功能遗漏 | P2 | 后端 `agent.py` GET `/models`、POST `/research`、GET `/strategies`；前端 `apps/dsa-web/src/api/agent.ts` | 后端有 3 个端点，前端未封装 | 前端补 3 个方法（如 ChatPage 需要时） |
| C-04 | 分析 | 命名不一致 | P2 | `apps/dsa-web/src/api/agent.ts` | agent.ts 不走 toCamelCase（snake_case 直传），与其他 5 个客户端不一致 | 评估是否统一；当前类型自洽，低优先 |
| C-05 | 历史 | 状态缺失 | P2 | `apps/dsa-web/src/api/history.ts` loadStockBar | 错误被静默吞（无错误状态） | 补错误处理/提示 |

---

## 三、状态覆盖检查清单

| 域/页面 | 加载中 | 空数据 | 错误 | 成功 | 结论 |
|---|---|---|---|---|---|
| AlertsPage | ✅ | ✅ | ✅ | ✅ | 完整 |
| PortfolioPage | ✅ | ✅ | ✅ | ✅ | 完整 |
| DecisionSignalsPage | ✅ | ✅ | ✅ | ✅ | 完整 |
| HomePage(History) | ✅ | ✅ | ⚠️ loadStockBar 静默 | ✅ | 轻微缺失 |
| ChatPage(Agent) | ✅ | ✅ | ✅ | ✅ | 完整 |
| SettingsPage | ✅ | ✅ | ✅ | ✅ | 完整 |

---

## 四、修复优先级

### P1 本迭代
- [ ] **C-01** 前端补 Alerts `updateRule` 方法 + 编辑入口
- [ ] **C-02** 前端补 Portfolio `updateAccount` 方法 + 编辑入口

### P2 后续优化
- [ ] **C-03** 前端补 agent `/models` `/research` `/strategies` 3 个方法
- [ ] **C-04** 统一 agent.ts 命名风格（评估）
- [ ] **C-05** 补 loadStockBar 错误状态

---

## 五、结论

6 个既有业务域契约整体健康（分页/字段/状态全部对齐），无 P0 级阻断。缺口集中在**后端已实现但前端未暴露的编辑类能力**（Alerts 规则编辑、Portfolio 账户编辑）——属于增量补齐，不涉及契约破坏。修复顺序建议 P1→P2。
