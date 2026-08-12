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

    def __init__(
        self,
        scope: str = "",
        subject: str = "",
        summary: str = "",
        takeaway: str = "",
        lessons: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        mood: str = "neutral",
        raw_response: Optional[str] = None,
        error: Optional[str] = None,
        elapsed_seconds: float = 0.0,
        used_fallback: bool = False,
        account_id: Optional[int] = None,
        trade_id: Optional[int] = None,
        order_id: Optional[int] = None,
        signal_id: Optional[int] = None,
        code: Optional[str] = None,
        row_id: Optional[int] = None,
        created_at: Optional[datetime] = None,
        agent_action: Optional[str] = None,
    ):
        self.scope = scope
        self.subject = subject
        self.summary = summary
        self.takeaway = takeaway
        self.lessons = list(lessons or [])
        self.tags = list(tags or [])
        self.mood = mood
        self.raw_response = raw_response
        self.error = error
        self.elapsed_seconds = elapsed_seconds
        self.used_fallback = used_fallback
        self.account_id = account_id
        self.trade_id = trade_id
        self.order_id = order_id
        self.signal_id = signal_id
        self.code = code
        self.row_id = row_id
        self.created_at = created_at
        self.agent_action = agent_action

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
def build_reflection_engine(
    trading_engine: Optional[Any] = None,
    account_id: int = 0,
    timeout_seconds: float = 180.0,
    db_manager: Optional[Any] = None,
    config: Optional[Any] = None,
) -> "ReflectionEngine":
    """Convenience factory for ReflectionEngine mirroring the pattern in other modules.

    Args:
        trading_engine: TradingEngine instance for context.
        account_id: Default account ID for reflections.
        timeout_seconds: Hard cap on agent call duration.
        db_manager: Database manager (defaults to global get_db()).
        config: Application config for building the agent executor.

    Returns:
        Initialized ReflectionEngine instance.
    """
    if db_manager is None:
        db_manager = get_db()
    return ReflectionEngine(
        config=config,
        trading_engine=trading_engine,
        account_id=account_id,
        timeout_seconds=timeout_seconds,
        db_manager=db_manager,
    )


