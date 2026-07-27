# -*- coding: utf-8 -*-
"""AI reflection / post-trade review system (P0-D).

The reflection system turns experience into memory:
- After every trade fill (``reflect_on_trade``) the engine asks the LLM
  to review the decision quality, execution, and what could be improved.
- After every trading day (``reflect_on_daily``) the engine produces a
  higher-level recap of the day's PnL, position management, and market
  read.
- Notes are persisted to ``PaperReflection`` (immutable append-only) and
  later surfaced to the PM agent via ``get_recent_notes()`` so past lessons
  influence future decisions (P0-E memory loop).

Design mirrors :class:`paper_trading.agent_risk.AgentRiskReviewer`:
- Lazy executor construction via ``build_agent_executor()``.
- Hard timeout enforced by a daemon worker thread.
- Lenient JSON parsing with strict -> json_repair -> keyword fallback.
- ``fallback_note`` ensures the loop is never blocked by agent failure.

Public API:
    >>> engine = ReflectionEngine(trading_engine=eng, account_id=1)
    >>> note = engine.reflect_on_trade(trade_id=42)
    >>> notes = engine.get_recent_notes(limit=5)
    >>> context_str = engine.format_notes_for_context(notes)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from json_repair import repair_json
from sqlalchemy import desc, select

from src.storage import (
    DatabaseManager,
    PaperOrder,
    PaperPosition,
    PaperReflection,
    PaperTrade,
    get_db,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

REFLECTION_SYSTEM_PROMPT = """你是模拟交易系统的复盘反思 Agent,负责对每笔交易和每日操作进行结构化复盘。

## 复盘原则

1. **诚实**: 不粉饰亏损,不夸大盈利,客观陈述事实。
2. **可执行**: 复盘结论必须可指导下一次决策(避免"下次注意"这类空话)。
3. **结构化**: 严格按 JSON 输出,字段分明。
4. **聚焦**: 每次复盘只提炼 1 条最重要的 takeaway 和 2-4 条 lessons。

## 输出格式(严格 JSON,不要附加任何解释文字)

{
  "subject": "复盘标题(20字以内)",
  "summary": "1-2 句话概括这次交易/今日表现(发生了什么,结果如何)",
  "takeaway": "最重要的一条教训(可执行,不要空话)",
  "lessons": [
    "教训1: ...",
    "教训2: ..."
  ],
  "tags": "关键词1,关键词2,关键词3",
  "mood": "good | bad | neutral"
}

mood 取值:
- good: 盈利且决策逻辑正确
- bad: 亏损或决策逻辑错误
- neutral: 持平或决策逻辑有瑕疵但可接受
"""


TRADE_REFLECTION_PROMPT_TEMPLATE = """## 交易复盘请求

请对以下已成交的交易进行复盘:

- 账户 ID: {account_id}
- 交易 ID: {trade_id}
- 股票代码: {code}
- 股票名称: {name}
- 方向: {side}
- 成交价: {fill_price:.4f}
- 成交量: {fill_quantity:.0f}
- 成交金额: {fill_amount:.2f}
- 手续费: {fee:.2f}
- 成交时间: {traded_at}

## 决策背景(由 PM Agent 或策略引擎提供)

{decision_context}

## 当前持仓(成交后)

{positions_summary}

## 任务

请基于以上信息复盘这笔交易,评估:
1. 进场时机是否合理(是否追高/抄底过早)
2. 仓位控制是否得当(是否过度集中或过轻)
3. 风险收益比是否合理(止盈止损位是否设置)
4. 是否符合既定策略规则

输出严格 JSON。
"""


DAILY_REFLECTION_PROMPT_TEMPLATE = """## 每日复盘请求

请对今日的交易操作和账户表现进行复盘:

- 账户 ID: {account_id}
- 复盘日期: {review_date}
- 期初资产: {start_assets:.2f}
- 期末资产: {end_assets:.2f}
- 当日盈亏: {daily_pnl:.2f} ({daily_pnl_pct:.2f}%)
- 当日交易笔数: {trade_count}
- 当日决策数: {decision_count}
- 当前持仓数: {position_count}
- 当前现金: {cash:.2f}

