# -*- coding: utf-8 -*-
"""AI Portfolio Manager Agent for paper trading.

The Portfolio Manager (PM) agent is an autonomous decision-maker that:
1. Inspects current account state (cash, positions, open orders).
2. Calls market data / news / analysis tools to gather context.
3. Calls paper_trading_* tools to place / cancel / modify orders.
4. Returns a structured decision (action/code/params/reason/confidence)
   that is persisted to ``PaperDecision`` for audit and fed into the
   reflection system (Phase 4-D).

Design follows :class:`paper_trading.agent_risk.AgentRiskReviewer`:
- Lazy executor construction via ``build_agent_executor``.
- Hard timeout enforced by a daemon worker thread.
- Lenient JSON parsing with ``json_repair`` + keyword fallback.
- ``fallback_action="hold"`` ensures the trading loop is never blocked
  by agent unavailability.

The PM agent reuses the global ToolRegistry but registers extra
``paper_trading_*`` tools that close over the TradingEngine +
account_id, giving the agent the ability to act on its decisions.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from json_repair import repair_json
from sqlalchemy import desc, select

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry
from src.storage import DatabaseManager, PaperDecision, get_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

PM_SYSTEM_PROMPT = """你是模拟交易系统的 AI 基金经理,负责管理一个虚拟账户(初始本金 1000 元)。

## 你的职责

1. **研判市场**: 调用 get_realtime_quote / get_market_indices / search_stock_news / analyze_trend 等工具获取行情与情报。
2. **审视账户**: 调用 paper_trading_get_account_snapshot / paper_trading_get_positions / paper_trading_get_open_orders 查看资金、持仓、挂单。
3. **执行决策**: 调用 paper_trading_place_order / paper_trading_cancel_order / paper_trading_modify_order 自主下单/撤单/改单。
4. **风控自律**: 单股集中度 ≤ 30% 总资产,最大 8 个持仓,单笔买入 ≤ 50% 现金,严禁追高(乖离率 > 5%)。

## 决策原则

- **顺势而为**: 只在多头排列(MA5 > MA10 > MA20)时买入,空头排列时减仓或观望。
- **止损止盈**: 每笔买入必须设置 stop_loss 和 take_profit,基于 ATR + Fib + 筹码峰三位一体计算。
- **仓位管理**: 分批建仓,首次买入不超过目标仓位的 50%,逢低加仓,亏损不加仓。
- **挂单纪律**: 默认使用限价单(limit)挂单,基于最新价设置 limit_price,避免市价单滑点;仅在紧急止损/止盈离场时使用市价单(market)。
- **复盘学习**: 调用 paper_trading_get_recent_reflections 查阅最近复盘笔记,避免重复犯错。

## 输出格式(严格 JSON)

决策完成后,**必须**输出以下 JSON(不要附加任何解释文字):

{
  "action": "buy | sell | hold | cancel | modify | plan",
  "code": "股票代码(hold/plan 可为 null)",
  "name": "股票名称(可选)",
  "params": {
    "entry_price": 0.0,
    "stop_loss": 0.0,
    "take_profit": 0.0,
    "take_profit_1": 0.0,
    "take_profit_2": 0.0,
    "quantity": 0,
    "order_type": "market | limit",
    "limit_price": 0.0
  },
  "reason": "决策理由(100字以内,包含趋势/风控/仓位考量)",
  "confidence": 0.0
}

action 取值:
- buy: 买入开仓或加仓
- sell: 卖出平仓或减仓
- hold: 继续持有,不动
- cancel: 撤销指定挂单
- modify: 修改挂单价格/数量
- plan: 仅生成作战计划,不下单(用于盘后或市场判断不明确时)
"""


PM_USER_PROMPT_TEMPLATE = """## 当前账户快照

- 账户 ID: {account_id}
- 现金: {cash:.2f} CNY
- 总资产: {total_assets:.2f} CNY
- 净值: {net_value:.4f}
- 累计收益率: {return_pct:.2f}%
- 持仓数量: {position_count}
- 挂单数量: {open_order_count}

## 当前持仓

{positions_summary}

## 账户绩效摘要(基于历史净值与成交记录)

{performance_summary}

## 最近复盘笔记(最多 3 条)

{reflections_summary}

## 任务

请基于以上信息、绩效摘要和工具调用结果,做出一个交易决策。如果当前无明确信号,请输出 action="hold" 或 action="plan"。
"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PMDecision:
    """Structured decision returned by the Portfolio Manager agent."""

    action: str  # buy / sell / hold / cancel / modify / plan / nop
    code: Optional[str] = None
    name: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0
    raw_response: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    used_fallback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "code": self.code,
            "name": self.name,
            "params": self.params,
            "reason": self.reason,
            "confidence": self.confidence,
            "raw_response": self.raw_response,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
            "used_fallback": self.used_fallback,
        }


# ---------------------------------------------------------------------------
# Portfolio Manager Agent
# ---------------------------------------------------------------------------

