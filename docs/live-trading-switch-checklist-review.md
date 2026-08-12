# 实盘切换检查单 — 逐项核验报告

> 核验日期：2026-08-13
> 依据：`docs/live-trading-switch-checklist.md` + 代码/配置实测 + 测试结果
> 前提：T-06 5 日演练已通过（净值曲线连续 741005→760356）；本阶段**不接实盘**
> 结论符号：✅ 通过 ｜ ⚠️ 有缺口（切换实盘前需补）｜ 🔒 本阶段不执行

---

## 第 1 类 · 风控参数校验

| # | 检查项 | 现状（实测） | 判定 |
| --- | --- | --- | --- |
| 1.1 | 单票集中度 `max_pct_per_stock` | `RiskConfig` 默认 30% | ✅ 通过（实盘建议收紧 15-20%，评审项） |
| 1.2 | 单笔现金占比 `max_pct_cash_per_buy` | 默认 50% | ✅ 通过 |
| 1.3 | 当日亏损熔断 `max_daily_loss_pct` | 默认 5% | ✅ 通过 |
| 1.4 | 三级熔断 | `circuit_breaker.py` soft 3% / hard 5% / liquidation 8% / VaR 10% | ✅ 通过（与检查单一致） |
| 1.5 | 滑点模型 `slippage_bps` | `FeeModel` 默认 5bps | ✅ 通过（实盘需按券商校准） |
| 1.6 | 极端行情暂停 buy + 禁用市价单 | `extreme_market.py` `hold_buy_on_activation` 默认开 | ✅ 通过 |

## 第 2 类 · 券商接入（sandbox 验证，不开真实资金）

| # | 检查项 | 现状 | 判定 |
| --- | --- | --- | --- |
| 2.1 | `BrokerRouter.resolve_by_account` 路由 | 已修复（`_get_account_by_id` + 注入），`tests/test_broker_router.py` 5 passed | ✅ 通过 |
| 2.2 | 券商 adapter（eastmoney/longbridge）sandbox 验证 | `EastMoneyBroker` 存在，`ENABLE_EASTMONEY_PATCH=false` 未启用 | ⚠️ 缺口：sandbox 一单真实链路未验证 |
| 2.3 | 订单幂等（乐观锁） | `PaperOrder.version` + `expected_version`（order.py:269/290） | ✅ 通过 |
| 2.4 | 券商断连 fallback | `BROKER_FAILOVER_TO_PAPER_ON_DISCONNECT=true` | ✅ 通过（断线演练待做） |
| 2.5 | 订单状态机映射券商回报 | 纸面状态机完整；真实券商回报字段未映射 | ⚠️ 缺口：sandbox 回报映射未验证 |

## 第 3 类 · 订单生命周期与对账

| # | 检查项 | 现状 | 判定 |
| --- | --- | --- | --- |
| 3.1 | 部分成交 | 纸面支持（`partially_filled`） | ✅ 通过 |
| 3.2 | 撤单/改单竞态 | 乐观锁 | ✅ 通过 |
| 3.3 | 日终对账（本地 vs 券商持仓） | 无对账模块（grep 仅 config/诊断 reconcile） | ⚠️ 缺口：需新增对账任务 |
| 3.4 | 资金/冻结一致性 | `freeze_cash` / `settle_buy/sell` | ✅ 通过 |

## 第 4 类 · 权限与告警

| # | 检查项 | 现状 | 判定 |
| --- | --- | --- | --- |
| 4.1 | 实盘操作确认流程 | 无 | ⚠️ 缺口：需人工二次确认（大单/风控触发） |
| 4.2 | 告警通道 | `PAPER_TRADING_USE_NOTIFICATION_SERVICE=true`（NotificationService 已接） | ✅ 通过 |
| 4.3 | 审计日志 | 信号/订单/决策落库审计 | ✅ 通过 |
| 4.4 | 权限边界 | `require_login` + `verify_account_ownership` + WS 归属 | ✅ 通过 |

## 第 5 类 · 回滚与应急预案

| # | 检查项 | 现状 | 判定 |
| --- | --- | --- | --- |
| 5.1 | 一键切回 paper | `resolve_by_account` 未注册 broker 自动 fallback paper | ✅ 通过 |
| 5.2 | 熔断人工接管 | CircuitBreaker 状态 + 通知回调（`on_soft_trigger`） | ✅ 通过 |
| 5.3 | 极端行情应急 | ExtremeMarket 暂停 buy/禁市价单 | ✅ 通过 |

---

## 结论

**纸面交易完备度：高。** 5 大类 22 项中 **18 项通过**，4 项缺口。

**当前阶段（不接实盘）判定**：纸面交易已具备进入实盘切换准备的完备度。4 项缺口均属"切换实盘前必补"，且与"本阶段不接实盘"的约束一致，不阻塞纸面继续演进。

**切换实盘前必须补齐的 4 项（按优先级）**：
1. **2.2/2.5 券商 sandbox 一单真实链路**（下单/撤销/回报回写 + 状态机映射）—— 最高优先级，验证 `BrokerRouter` 真实路由与券商契约
2. **3.3 日终对账任务**（本地持仓 vs 券商持仓）—— 数据一致性保障
3. **4.1 实盘操作确认流程**（大单/风控触发人工二次确认）—— 风险控制
4. **1.1/1.5 风控参数实盘校准**（集中度 15-20%、滑点按券商）—— 配置评审

**建议下一步**：优先推进 2.2/2.5（券商 sandbox 接入验证），这是从"纸面"到"可执行"的关键一跳。其余三项可在纸面完备期并行准备。

---

*本报告随 `docs/live-trading-switch-checklist.md` 维护；4 项缺口补齐后再作切换决策。*