## 今日交易明细

{trades_summary}

## 当前持仓

{positions_summary}

## 今日决策日志

{decisions_summary}

## 任务
"""


# ---------------------------------------------------------------------------
# ReflectionNote model (dataclass-like for persistence)
# ---------------------------------------------------------------------------

class ReflectionNote:
    """Structured reflection note returned by the reflection engine."""

    scope: str  # trade / daily / weekly / adhoc
    subject: str = ""
    summary: str = ""
    takeaway: str = ""
    lessons: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    mood: str = "neutral"  # good / bad / neutral
    raw_response: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    used_fallback: bool = False
    # Optional related entity IDs.
    account_id: Optional[int] = None
    trade_id: Optional[int] = None
    order_id: Optional[int] = None
    signal_id: Optional[int] = None
    code: Optional[str] = None
    # Row id after persistence (set by _persist_note).
    row_id: Optional[int] = None
    # Timestamp the note was created (set on persistence / row_to_note).
    created_at: Optional[datetime] = None
    # P0-C: Agent action from verdict (e.g., cancel, sell, modify, hold, approve).
    agent_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "subject": self.subject,
            "summary": self.summary,
            "takeaway": self.takeaway,
            "lessons": self.lessons,
            "tags": self.tags,
            "mood": self.mood,
            "raw_response": self.raw_response,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
            "used_fallback": self.used_fallback,
            "account_id": self.account_id,
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "signal_id": self.signal_id,
            "code": self.code,
            "row_id": self.row_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "agent_action": self.agent_action,
        }

    def to_markdown(self) -> str:
        """Render the note as a Markdown block for display / push (P0-D gap fill).

        Format:
            ### 🧠 基金经理笔记
            **[scope]** subject  *(mood)*

            > summary

            **核心教训**: takeaway

            **lessons**:
            - lesson 1
            - lesson 2

            *tags: tag1, tag2*
        """
        mood_emoji = {"good": "✅", "bad": "❌", "neutral": "➖"}.get(
            self.mood, "➖"
        )
        lines = [f"### 🧠 基金经理笔记"]
        header_parts = [f"**[{self.scope}]**"]
        if self.subject:
            header_parts.append(self.subject)
        header_parts.append(f"*({mood_emoji} {self.mood})*")
        lines.append(" ".join(header_parts))
        lines.append("")

        if self.summary:
            lines.append(f"> {self.summary}")
            lines.append("")

        if self.takeaway:
            lines.append(f"**核心教训**: {self.takeaway}")
            lines.append("")

        if self.lessons:
            lines.append("**经验教训**:")
            for lesson in self.lessons:
                lines.append(f"- {lesson}")
            lines.append("")

        if self.tags:
            lines.append(f"*tags: {', '.join(self.tags)}*")

        if self.code:
            lines.append(f"\n*股票: {self.code}*")

        if self.agent_action:
            lines.append(f"\n**代理动作**: {self.agent_action}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reflection Engine
# ---------------------------------------------------------------------------

class ReflectionEngine:
    """Turn trade fills and daily summaries into structured reflection notes.

    The engine is intentionally synchronous: callers (TradingEngine hooks,
    MarketListener's daily settle) invoke it once per event and wait for the
    note. A daemon-thread timeout prevents agent unavailability from blocking
    the trading loop.
    """

    def __init__(
        self,
        config: Optional[Any] = None,
        executor: Optional[Any] = None,
        skills: Optional[List[str]] = None,
        trading_engine: Optional[Any] = None,
        account_id: Optional[int] = None,
        timeout_seconds: float = 180.0,
        fallback_on_failure: bool = True,
        max_retries: int = 0,
        db_manager: Optional[DatabaseManager] = None,
    ):
        """Initialize the reflection engine.

        Args:
            config: Application config (for build_agent_executor).
            executor: Pre-built AgentExecutor. If None, built lazily.
            skills: Skill ids to activate.
            trading_engine: TradingEngine for fetching trade/position context.
            account_id: Default account id for reflections.
            timeout_seconds: Hard cap on agent call duration.
            fallback_on_failure: If True, return a minimal note on failure
                instead of raising.
            max_retries: Number of retry attempts on agent failure.
            db_manager: Database manager (defaults to global get_db()).
        """
        self._config = config
        self._executor = executor
        self._skills = skills
        self.trading_engine = trading_engine
        self.account_id = int(account_id) if account_id else 0
        self.timeout_seconds = float(timeout_seconds)
        self.fallback_on_failure = bool(fallback_on_failure)
        self.max_retries = int(max_retries)
        self.db = db_manager or (
            getattr(trading_engine, "db", None) if trading_engine else None
        ) or get_db()

    # ------------------------------------------------------------------
    # Lazy executor
    # ------------------------------------------------------------------

    @property
    def executor(self):
        """Lazily build the AgentExecutor (reuses agent factory)."""
        if self._executor is None:
            from src.agent.factory import build_agent_executor

            self._executor = build_agent_executor(self._config, skills=self._skills)
            logger.info(
                "[ReflectionEngine] Executor built (timeout=%ss)", self.timeout_seconds
            )
        return self._executor

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def reflect_on_trade(
        self,
        trade_id: int,
        account_id: Optional[int] = None,
        decision_context: Optional[str] = None,
        verdict_action: Optional[str] = None,
    ) -> ReflectionNote:
        """Reflect on a completed trade.

        Args:
            trade_id: PaperTrade.id to reflect on.
            account_id: Override default account_id.
            decision_context: Free-text context (e.g., the PM agent's reason,
                the rule that triggered the signal).
            verdict_action: Optional agent action from AgentReviewResult
                (e.g., "cancel", "sell", "modify", "hold", "approve").

        Returns:
            ReflectionNote with scope='trade'.
        """
        acct_id = int(account_id) if account_id else self.account_id
        trade = self._fetch_trade(trade_id)
        if trade is None:
            note = ReflectionNote(
                scope="trade",
                subject=f"trade {trade_id} not found",
                summary=f"PaperTrade id={trade_id} not found",
                takeaway="no reflection possible",
                mood="neutral",
                used_fallback=True,
                error="trade_not_found",
                account_id=acct_id,
                trade_id=trade_id,
            )
            # Include verdict_action if provided
            if verdict_action:
                note.agent_action = verdict_action
            return note

        # Incorporate verdict_action into decision_context if available
        if verdict_action:
            extra_info = f"\n[P0-C] Agent action: {verdict_action}"
            decision_context = (decision_context or "") + extra_info

        prompt = self._build_trade_reflection_prompt(
            trade=trade,
            account_id=acct_id,
            decision_context=decision_context or "(无决策背景)",
        )
        note = self._run_reflection(
            prompt=prompt,
            scope="trade",
            account_id=acct_id,
            trade_id=trade_id,
            order_id=getattr(trade, "order_id", None),
            code=getattr(trade, "code", None),
        )
        # Persist verdict_action if passed separately
        if verdict_action:
            note.agent_action = verdict_action
            # Re-persist to include agent_action in DB row
            self._persist_note_with_action(note, verdict_action)
        return note

    def reflect_on_daily(
        self,
        account_id: Optional[int] = None,
        review_date: Optional[Any] = None,
    ) -> ReflectionNote:
        """Reflect on the day's overall performance.

        Args:
            account_id: Override default account_id.
            review_date: date object for the review (defaults to today).

        Returns:
            ReflectionNote with scope='daily'.
        """
        from datetime import date as date_cls

        acct_id = int(account_id) if account_id else self.account_id
        rev_date = review_date or date_cls.today()
        prompt = self._build_daily_reflection_prompt(
            account_id=acct_id,
            review_date=rev_date,
        )
        note = self._run_reflection(
            prompt=prompt,
            scope="daily",
            account_id=acct_id,
        )
        return note

    # ------------------------------------------------------------------
    # Markdown persistence (P3-C)
    # ------------------------------------------------------------------

    def save_reflection_markdown(
        self, note, output_dir: Optional[Path] = None
    ) -> Optional[Path]:
        """Save a reflection note as a markdown file.

        Args:
            note: ReflectionNote object with to_markdown() method.
            output_dir: Directory to save to. Defaults to data/paper_trading/reports/.

        Returns:
            Path to the saved file, or None on failure.
        """
        from pathlib import Path
        try:
            md = note.to_markdown() if hasattr(note, "to_markdown") else str(note)
            out = Path(output_dir) if output_dir else Path("data/paper_trading/reports")
            out.mkdir(parents=True, exist_ok=True)
            created = getattr(note, "created_at", None)
            note_date = created.strftime("%Y-%m-%d") if created else "unknown"
            filename = f"reflection_{note_date}.md"
            filepath = out / filename
            filepath.write_text(md, encoding="utf-8")
            logger.info("Reflection markdown saved: %s", filepath)
            return filepath
        except Exception as exc:
            logger.warning("Failed to save reflection markdown: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Memory retrieval (P0-E integration point)
    # ------------------------------------------------------------------

    def get_recent_notes(
        self,
        limit: int = 5,
        scope: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> List[ReflectionNote]:
        """Fetch recent reflection notes (newest first).

        Args:
            limit: Max number of notes.
            scope: Filter by scope (trade / daily / weekly / adhoc).
            account_id: Filter by account. If None, uses engine default.

        Returns:
            List of detached ReflectionNote snapshots (empty if none or on error).
            Detached snapshots are used so callers can safely access attributes
            after the DB session has closed (avoids DetachedInstanceError).
        """
        acct_id = int(account_id) if account_id else self.account_id
        try:
            with self.db.session_scope() as session:
                stmt = select(PaperReflection).where(
                    PaperReflection.account_id == acct_id
                )
                if scope:
                    stmt = stmt.where(PaperReflection.scope == scope)
                stmt = stmt.order_by(desc(PaperReflection.created_at)).limit(limit)
                rows = list(session.execute(stmt).scalars().all())
                return [self._row_to_note(r) for r in rows]
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] get_recent_notes failed: %s", exc
            )
            return []

    def get_relevant_notes(
        self,
        code: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
        account_id: Optional[int] = None,
    ) -> List[ReflectionNote]:
        """Fetch notes relevant to a code or set of tags.

        Used by the PM agent to inject memory relevant to the current
        decision (e.g., notes about the same stock, or notes tagged with
        the same patterns like "追高").

        Args:
            code: Filter by stock code (exact match).
            tags: List of tag keywords; notes whose tags column contains
                any of these keywords are returned.
            limit: Max notes.
            account_id: Filter by account. If None, uses engine default.

        Returns:
            List of detached ReflectionNote snapshots.
        """
        acct_id = int(account_id) if account_id else self.account_id
        try:
            with self.db.session_scope() as session:
                stmt = select(PaperReflection).where(
                    PaperReflection.account_id == acct_id
                )
                if code:
                    stmt = stmt.where(PaperReflection.code == code)
                if tags:
                    # Use SQL LIKE for tag matching (comma-separated tags column).
                    from sqlalchemy import or_

                    tag_clauses = []
                    for t in tags:
                        if not t:
                            continue
                        # Match both "tag,..." and "...,tag,..." and "...,tag".
                        tag_clauses.append(PaperReflection.tags.like(f"%{t}%"))
                    if tag_clauses:
                        stmt = stmt.where(or_(*tag_clauses))
                stmt = stmt.order_by(desc(PaperReflection.created_at)).limit(limit)
                rows = list(session.execute(stmt).scalars().all())
                return [self._row_to_note(r) for r in rows]
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] get_relevant_notes failed: %s", exc
            )
            return []

    @staticmethod
    def _row_to_note(row: PaperReflection) -> ReflectionNote:
        """Convert a PaperReflection ORM row to a detached ReflectionNote.

        Must be called while the session is still open (attributes are
        loaded eagerly here). After this returns, the session can be
        closed safely and the snapshot remains usable.
        """
        # Parse lessons_json back into a list.
        lessons: List[str] = []
        if row.lessons_json:
            try:
                parsed = json.loads(row.lessons_json)
                if isinstance(parsed, list):
                    lessons = [str(s) for s in parsed]
            except (TypeError, ValueError):
                lessons = []
        # Parse tags (comma-separated string -> list).
        tags: List[str] = []
        if row.tags:
            tags = [t.strip() for t in str(row.tags).split(",") if t.strip()]
        return ReflectionNote(
            scope=row.scope or "adhoc",
            subject=row.subject or "",
            summary=row.summary or "",
            takeaway=row.takeaway or "",
            lessons=lessons,
            tags=tags,
            mood=row.mood or "neutral",
            account_id=row.account_id,
            trade_id=row.trade_id,
            order_id=row.order_id,
            signal_id=row.signal_id,
            code=row.code,
            row_id=row.id,
            used_fallback=bool(row.used_fallback),
            elapsed_seconds=float(row.elapsed_seconds or 0.0),
            created_at=row.created_at,
            agent_action=getattr(row, "agent_action", None),
        )

    def format_notes_for_context(
        self,
        notes: List[ReflectionNote],
        max_chars: int = 1200,
    ) -> str:
        """Format notes into a compact text block for prompt injection.

        Args:
            notes: List of ReflectionNote snapshots (detached from session).
            max_chars: Soft cap on total length (truncated if exceeded).

        Returns:
            Formatted text block (may be empty if notes is empty).
        """
        if not notes:
            return "(无历史复盘笔记)"
        lines: List[str] = []
        total = 0
        for n in notes:
            ts = n.created_at
            ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "?"
            scope = n.scope or "?"
            takeaway = (n.takeaway or "").strip() or "(无 takeaway)"
            subject = (n.subject or "").strip()
            line = f"- [{ts_str}][{scope}] {subject} → {takeaway}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines) if lines else "(无历史复盘笔记)"

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_trade_reflection_prompt(
        self,
        trade: PaperTrade,
        account_id: int,
        decision_context: str,
    ) -> str:
        """Build the user prompt for a single-trade reflection."""
        positions_summary = self._render_positions_summary(account_id)
        return TRADE_REFLECTION_PROMPT_TEMPLATE.format(
            account_id=account_id,
            trade_id=trade.id,
            code=trade.code or "?",
            name=trade.name or "",
            side=trade.side or "?",
            fill_price=float(trade.price or 0.0),
            fill_quantity=float(trade.quantity or 0.0),
            fill_amount=float(trade.amount or 0.0),
            fee=float(trade.fee or 0.0),
            traded_at=trade.traded_at.isoformat() if trade.traded_at else "?",
            decision_context=decision_context,
            positions_summary=positions_summary,
        )

    def _build_daily_reflection_prompt(
        self,
        account_id: int,
        review_date: Any,
    ) -> str:
        """Build the user prompt for a daily reflection.

        Gathers today's trades, decisions, and current account snapshot.
        Missing data falls to "(无...)" so the prompt still renders.
        """
        snapshot = self._fetch_account_snapshot(account_id)
        trades = self._fetch_today_trades(account_id, review_date)
        decisions = self._fetch_today_decisions(account_id, review_date)
        positions = self._render_positions_summary(account_id)

        start_assets = float(snapshot.get("start_assets", 0.0))
        end_assets = float(snapshot.get("total_assets", 0.0))
        daily_pnl = end_assets - start_assets
        daily_pnl_pct = (
            (daily_pnl / start_assets * 100.0) if start_assets > 0 else 0.0
        )

        trades_summary = (
            "\n".join(
                f"- {t.traded_at.strftime('%H:%M') if t.traded_at else '?'} "
                f"{t.side} {t.code} {float(t.quantity):.0f}@{float(t.price):.4f}"
                for t in trades
            )
            if trades
            else "(今日无成交)"
        )

        decisions_summary = (
            "\n".join(
                f"- [{d.action}] {d.code or ''} conf={float(d.confidence or 0):.2f}: {(d.reason or '')[:80]}"
                for d in decisions
            )
            if decisions
            else "(今日无决策记录)"
        )

        return DAILY_REFLECTION_PROMPT_TEMPLATE.format(
            account_id=account_id,
            review_date=str(review_date),
            start_assets=start_assets,
            end_assets=end_assets,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            trade_count=len(trades),
            decision_count=len(decisions),
            position_count=int(snapshot.get("position_count", 0)),
            cash=float(snapshot.get("cash", 0.0)),
            trades_summary=trades_summary,
            positions_summary=positions,
            decisions_summary=decisions_summary,
        )

    # ------------------------------------------------------------------
    # Agent invocation (with timeout)
    # ------------------------------------------------------------------

    def _call_agent_with_timeout(self, prompt: str, session_id: str) -> str:
        """Call the agent with a hard timeout via a daemon worker thread."""
        result_holder: Dict[str, Any] = {}

        def _worker():
            try:
                agent_result = self.executor.chat(
                    message=prompt, session_id=session_id
                )
                result_holder["content"] = agent_result.content or ""
                result_holder["success"] = agent_result.success
                result_holder["error"] = agent_result.error
            except Exception as exc:
                result_holder["content"] = ""
                result_holder["success"] = False
                result_holder["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(timeout=self.timeout_seconds)

        if thread.is_alive():
            logger.warning(
                "[ReflectionEngine] Agent timed out after %ss, abandoning",
                self.timeout_seconds,
            )
            raise TimeoutError(
                f"Reflection agent exceeded {self.timeout_seconds}s timeout"
            )

        if not result_holder.get("success", False):
            err = result_holder.get("error") or "agent returned failure"
            raise RuntimeError(err)
        return str(result_holder.get("content", ""))

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_reflection(self, raw_text: str, scope: str) -> ReflectionNote:
        """Parse the agent's JSON reflection leniently.

        Fallback chain: strict JSON -> json_repair -> minimal note.
        """
        if not raw_text or not raw_text.strip():
            return ReflectionNote(
                scope=scope,
                subject="empty response",
                summary="agent returned empty response",
                takeaway="no reflection generated",
                mood="neutral",
                raw_response=raw_text,
                used_fallback=True,
            )

        verdict = None
        try:
            verdict = json.loads(raw_text)
        except (TypeError, ValueError):
            try:
                fixed = repair_json(raw_text, return_objects=True)
                if isinstance(fixed, dict):
                    verdict = fixed
                else:
                    verdict = json.loads(fixed)
            except Exception:
                verdict = None

        if isinstance(verdict, dict):
            subject = str(verdict.get("subject") or "")[:255]
            summary = str(verdict.get("summary") or "")[:2000]
            takeaway = str(verdict.get("takeaway") or "")[:1000]
            lessons_raw = verdict.get("lessons") or []
            if not isinstance(lessons_raw, list):
                lessons_raw = [str(lessons_raw)]
            lessons = [str(s)[:300] for s in lessons_raw][:8]
            tags_raw = verdict.get("tags") or ""
            if isinstance(tags_raw, list):
                tags = [str(t).strip() for t in tags_raw if str(t).strip()]
            else:
                tags = [
                    t.strip() for t in str(tags_raw).split(",") if t.strip()
                ]
            tags = tags[:10]
            mood = str(verdict.get("mood") or "neutral").strip().lower()
            if mood not in ("good", "bad", "neutral"):
                mood = "neutral"
            return ReflectionNote(
                scope=scope,
                subject=subject,
                summary=summary,
                takeaway=takeaway,
                lessons=lessons,
                tags=tags,
                mood=mood,
                raw_response=raw_text,
            )

        # Fallback: minimal note.
        return ReflectionNote(
            scope=scope,
            subject="unparseable reflection",
            summary=f"agent response could not be parsed: {raw_text[:200]}",
            takeaway="no structured takeaway extracted",
            mood="neutral",
            raw_response=raw_text,
            used_fallback=True,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_note(self, note: ReflectionNote) -> None:
        """Persist the note to PaperReflection (immutable append)."""
        try:
            with self.db.session_scope() as session:
                row = PaperReflection(
                    account_id=note.account_id or 0,
                    scope=note.scope,
                    subject=note.subject,
                    summary=note.summary,
                    takeaway=note.takeaway,
                    lessons_json=json.dumps(
                        note.lessons, ensure_ascii=False
                    ) if note.lessons else None,
                    tags=",".join(note.tags) if note.tags else None,
                    mood=note.mood,
                    trade_id=note.trade_id,
                    order_id=note.order_id,
                    signal_id=note.signal_id,
                    code=note.code,
                    raw_response=note.raw_response,
                    elapsed_seconds=note.elapsed_seconds,
                    used_fallback=note.used_fallback,
                )
                session.add(row)
                session.flush()
                note.row_id = row.id
                note.created_at = row.created_at
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] Failed to persist note: %s", exc
            )

    def _persist_note_with_action(self, note: ReflectionNote, action: str) -> None:
        """Persist the note with explicit agent_action override."""
        try:
            with self.db.session_scope() as session:
                # Update the existing row to add agent_action
                # We need to find the row by note.row_id
                if note.row_id is None:
                    return
                row = session.query(PaperReflection).filter(
                    PaperReflection.id == note.row_id
                ).first()
                if row:
                    row.agent_action = action
                    session.flush()
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] Failed to persist agent_action: %s", exc
            )

    # ------------------------------------------------------------------
    # Context fetchers
    # ------------------------------------------------------------------

    def _fetch_trade(self, trade_id: int) -> Optional[PaperTrade]:
        try:
            with self.db.session_scope() as session:
                trade = session.execute(
                    select(PaperTrade).where(PaperTrade.id == trade_id)
                ).scalar_one_or_none()
                if trade is not None:
                    session.expunge(trade)
                return trade
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] _fetch_trade(%s) failed: %s", trade_id, exc
            )
            return None

    def _fetch_account_snapshot(self, account_id: int) -> Dict[str, Any]:
        """Fetch account snapshot for daily reflection."""
        from src.storage import PaperAccount

        try:
            with self.db.session_scope() as session:
                account = session.query(PaperAccount).filter(
                    PaperAccount.id == account_id
                ).first()
                if account:
                    return {
                        "start_assets": account.start_assets,
                        "total_assets": account.total_assets,
                        "cash": account.cash,
                    }
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] Failed to fetch account snapshot: %s", exc
            )
        return {"start_assets": 0.0, "total_assets": 0.0, "cash": 0.0}

    def _render_positions_summary(self, account_id: int) -> str:
        """Render a summary of current positions."""
        from src.storage import PaperPosition

        try:
            with self.db.session_scope() as session:
                stmt = select(PaperPosition).where(
                    PaperPosition.account_id == account_id
                )
                rows = session.execute(stmt).scalars().all()
                if not rows:
                    return "(无持仓)"
                lines = []
                for pos in rows:
                    if pos and pos.quantity and pos.quantity > 0:
                        lines.append(
                            f"- {pos.code}: {int(pos.quantity)} @ "
                            f"${pos.last_price:.4f} (avg: {pos.avg_cost:.4f})"
                        )
                return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] Failed to render positions: %s", exc
            )
            return "(无法获取持仓信息)"

    def _fetch_today_trades(
        self, account_id: int, review_date: Any
    ) -> List[PaperTrade]:
        """Fetch trades for the given date."""
        from src.storage import PaperTrade, func

        try:
            with self.db.session_scope() as session:
                stmt = (
                    select(PaperTrade)
                    .where(PaperTrade.account_id == account_id)
                    .where(func.date(PaperTrade.traded_at) == review_date)
                )
                return session.execute(stmt).scalars().all()
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] Failed to fetch today's trades: %s", exc
            )
            return []

    def _fetch_today_decisions(
        self, account_id: int, review_date: Any
    ) -> List[Any]:
        """Fetch decisions for the given date (stub)."""
        # This is a placeholder; actual implementation depends on
        # how decisions are stored. Currently using the approach from
        # the original code where they query from some decisions table.
        try:
            with self.db.session_scope() as session:
                # In the original code, this queries PaperDecision or similar
                # For now, return empty list as no specific decision table exists
                return []
        except Exception as exc:
            logger.warning(
                "[ReflectionEngine] Failed to fetch today's decisions: %s", exc
            )
            return []
