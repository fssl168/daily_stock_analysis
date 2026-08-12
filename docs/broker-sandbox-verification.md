# 券商 Sandbox 接入验证方案（Checklist 2.2 / 2.5）

> 状态：方案（待真实 Windows sandbox 环境执行）
> 更新：2026-08-13
> 定位：实盘切换检查单第 2 类（券商接入）与第 3 类（订单回报）的**验证方案**。本阶段不接实盘；在券商 sandbox / 模拟环境验证单笔真实链路。

---

## 1. 目标

验证纸面系统到真实券商 adapter 的最小闭环：
1. **2.2** `BrokerRouter` 按账户路由到非 paper broker（`EastMoneyBroker`），提交一单 sandbox 委托并收到券商委托号。
2. **2.5** 券商回报（`query_order` 中文状态）→ 纸面订单状态映射，成交回报回写本地订单/持仓。

## 2. 现实约束（为什么沙箱 Linux 不能直接执行）

| 约束 | 说明 |
| --- | --- |
| easytrader 为 **Windows-only** | 通过 COM 自动化操作东方财富桌面客户端（`xiadan.exe`），Linux 无法运行 |
| 需要**已登录的桌面客户端** | `prepare(user, password)` 需真实账号 |
| 凭据占位 | `.env` 中 `BROKER_EASTMONEY_USER/PASSWORD` 为占位符 |
| 本阶段不接实盘 | 验证在券商提供的 **sandbox / 模拟环境** 进行，不触真实资金 |

> 沙箱内已完成**代码层契约验证**：`tests/test_broker_contract.py`（mock easytrader 客户端，11 项）+ `tests/test_broker_router.py`（路由，5 项）共 16 passed。本方案用于真实 Windows 环境的最终核验。

## 3. 已就绪的代码基础

- `paper_trading/broker/router.py`：`BrokerRouter.resolve_by_account(account_id, account_mgr)` 已修复（支持注入、未注册 broker 自动 fallback paper）。
- `paper_trading/broker/eastmoney_broker.py`：`EastMoneyBroker` 实现 `BaseBroker` 契约（submit/cancel/query_order/query_positions/query_account/is_connected）。
- `paper_trading/broker/order_status.py`：`map_broker_status` / `build_order_update` / `is_terminal`（券商中文状态 → 纸面状态）。
- 测试：`tests/test_broker_contract.py`、`tests/test_broker_router.py`。

## 4. 真实验证步骤（Windows + 券商 sandbox）

### 4.1 环境准备
```bash
pip install easytrader
# 启动并登录东方财富桌面客户端（sandbox 环境）
```
`.env` 配置：
```
BROKER_EASTMONEY_USER=<sandbox 账号>
BROKER_EASTMONEY_PASSWORD=<sandbox 密码>
BROKER_EASTMONEY_CLIENT_PATH=C:\Program Files\东方财富\xiadan.exe
ENABLE_EASTMONEY_PATCH=false   # 验证阶段不开启实盘 patch
```

### 4.2 路由验证
- 建一个账户，`broker` 字段设为 `eastmoney`。
- 调用 `BrokerRouter.resolve_by_account(account_id)` → 应返回 `EastMoneyBroker`（非 fallback paper）。
- 验证 `is_connected() == True`（客户端可达）。

### 4.3 单笔委托闭环
1. `broker.submit_order(OrderRequest(code, side, price, qty))` → 返回 `broker_order_id`（委托号）与 `status=queued`。
2. 轮询 `broker.query_order(broker_order_id)` → 券商回报（`status` 中文，如 `已成/部成/已撤`）。
3. 用 `build_order_update(report)` 映射 → 更新本地 `PaperOrder`：
   - `filled` / `partially_filled` → 更新 `filled_quantity` / `filled_price_avg`
   - `canceled` / `rejected` → 标记终态
4. 成交回报写回：`PositionManager.apply_buy/sell` + `AccountManager.settle_buy/sell`（复用纸面成交路径）。

### 4.4 日终对账（Checklist 3.3 前置）
- `broker.query_positions()` vs `PositionManager.list_positions(account_id)`：按 `code` 对齐数量/可用/成本。
- 差异项输出清单，人工复核。

## 5. 验收标准

| # | 标准 | 证据 |
| --- | --- | --- |
| 5.1 | `resolve_by_account` 返回 `EastMoneyBroker` | 单测 + sandbox 实测 |
| 5.2 | `submit_order` 返回券商委托号 | sandbox 委托单 |
| 5.3 | `query_order` 回报被 `map_broker_status` 正确映射 | 实测：已成→filled / 部成→partially_filled / 已撤→canceled |
| 5.4 | 成交回报回写本地订单/持仓一致 | 对账无差异 |
| 5.5 | 断连时 `BROKER_FAILOVER_TO_PAPER_ON_DISCONNECT=true` 自动切 paper | 断线演练 |

## 6. 回滚与安全

- 验证全程在券商 **sandbox**，不触真实资金；`.env` 保持 `ENABLE_EASTMONEY_PATCH=false`。
- 任何异常：`BrokerRouter` 未注册/断连 → fallback paper，不影响主流程。
- 验证完成后清理 sandbox 委托记录与本地测试账户。

---

*本方案随 `docs/live-trading-switch-checklist.md` / `-review.md` 维护；4 项缺口之一（2.2/2.5），真实环境执行后更新核验报告。*
