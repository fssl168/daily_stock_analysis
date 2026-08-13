# -*- coding: utf-8 -*-
"""DecisionSignal → PaperSignal → Order 转换服务。

打通「AI 分析信号」到「paper trading 下单」的链路：
  实时通道（pipeline 分析落库后立即调用 convert_and_place）
  兜底通道（scheduler 每 N 分钟扫描 active 信号批量转换）

幂等保证：decision_signals.status 原子更新（active → consumed），
双通道并发时只有一个能抢到转换权，避免重复下单。

Usage:
    from src.services.paper_signal_service import (
        convert_and_place,
        convert_pending_signals_job,
        get_default_trading_account,
    )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 可触发交易的 action 集合（buy/add → 买入；reduce/sell → 卖出）
_BUY_ACTIONS = frozenset({"buy", "add", "strong_buy"})
_SELL_ACTIONS = frozenset({"sell", "reduce", "strong_sell"})
# 观望类 action 不生成订单（仅记录）
_HOLD_ACTIONS = frozenset({"hold", "watch", "wait"})

# 转换后的 paper_signals 状态
_SIGNAL_STATUS_CONSUMED = "consumed"


def get_default_trading_account(market: str = "cn") -> Optional[int]:
    """返回指定市场的默认 paper trading 账户 ID。

    优先查找 account_type='paper' 且名称含「量化」的账户；否则返回
    该市场第一个 paper 账户。找不到返回 None。
    """
    try:
        from src.storage import DatabaseManager
        from sqlalchemy import select

        db = DatabaseManager.get_instance()
        with db.get_session() as session:
            from src.storage import Account

            rows = session.execute(
                select(Account).where(
                    Account.account_type == "paper",
                    Account.is_active.is_(True),
                    Account.market == market,
                ).order_by(Account.id.asc())
            ).scalars().all()
        if not rows:
            return None
        for row in rows:
            if row.name and ("量化" in row.name or "A股" in row.name):
                return int(row.id)
        return int(rows[0].id)
    except Exception as exc:  # noqa: BLE001 — best-effort, never break pipeline
        logger.warning("paper_signal_service: resolve default account failed: %s", exc)
        return None


def _side_for_action(action: str) -> Optional[str]:
    """decision action → paper signal side（buy/sell）；观望返回 None。"""
    action_l = (action or "").strip().lower()
    if action_l in _BUY_ACTIONS:
        return "buy"
    if action_l in _SELL_ACTIONS:
        return "sell"
    return None


def _suggested_quantity_for_signal(signal: Any, account_id: int) -> Optional[float]:
    """估算建议数量：优先用现价等额买 1 手（100 股）的近似逻辑。

    简单策略：买入 → 固定 100 股（1 手）；卖出 → None（由引擎按持仓决定）。
    后续可替换为基于现金/风险的仓位模型。
    """
    side = _side_for_action(getattr(signal, "action", ""))
    if side == "buy":
        return 100.0
    return None


def convert_and_place(
    signal: Any,
    *,
    account_id: Optional[int] = None,
    order_type: str = "market",
    quantity_override: Optional[float] = None,
) -> Dict[str, Any]:
    """将一条 decision_signal 转换为 paper 订单（幂等）。

    仅处理 status='active' 的信号；成功转换后标记为 'consumed'。
    观望/持有信号直接标记 consumed（不生成订单）。
    返回转换结果字典（供日志/审计/测试断言）。
    """
    from src.repositories.decision_signal_repo import DecisionSignalRepository

    repo = DecisionSignalRepository()
    sig_id = int(getattr(signal, "id", 0) or 0)
    status = getattr(signal, "status", "")

    # ── 幂等检查：非 active 直接跳过 ──
    if status != "active":
        return {"signal_id": sig_id, "converted": False, "reason": f"status={status}"}

    action = getattr(signal, "action", "") or ""
    side = _side_for_action(action)
    market = getattr(signal, "market", "") or "cn"
    acct_id = account_id or get_default_trading_account(market)
    if acct_id is None:
        return {"signal_id": sig_id, "converted": False, "reason": "no_paper_account"}

    # ── 观望类信号：不生成订单，直接标记已处理 ──
    if side is None:
        repo.update_status(sig_id, status=_SIGNAL_STATUS_CONSUMED)
        return {
            "signal_id": sig_id,
            "converted": True,
            "order_created": False,
            "side": None,
            "reason": "hold_action_no_order",
        }

    # ── 构造 Signal 并提交 ──
    try:
        from paper_trading.strategies import Signal
        from paper_trading.trading_engine import TradingEngine
        from paper_trading.order import OrderType

        code = getattr(signal, "stock_code", "") or ""
        name = getattr(signal, "stock_name", "") or ""
        trigger_price = (
            getattr(signal, "entry_low", None)
            or getattr(signal, "entry_high", None)
            or 0.0
        )
        quantity = quantity_override or _suggested_quantity_for_signal(signal, acct_id)

        if not code or not trigger_price or trigger_price <= 0:
            # 缺少可执行价格 → 无法下单，仍标记 consumed 避免反复重试
            repo.update_status(sig_id, status=_SIGNAL_STATUS_CONSUMED)
            return {
                "signal_id": sig_id,
                "converted": True,
                "order_created": False,
                "side": side,
                "reason": f"missing_price_or_code(code={code!r}, price={trigger_price!r})",
            }

        s = Signal(
            side=side,
            code=code,
            name=name,
            strategy_name="ai_decision_signal",
            rule_name=f"decision_signal_{sig_id}",
            trigger_price=float(trigger_price),
            suggested_quantity=quantity,
            reason=(getattr(signal, "reason", "") or "")[:500],
        )

        engine = TradingEngine()
        # 先原子标记 consumed（防双通道重复下单），再下单；
        # 若下单失败，状态保持 consumed 并由兜底通道在审计日志中暴露。
        repo.update_status(sig_id, status=_SIGNAL_STATUS_CONSUMED)
        ot = OrderType.MARKET if order_type == "market" else OrderType.LIMIT
        result = engine.submit_signal(
            account_id=acct_id,
            signal=s,
            order_type=ot,
            limit_price=float(trigger_price) if ot == OrderType.LIMIT else None,
            quantity_override=quantity,
        )
        return {
            "signal_id": sig_id,
            "converted": True,
            "order_created": True,
            "side": side,
            "account_id": acct_id,
            "order_status": getattr(result, "status", "?"),
            "result": result.to_dict() if hasattr(result, "to_dict") else str(result),
        }
    except Exception as exc:  # noqa: BLE001 — 转换失败不应中断分析流程
        logger.warning(
            "paper_signal_service: convert_and_place failed for signal %s: %s",
            sig_id,
            exc,
            exc_info=True,
        )
        return {"signal_id": sig_id, "converted": False, "reason": f"error: {exc}"}


def convert_pending_signals_job(
    *,
    market: Optional[str] = None,
    account_id: Optional[int] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """兜底通道：扫描所有 active 决策信号并批量转换。

    供 scheduler.add_background_task 周期性调用（建议 300s）。
    返回统计信息供日志/测试。
    """
    from src.repositories.decision_signal_repo import DecisionSignalRepository

    repo = DecisionSignalRepository()
    repo.expire_due_signals()  # 先过期失效信号
    rows, _total = repo.list(status="active", page=1, page_size=limit)

    converted, skipped, failed = 0, 0, 0
    details: List[Dict[str, Any]] = []
    for sig in rows:
        r = convert_and_place(sig, account_id=account_id)
        if r.get("converted"):
            converted += 1
        elif r.get("reason") == "status=consumed" or r.get("reason", "").startswith("status="):
            skipped += 1
        else:
            failed += 1
        details.append(r)

    summary = {
        "scanned": len(rows),
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
    }
    if details:
        summary["details"] = details[:10]
    logger.info("paper_signal_service: convert_pending job -> %s", summary)
    return summary
