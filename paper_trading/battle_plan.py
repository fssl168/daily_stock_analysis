# -*- coding: utf-8 -*-
"""Daily battle plan generator (P1-B).

A "battle plan" is the next-trading-day operations card the AI portfolio
manager works from. Each plan contains:

1. **Holdings plans** — for every open position, a three-scenario response
   (strong / neutral / weak market open) plus the existing stop-loss and
   take-profit lines.
2. **Candidate plans** — for each watched code that is *not* currently
   held, a buy plan with auction condition, intraday trigger, suggested
   position ratio, and three SL/TP lines (computed via
   :class:`paper_trading.sltp_calculator.SLTPCalculator`).
3. **Market review** — an AI-generated paragraph + sentiment score +
   main theme, produced by the PM agent when available (falls back to a
   rule-based summary if the agent is offline).

Persistence: each plan is upserted into ``PaperBattlePlan`` keyed by
``(account_id, date)`` so re-generating the plan for the same day replaces
the prior version.

Public API::

    >>> gen = BattlePlanGenerator(pm_agent=pm, sltp_calculator=calc, ...)
    >>> plan = gen.generate(account_id=1, target_date=date(2026, 7, 28))
    >>> plan.to_markdown()  # push-friendly Markdown card
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import desc, select

from src.storage import DatabaseManager, PaperBattlePlan, get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HoldingPlan:
    """Three-scenario plan for an existing holding."""

    code: str
    name: str
    current_price: float
    strong_scenario: str = ""        # bullish open: how to react
    neutral_scenario: str = ""       # flat open: how to react
    weak_scenario: str = ""          # bearish open: how to react
    action_conditions: List[str] = field(default_factory=list)
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "current_price": self.current_price,
            "strong_scenario": self.strong_scenario,
            "neutral_scenario": self.neutral_scenario,
            "weak_scenario": self.weak_scenario,
            "action_conditions": list(self.action_conditions),
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
        }


@dataclass
class CandidatePlan:
    """Plan for a candidate stock to buy."""

    code: str
    name: str
    auction_condition: str = ""      # 集合竞价条件
    intraday_trigger: str = ""       # 盘中触发条件
    position_ratio: float = 0.0      # suggested % of cash to deploy
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    technical_score: float = 0.0     # 0-100, higher = more attractive

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "auction_condition": self.auction_condition,
            "intraday_trigger": self.intraday_trigger,
            "position_ratio": self.position_ratio,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "technical_score": self.technical_score,
        }


@dataclass
class BattlePlan:
    """Daily battle plan (次日作战卡)."""

    plan_id: Optional[int] = None
    account_id: int = 0
    date: Optional[date] = None
    holdings_plans: List[HoldingPlan] = field(default_factory=list)
    candidates: List[CandidatePlan] = field(default_factory=list)
    market_review: str = ""
    sentiment_score: int = 50            # 0-100
    main_theme: str = ""
    used_fallback: bool = False
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "account_id": self.account_id,
            "date": self.date.isoformat() if self.date else None,
            "holdings_plans": [p.to_dict() for p in self.holdings_plans],
            "candidates": [p.to_dict() for p in self.candidates],
            "market_review": self.market_review,
            "sentiment_score": self.sentiment_score,
            "main_theme": self.main_theme,
            "used_fallback": self.used_fallback,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_markdown(self) -> str:
        """Render the plan as a Markdown operations card.

        Format designed for direct push to Lark/DingTalk or display in WebUI.
        """
        date_str = self.date.isoformat() if self.date else "N/A"
        lines: List[str] = [
            f"# 📋 次日作战卡 - {date_str}",
            "",
            "## 市场综述",
            self.market_review or "(暂无)",
            "",
            f"**情绪评级**: {self.sentiment_score}/100  |  **主线**: {self.main_theme or '(暂无)'}",
            "",
        ]

        if self.holdings_plans:
            lines.append("## 持仓应对（三情景）")
            for h in self.holdings_plans:
                lines.append(f"### {h.code} {h.name or ''}")
                lines.append(f"- 当前价: {h.current_price:.4f}")
                lines.append(f"- 强势: {h.strong_scenario or '(暂无)'}")
                lines.append(f"- 中性: {h.neutral_scenario or '(暂无)'}")
                lines.append(f"- 弱势: {h.weak_scenario or '(暂无)'}")
                sl = f"{h.stop_loss:.4f}" if h.stop_loss else "N/A"
                tp1 = f"{h.take_profit_1:.4f}" if h.take_profit_1 else "N/A"
                tp2 = f"{h.take_profit_2:.4f}" if h.take_profit_2 else "N/A"
                lines.append(f"- 止损: {sl} | 一止: {tp1} | 二止: {tp2}")
                if h.action_conditions:
                    lines.append("- 触发条件:")
                    for cond in h.action_conditions:
                        lines.append(f"  - {cond}")
                lines.append("")

        if self.candidates:
            lines.append("## 候选标的")
            for c in self.candidates:
                lines.append(
                    f"### {c.code} {c.name or ''} (评分: {c.technical_score:.1f})"
                )
                lines.append(f"- 集合竞价: {c.auction_condition or '(暂无)'}")
                lines.append(f"- 盘中触发: {c.intraday_trigger or '(暂无)'}")
                lines.append(f"- 建议仓位: {c.position_ratio:.1%}")
                sl = f"{c.stop_loss:.4f}" if c.stop_loss else "N/A"
                tp1 = f"{c.take_profit_1:.4f}" if c.take_profit_1 else "N/A"
                tp2 = f"{c.take_profit_2:.4f}" if c.take_profit_2 else "N/A"
                lines.append(f"- 三线: SL={sl} TP1={tp1} TP2={tp2}")
                lines.append("")

        if not self.holdings_plans and not self.candidates:
            lines.append("## 持仓与候选")
            lines.append("(无持仓且无候选标的)")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class BattlePlanGenerator:
    """Generates daily battle plans combining AI review with technical analysis.

    The generator is intentionally tolerant: if the PM agent is unavailable
    (LLM down, no API key, etc.) it falls back to rule-based scenarios and
    still produces a usable plan with SL/TP lines computed by
    :class:`SLTPCalculator`.
    """

    def __init__(
        self,
        pm_agent: Optional[Any] = None,
        sltp_calculator: Optional[Any] = None,
        data_provider: Optional[Any] = None,
        db_manager: Optional[DatabaseManager] = None,
        account_manager: Optional[Any] = None,
        position_manager: Optional[Any] = None,
        config: Optional[Any] = None,
        max_candidates: int = 5,
    ):
        """Initialize the generator.

        Args:
            pm_agent: Portfolio Manager agent for market review + scenarios.
                If None, market review is rule-based.
            sltp_calculator: SLTPCalculator instance for three-line computation.
                If None, candidate plans omit SL/TP lines.
            data_provider: Object with ``get_daily_data(code, days=...)``
                method (e.g., ``DataFetcherManager``). May return either a
                bare DataFrame or a ``(df, source)`` tuple.
            db_manager: DatabaseManager for persistence.
            account_manager: PaperAccountManager for account snapshots.
            position_manager: PositionManager for listing holdings.
            config: Application config (used for watched_codes fallback).
            max_candidates: Max number of candidate plans to include.
        """
        self.pm_agent = pm_agent
        self.sltp_calculator = sltp_calculator
        self.data_provider = data_provider
        self.db = db_manager or get_db()
        self.account_mgr = account_manager
        self.position_mgr = position_manager
        self.config = config
        self.max_candidates = int(max_candidates)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(
        self,
        account_id: int,
        target_date: Optional[date] = None,
        watched_codes: Optional[List[str]] = None,
    ) -> BattlePlan:
        """Generate (and persist) the battle plan for ``target_date``.

        Args:
            account_id: Paper trading account the plan applies to.
            target_date: The trading day the plan covers. Defaults to today.
            watched_codes: Candidate codes to evaluate. If None, falls back
                to ``config.stock_list`` and current holdings' codes.

        Returns:
            The persisted :class:`BattlePlan`.
        """
        target_date = target_date or date.today()

        # Resolve watched codes.
        if watched_codes is None:
            watched_codes = self._default_watched_codes(account_id)

        # Account snapshot for cash context.
        cash_available = self._fetch_cash(account_id)

        # Existing holdings.
        holdings_dicts = self._fetch_positions(account_id)
        held_codes = {h["code"] for h in holdings_dicts}

        # Build holding plans.
        holdings_plans: List[HoldingPlan] = []
        for pos in holdings_dicts:
            df = self._fetch_daily_df(pos["code"])
            plan = self._generate_holding_plan(pos, df)
            holdings_plans.append(plan)

        # Build candidate plans for non-held watched codes.
        candidate_codes = [c for c in watched_codes if c not in held_codes]
        candidate_plans: List[CandidatePlan] = []
        for code in candidate_codes[: self.max_candidates * 2]:
            df = self._fetch_daily_df(code)
            if df is None or df.empty:
                continue
            plan = self._generate_candidate_plan(
                code=code,
                df=df,
                cash_available=cash_available,
            )
            candidate_plans.append(plan)

        # Rank candidates by technical_score, keep top N.
        candidate_plans.sort(key=lambda p: p.technical_score, reverse=True)
        candidate_plans = candidate_plans[: self.max_candidates]

        # Market review (AI when available, rule-based fallback).
        market_review, sentiment, theme, used_fallback = self._generate_market_review(
            account_id=account_id,
            target_date=target_date,
            holdings=holdings_plans,
            candidates=candidate_plans,
        )

        plan = BattlePlan(
            account_id=account_id,
            date=target_date,
            holdings_plans=holdings_plans,
            candidates=candidate_plans,
            market_review=market_review,
            sentiment_score=sentiment,
            main_theme=theme,
            used_fallback=used_fallback,
        )

        plan_id = self._persist_plan(plan)
        plan.plan_id = plan_id
        logger.info(
            "[BattlePlanGenerator] generated plan_id=%s for account=%s date=%s "
            "(holdings=%d candidates=%d fallback=%s)",
            plan_id, account_id, target_date,
            len(holdings_plans), len(candidate_plans), used_fallback,
        )
        return plan

    # ------------------------------------------------------------------
    # Sub-plan generators
    # ------------------------------------------------------------------

    def _generate_holding_plan(
        self,
        position: Dict[str, Any],
        df: Optional[pd.DataFrame],
    ) -> HoldingPlan:
        """Build a three-scenario plan for an existing holding.

        Uses the existing position's SL/TP fields when set; otherwise
        attempts to recompute via :class:`SLTPCalculator`.
        """
        code = position.get("code", "")
        name = position.get("name") or ""
        current_price = float(position.get("last_price") or position.get("avg_cost") or 0.0)

        # Prefer the SL/TP already on the position (strategy/agent set).
        stop_loss = position.get("stop_loss")
        take_profit_1 = position.get("take_profit")
        take_profit_2 = position.get("take_profit_2")

        # If any of SL/TP1/TP2 is missing and we have a calculator + df,
        # recompute the three lines from current price.
        if (
            (stop_loss is None or take_profit_1 is None or take_profit_2 is None)
            and self.sltp_calculator is not None
            and current_price > 0
            and df is not None
            and not df.empty
        ):
            try:
                result = self.sltp_calculator.compute(
                    code=code,
                    entry_price=current_price,
                    df=df,
                )
                if stop_loss is None:
                    stop_loss = result.stop_loss
                if take_profit_1 is None:
                    take_profit_1 = result.take_profit_1
                if take_profit_2 is None:
                    take_profit_2 = result.take_profit_2
            except Exception as exc:
                logger.warning(
                    "[BattlePlanGenerator] SLTP recompute failed for holding %s: %s",
                    code, exc,
                )

        # Three-scenario response (rule-based defaults, AI may override).
        pnl_pct = float(position.get("floating_pnl_pct") or 0.0)
        strong, neutral, weak, conditions = self._holding_scenarios(
            code=code, current_price=current_price, pnl_pct=pnl_pct,
            stop_loss=stop_loss, take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
        )

        return HoldingPlan(
            code=code,
            name=name,
            current_price=current_price,
            strong_scenario=strong,
            neutral_scenario=neutral,
            weak_scenario=weak,
            action_conditions=conditions,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
        )

    def _generate_candidate_plan(
        self,
        code: str,
        df: pd.DataFrame,
        cash_available: float,
    ) -> CandidatePlan:
        """Build a candidate-buy plan for a non-held stock."""
        name = ""
        current_price = 0.0
        if df is not None and not df.empty:
            current_price = float(df["close"].iloc[-1])

        # SL/TP via calculator.
        stop_loss: Optional[float] = None
        tp1: Optional[float] = None
        tp2: Optional[float] = None
        if self.sltp_calculator is not None and current_price > 0:
            try:
                result = self.sltp_calculator.compute(
                    code=code, entry_price=current_price, df=df,
                )
                stop_loss = result.stop_loss
                tp1 = result.take_profit_1
                tp2 = result.take_profit_2
            except Exception as exc:
                logger.warning(
                    "[BattlePlanGenerator] SLTP compute failed for candidate %s: %s",
                    code, exc,
                )

        # Technical score (rule-based: combine trend + ATR-relative strength).
        score = self._compute_technical_score(df)

        # Suggested position ratio: scale down if cash is thin.
        # Default 20% of cash; cap at 30% (risk checker enforces 50% max).
        if cash_available <= 0:
            ratio = 0.0
        elif score >= 70:
            ratio = 0.30
        elif score >= 50:
            ratio = 0.20
        else:
            ratio = 0.10

        # Rule-based auction / intraday triggers.
        auction, intraday = self._candidate_triggers(
            code=code, current_price=current_price,
            stop_loss=stop_loss, take_profit_1=tp1, score=score,
        )

        return CandidatePlan(
            code=code,
            name=name,
            auction_condition=auction,
            intraday_trigger=intraday,
            position_ratio=ratio,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            technical_score=score,
        )

    def _generate_market_review(
        self,
        account_id: int,
        target_date: date,
        holdings: List[HoldingPlan],
        candidates: List[CandidatePlan],
    ) -> Tuple[str, int, str, bool]:
        """Generate the AI market review paragraph.

        Returns:
            (review_text, sentiment_score 0-100, main_theme, used_fallback)
        """
        # Try PM agent first.
        if self.pm_agent is not None:
            try:
                review, sentiment, theme = self._call_pm_for_review(
                    account_id=account_id,
                    target_date=target_date,
                    holdings=holdings,
                    candidates=candidates,
                )
                if review:
                    return review, sentiment, theme, False
            except Exception as exc:
                logger.warning(
                    "[BattlePlanGenerator] PM market review failed, using fallback: %s",
                    exc,
                )

        # Fallback: rule-based summary.
        review = self._fallback_market_review(holdings, candidates)
        sentiment = self._fallback_sentiment_score(holdings, candidates)
        theme = self._fallback_main_theme(holdings, candidates)
        return review, sentiment, theme, True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_plan(self, plan: BattlePlan) -> int:
        """Upsert the plan into PaperBattlePlan. Returns the row id."""
        holdings_json = json.dumps(
            [h.to_dict() for h in plan.holdings_plans], ensure_ascii=False,
        )
        candidates_json = json.dumps(
            [c.to_dict() for c in plan.candidates], ensure_ascii=False,
        )
        with self.db.session_scope() as session:
            existing = session.execute(
                select(PaperBattlePlan).where(
                    PaperBattlePlan.account_id == plan.account_id,
                    PaperBattlePlan.date == plan.date,
                )
            ).scalar_one_or_none()

            if existing is not None:
                # Update in place — keep the original id and created_at.
                existing.holdings_plans_json = holdings_json
                existing.candidates_json = candidates_json
                existing.market_review = plan.market_review
                existing.sentiment_score = plan.sentiment_score
                existing.main_theme = plan.main_theme
                existing.used_fallback = plan.used_fallback
                session.flush()
                plan_id = int(existing.id)
                plan.created_at = existing.created_at
            else:
                row = PaperBattlePlan(
                    account_id=plan.account_id,
                    date=plan.date,
                    holdings_plans_json=holdings_json,
                    candidates_json=candidates_json,
                    market_review=plan.market_review,
                    sentiment_score=plan.sentiment_score,
                    main_theme=plan.main_theme,
                    used_fallback=plan.used_fallback,
                )
                session.add(row)
                session.flush()
                plan_id = int(row.id)
                plan.created_at = row.created_at
        return plan_id

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_plan(
        self,
        account_id: int,
        target_date: date,
    ) -> Optional[BattlePlan]:
        """Load a persisted plan by (account_id, date)."""
        with self.db.session_scope() as session:
            row = session.execute(
                select(PaperBattlePlan).where(
                    PaperBattlePlan.account_id == account_id,
                    PaperBattlePlan.date == target_date,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_plan(row)

    def list_recent_plans(
        self,
        account_id: int,
        limit: int = 10,
    ) -> List[BattlePlan]:
        """Return the most recent N plans for an account (newest first)."""
        with self.db.session_scope() as session:
            rows = session.execute(
                select(PaperBattlePlan)
                .where(PaperBattlePlan.account_id == account_id)
                .order_by(desc(PaperBattlePlan.date))
                .limit(int(limit))
            ).scalars().all()
            return [self._row_to_plan(r) for r in rows]

    @staticmethod
    def _row_to_plan(row: PaperBattlePlan) -> BattlePlan:
        try:
            holdings_raw = json.loads(row.holdings_plans_json or "[]")
        except (ValueError, TypeError):
            holdings_raw = []
        try:
            candidates_raw = json.loads(row.candidates_json or "[]")
        except (ValueError, TypeError):
            candidates_raw = []

        holdings = [HoldingPlan(**h) for h in holdings_raw if isinstance(h, dict)]
        candidates = [CandidatePlan(**c) for c in candidates_raw if isinstance(c, dict)]
        return BattlePlan(
            plan_id=int(row.id),
            account_id=int(row.account_id),
            date=row.date,
            holdings_plans=holdings,
            candidates=candidates,
            market_review=row.market_review or "",
            sentiment_score=int(row.sentiment_score or 50),
            main_theme=row.main_theme or "",
            used_fallback=bool(row.used_fallback),
            created_at=row.created_at,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _default_watched_codes(self, account_id: int) -> List[str]:
        """Default candidate list: config.stock_list + current holdings."""
        codes: List[str] = []
        if self.config is not None:
            cfg_codes = getattr(self.config, "stock_list", None) or []
            codes.extend([c for c in cfg_codes if c not in codes])
        # Append holdings' codes too so they get a holding-plan entry.
        for pos in self._fetch_positions(account_id):
            c = pos.get("code")
            if c and c not in codes:
                codes.append(c)
        return codes

    def _fetch_cash(self, account_id: int) -> float:
        try:
            if self.account_mgr is not None:
                snap = self.account_mgr.snapshot(account_id)
                return float(snap.cash)
        except Exception as exc:
            logger.debug("[BattlePlanGenerator] cash fetch failed: %s", exc)
        return 0.0

    def _fetch_positions(self, account_id: int) -> List[Dict[str, Any]]:
        try:
            if self.position_mgr is not None:
                return self.position_mgr.list_positions(account_id)
        except Exception as exc:
            logger.debug("[BattlePlanGenerator] positions fetch failed: %s", exc)
        return []

    def _fetch_daily_df(self, code: str) -> Optional[pd.DataFrame]:
        """Fetch a daily-bar DataFrame via the data provider.

        Handles both return shapes: bare DataFrame or ``(df, source)`` tuple.
        """
        if self.data_provider is None:
            return None
        try:
            result = self.data_provider.get_daily_data(code, days=120)
        except Exception as exc:
            logger.debug(
                "[BattlePlanGenerator] get_daily_data failed for %s: %s", code, exc,
            )
            return None
        if result is None:
            return None
        # Unpack tuple if needed.
        if isinstance(result, tuple) and len(result) >= 1:
            df = result[0]
        else:
            df = result
        if df is None or getattr(df, "empty", True):
            return None
        # Standardize index.
        if not isinstance(df.index, pd.DatetimeIndex):
            if "date" in df.columns:
                df = df.set_index("date")
            else:
                try:
                    df.index = pd.to_datetime(df.index)
                except Exception:
                    return None
        df = df.sort_index()
        return df

    # ------------------------------------------------------------------
    # Rule-based scenario / trigger helpers
    # ------------------------------------------------------------------

    def _holding_scenarios(
        self,
        code: str,
        current_price: float,
        pnl_pct: float,
        stop_loss: Optional[float],
        take_profit_1: Optional[float],
        take_profit_2: Optional[float],
    ) -> Tuple[str, str, str, List[str]]:
        """Rule-based three-scenario response for a holding.

        Returns (strong, neutral, weak, action_conditions).
        """
        in_profit = pnl_pct >= 0
        sl_str = f"{stop_loss:.4f}" if stop_loss else "未设置"
        tp1_str = f"{take_profit_1:.4f}" if take_profit_1 else "未设置"
        tp2_str = f"{take_profit_2:.4f}" if take_profit_2 else "未设置"

        if in_profit:
            strong = (
                f"高开且站稳 {current_price:.4f} 上方,可持有待涨,触及 {tp2_str} 减半仓,"
                f"突破后上调止损至成本价"
            )
            neutral = (
                f"平开震荡则按计划在 {tp1_str} 减仓 1/3,止损守住 {sl_str}"
            )
            weak = (
                f"低开跌破 {current_price:.4f} 且接近 {sl_str},立即减仓 1/2 防守"
            )
        else:
            strong = (
                f"反弹至成本价附近减仓 1/3 止损,避免情绪化加仓"
            )
            neutral = (
                f"震荡持有,严守止损 {sl_str},不补仓"
            )
            weak = (
                f"跌破 {sl_str} 立即清仓,不留情面"
            )

        conditions: List[str] = []
        if stop_loss:
            conditions.append(f"价格触及 {sl_str} -> 自动止损")
        if take_profit_1:
            conditions.append(f"价格触及 {tp1_str} -> 减仓 1/3")
        if take_profit_2:
            conditions.append(f"价格触及 {tp2_str} -> 清仓")
        return strong, neutral, weak, conditions

    def _candidate_triggers(
        self,
        code: str,
        current_price: float,
        stop_loss: Optional[float],
        take_profit_1: Optional[float],
        score: float,
    ) -> Tuple[str, str]:
        """Rule-based auction + intraday triggers for a candidate buy."""
        # Auction: small discount to current price for entry.
        if current_price > 0:
            auction_price = round(current_price * 0.995, 2)
            auction = (
                f"集合竞价低开至 {auction_price:.4f} 附近(现价 {current_price:.4f} 的 99.5%),"
                f"且量比 > 1.0 时挂限价单"
            )
        else:
            auction = "等待开盘后首根 5 分钟 K 线确认方向"

        if score >= 70:
            intraday = (
                f"盘中突破今日高点且量能放大,回调不破止损 {stop_loss} 时加仓 1/3"
                if stop_loss
                else "盘中突破今日高点且量能放大时建仓 1/2"
            )
        elif score >= 50:
            intraday = (
                f"盘中站稳 {current_price:.4f} 上方且量比 > 1.2 时建仓 1/2,"
                f"触及 {take_profit_1} 减半" if take_profit_1
                else f"盘中站稳 {current_price:.4f} 上方且量比 > 1.2 时建仓 1/2"
            )
        else:
            intraday = (
                "信号偏弱,仅做观察;若三日不破前低且量能回升再介入"
            )
        return auction, intraday

    def _compute_technical_score(self, df: Optional[pd.DataFrame]) -> float:
        """Rule-based 0-100 score for a candidate.

        Combines: trend (MA5 vs MA20), momentum (recent return), and
        volatility (ATR-relative). Returns 50.0 when data is insufficient.
        """
        if df is None or df.empty or len(df) < 20:
            return 50.0
        try:
            close = df["close"]
            ma5 = close.rolling(5).mean().iloc[-1]
            ma20 = close.rolling(20).mean().iloc[-1]
            ret_5d = (close.iloc[-1] / close.iloc[-6] - 1.0) * 100.0 if len(close) >= 6 else 0.0

            score = 50.0
            if ma5 > ma20:
                score += 15.0
            else:
                score -= 10.0
            if ret_5d > 2.0:
                score += 15.0
            elif ret_5d < -2.0:
                score -= 15.0
            else:
                score += 5.0

            # Volatility sanity: ATR / close ratio in [0.5%, 5%] is healthy.
            if {"high", "low"}.issubset(df.columns):
                tr = (df["high"] - df["low"]).tail(14)
                atr = tr.mean()
                if atr > 0 and close.iloc[-1] > 0:
                    atr_pct = atr / close.iloc[-1]
                    if 0.005 <= atr_pct <= 0.05:
                        score += 10.0
                    elif atr_pct > 0.08:
                        score -= 10.0  # too volatile

            return max(0.0, min(100.0, float(score)))
        except Exception as exc:
            logger.debug("[BattlePlanGenerator] technical score failed: %s", exc)
            return 50.0

    # ------------------------------------------------------------------
    # PM agent market review
    # ------------------------------------------------------------------

    def _call_pm_for_review(
        self,
        account_id: int,
        target_date: date,
        holdings: List[HoldingPlan],
        candidates: List[CandidatePlan],
    ) -> Tuple[str, int, str]:
        """Call the PM agent to produce a market review paragraph.

        Returns (review_text, sentiment_score, main_theme). Raises on
        unrecoverable failure so the caller can fall back.
        """
        # We piggyback on PMDecision via extra_context — the agent's prompt
        # already includes account/positions/reflections context. The
        # extra_context tells it to emit a "plan" action with a market
        # review JSON payload.
        extra_context = {
            "task": "generate_battle_plan_review",
            "target_date": target_date.isoformat(),
            "holdings_summary": [
                {"code": h.code, "name": h.name, "current_price": h.current_price}
                for h in holdings
            ],
            "candidates_summary": [
                {"code": c.code, "name": c.name, "score": c.technical_score}
                for c in candidates
            ],
            "output_format": (
                "Return JSON: {\"market_review\": str, \"sentiment_score\": int 0-100, "
                "\"main_theme\": str}"
            ),
        }
        decision = self.pm_agent.make_decision(
            account_id=account_id, extra_context=extra_context,
        )
        # Parse the response payload.
        params = decision.params or {}
        review = str(params.get("market_review") or "").strip()
        sentiment_raw = params.get("sentiment_score", 50)
        try:
            sentiment = int(sentiment_raw)
        except (ValueError, TypeError):
            sentiment = 50
        sentiment = max(0, min(100, sentiment))
        theme = str(params.get("main_theme") or "").strip()

        # If the agent didn't fill the structured fields, treat as failure
        # so the caller falls back to rule-based.
        if not review and not theme:
            raise RuntimeError("PM agent returned empty market review")
        return review, sentiment, theme

    def _fallback_market_review(
        self,
        holdings: List[HoldingPlan],
        candidates: List[CandidatePlan],
    ) -> str:
        parts: List[str] = []
        if holdings:
            parts.append(
                f"当前持有 {len(holdings)} 只标的:"
                + ",".join(f"{h.code}({h.name or ''})" for h in holdings)
                + "。"
            )
        else:
            parts.append("当前无持仓。")
        if candidates:
            parts.append(
                f"候选观察 {len(candidates)} 只:"
                + ",".join(
                    f"{c.code}(评分{c.technical_score:.0f})" for c in candidates[:3]
                )
                + "。"
            )
        parts.append(
            "AI 市场综述暂不可用,以上为规则摘要;建议结合大盘指数与板块轮动进一步研判。"
        )
        return "".join(parts)

    def _fallback_sentiment_score(
        self,
        holdings: List[HoldingPlan],
        candidates: List[CandidatePlan],
    ) -> int:
        # Default neutral; nudge up if many high-score candidates, down if
        # many holdings in loss.
        if not holdings and not candidates:
            return 50
        score = 50
        if candidates:
            avg_score = sum(c.technical_score for c in candidates) / len(candidates)
            score += int((avg_score - 50) * 0.2)
        return max(0, min(100, score))

    def _fallback_main_theme(
        self,
        holdings: List[HoldingPlan],
        candidates: List[CandidatePlan],
    ) -> str:
        if not candidates:
            return "防守为主,等待信号"
        top = sorted(candidates, key=lambda c: c.technical_score, reverse=True)[:3]
        names = ",".join(f"{c.code}({c.name or ''})" for c in top)
        return f"关注 {names} 的低吸机会"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_battle_plan_generator(
    config: Optional[Any] = None,
    account_id: int = 0,
    pm_agent: Optional[Any] = None,
    sltp_calculator: Optional[Any] = None,
    data_provider: Optional[Any] = None,
    trading_engine: Optional[Any] = None,
    account_manager: Optional[Any] = None,
    position_manager: Optional[Any] = None,
    max_candidates: Optional[int] = None,
) -> BattlePlanGenerator:
    """Build a BattlePlanGenerator wired to project defaults.

    Args:
        config: Application config. If None, ``get_config()`` is called.
        account_id: Default account id (used only for context; the actual
            account is passed to :meth:`generate`).
        pm_agent: Pre-built PortfolioManagerAgent. If None, built lazily
            via ``build_portfolio_manager_agent`` only if config enables it.
        sltp_calculator: Pre-built SLTPCalculator. If None, a default
            instance is built when ``data_provider`` is available.
        data_provider: Data provider for daily-bar fetches.
        trading_engine: TradingEngine (used to derive account/position
            managers when those aren't supplied directly).
        account_manager: Optional override.
        position_manager: Optional override.
        max_candidates: Override the default candidate count (5).

    Returns:
        A configured :class:`BattlePlanGenerator`.
    """
    if config is None:
        try:
            from src.config import get_config
            config = get_config()
        except Exception:
            config = None

    # Account / position managers.
    if account_manager is None and trading_engine is not None:
        account_manager = getattr(trading_engine, "account_mgr", None)
    if position_manager is None and trading_engine is not None:
        position_manager = getattr(trading_engine, "position_mgr", None)

    # SLTP calculator: build a default if data_provider is available.
    if sltp_calculator is None and data_provider is not None:
        try:
            from paper_trading.sltp_calculator import build_sltp_calculator
            sltp_calculator = build_sltp_calculator(data_provider=data_provider)
        except Exception as exc:
            logger.warning(
                "[BattlePlanGenerator] SLTPCalculator build failed: %s", exc,
            )

    # PM agent: only build when config enables it (cost control).
    if pm_agent is None and config is not None and trading_engine is not None:
        enable_pm = bool(
            getattr(config, "paper_trading_enable_pm_agent", False)
            or getattr(config, "paper_trading_enable_battle_plan_ai", False)
        )
        if enable_pm:
            try:
                from src.agent.portfolio_manager_agent import (
                    build_portfolio_manager_agent,
                )
                pm_agent = build_portfolio_manager_agent(
                    config=config,
                    account_id=account_id,
                    trading_engine=trading_engine,
                )
            except Exception as exc:
                logger.warning(
                    "[BattlePlanGenerator] PM agent build failed, using fallback: %s",
                    exc,
                )

    return BattlePlanGenerator(
        pm_agent=pm_agent,
        sltp_calculator=sltp_calculator,
        data_provider=data_provider,
        account_manager=account_manager,
        position_manager=position_manager,
        config=config,
        max_candidates=int(max_candidates or 5),
    )