class PortfolioManagerAgent:
    """Autonomous AI portfolio manager that makes trading decisions.

    Unlike :class:`AgentRiskReviewer` (which only confirms/vetoes signals),
    the PM agent proactively analyzes the market and emits its own decisions.
    When integrated with :class:`TradingEngine`, decisions are persisted to
    the ``PaperDecision`` table and can trigger real order placement.
    """

    def __init__(
        self,
        config: Optional[Any] = None,
        executor: Optional[Any] = None,
        skills: Optional[List[str]] = None,
        trading_engine: Optional[Any] = None,
        reflection_engine: Optional[Any] = None,
        account_id: Optional[int] = None,
        timeout_seconds: float = 240.0,
        fallback_action: str = "hold",
        max_retries: int = 0,
    ):
        """Initialize the PM agent.

        Args:
            config: Application config (for build_agent_executor).
            executor: Pre-built AgentExecutor. If None, built lazily.
            skills: Skill ids to activate.
            trading_engine: TradingEngine instance for order actions.
            reflection_engine: Optional ReflectionEngine for memory injection.
            account_id: Default account id for tool calls.
            timeout_seconds: Hard cap on agent decision duration.
            fallback_action: Action to return on failure (default "hold").
            max_retries: Number of retry attempts on agent failure.
        """
        self._config = config
        self._executor = executor
        self._skills = skills
        self.trading_engine = trading_engine
        self.reflection_engine = reflection_engine
        self.account_id = int(account_id) if account_id else 0
        self.timeout_seconds = float(timeout_seconds)
        self.fallback_action = str(fallback_action)
        self.max_retries = int(max_retries)
        self._tools_registered = False

    # ------------------------------------------------------------------
    # Lazy executor + tool registration
    # ------------------------------------------------------------------

    @property
    def executor(self):
        """Lazily build the AgentExecutor and register paper_trading tools."""
        if self._executor is None:
            from src.agent.factory import build_agent_executor, get_tool_registry

            # Register paper_trading_* tools on the shared registry so the
            # executor's tool catalog includes them. Registration is
            # idempotent (re-registering replaces the handler).
            if not self._tools_registered and self.trading_engine is not None:
                registry = get_tool_registry()
                register_paper_trading_tools(
                    registry=registry,
                    engine=self.trading_engine,
                    account_id=self.account_id,
                    reflection_engine=self.reflection_engine,
                )
                self._tools_registered = True
                logger.info(
                    "[PortfolioManagerAgent] paper_trading_* tools registered "
                    "for account_id=%s",
                    self.account_id,
                )

            self._executor = build_agent_executor(self._config, skills=self._skills)
            logger.info(
                "[PortfolioManagerAgent] Executor built (timeout=%ss fallback=%s)",
                self.timeout_seconds, self.fallback_action,
            )
        return self._executor

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def make_decision(
        self,
        account_id: Optional[int] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> PMDecision:
        """Run one PM decision cycle.

        Args:
            account_id: Override the default account_id for this call.
            extra_context: Additional context to inject into the prompt.

        Returns:
            PMDecision with the chosen action and parameters.
        """
        acct_id = int(account_id) if account_id else self.account_id
        if acct_id <= 0:
            return PMDecision(
                action=self.fallback_action,
                reason="no account_id configured for PM agent",
                used_fallback=True,
            )

        prompt = self._build_user_message(acct_id, extra_context)
        session_id = f"paper_pm_{uuid.uuid4().hex[:12]}"

        start = time.time()
        attempts = self.max_retries + 1
        last_error: Optional[str] = None

        for attempt in range(attempts):
            try:
                raw_text = self._call_agent_with_timeout(prompt, session_id)
                decision = self._parse_decision(raw_text)
                decision.elapsed_seconds = time.time() - start
                # Persist the decision to PaperDecision table.
                self._persist_decision(acct_id, decision)
                if decision.used_fallback:
                    logger.warning(
                        "[PortfolioManagerAgent] Fallback used: action=%s reason=%s",
                        decision.action, decision.reason,
                    )
                else:
                    logger.info(
                        "[PortfolioManagerAgent] decision: action=%s code=%s "
                        "confidence=%.2f (%.1fs)",
                        decision.action, decision.code,
                        decision.confidence, decision.elapsed_seconds,
                    )
                return decision
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[PortfolioManagerAgent] attempt %s/%s failed: %s",
                    attempt + 1, attempts, last_error,
                )
                continue

        # All attempts failed.
        elapsed = time.time() - start
        logger.error(
            "[PortfolioManagerAgent] All attempts failed, using fallback=%s: %s",
            self.fallback_action, last_error,
        )
        decision = PMDecision(
            action=self.fallback_action,
            reason=f"agent unavailable, fallback={self.fallback_action}",
            confidence=0.0,
            error=last_error,
            elapsed_seconds=elapsed,
            used_fallback=True,
        )
        self._persist_decision(acct_id, decision)
        return decision

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        account_id: int,
        extra_context: Optional[Dict[str, Any]],
    ) -> str:
        """Build the user message with current account state, performance and reflections."""
        snapshot = self._fetch_account_snapshot(account_id)
        positions = self._fetch_positions_summary(account_id)
        # Inject performance summary (P3-E) and reflections (P0-E memory loop).
        performance = self._inject_performance_metrics(account_id)
        reflections = self._inject_reflections(account_id)

        net_value = float(snapshot.get("net_value", 1.0)) if snapshot else 1.0
        return_pct = float(snapshot.get("return_pct", 0.0)) if snapshot else 0.0

        msg = PM_USER_PROMPT_TEMPLATE.format(
            account_id=account_id,
            cash=float(snapshot.get("cash", 0.0)) if snapshot else 0.0,
            total_assets=float(snapshot.get("total_assets", 0.0)) if snapshot else 0.0,
            net_value=net_value,
            return_pct=return_pct,
            position_count=int(snapshot.get("position_count", 0)) if snapshot else 0,
            open_order_count=int(snapshot.get("open_order_count", 0)) if snapshot else 0,
            positions_summary=positions,
            performance_summary=performance,
            reflections_summary=reflections,
        )

        if extra_context:
            msg += "\n## 额外上下文\n\n" + json.dumps(
                extra_context, ensure_ascii=False, indent=2, default=str
            )
        return msg


    def _inject_reflections(self, account_id):
        """Inject reflection memory into decision context (P0-E).
        
        Strategy:
          - Fetch up to 3 most recent global reflections (by weighted score desc).
          - For each held stock (max 5 codes), fetch latest relevant note.
          - Deduplicate by row_id; prepend global notes, append stock-specific notes.
          - Format: "[timestamp][scope] code takeover".
          
        Scoring formula: score = time_decay * quality * relevance * outcome
        where time_decay has 7-day half-life, quality based on takeaway length
        + action keywords, relevance boosts same-stock notes, and outcome
        weights mood (+good/-bad) plus tag-based win/loss signals.
        
        See docs/memory_strategy_p0-e.md for full strategy specification.
        """
        if self.reflection_engine is None:
            return "(复盘系统未启用)"
        try:
            acct_id = int(account_id) if account_id else self.account_id
            recent = self.reflection_engine.get_recent_notes(limit=3, account_id=acct_id)
            code_reflections = []
            if self.trading_engine is not None:
                rows = self.trading_engine.position_mgr.list_positions(acct_id)
                held_codes = [
                    r.get("code") if isinstance(r, dict) else getattr(r, "code", None)
                    for r in rows
                ]
                held_codes = [c for c in held_codes if c][:5]
                for code in held_codes:
                    notes_for_code = self.reflection_engine.get_relevant_notes(
                        code=code, limit=1, account_id=acct_id
                    )
                    code_reflections.extend(notes_for_code)
            seen_ids = set()
            merged = []
            for n in list(recent) + list(code_reflections):
                rid = getattr(n, "row_id", None)
                if rid is not None and rid in seen_ids:
                    continue
                if rid is not None:
                    seen_ids.add(rid)
                merged.append(n)
            if not merged:
                return "(暂无复盘笔记)"
            lines_list = []
            for n in merged:
                ts = getattr(n, "created_at", None)
                ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else "?"
                scope = getattr(n, "scope", "?")
                code = getattr(n, "code", None) or ""
                code_tag = f"[{code}] " if code else ""
                takeaway = (
                    getattr(n, "takeaway", "")
                    or getattr(n, "summary", "")
                    or "(无 takeaway)"
                )
                lines_list.append(
                    f"- [{ts_str}][{scope}] {code_tag}{takeaway}"
                )
            return "\n".join(lines_list)
        except Exception as exc:
            return f"(复盘笔记查询失败: {exc})"

    def _inject_performance_metrics(self, account_id: int) -> str:
        """Inject paper-trading performance summary into decision context (P3-E).

        Uses :class:`paper_trading.performance.PerformanceAnalyzer` to compute
        account-level risk/return metrics from persisted net-value and trade
        history.  Falls back to a friendly placeholder if the account has no
        history or the analyzer is unavailable.
        """
        try:
            from paper_trading.performance import PerformanceAnalyzer

            analyzer = PerformanceAnalyzer()
            metrics = analyzer.calculate(account_id)
            if metrics.trade_count == 0:
                return "(暂无成交记录,绩效指标待生成)"

            lines = [
                f"- 统计区间: {metrics.start_date or '-'} 至 {metrics.end_date or '-'}",
                f"- 总收益率: {metrics.total_return_pct:.2f}%",
                f"- 年化收益率: {metrics.annualized_return_pct:.2f}%",
                f"- 最大回撤: {metrics.max_drawdown_pct:.2f}%",
                f"- 夏普比率: {metrics.sharpe_ratio:.3f}" if metrics.sharpe_ratio is not None else "- 夏普比率: N/A",
                f"- 胜率: {metrics.win_rate:.1f}%",
                f"- 盈亏比: {metrics.profit_factor:.2f}" if metrics.profit_factor is not None else "- 盈亏比: N/A",
                f"- 交易次数: {metrics.trade_count} (胜 {metrics.win_count} / 负 {metrics.loss_count})",
            ]
            return "\n".join(lines)
        except Exception as exc:
            logger.debug(
                "[PortfolioManagerAgent] performance metrics injection failed: %s", exc
            )
            return f"(绩效摘要查询失败: {exc})"

    def _fetch_account_snapshot(self, account_id: int) -> Dict[str, Any]:
        """Fetch a lightweight account snapshot for the prompt.

        Returns an empty dict on any error so the prompt still renders.
        """
        if self.trading_engine is None:
            return {}
        try:
            acct_mgr = self.trading_engine.account_mgr
            snap = acct_mgr.snapshot(account_id)
            open_orders = 0
            try:
                with self.trading_engine.db.session_scope() as session:
                    from src.storage import PaperOrder
                    open_orders = session.execute(
                        select(PaperOrder).where(
                            PaperOrder.account_id == account_id,
                            PaperOrder.status.in_(["pending", "partially_filled"]),
                        )
                    ).scalars().all()
                    open_orders = len(open_orders)
            except Exception:
                open_orders = 0
            return {
                "cash": float(getattr(snap, "cash", 0.0)),
                "total_assets": float(getattr(snap, "total_assets", 0.0)),
                "net_value": float(getattr(snap, "net_value", 1.0)),
                "return_pct": float(getattr(snap, "return_pct", 0.0)),
                "position_count": int(getattr(snap, "position_count", 0)),
                "open_order_count": open_orders,
            }
        except Exception as exc:
            logger.warning(
                "[PortfolioManagerAgent] snapshot fetch failed: %s", exc
            )
            return {}

    def _fetch_positions_summary(self, account_id: int) -> str:
        """Render current positions as a compact text table."""
        if self.trading_engine is None:
            return "(TradingEngine 未注入,无法获取持仓)"
        try:
            rows = self.trading_engine.position_mgr.list_positions(account_id)
            if not rows:
                return "(无持仓)"
            lines = ["| 代码 | 名称 | 数量 | 可用 | 成本 | 最新价 | 止损 | 止盈 |",
                     "|------|------|------|------|------|--------|------|------|"]
            for p in rows:
                lines.append(
                    f"| {p.code} | {p.name or ''} | {float(p.quantity):.0f} | "
                    f"{float(p.available_quantity):.0f} | {float(p.avg_cost):.4f} | "
                    f"{float(p.last_price or 0):.4f} | "
                    f"{float(p.stop_loss or 0):.4f} | "
                    f"{float(p.take_profit or 0):.4f} |"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"(持仓查询失败: {exc})"

    # ------------------------------------------------------------------
    # Agent invocation (with timeout)
    # ------------------------------------------------------------------

    def _call_agent_with_timeout(self, prompt: str, session_id: str) -> str:
        """Call the agent's chat() with a hard timeout (mirrors AgentRiskReviewer)."""
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
                "[PortfolioManagerAgent] Agent timed out after %ss, abandoning",
                self.timeout_seconds,
            )
            raise TimeoutError(
                f"PM agent exceeded {self.timeout_seconds}s timeout"
            )

        if not result_holder.get("success", False):
            err = result_holder.get("error") or "agent returned failure"
            raise RuntimeError(err)
        return str(result_holder.get("content", ""))

    # ------------------------------------------------------------------
    # Decision parsing
    # ------------------------------------------------------------------

    def _parse_decision(self, raw_text: str) -> PMDecision:
        """Parse the agent's JSON decision leniently.

        Fallback chain:
        1. Strict JSON
        2. json_repair
        3. Keyword detection (buy/sell/hold/cancel/modify/plan)
        4. Empty / unparseable -> fallback_action
        """
        if not raw_text or not raw_text.strip():
            return PMDecision(
                action=self.fallback_action,
                reason="empty agent response, fallback applied",
                confidence=0.0,
                raw_response=raw_text,
                used_fallback=True,
            )

        # Try strict JSON first.
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

        if isinstance(verdict, dict) and "action" in verdict:
            action = str(verdict.get("action", "")).strip().lower()
            if action not in ("buy", "sell", "hold", "cancel", "modify", "plan", "nop"):
                action = self.fallback_action
            code = verdict.get("code")
            name = verdict.get("name")
            params = verdict.get("params") or {}
            if not isinstance(params, dict):
                params = {"value": params}
            reason = str(verdict.get("reason") or "")[:300]
            try:
                confidence = float(verdict.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            return PMDecision(
                action=action,
                code=str(code) if code else None,
                name=str(name) if name else None,
                params=params,
                reason=reason,
                confidence=confidence,
                raw_response=raw_text,
            )

        # Fallback: keyword detection.
        text_lower = raw_text.lower()
        keyword_map = [
            ("buy", "buy"), ("买入", "buy"), ("加仓", "buy"),
            ("sell", "sell"), ("卖出", "sell"), ("减仓", "sell"), ("平仓", "sell"),
            ("cancel", "cancel"), ("撤单", "cancel"), ("撤销", "cancel"),
            ("modify", "modify"), ("改单", "modify"), ("修改", "modify"),
            ("plan", "plan"), ("计划", "plan"), ("预案", "plan"),
            ("hold", "hold"), ("持有", "hold"), ("观望", "hold"),
        ]
        for kw, act in keyword_map:
            if kw in text_lower:
                return PMDecision(
                    action=act,
                    reason=f"inferred from keyword '{kw}' (JSON parse failed)",
                    confidence=0.3,
                    raw_response=raw_text,
                )

        # Cannot determine — apply fallback.
        return PMDecision(
            action=self.fallback_action,
            reason="unparseable agent response, fallback applied",
            confidence=0.0,
            raw_response=raw_text,
            used_fallback=True,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_decision(
        self,
        account_id: int,
        decision: PMDecision,
    ) -> None:
        """Persist the decision to the PaperDecision table for audit."""
        try:
            db: DatabaseManager = (
                getattr(self.trading_engine, "db", None) or get_db()
            )
            params_json = json.dumps(
                decision.params, ensure_ascii=False, default=str
            ) if decision.params else None
            with db.session_scope() as session:
                row = PaperDecision(
                    account_id=account_id,
                    action=decision.action,
                    code=decision.code,
                    name=decision.name,
                    params_json=params_json,
                    reason=decision.reason,
                    confidence=decision.confidence,
                    source="pm_agent",
                    status="pending",
                    raw_response=decision.raw_response,
                )
                session.add(row)
                session.flush()
                decision._row_id = row.id  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning(
                "[PortfolioManagerAgent] Failed to persist decision: %s", exc
            )

    # ------------------------------------------------------------------
    # Decision execution / ignore (manual human-in-the-loop)
    # ------------------------------------------------------------------

    def execute_decision(
        self,
        decision_id: int,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute a pending PM decision by id.

        Translates the stored decision into a Signal and submits it through
        the TradingEngine. After execution the decision row is updated to
        ``status='executed'`` with the resulting ``signal_id`` / ``order_id``.

        Currently supports ``action`` = ``buy`` / ``sell``. Other actions
        (cancel/modify/plan/hold) are rejected with a clear reason.
        """
        if self.trading_engine is None:
            raise RuntimeError("trading_engine not configured")

        db: DatabaseManager = getattr(self.trading_engine, "db", None) or get_db()
        with db.session_scope() as session:
            row = session.execute(
                select(PaperDecision).where(PaperDecision.id == decision_id)
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"Decision id={decision_id} not found")

            acct_id = int(account_id) if account_id else row.account_id
            if row.account_id != acct_id:
                raise ValueError(
                    f"Decision id={decision_id} belongs to account_id={row.account_id}, "
                    f"not {acct_id}"
                )
            if row.status != "pending":
                raise ValueError(
                    f"Decision id={decision_id} status={row.status}, cannot execute"
                )
            if row.action not in ("buy", "sell"):
                raise ValueError(
                    f"Decision action={row.action} is not executable (buy/sell only)"
                )

            params: Dict[str, Any] = {}
            if row.params_json:
                try:
                    parsed = json.loads(row.params_json)
                    if isinstance(parsed, dict):
                        params = parsed
                except (ValueError, TypeError):
                    params = {}

            # Resolve order type and price.
            order_type_str = str(params.get("order_type") or "limit").strip().lower()
            order_type = OrderType.LIMIT if order_type_str == "limit" else OrderType.MARKET
            limit_price = params.get("limit_price")
            entry_price = params.get("entry_price")
            trigger_price = params.get("trigger_price")

            if order_type == OrderType.LIMIT:
                ref_price = float(limit_price or entry_price or trigger_price or 0.0)
            else:
                ref_price = float(trigger_price or entry_price or limit_price or 0.0)
            if ref_price <= 0:
                raise ValueError("Decision has no positive execution price")

            quantity = float(params.get("quantity") or 0.0)
            if quantity <= 0:
                raise ValueError("Decision quantity must be positive")

            from paper_trading.order import OrderSide, OrderType
            from paper_trading.strategies.engine.rule_engine import Signal

            signal = Signal(
                side=OrderSide.BUY if row.action == "buy" else OrderSide.SELL,
                code=row.code or "",
                name=row.name,
                strategy_name="pm_agent_manual_execute",
                rule_name="pm_decision_execute",
                trigger_price=ref_price,
                suggested_quantity=quantity,
                reason=row.reason or f"Manual execution of PM decision {decision_id}",
            )

            result = self.trading_engine.submit_signal(
                account_id=acct_id,
                signal=signal,
                order_type=order_type,
                limit_price=float(limit_price) if limit_price and order_type == OrderType.LIMIT else None,
                quantity_override=quantity,
            )

            # Update SL/TP on resulting position for buys.
            stop_loss = params.get("stop_loss")
            take_profit = params.get("take_profit")
            if (
                result.status == "executed"
                and row.action == "buy"
                and (stop_loss or take_profit)
                and row.code
            ):
                try:
                    self.trading_engine.position_mgr.update_stop_loss_take_profit(
                        account_id=acct_id,
                        code=row.code,
                        stop_loss=float(stop_loss) if stop_loss else None,
                        take_profit=float(take_profit) if take_profit else None,
                    )
                except Exception as exc:
                    logger.warning(
                        "[PortfolioManagerAgent] SL/TP update failed after decision execution: %s",
                        exc,
                    )

            row.status = "executed"
            row.signal_id = result.signal_id
            row.order_id = result.order_id

            logger.info(
                "[PortfolioManagerAgent] Executed decision id=%s action=%s code=%s "
                "signal_id=%s order_id=%s status=%s",
                decision_id, row.action, row.code,
                result.signal_id, result.order_id, result.status,
            )
            return result.to_dict()

    def ignore_decision(
        self,
        decision_id: int,
        account_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Mark a pending PM decision as skipped (human ignored)."""
        db: DatabaseManager = (
            getattr(self.trading_engine, "db", None) or get_db()
            if self.trading_engine is not None
            else get_db()
        )
        with db.session_scope() as session:
            row = session.execute(
                select(PaperDecision).where(PaperDecision.id == decision_id)
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"Decision id={decision_id} not found")

            acct_id = int(account_id) if account_id else row.account_id
            if row.account_id != acct_id:
                raise ValueError(
                    f"Decision id={decision_id} belongs to account_id={row.account_id}, "
                    f"not {acct_id}"
                )
            if row.status != "pending":
                raise ValueError(
                    f"Decision id={decision_id} status={row.status}, cannot ignore"
                )

            row.status = "skipped"
            row.reject_reason = reason or "ignored by user"
            logger.info(
                "[PortfolioManagerAgent] Ignored decision id=%s action=%s code=%s",
                decision_id, row.action, row.code,
            )


# ---------------------------------------------------------------------------
# Paper-trading tool registration
# ---------------------------------------------------------------------------

def register_paper_trading_tools(
    registry: ToolRegistry,
    engine: Any,
    account_id: int,
    reflection_engine: Optional[Any] = None,
) -> None:
    """Register ``paper_trading_*`` tools on the given registry.

    Tools are closures over the TradingEngine + account_id, so each tool
    call automatically targets the PM agent's account. Re-registering a
    tool replaces the handler (idempotent).

    Tools registered:
    - paper_trading_get_account_snapshot
    - paper_trading_get_positions
    - paper_trading_get_open_orders
    - paper_trading_place_order
    - paper_trading_cancel_order
    - paper_trading_modify_order
    - paper_trading_get_recent_reflections (no-op if reflection_engine is None)
    """
    from paper_trading.order import OrderRequest, OrderSide, OrderType

    # ---- Account snapshot ----
    def _handle_account_snapshot(**kwargs) -> dict:
        acct_id = int(kwargs.get("account_id") or account_id)
        try:
            snap = engine.account_mgr.snapshot(acct_id)
            return {
                "account_id": acct_id,
                "cash": float(getattr(snap, "cash", 0.0)),
                "total_assets": float(getattr(snap, "total_assets", 0.0)),
                "net_value": float(getattr(snap, "net_value", 1.0)),
                "return_pct": float(getattr(snap, "return_pct", 0.0)),
                "frozen_cash": float(getattr(snap, "frozen_cash", 0.0)),
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_get_account_snapshot",
        description="Get the current paper-trading account snapshot (cash, total assets, net value, return).",
        parameters=[
            ToolParameter(
                name="account_id", type="integer",
                description="Paper trading account id. If omitted, uses the PM agent's default account.",
                required=False,
            ),
        ],
        handler=_handle_account_snapshot,
        category="action",
    ))

    # ---- Positions ----
    def _handle_get_positions(**kwargs) -> dict:
        acct_id = int(kwargs.get("account_id") or account_id)
        try:
            rows = engine.position_mgr.list_positions(acct_id)
            out = []
            for p in rows:
                out.append({
                    "code": p.code,
                    "name": p.name,
                    "quantity": float(p.quantity),
                    "available_quantity": float(p.available_quantity),
                    "avg_cost": float(p.avg_cost),
                    "last_price": float(p.last_price or 0.0),
                    "stop_loss": float(p.stop_loss or 0.0),
                    "take_profit": float(p.take_profit or 0.0),
                })
            return {"positions": out, "count": len(out)}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_get_positions",
        description="List all open positions in the paper-trading account.",
        parameters=[
            ToolParameter(
                name="account_id", type="integer",
                description="Paper trading account id. If omitted, uses the PM agent's default account.",
                required=False,
            ),
        ],
        handler=_handle_get_positions,
        category="action",
    ))

    # ---- Open orders ----
    def _handle_get_open_orders(**kwargs) -> dict:
        acct_id = int(kwargs.get("account_id") or account_id)
        try:
            from src.storage import PaperOrder
            with engine.db.session_scope() as session:
                rows = session.execute(
                    select(PaperOrder).where(
                        PaperOrder.account_id == acct_id,
                        PaperOrder.status.in_(["pending", "partially_filled"]),
                    ).order_by(PaperOrder.created_at.desc())
                ).scalars().all()
                out = []
                for o in rows:
                    out.append({
                        "order_id": o.id,
                        "code": o.code,
                        "name": o.name,
                        "side": o.side,
                        "order_type": o.order_type,
                        "price": float(o.price) if o.price is not None else None,
                        "quantity": float(o.quantity),
                        "filled_quantity": float(o.filled_quantity or 0.0),
                        "status": o.status,
                        "strategy_name": o.strategy_name,
                        "created_at": o.created_at.isoformat() if o.created_at else None,
                    })
                return {"open_orders": out, "count": len(out)}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_get_open_orders",
        description="List all pending / partially-filled orders in the paper-trading account.",
        parameters=[
            ToolParameter(
                name="account_id", type="integer",
                description="Paper trading account id. If omitted, uses the PM agent's default account.",
                required=False,
            ),
        ],
        handler=_handle_get_open_orders,
        category="action",
    ))

    # ---- Place order ----
    def _handle_place_order(**kwargs) -> dict:
        acct_id = int(kwargs.get("account_id") or account_id)
        code = str(kwargs.get("code") or "").strip()
        side = str(kwargs.get("side") or "").strip().lower()
        quantity = float(kwargs.get("quantity") or 0.0)
        order_type = str(kwargs.get("order_type") or "limit").strip().lower()
        limit_price = kwargs.get("limit_price")
        entry_price = kwargs.get("entry_price")
        trigger_price_kw = kwargs.get("trigger_price")
        name = kwargs.get("name")
        reason = kwargs.get("reason")
        stop_loss = kwargs.get("stop_loss")
        take_profit = kwargs.get("take_profit")

        if not code or side not in ("buy", "sell") or quantity <= 0:
            return {"error": "code, side (buy/sell), quantity are required and quantity must be positive"}

        try:
            # Build a synthetic Signal so we can reuse submit_signal which
            # handles risk checks, fee model, agent review (if any), and
            # persistence.
            from paper_trading.strategies.engine.rule_engine import Signal
            ot = OrderType.LIMIT if order_type == "limit" else OrderType.MARKET
            if ot == OrderType.LIMIT:
                trigger_price = float(
                    limit_price or entry_price or trigger_price_kw or 0.0
                )
            else:
                # Market orders need a valid reference price. Accept
                # entry_price / trigger_price explicitly, or fall back to
                # limit_price for backward compatibility.
                trigger_price = float(
                    trigger_price_kw or entry_price or limit_price or 0.0
                )
            if trigger_price <= 0:
                return {"error": "order requires a positive price"}
            signal = Signal(
                side=side,
                code=code,
                name=name,
                strategy_name="pm_agent",
                rule_name="pm_autonomous",
                trigger_price=trigger_price,
                suggested_quantity=quantity,
                reason=reason or f"PM agent autonomous {side} {quantity} {code}",
            )
            result = engine.submit_signal(
                account_id=acct_id,
                signal=signal,
                order_type=ot,
                limit_price=float(limit_price) if limit_price and ot == OrderType.LIMIT else None,
                quantity_override=quantity,
            )
            # If buy filled and SL/TP provided, update the position.
            if (
                result.status == "executed"
                and side == "buy"
                and (stop_loss or take_profit)
            ):
                try:
                    engine.position_mgr.update_stop_loss_take_profit(
                        account_id=acct_id,
                        code=code,
                        stop_loss=float(stop_loss) if stop_loss else None,
                        take_profit=float(take_profit) if take_profit else None,
                    )
                except Exception as exc:
                    logger.warning(
                        "[paper_trading_place_order] SL/TP update failed: %s", exc
                    )
            return result.to_dict()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_place_order",
        description="Place a buy or sell order in the paper-trading account. "
                    "Risk checks (cash sufficiency, position availability, concentration) are enforced.",
        parameters=[
            ToolParameter(name="code", type="string", description="Stock code, e.g. '600519' or 'AAPL'.", required=True),
            ToolParameter(name="side", type="string", description="Order side.", enum=["buy", "sell"], required=True),
            ToolParameter(name="quantity", type="number", description="Number of shares (must be positive).", required=True),
            ToolParameter(name="order_type", type="string", description="Order type. Default 'limit' to control execution price; use 'market' only for urgent stop-loss/take-profit exits.", enum=["market", "limit"], required=False, default="limit"),
            ToolParameter(name="limit_price", type="number", description="Required for limit orders. For limit orders without explicit limit_price, entry_price is used as fallback. Ignored for market orders.", required=False),
            ToolParameter(name="entry_price", type="number", description="Reference price for market orders (required when order_type='market' and no trigger_price).", required=False),
            ToolParameter(name="trigger_price", type="number", description="Alias for entry_price; used as the market order reference price if entry_price is omitted.", required=False),
            ToolParameter(name="name", type="string", description="Stock display name (optional).", required=False),
            ToolParameter(name="reason", type="string", description="Reason for the order (optional, recorded for audit).", required=False),
            ToolParameter(name="stop_loss", type="number", description="Stop-loss price to attach to the resulting position (buys only, optional).", required=False),
            ToolParameter(name="take_profit", type="number", description="Take-profit price to attach to the resulting position (buys only, optional).", required=False),
            ToolParameter(name="account_id", type="integer", description="Account id. If omitted, uses the PM agent's default.", required=False),
        ],
        handler=_handle_place_order,
        category="action",
    ))

    # ---- Cancel order ----
    def _handle_cancel_order(**kwargs) -> dict:
        acct_id = int(kwargs.get("account_id") or account_id)
        order_id = int(kwargs.get("order_id") or 0)
        if order_id <= 0:
            return {"error": "order_id is required"}
        try:
            # OrderManager.cancel_order is added in P0-C; for now use a
            # graceful fallback if the method is missing.
            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": "cancel_order not implemented on OrderManager yet (P0-C pending)"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_cancel_order",
        description="Cancel a pending paper-trading order by id.",
        parameters=[
            ToolParameter(name="order_id", type="integer", description="The id of the order to cancel.", required=True),
            ToolParameter(name="account_id", type="integer", description="Account id. If omitted, uses the PM agent's default.", required=False),
        ],
        handler=_handle_cancel_order,
        category="action",
    ))

    # ---- Modify order ----
    def _handle_modify_order(**kwargs) -> dict:
        order_id = int(kwargs.get("order_id") or 0)
        new_price = kwargs.get("new_price")
        new_quantity = kwargs.get("new_quantity")
        if order_id <= 0:
            return {"error": "order_id is required"}
        try:
            if hasattr(engine.order_mgr, "modify_order"):
                row = engine.order_mgr.modify_order(
                    order_id,
                    new_price=float(new_price) if new_price else None,
                    new_quantity=float(new_quantity) if new_quantity else None,
                )
                # row is the replacement order (new id assigned by modify_order).
                # Return the NEW order_id so callers can track the replacement,
                # and include original_order_id for audit linkage.
                return {
                    "status": "modified",
                    "order_id": int(getattr(row, "id", 0) or 0),
                    "original_order_id": order_id,
                    "code": row.code,
                }
            return {"error": "modify_order not implemented on OrderManager yet (P0-C pending)"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_modify_order",
        description="Modify the price or quantity of a pending paper-trading order.",
        parameters=[
            ToolParameter(name="order_id", type="integer", description="The id of the order to modify.", required=True),
            ToolParameter(name="new_price", type="number", description="New limit price (optional).", required=False),
            ToolParameter(name="new_quantity", type="number", description="New quantity (optional).", required=False),
        ],
        handler=_handle_modify_order,
        category="action",
    ))

    # ---- Recent reflections ----
    def _handle_get_recent_reflections(**kwargs) -> dict:
        limit = int(kwargs.get("limit") or 5)
        if reflection_engine is None:
            return {"reflections": [], "note": "reflection engine not configured"}
        try:
            notes = reflection_engine.get_recent_notes(limit=limit)
            out = []
            for n in notes:
                out.append({
                    "created_at": getattr(n, "created_at", None).isoformat()
                                  if getattr(n, "created_at", None) else None,
                    "scope": getattr(n, "scope", None),
                    "subject": getattr(n, "subject", None),
                    "takeaway": getattr(n, "takeaway", None) or getattr(n, "summary", None),
                    "lessons": getattr(n, "lessons", None),
                })
            return {"reflections": out, "count": len(out)}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_get_recent_reflections",
        description="Fetch recent reflection notes from the AI reflection system. "
                    "Use these to avoid repeating past mistakes and to reinforce winning patterns.",
        parameters=[
            ToolParameter(name="limit", type="integer", description="Max number of notes to return (default 5).", required=False, default=5),
        ],
        handler=_handle_get_recent_reflections,
        category="data",
    ))

    # ---- Compute SLTP (smart stop-loss/take-profit) ----
    def _handle_compute_sltp(**kwargs) -> dict:
        """Compute the three-line exit plan for a position (P1-A gap fill)."""
        try:
            from paper_trading.sltp_calculator import build_sltp_calculator
            calc = build_sltp_calculator(data_provider=None)
            # We need entry_price and code; fallback to current market price if needed
            entry_price = float(kwargs.get("entry_price", 0.0))
            code = str(kwargs.get("code", "")).strip()
            if entry_price <= 0 or not code:
                return {"error": "entry_price and code required"}
            result = calc.compute(code=code, entry_price=entry_price)
            return {
                "stop_loss": result.stop_loss,
                "take_profit_1": result.take_profit_1,
                "take_profit_2": result.take_profit_2,
                "entry_price": result.entry_price,
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    registry.register(ToolDefinition(
        name="paper_trading_compute_sltp",
        description="Compute the smart stop-loss/take-profit three-line exit plan for a stock.",
        parameters=[
            ToolParameter(name="code", type="string", description="Stock code (e.g., 600519)", required=True),
            ToolParameter(name="entry_price", type="number", description="Entry price for calculating SL/TP", required=True),
        ],
        handler=_handle_compute_sltp,
        category="data",
    ))

    logger.info(
        "[paper_trading_tools] Registered 8 tools for account_id=%s",
        account_id,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_portfolio_manager_agent(
    config: Optional[Any] = None,
    account_id: int = 0,
    trading_engine: Optional[Any] = None,
    reflection_engine: Optional[Any] = None,
    skills: Optional[List[str]] = None,
    timeout_seconds: Optional[float] = None,
) -> PortfolioManagerAgent:
    """Build a PortfolioManagerAgent wired to a TradingEngine.

    Args:
        config: Application config. If None, ``get_config()`` is called.
        account_id: Paper-trading account id the agent will manage.
        trading_engine: TradingEngine instance for order placement.
        reflection_engine: Optional ReflectionEngine for memory injection.
        skills: Skill ids to activate (defaults to DEFAULT_AGENT_SKILLS).
        timeout_seconds: Decision timeout. If None, reads from config
            ``paper_trading_pm_timeout_seconds`` or defaults to 240.

    Returns:
        A configured :class:`PortfolioManagerAgent` (executor is built lazily).
    """
    if config is None:
        from src.config import get_config
        config = get_config()

    if timeout_seconds is None:
        timeout_seconds = float(
            getattr(config, "paper_trading_pm_timeout_seconds", 240.0) or 240.0
        )

    return PortfolioManagerAgent(
        config=config,
        skills=skills,
        trading_engine=trading_engine,
        reflection_engine=reflection_engine,
        account_id=account_id,
        timeout_seconds=timeout_seconds,
        fallback_action=getattr(config, "paper_trading_pm_fallback_action", "hold"),
        max_retries=int(getattr(config, "paper_trading_pm_max_retries", 0)),
    )
