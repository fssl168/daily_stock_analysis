# -*- coding: utf-8 -*-
"""Broker order-status mapping (T-07 / checklist 2.5).

Maps broker-side (easytrader / EastMoney) order status strings to the
paper-trading ``PaperOrder.status`` vocabulary:

    pending / partially_filled / filled / canceled / rejected

Also builds a paper-order update dict from a ``query_order`` report so the
reconciliation path can apply broker fills back to the local order book.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Common EastMoney / easytrader 委托状态 -> paper status.
_BROKER_STATUS_MAP: Dict[str, str] = {
    "已成": "filled",
    "全部成交": "filled",
    "完全成交": "filled",
    "部成": "partially_filled",
    "部分成交": "partially_filled",
    "部分撤单": "partially_filled",
    "部撤": "partially_filled",
    "已报": "pending",
    "未成交": "pending",
    "已报待撤": "pending",
    "待撤": "pending",
    "已撤": "canceled",
    "已撤单": "canceled",
    "撤单": "canceled",
    "废单": "rejected",
    "拒绝": "rejected",
    "委托失败": "rejected",
}


def map_broker_status(broker_status: Optional[str]) -> str:
    """Map a broker-side status string to a paper status value.

    Unknown / empty values default to ``pending`` so the caller can reconcile
    again later rather than mis-marking a live order.
    """
    if not broker_status:
        return "pending"
    return _BROKER_STATUS_MAP.get(str(broker_status).strip(), "pending")


def build_order_update(report: Dict[str, Any]) -> Dict[str, Any]:
    """Build a paper-order update dict from a broker ``query_order`` report.

    The report is the dict returned by ``BaseBroker.query_order`` (broker-side
    field names). Produces fields safe to apply to ``PaperOrder``.
    """
    return {
        "status": map_broker_status(report.get("status", "")),
        "filled_quantity": float(report.get("filled_quantity", 0) or 0),
        "filled_price": report.get("filled_price"),
    }


def is_terminal(status: str) -> bool:
    """True when the paper status is terminal (no further reconciliation)."""
    return status in ("filled", "canceled", "rejected")
