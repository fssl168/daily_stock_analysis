# -*- coding: utf-8 -*-
"""Fee and slippage model for paper trading.

Models A-share transaction costs:
- Commission: broker fee, default 0.025% with min 5 CNY per trade (both sides).
- Stamp duty: 0.05% sell-side only.
- Transfer fee: 0.001% both sides (SSE/SZSE combined).

Slippage:
- Market orders: a few bps against the trader (price * (1 +/- bps)).
- Limit orders: no slippage (filled at limit price when trigger conditions met).

All rates are configurable via the FeeModel dataclass; defaults reflect the
typical retail A-share broker schedule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class FeeModel:
    """Transaction cost model.

    Attributes are stored as fractions (0.0003 = 3 bps). Set any rate to 0
    to disable that component.
    """

    # Commission.
    commission_rate: float = 0.00025  # 0.025%
    commission_min: float = 5.0       # Min commission per trade (CNY).
    # Stamp duty (sell-side only).
    stamp_duty_rate: float = 0.0005   # 0.05%
    # Transfer fee (both sides).
    transfer_fee_rate: float = 0.00001  # 0.001%
    # Slippage (in bps, applied to market orders only).
    slippage_bps: float = 5.0         # 5 bps = 0.05%

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commission_rate": self.commission_rate,
            "commission_min": self.commission_min,
            "stamp_duty_rate": self.stamp_duty_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeeModel":
        """Build a FeeModel from a config dict, ignoring unknown keys."""
        if not data:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    # ------------------------------------------------------------------
    # Slippage
    # ------------------------------------------------------------------

    def apply_slippage(self, price: float, side: str) -> float:
        """Return the effective fill price for a market order.

        Buy: price * (1 + bps/10000)  -> pay more.
        Sell: price * (1 - bps/10000) -> receive less.
        """
        if self.slippage_bps <= 0 or price <= 0:
            return price
        bps = self.slippage_bps / 10000.0
        if side == "buy":
            return round(price * (1.0 + bps), 4)
        if side == "sell":
            return round(price * (1.0 - bps), 4)
        return price

    # ------------------------------------------------------------------
    # Fee calculation
    # ------------------------------------------------------------------

    def compute_fee(self, side: str, price: float, quantity: float) -> float:
        """Total fee for a single fill.

        Args:
            side: "buy" or "sell".
            price: Fill price (post-slippage for market orders).
            quantity: Filled shares.

        Returns:
            Total fee in CNY (commission + stamp duty + transfer fee).
        """
        if price <= 0 or quantity <= 0:
            return 0.0

        amount = price * quantity

        # Commission (both sides, with minimum).
        commission = max(amount * self.commission_rate, self.commission_min)

        # Stamp duty (sell-side only).
        stamp_duty = 0.0
        if side == "sell":
            stamp_duty = amount * self.stamp_duty_rate

        # Transfer fee (both sides).
        transfer = amount * self.transfer_fee_rate

        total = commission + stamp_duty + transfer
        return round(total, 4)

    def estimate_buy_cost(self, price: float, quantity: float) -> float:
        """Total cash needed for a buy: amount + fee (slippage not applied here)."""
        if price <= 0 or quantity <= 0:
            return 0.0
        amount = price * quantity
        fee = self.compute_fee("buy", price, quantity)
        return round(amount + fee, 4)

    def estimate_sell_proceeds(self, price: float, quantity: float) -> float:
        """Net cash received from a sell: amount - fee (slippage not applied here)."""
        if price <= 0 or quantity <= 0:
            return 0.0
        amount = price * quantity
        fee = self.compute_fee("sell", price, quantity)
        return round(amount - fee, 4)


# Default singleton for convenience.
DEFAULT_FEE_MODEL = FeeModel()