def _compute_note_score(note, now, target_code=None):
    """Compute score = time_decay * quality * relevance * outcome_weight (P0-E)."""
    from datetime import timedelta
    import math

    # Time decay: exp(-delta_days / 7), half-life = 7 days
    if note.created_at:
        delta_days = (now - note.created_at).total_seconds() / 86400.0
        decay = math.exp(-delta_days / 7.0)
        decay = max(decay, 0.1)
    else:
        decay = 1.0
    
    # Content quality based on takeaway length + action keywords
    text = (note.takeaway or '') + ' ' + (note.summary or '')
    quality = min(1.0, max(0.5, len(text) / 200.0)) if text else 0.5
    actions = ['should','need','must','avoid','check','monitor','limit','stop']
    actions_found = sum(1 for kw in actions if kw.lower() in text.lower())
    quality = min(1.0, quality * (1.0 + 0.1 * actions_found))
    
    # Relevance boost for matching stock code
    rel = 1.0
    if target_code and note.code:
        if note.code == target_code:
            rel = 1.5
        elif target_code in note.code or note.code in target_code:
            rel = 1.2
    
    # Outcome weight from mood and tags
    mood = note.mood or 'neutral'
    raw_tags = note.tags or []
    if isinstance(raw_tags, str):
        tags = raw_tags.lower()
    else:
        tags = " ".join(str(t) for t in raw_tags).lower()
    good = ['win','profit','gain','success','good','outperform']
    bad = ['loss','fail','mistake','bad','underperform','stop']
    g_count = sum(1 for k in good if k in tags)
    b_count = sum(1 for k in bad if k in tags)
    mood_factor = {'good':1.2,'neutral':1.0,'bad':0.7}.get(mood, 1.0)
    outcome = mood_factor * (1.0 + 0.1*g_count - 0.15*b_count)
    outcome = max(0.3, min(2.0, outcome))
    
    return max(0.01, min(1.0, decay * quality * rel * outcome))

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
    # Trade retrieval helper
    # ------------------------------------------------------------------

    def _fetch_trade(self, trade_id: int):
        """Fetch a PaperTrade row by id, or None when not found.

        Returns a simple object (not a SQLAlchemy ORM instance) so all
        attribute reads work after the session closes.
        """
        from types import SimpleNamespace

        with self.db.session_scope() as session:
            stmt = select(PaperTrade).where(PaperTrade.id == trade_id)
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            # Snapshot all fields eagerly to avoid DetachedInstanceError.
            return SimpleNamespace(
                id=row.id,
                account_id=row.account_id,
                order_id=row.order_id,
                code=row.code,
                name=row.name,
                side=row.side,
                price=row.price,
                quantity=row.quantity,
                amount=row.amount,
                fee=row.fee,
                traded_at=row.traded_at,
            )

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_trade_reflection_prompt(
        self,
        trade,
        account_id: int,
        decision_context: str = "",
    ) -> str:
        """Build the LLM prompt for reflecting on a single trade."""
        positions_summary = self._positions_summary(account_id)
        return TRADE_REFLECTION_PROMPT_TEMPLATE.format(
            account_id=account_id,
            trade_id=getattr(trade, "id", ""),
            code=getattr(trade, "code", ""),
            name=getattr(trade, "name", "") or "",
            side=getattr(trade, "side", ""),
            fill_price=float(getattr(trade, "price", 0) or 0),
            fill_quantity=float(getattr(trade, "quantity", 0) or 0),
            fill_amount=float(getattr(trade, "amount", 0) or 0),
            fee=float(getattr(trade, "fee", 0) or 0),
            traded_at=getattr(trade, "traded_at", "") or "",
            decision_context=decision_context or "(无决策背景)",
            positions_summary=positions_summary,
        )

    def _build_daily_reflection_prompt(
        self,
        account_id: int,
        review_date,
    ) -> str:
        """Build the LLM prompt for end-of-day reflection."""
        snap = None
        try:
            snap = self.trading_engine.account_mgr.snapshot(account_id)
            start_assets = float(getattr(snap, "initial_capital", 0) or 0)
            end_assets = float(getattr(snap, "total_assets", 0) or 0)
        except Exception:
            start_assets = 0.0
            end_assets = 0.0
        daily_pnl = end_assets - start_assets
        daily_pnl_pct = (daily_pnl / start_assets * 100) if start_assets > 0 else 0.0
        cash = float(getattr(snap, "cash", 0) or 0) if snap else 0.0
        trades = []
        try:
            trades = (
                self.trading_engine.order_mgr.list_trades(account_id)
                if hasattr(self.trading_engine, "order_mgr")
                else []
            )
            trade_count = len(trades)
        except Exception:
            trade_count = 0
        try:
            positions = self.trading_engine.position_mgr.list_positions(account_id)
            position_count = len(positions)
        except Exception:
            position_count = 0
        trades_lines = []
        for t in trades[:10]:
            trades_lines.append(
                f"{t.get('code')} {t.get('side')} x{t.get('quantity', 0)} @{t.get('price', 0)}"
            )
        trades_summary = "\n".join(trades_lines) if trades_lines else "(无成交明细)"
        return DAILY_REFLECTION_PROMPT_TEMPLATE.format(
            account_id=account_id,
            review_date=str(review_date),
            start_assets=start_assets,
            end_assets=end_assets,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            trade_count=trade_count,
            decision_count=0,
            position_count=position_count,
            cash=cash,
            trades_summary=trades_summary,
            positions_summary=self._positions_summary(account_id),
            decisions_summary="",
        )

    def _positions_summary(self, account_id: int) -> str:
        """Render a compact positions summary for prompt context."""
        try:
            positions = self.trading_engine.position_mgr.list_positions(account_id)
        except Exception:
            return "(无持仓信息)"
        if not positions:
            return "(无持仓)"
        lines = []
        for p in positions[:10]:
            lines.append(
                f"{p.get('code')} qty={p.get('available_quantity', 0)} "
                f"avg_cost={p.get('avg_cost', 0)}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Reflection execution + persistence
    # ------------------------------------------------------------------

    def _run_reflection(
        self,
        prompt: str,
        scope: str,
        account_id: int,
        trade_id: Optional[int] = None,
        order_id: Optional[int] = None,
        signal_id: Optional[int] = None,
        code: Optional[str] = None,
    ) -> ReflectionNote:
        """Run the LLM reflection, parse JSON output, and persist the note."""
        import json
        import time

        start = time.time()
        try:
            executor = self.executor
            if executor is None:
                raise RuntimeError("no agent executor configured")
            result = executor.chat(message=prompt, session_id="reflection")
            raw = getattr(result, "content", "") or ""
            elapsed = time.time() - start
            note = self._parse_reflection_json(raw, scope, account_id)
            note.raw_response = raw
            note.elapsed_seconds = round(elapsed, 2)
            note.trade_id = trade_id
            note.order_id = order_id
            note.signal_id = signal_id
            note.code = code
            self._persist_note(note)
            return note
        except Exception as exc:
            logger.warning("Reflection run failed: %s", exc)
            note = ReflectionNote(
                scope=scope,
                subject=f"{scope} reflection failed",
                summary=str(exc),
                takeaway="no reflection possible",
                mood="neutral",
                used_fallback=True,
                error=str(exc),
                account_id=account_id,
                trade_id=trade_id,
                order_id=order_id,
                code=code,
                elapsed_seconds=round(time.time() - start, 2),
            )
            return note

    def _parse_reflection_json(
        self,
        raw: str,
        scope: str,
        account_id: int,
    ) -> ReflectionNote:
        """Parse the LLM's JSON reflection payload into a ReflectionNote."""
        import json
        import re

        note = ReflectionNote(scope=scope, account_id=account_id)
        text = (raw or "").strip()
        # Extract JSON object from the response (may have prose around it).
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        try:
            data = json.loads(text)
        except Exception:
            data = {}
        note.subject = str(data.get("subject", ""))
        note.summary = str(data.get("summary", ""))
        note.takeaway = str(data.get("takeaway", ""))
        lessons = data.get("lessons", [])
        note.lessons = [str(x) for x in lessons] if isinstance(lessons, list) else []
        tags = data.get("tags", "")
        note.tags = [t.strip() for t in str(tags).split(",") if t.strip()]
        note.mood = str(data.get("mood", "neutral"))
        return note

    def _persist_note(self, note: ReflectionNote) -> Optional[int]:
        """Persist a ReflectionNote into the paper_reflections table."""
        import json
        try:
            from src.storage import PaperReflection
            with self.db.session_scope() as session:
                row = PaperReflection(
                    account_id=note.account_id or 0,
                    scope=note.scope,
                    subject=note.subject,
                    summary=note.summary,
                    takeaway=note.takeaway,
                    lessons_json=json.dumps(note.lessons, ensure_ascii=False),
                    tags=",".join(note.tags),
                    mood=note.mood,
                    trade_id=note.trade_id,
                    order_id=note.order_id,
                    signal_id=note.signal_id,
                    code=note.code,
                    raw_response=note.raw_response,
                    elapsed_seconds=note.elapsed_seconds,
                    used_fallback=note.used_fallback,
                    agent_action=note.agent_action,
                )
                session.add(row)
                session.flush()
                note.row_id = int(row.id)
                note.created_at = row.created_at or datetime.now()
            return note.row_id
        except Exception as exc:
            logger.warning("Reflection persist failed: %s", exc)
            return None

    def _persist_note_with_action(self, note: ReflectionNote, action: str) -> None:
        """Re-persist a note that carries an agent_action field."""
        note.agent_action = action
        self._persist_note(note)

    def _row_to_note(self, row) -> ReflectionNote:
        """Convert a PaperReflection DB row back into a ReflectionNote."""
        import json
        note = ReflectionNote(
            scope=row.scope,
            subject=row.subject or "",
            summary=row.summary or "",
            takeaway=row.takeaway or "",
            mood=row.mood or "neutral",
            account_id=row.account_id,
            trade_id=row.trade_id,
            order_id=row.order_id,
            signal_id=row.signal_id,
            code=row.code,
            raw_response=row.raw_response,
            elapsed_seconds=row.elapsed_seconds or 0.0,
            used_fallback=bool(row.used_fallback),
            agent_action=row.agent_action,
            row_id=row.id,
            created_at=row.created_at,
        )
        try:
            lessons = json.loads(row.lessons_json) if row.lessons_json else []
            note.lessons = [str(x) for x in lessons] if isinstance(lessons, list) else []
        except Exception:
            note.lessons = []
        note.tags = [t.strip() for t in (row.tags or "").split(",") if t.strip()]
        return note

    # ------------------------------------------------------------------
    # Memory retrieval (P0-E integration point)
    # ------------------------------------------------------------------

    def get_recent_notes(self, limit: int = 5, account_id=None):
        """Fetch notes weighted by time-decay scoring (P0-E Memory decay).
        
        Returns top-N notes sorted by computed score instead of pure time order.
        Score formula: score = time_decay * quality * relevance * outcome
        with 7-day half-life for temporal decay.
        """
        acct_id = account_id if account_id is not None else self.account_id

        from src.storage import PaperReflection
        # Build notes inside the session so ORM attributes load eagerly;
        # accessing detached rows after the session closes raises
        # DetachedInstanceError.
        notes: List[ReflectionNote] = []
        with self.db.session_scope() as session:
            stmt = select(PaperReflection).where(
                PaperReflection.account_id == acct_id
            )
            for row in session.execute(stmt).scalars().all():
                notes.append(self._row_to_note(row))

        from datetime import datetime
        now = datetime.now()
        scored = []

        for note in notes:
            score = _compute_note_score(note, now, target_code=None)
            scored.append((score, note))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [note for _, note in scored[:limit]]

    def get_relevant_notes(self, code: str, limit: int = 3, account_id=None):
        """Fetch notes relevant to a specific stock code (P0-E).

        Filters notes for the account, boosts same-code matches via
        ``_compute_note_score(..., target_code=code)``, and returns the
        top-N by score.
        """
        acct_id = account_id if account_id is not None else self.account_id

        from src.storage import PaperReflection
        # Same eager-load pattern: build notes inside the session.
        notes: List[ReflectionNote] = []
        with self.db.session_scope() as session:
            stmt = select(PaperReflection).where(
                PaperReflection.account_id == acct_id
            )
            for row in session.execute(stmt).scalars().all():
                notes.append(self._row_to_note(row))

        from datetime import datetime
        now = datetime.now()
        scored = []
        for note in notes:
            score = _compute_note_score(note, now, target_code=code)
            scored.append((score, note))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [note for _, note in scored[:limit]]

