# -*- coding: utf-8 -*-
"""EastMoneyBroker: real brokerage adapter for 东方财富 (Eastmoney).

Wraps ``easytrader`` to execute real orders via the 东方财富 desktop client
(``xiadan.exe``). Implements the ``BaseBroker`` contract so upper layers can
route orders through ``BrokerRouter`` without code changes.

Environment:
- Windows only (easytrader uses COM automation against the trading client).
- Requires ``easytrader``: ``pip install easytrader``.
- The 东方财富 desktop client must be running and logged in.

Config (via .env or Config):
- ``BROKER_EASTMONEY_USER`` / ``BROKER_EASTMONEY_PASSWORD``
- ``BROKER_EASTMONEY_CLIENT_PATH`` (optional, defaults to standard install path)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from paper_trading.broker.base import BaseBroker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# easytrader availability check (only on Windows)
# ---------------------------------------------------------------------------

_EASYTRADER_AVAILABLE = False
try:
    import easytrader  # type: ignore[import-untyped]

    _EASYTRADER_AVAILABLE = True
except ImportError:
    logger.info("easytrader not installed; EastMoneyBroker will be unavailable. "
                "Install with: pip install easytrader")


class EastMoneyBroker(BaseBroker):
    """Real brokerage adapter for 东方财富 (Eastmoney) desktop client.

    Uses ``easytrader`` to automate the trading client's COM interface.
    Falls back gracefully: ``is_connected()`` returns False when the
    client is unreachable or ``easytrader`` is not installed.
    """

    def __init__(
        self,
        user: Optional[str] = None,
        password: Optional[str] = None,
        client_path: Optional[str] = None,
        broker_name: str = "eastmoney",
    ) -> None:
        self._user = user or os.getenv("BROKER_EASTMONEY_USER", "")
        self._password = password or os.getenv("BROKER_EASTMONEY_PASSWORD", "")
        self._client_path = client_path or os.getenv(
            "BROKER_EASTMONEY_CLIENT_PATH",
            r"C:\Program Files\东方财富\xiadan.exe",
        )
        self._broker_name = broker_name
        self._client: Any = None
        self._connected: bool = False

        if _EASYTRADER_AVAILABLE and self._user and self._password:
            self._try_connect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _try_connect(self) -> bool:
        """Attempt to connect to the Eastmoney client.

        Returns True on success, False on any failure. Never raises.
        """
        try:
            self._client = easytrader.use("eastmoney")
            if self._client_path and os.path.exists(self._client_path):
                self._client.connect(self._client_path)
            else:
                self._client.connect(r"C:\Program Files\东方财富\xiadan.exe")
            self._client.prepare(
                self._user,
                self._password,
                comm_password=None,
            )
            self._connected = True
            logger.info("EastMoneyBroker connected")
            return True
        except Exception as exc:
            logger.warning("EastMoneyBroker connection failed: %s", exc)
            self._connected = False
            return False

    def is_connected(self) -> bool:
        """Return whether the Eastmoney client is reachable."""
        if not _EASYTRADER_AVAILABLE:
            return False
        if not self._connected or self._client is None:
            return False
        try:
            # Lightweight ping: query positions (cached by the client).
            self._client.position
            return True
        except Exception:
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # Broker interface
    # ------------------------------------------------------------------

    def submit_order(self, order: Any, account_id: Optional[int] = None) -> Dict[str, Any]:
        """Submit an order and return the broker-side entrust_no."""
        if not self._connected or self._client is None:
            raise RuntimeError("EastMoneyBroker not connected")

        code = str(getattr(order, "code", ""))
        price = float(getattr(order, "price", 0) or 0)
        quantity = int(getattr(order, "quantity", 0) or 0)
        side = str(getattr(order, "side", "")).lower()

        if side == "buy":
            result = self._client.buy(code, price, quantity)
        elif side == "sell":
            result = self._client.sell(code, price, quantity)
        else:
            raise ValueError(f"Unknown side: {side}")

        entrust_no = result.get("entrust_no", "")
        logger.info(
            "EastMoneyBroker order submitted: side=%s code=%s qty=%s entrust_no=%s",
            side, code, quantity, entrust_no,
        )
        return {
            "broker_order_id": entrust_no,
            "status": "queued",
            "filled_quantity": 0,
            "filled_price": None,
        }

    def cancel_order(self, order_id: Any, reason: Optional[str] = None) -> Dict[str, Any]:
        """Cancel an order by entrust_no.

        The ``order_id`` parameter should be the broker_order_id returned
        by ``submit_order``.
        """
        if not self._connected or self._client is None:
            raise RuntimeError("EastMoneyBroker not connected")

        entrust_no = str(order_id)
        success = self._client.cancel_entrust(entrust_no)
        logger.info(
            "EastMoneyBroker cancel: entrust_no=%s success=%s reason=%s",
            entrust_no, success, reason,
        )
        return {
            "broker_order_id": entrust_no,
            "canceled": bool(success),
            "reason": reason,
        }

    def query_order(self, order_id: Any) -> Optional[Dict[str, Any]]:
        """Query order status by entrust_no.

        Returns None if the order is not found.
        """
        if not self._connected or self._client is None:
            return None
        try:
            entrust_no = str(order_id)
            orders = self._client.today_entrusts
            for o in orders:
                if str(o.get("entrust_no", "")) == entrust_no:
                    return {
                        "broker_order_id": entrust_no,
                        "status": o.get("status", "unknown"),
                        "code": o.get("证券代码", ""),
                        "price": float(o.get("委托价格", 0) or 0),
                        "quantity": int(o.get("委托数量", 0) or 0),
                        "filled_quantity": int(o.get("成交数量", 0) or 0),
                        "filled_price": float(o.get("成交均价", 0) or 0),
                    }
            return None
        except Exception as exc:
            logger.warning("EastMoneyBroker query_order failed: %s", exc)
            return None

    def query_positions(self, account_id: Any = None) -> List[Dict[str, Any]]:
        """Return current positions from the Eastmoney client."""
        if not self._connected or self._client is None:
            return []
        try:
            positions = self._client.position
            result: List[Dict[str, Any]] = []
            for p in positions:
                result.append({
                    "code": str(p.get("证券代码", "")),
                    "name": str(p.get("证券名称", "")),
                    "quantity": int(p.get("股票余额", 0) or 0),
                    "available_quantity": int(p.get("可用余额", 0) or 0),
                    "avg_cost": float(p.get("成本价", 0) or 0),
                    "current_price": float(p.get("市价", 0) or 0),
                    "market_value": float(p.get("市值", 0) or 0),
                    "profit_loss": float(p.get("盈亏", 0) or 0),
                    "profit_loss_pct": float(p.get("盈亏比例(%)", 0) or 0),
                })
            return result
        except Exception as exc:
            logger.warning("EastMoneyBroker query_positions failed: %s", exc)
            return []

    def query_account(self, account_id: Any = None) -> Dict[str, Any]:
        """Return account summary from the Eastmoney client."""
        if not self._connected or self._client is None:
            return {"account_id": "", "total_assets": 0.0, "available_cash": 0.0,
                    "frozen_cash": 0.0, "positions": []}
        try:
            balance = self._client.balance
            return {
                "account_id": self._user,
                "total_assets": float(balance.get("总资产", 0) or 0),
                "available_cash": float(balance.get("可用资金", 0) or 0),
                "frozen_cash": float(balance.get("冻结资金", 0) or 0),
                "positions": [],
            }
        except Exception as exc:
            logger.warning("EastMoneyBroker query_account failed: %s", exc)
            return {"account_id": "", "total_assets": 0.0, "available_cash": 0.0,
                    "frozen_cash": 0.0, "positions": []}
