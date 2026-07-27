# -*- coding: utf-8 -*-
"""Pre-trade risk checks for paper trading.

Each check returns a RiskDecision(passed, reason). The TradingEngine collects
all decisions and rejects the order if ANY check fails, recording the reason
for audit.

Implemented checks:
1. Account status (must be 'active').
2. Cash sufficiency for buys (estimated cost incl. fees).
3. Position availability for sells (T+1 enforced via PositionManager).
4. Single-stock concentration cap (max_pct_per_stock of total assets).
5. Max open positions count.
6. Stop-loss / take-profit auto-trigger (handled at engine level, not here).

Each check is idempotent and side-effect free — it only reads state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import func, select

from paper_trading.account import PaperAccountManager
from paper_trading.fees import FeeModel
from paper_trading.performance import PerformanceAnalyzer
from paper_trading.position import PositionManager
from src.storage import DatabaseManager, PaperPosition, get_db

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    """Result of a single risk check."""

    passed: bool
    check_name: str
    reason: str
    code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "check_name": self.check_name,
            "reason": self.reason,
        }


@dataclass
class RiskConfig:
    """Risk-control configuration.

    Defaults are conservative for a 1000 CNY paper account.
    """

    # Max fraction of total assets that may be allocated to a single stock
    # (by market value) after a new buy.
    max_pct_per_stock: float = 0.30  # 30%
    # Max number of concurrently held positions.
    max_open_positions: int = 8
    # Max fraction of available cash a single buy may consume.
    max_pct_cash_per_buy: float = 0.50  # 50%
    # Max daily realized loss as a fraction of initial capital.
    # 0 disables the check.
    max_daily_loss_pct: float = 0.05  # 5%


class RiskChecker:
    """Stateless pre-trade risk checker.

    Does not mutate any state; only reads. The engine decides what to do
    with the returned RiskDecision list.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        account_manager: Optional[PaperAccountManager] = None,
        position_manager: Optional[PositionManager] = None,
        fee_model: Optional[FeeModel] = None,
        config: Optional[RiskConfig] = None,
    ):
        self.db = db_manager or get_db()
        self.account_mgr = account_manager or PaperAccountManager(self.db)
        self.position_mgr = position_manager or PositionManager(self.db)
        self.fee_model = fee_model or FeeModel()
        self.config = config or RiskConfig()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def check_buy(
        self,
        account_id: int,
        code: str,
        price: float,
        quantity: float,
    ) -> list[RiskDecision]:
        """Run all buy-side pre-trade checks."""
        decisions: list[RiskDecision] = []

        decisions.append(self._check_account_active(account_id))
        decisions.append(self._check_max_open_positions(account_id, code))
        decisions.append(self._check_cash_sufficiency(account_id, price, quantity))
        decisions.append(
            self._check_concentration(account_id, code, price, quantity, side="buy")
        )
        decisions.append(
            self._check_cash_pct_per_buy(account_id, price, quantity)
        )
        # Sector concentration requires industry tags which are not yet stored
        # on PaperPosition. Keep a placeholder decision for audit.
        decisions.append(self._check_sector_concentration(account_id))

        return decisions

    def check_sell(
        self,
        account_id: int,
        code: str,
        price: float,
        quantity: float,
    ) -> list[RiskDecision]:
        """Run all sell-side pre-trade checks."""
        decisions: list[RiskDecision] = []

        decisions.append(self._check_account_active(account_id))
        decisions.append(self._check_position_available(account_id, code, quantity))
        decisions.append(
            self._check_daily_loss_limit(account_id, code, price, quantity)
        )
        # Concentration after a sell decreases, so we don't enforce a cap there.
        # But we still record a (passing) decision for audit completeness.
        decisions.append(
            RiskDecision(
                passed=True,
                check_name="concentration_after_sell",
                reason="sell reduces concentration; skipped",
            )
        )
        # Sector concentration requires industry tags which are not yet stored
        # on PaperPosition. Keep a placeholder decision for audit.
        decisions.append(self._check_sector_concentration(account_id))

        return decisions

    def evaluate(self, decisions: list[RiskDecision]) -> RiskDecision:
        """Aggregate a decision list into a single overall decision.

        Returns the FIRST failing decision (preserving check order), or a
        passing decision if all checks passed.
        """
        for d in decisions:
            if not d.passed:
                return d
        return RiskDecision(
            passed=True,
            check_name="all_checks",
            reason="all risk checks passed",
        )

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_account_active(self, account_id: int) -> RiskDecision:
        snap = self.account_mgr.snapshot(account_id)
        if snap.status != "active":
            return RiskDecision(
                passed=False,
                check_name="account_status",
                reason=f"account status is '{snap.status}', expected 'active'",
            )
        return RiskDecision(
            passed=True, check_name="account_status", reason="account active"
        )

    def _check_cash_sufficiency(
        self, account_id: int, price: float, quantity: float
    ) -> RiskDecision:
        snap = self.account_mgr.snapshot(account_id)
        # Estimate cost using post-slippage price for a market buy.
        eff_price = self.fee_model.apply_slippage(price, "buy")
        cost = self.fee_model.estimate_buy_cost(eff_price, quantity)
        if float(snap.cash) < cost - 1e-6:
            return RiskDecision(
                passed=False,
                check_name="cash_sufficiency",
                reason=(
                    f"insufficient cash: have {snap.cash:.2f}, "
                    f"need {cost:.2f} (price={eff_price:.4f} qty={quantity})"
                ),
            )
        return RiskDecision(
            passed=True,
            check_name="cash_sufficiency",
            reason=f"cash ok: have {snap.cash:.2f}, need {cost:.2f}",
        )

    def _check_position_available(
        self, account_id: int, code: str, quantity: float
    ) -> RiskDecision:
        pos = self.position_mgr.get_position(account_id, code)
        if pos is None or float(pos.quantity or 0.0) <= 0:
            return RiskDecision(
                passed=False,
                check_name="position_available",
                reason=f"no position to sell: code={code}",
            )
        available = float(pos.available_quantity or 0.0)
        if available < quantity - 1e-6:
            return RiskDecision(
                passed=False,
                check_name="position_available",
                reason=(
                    f"insufficient available quantity: have {available}, "
                    f"need {quantity} (T+1 may apply)"
                ),
            )
        return RiskDecision(
            passed=True,
            check_name="position_available",
            reason=f"available ok: have {available}, need {quantity}",
        )

    def _check_max_open_positions(
        self, account_id: int, code: str
    ) -> RiskDecision:
        """If this is a new position, ensure we don't exceed max_open_positions."""
        existing = self.position_mgr.get_position(account_id, code)
        if existing is not None and float(existing.quantity or 0.0) > 0:
            # Adding to an existing position — no count increase.
            return RiskDecision(
                passed=True,
                check_name="max_open_positions",
                reason="adding to existing position",
            )
        with self.db.session_scope() as session:
            count = session.execute(
                select(func.count(PaperPosition.id)).where(
                    PaperPosition.account_id == account_id,
                    PaperPosition.quantity > 0,
                )
            ).scalar_one()
        if int(count) >= int(self.config.max_open_positions):
            return RiskDecision(
                passed=False,
                check_name="max_open_positions",
                reason=(
                    f"open positions {count} >= max {self.config.max_open_positions}"
                ),
            )
        return RiskDecision(
            passed=True,
            check_name="max_open_positions",
            reason=f"open positions {count} < max {self.config.max_open_positions}",
        )

    def _check_concentration(
        self,
        account_id: int,
        code: str,
        price: float,
        quantity: float,
        side: str,
    ) -> RiskDecision:
        """After the buy, this stock's market value must be <= max_pct of total assets."""
        snap = self.account_mgr.snapshot(account_id)
        total_assets = float(snap.total_assets) or 1.0

        existing = self.position_mgr.get_position(account_id, code)
        existing_value = (
            float(existing.quantity or 0.0) * float(existing.last_price or 0.0)
            if existing is not None
            else 0.0
        )

        if side == "buy":
            new_value = existing_value + price * quantity
        else:
            new_value = max(0.0, existing_value - price * quantity)

        pct = new_value / total_assets if total_assets > 0 else 0.0
        if side == "buy" and pct > self.config.max_pct_per_stock + 1e-6:
            return RiskDecision(
                passed=False,
                check_name="concentration",
                reason=(
                    f"single-stock concentration {pct:.1%} exceeds max "
                    f"{self.config.max_pct_per_stock:.1%}"
                ),
            )
        return RiskDecision(
            passed=True,
            check_name="concentration",
            reason=f"concentration {pct:.1%} within limit",
        )

    def _check_cash_pct_per_buy(
        self, account_id: int, price: float, quantity: float
    ) -> RiskDecision:
        snap = self.account_mgr.snapshot(account_id)
        eff_price = self.fee_model.apply_slippage(price, "buy")
        cost = self.fee_model.estimate_buy_cost(eff_price, quantity)
        if float(snap.cash) <= 0:
            return RiskDecision(
                passed=False,
                check_name="cash_pct_per_buy",
                reason="no cash available",
            )
        pct = cost / float(snap.cash)
        if pct > self.config.max_pct_cash_per_buy + 1e-6:
            return RiskDecision(
                passed=False,
                check_name="cash_pct_per_buy",
                reason=(
                    f"buy cost {cost:.2f} = {pct:.1%} of cash, exceeds max "
                    f"{self.config.max_pct_cash_per_buy:.1%}"
                ),
            )
        return RiskDecision(
            passed=True,
            check_name="cash_pct_per_buy",
            reason=f"buy cost {pct:.1%} of cash within limit",
        )

    def _check_daily_loss_limit(
        self, account_id: int, code: str, price: float, quantity: float
    ) -> RiskDecision:
        """Ensure today's realized loss plus this sell's estimated loss stays within limit."""
        if self.config.max_daily_loss_pct <= 0:
            return RiskDecision(
                passed=True,
                check_name="daily_loss_limit",
                reason="daily loss limit check disabled",
            )

        snap = self.account_mgr.snapshot(account_id)
        limit = self.config.max_daily_loss_pct * float(snap.initial_capital or snap.total_assets or 1.0)

        today = date.today()
        analyzer = PerformanceAnalyzer(db_manager=self.db)
        metrics = analyzer.calculate(account_id, start_date=today, end_date=today)
        realized_loss_today = (
            metrics.avg_loss * metrics.loss_count if metrics.loss_count > 0 else 0.0
        )

        pos = self.position_mgr.get_position(account_id, code)
        estimated_additional_loss = 0.0
        if pos is not None and float(pos.avg_cost or 0.0) > 0:
            avg_cost = float(pos.avg_cost)
            estimated_additional_loss = max(0.0, (avg_cost - price) * quantity)

        total_estimated = realized_loss_today + estimated_additional_loss
        if total_estimated > limit + 1e-9:
            return RiskDecision(
                passed=False,
                check_name="daily_loss_limit",
                reason=(
                    f"estimated daily loss {total_estimated:.2f} exceeds limit "
                    f"{limit:.2f} ({self.config.max_daily_loss_pct:.1%} of capital)"
                ),
            )
        return RiskDecision(
            passed=True,
            check_name="daily_loss_limit",
            reason=f"estimated daily loss {total_estimated:.2f} within limit {limit:.2f}",
        )

    def _check_sector_concentration(self, account_id: int) -> RiskDecision:
        """Placeholder: sector concentration requires industry tags on positions."""
        return RiskDecision(
            passed=True,
            check_name="sector_concentration",
            reason="sector data unavailable; skipped",
        )

    def get_risk_snapshot(self, account_id: int) -> dict:
        """Return a read-only snapshot of current risk metrics for the account."""
        snap = self.account_mgr.snapshot(account_id)
        total_assets = float(snap.total_assets) or 1.0

        with self.db.session_scope() as session:
            position_rows = session.execute(
                select(PaperPosition).where(
                    PaperPosition.account_id == account_id,
                    PaperPosition.quantity > 0,
                )
            ).scalars().all()
            positions = [
                {
                    "quantity": float(pos.quantity or 0.0),
                    "last_price": float(pos.last_price or 0.0),
                }
                for pos in position_rows
            ]

        max_stock_pct = 0.0
        for pos in positions:
            value = pos["quantity"] * pos["last_price"]
            pct = value / total_assets if total_assets > 0 else 0.0
            if pct > max_stock_pct:
                max_stock_pct = pct

        analyzer = PerformanceAnalyzer(db_manager=self.db)
        current_dd = analyzer.get_current_drawdown(account_id)

        return {
            "account_id": account_id,
            "max_single_stock_concentration_pct": max_stock_pct * 100.0,
            "max_open_positions_limit": self.config.max_open_positions,
            "current_open_positions": len(positions),
            "max_pct_per_stock_limit": self.config.max_pct_per_stock * 100.0,
            "max_cash_per_buy_limit": self.config.max_pct_cash_per_buy * 100.0,
            "max_daily_loss_limit": self.config.max_daily_loss_pct * 100.0,
            "current_drawdown_pct": current_dd,
        }
