# -*- coding: utf-8 -*-
"""Paper trading 事件总线（pending-api §3）。

轻量级进程内 pub/sub，为 ``WS /ws/events`` 端点提供交易事件与风险告警流。
发射方发布符合前端契约的普通 dict：

- 交易事件：带 ``eventType``（15 种枚举）＋可选 code/orderId/side/price/
  quantity/strategyName/reason，前端 ``EventLogFeed`` 以 ``eventType`` 字段判别。
- 风险告警：带 ``alertType``（var_breach / liquidity_warning /
  market_anomaly）＋ message/detail/level，前端 ``RiskAlertToast`` 以
  ``alertType`` 字段判别。

线程安全；WS 端点订阅回调将事件排入连接级队列。保留最近事件用于连接时重放。

实现依据: docs/paper_trading_pending_api.md §3
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

EventHandler = Callable[[Dict[str, Any]], None]


class PaperTradingEventBus:
    """paper-trading 事件总线单例。"""

    _instance: Optional["PaperTradingEventBus"] = None
    _instance_lock = threading.Lock()

    def __init__(self, max_replay: int = 200) -> None:
        self._handlers: List[EventHandler] = []
        self._recent: deque = deque(maxlen=max_replay)
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> "PaperTradingEventBus":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def publish(self, payload: Dict[str, Any]) -> None:
        """同步分发事件到所有订阅者（订阅者异常不影响其他订阅者）。"""
        with self._lock:
            self._recent.append(payload)
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(payload)
            except Exception:
                logger.exception("[PaperTradingEventBus] handler failed")

    def subscribe(self, handler: EventHandler) -> EventHandler:
        """注册订阅，返回 handler 便于对称注销。"""
        with self._lock:
            self._handlers.append(handler)
        return handler

    def unsubscribe(self, handler: EventHandler) -> None:
        with self._lock:
            self._handlers = [h for h in self._handlers if h is not handler]

    def replay(self) -> List[Dict[str, Any]]:
        """返回最近事件（连接建立时重放，浅拷贝）。"""
        with self._lock:
            return list(self._recent)

    def clear(self) -> None:
        """清空订阅与重放缓存（仅用于测试）。"""
        with self._lock:
            self._handlers.clear()
            self._recent.clear()


# ---------------------------------------------------------------------------
# 发射辅助
# ---------------------------------------------------------------------------

_counter = itertools.count(1)


def _next_event_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"evt-{ts}-{next(_counter):04d}"


def emit_trade_event(
    event_type: str,
    *,
    code: Optional[str] = None,
    order_id: Optional[int] = None,
    side: Optional[str] = None,
    price: Optional[float] = None,
    quantity: Optional[float] = None,
    strategy_name: Optional[str] = None,
    reason: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> None:
    """发布一条交易事件（eventType 判别）。"""
    payload = {
        "eventId": _next_event_id(),
        "eventType": event_type,
        "code": code,
        "orderId": order_id,
        "side": side,
        "price": price,
        "quantity": quantity,
        "strategyName": strategy_name,
        "reason": reason,
        "timestamp": timestamp or datetime.now().isoformat(),
    }
    PaperTradingEventBus.instance().publish(payload)


def emit_risk_alert(
    alert_type: str,
    *,
    message: str,
    detail: Optional[str] = None,
    level: str = "warning",
    timestamp: Optional[str] = None,
) -> None:
    """发布一条风险告警（alertType 判别）。"""
    payload = {
        "alertType": alert_type,
        "message": message,
        "detail": detail,
        "level": level,
        "timestamp": timestamp or datetime.now().isoformat(),
    }
    PaperTradingEventBus.instance().publish(payload)
