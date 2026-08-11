# -*- coding: utf-8 -*-
"""Agent risk-control layer for paper trading.

Wraps the existing ``build_agent_executor`` factory to provide secondary
confirmation of programmatic signals emitted by ``strategies_v2``. The
agent has access to the same tools as the analysis pipeline (realtime
quotes, news search, technical analysis) so it can sanity-check a signal
against current market context before allowing the TradingEngine to
execute.

Flow:
    Signal (from rule engine)
        -> AgentRiskReviewer.review_signal(signal, context)
        -> AgentReviewResult(approved, reason, confidence, raw_response)
        -> TradingEngine proceeds or rejects

Design notes:
- Reuses ``build_agent_executor`` (no new agent infrastructure).
- The agent is given a focused review prompt and asked to return a JSON
  verdict. The verdict is parsed leniently (json_repair) so partial /
  malformed responses still yield a usable decision.
- On timeout / error / unparseable response, falls back to a configurable
  default (pass-through by default) so the trading loop is never blocked
  by agent unavailability.
- Persisted to ``PaperSignal.agent_confirmed`` / ``agent_reason`` /
  ``reviewed_at`` by the TradingEngine.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from json_repair import repair_json

logger = logging.getLogger(__name__)


# Default review prompt template. The agent is instructed to act as a
# risk-control reviewer, not as an analyst — its only job is to confirm
# or veto the signal based on current market context.
REVIEW_PROMPT_TEMPLATE = """你是模拟交易系统的风控审核 Agent。一个程序化策略刚刚发出了交易信号,请基于当前市场情况判断是否批准执行。

## 待审核信号

- 动作: {side_label} {qty} 股 {code} {name}
- 触发价格: {trigger_price:.4f}
- 策略: {strategy_name}
- 规则匹配: {rule_name}
- 触发原因: {reason}

## 当前账户状态

- 现金: {cash:.2f} CNY
- 总资产: {total_assets:.2f} CNY
- 持仓数量: {open_positions}
- 该股当前持仓: {current_position}

## 审核要点

请使用可用工具(get_realtime_quote / search_stock_news / analyze_trend 等)获取当前行情与情报,然后判断:

1. **价格合理性**: 触发价是否在合理区间? 是否在追高?(乖离率 > 5% 视为追高)
2. **趋势一致性**: 当前趋势是否与信号方向一致?(多头排列 / 空头排列)
3. **风险信号**: 是否有重大利空(减持、业绩预警、停牌风险)?
4. **仓位合理性**: 此笔交易是否会让单股集中度过高或现金过紧?

## 输出格式(严格 JSON)

只输出以下 JSON,不要附加任何解释文字:

{{
  "approved": true 或 false,
  "confidence": 0.0 到 1.0,
  "reason": "简短理由(50字以内)",
  "concerns": ["风险点1", "风险点2"],
  "action": "approve | reject | cancel | modify | sell | hold",
  "code": "股票代码(可选,当action为cancel/modify/sell时必填)",
  "stop_loss": 0.0,
  "take_profit": 0.0
}}
"""


@dataclass
class AgentReviewResult:
    """Outcome of an agent risk review."""

    approved: bool
    reason: str
    confidence: float = 0.0
    concerns: List[str] = field(default_factory=list)
    raw_response: Optional[str] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    used_fallback: bool = False
    action: str = "approve"  # approve / reject / cancel / modify / sell / hold
    code: Optional[str] = None
    quantity: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "confidence": self.confidence,
            "concerns": self.concerns,
            "raw_response": self.raw_response,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
            "used_fallback": self.used_fallback,
        }


class AgentRiskReviewer:
    """Secondary confirmation layer using the existing agent factory.

    The reviewer is intentionally optional — when not configured on the
    TradingEngine, signals flow through unchanged. When configured, every
    signal is sent to the agent for a yes/no review before execution.
    """

    def __init__(
        self,
        executor: Optional[Any] = None,
        config: Optional[Any] = None,
        skills: Optional[List[str]] = None,
        timeout_seconds: float = 180.0,
        fallback_on_failure: bool = True,
        fallback_decision: bool = True,
        max_retries: int = 0,
    ):
        """Initialize the reviewer.

        Args:
            executor: Pre-built AgentExecutor. If None, one is built lazily
                on first review via ``build_agent_executor(config, skills)``.
            config: Application config (for build_agent_executor).
            skills: Skill ids to activate (defaults to DEFAULT_AGENT_SKILLS).
            timeout_seconds: Hard cap on agent review duration.
            fallback_on_failure: If True, return a fallback decision when
                the agent fails/times out. If False, raise the exception.
            fallback_decision: Decision to return on fallback (default True
                = pass-through, do not block trading on agent issues).
            max_retries: Number of retry attempts on agent failure.
        """
        self._executor = executor
        self._config = config
        self._skills = skills
        self.timeout_seconds = float(timeout_seconds)
        self.fallback_on_failure = bool(fallback_on_failure)
        self.fallback_decision = bool(fallback_decision)
        self.max_retries = int(max_retries)

    # ------------------------------------------------------------------
    # Lazy executor construction
    # ------------------------------------------------------------------

    @property
    def executor(self):
        """Lazily build the AgentExecutor on first use."""
        if self._executor is None:
            from src.agent.factory import build_agent_executor
            self._executor = build_agent_executor(self._config, skills=self._skills)
            logger.info(
                "[AgentRiskReviewer] Executor built (timeout=%ss fallback=%s)",
                self.timeout_seconds, self.fallback_decision,
            )
        return self._executor

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def review_signal(
        self,
        signal: Any,
        account_snapshot: Optional[Any] = None,
        position: Optional[Any] = None,
    ) -> AgentReviewResult:
        """Ask the agent to confirm or veto a programmatic signal.

        Args:
            signal: A ``strategies_v2.rule_engine.Signal`` (or compatible
                object with side/code/name/strategy_name/rule_name/
                trigger_price/suggested_quantity/reason attributes).
            account_snapshot: Optional ``AccountSnapshot`` for context.
            position: Optional ``PaperPosition`` for the same code
                (relevant for sell signals and add-on buys).

        Returns:
            AgentReviewResult with approved=True/False and a reason.
        """
        prompt = self._build_prompt(signal, account_snapshot, position)
        session_id = f"paper_risk_{uuid.uuid4().hex[:12]}"

        import time
        start = time.time()
        attempts = self.max_retries + 1
        last_error: Optional[str] = None

        for attempt in range(attempts):
            try:
                raw_text = self._call_agent_with_timeout(prompt, session_id)
                result = self._parse_verdict(raw_text)
                result.elapsed_seconds = time.time() - start
                if result.used_fallback:
                    logger.warning(
                        "[AgentRiskReviewer] Fallback used for %s %s: %s",
                        signal.side, signal.code, result.reason,
                    )
                else:
                    logger.info(
                        "[AgentRiskReviewer] %s %s -> approved=%s confidence=%.2f (%.1fs)",
                        signal.side, signal.code, result.approved,
                        result.confidence, result.elapsed_seconds,
                    )
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[AgentRiskReviewer] attempt %s/%s failed for %s: %s",
                    attempt + 1, attempts, signal.code, last_error,
                )
                continue

        # All attempts failed.
        elapsed = time.time() - start
        if self.fallback_on_failure:
            logger.error(
                "[AgentRiskReviewer] All attempts failed for %s, using fallback=%s: %s",
                signal.code, self.fallback_decision, last_error,
            )
            return AgentReviewResult(
                approved=self.fallback_decision,
                reason=f"agent unavailable, fallback={self.fallback_decision}",
                confidence=0.0,
                error=last_error,
                elapsed_seconds=elapsed,
                used_fallback=True,
            )
        raise RuntimeError(f"Agent review failed and fallback disabled: {last_error}")

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        signal: Any,
        account_snapshot: Optional[Any],
        position: Optional[Any],
    ) -> str:
        side_label = "买入" if signal.side == "buy" else "卖出" if signal.side == "sell" else signal.side
        qty = signal.suggested_quantity if signal.suggested_quantity else "全部可用"
        name = signal.name or ""
        if name:
            name = f"({name})"

        if account_snapshot is not None:
            cash = float(account_snapshot.cash)
            total_assets = float(account_snapshot.total_assets)
            open_positions = len(getattr(account_snapshot, "config", {}) or {})
            # AccountSnapshot doesn't carry open_positions directly; use 0 as
            # a placeholder — the agent can call tools to inspect positions.
            open_positions = 0
        else:
            cash = 0.0
            total_assets = 0.0
            open_positions = 0

        if position is not None and float(getattr(position, "quantity", 0) or 0) > 0:
            current_position = (
                f"{float(position.quantity):.0f} 股, "
                f"成本 {float(position.avg_cost):.4f}, "
                f"可用 {float(position.available_quantity):.0f}"
            )
        else:
            current_position = "无持仓"

        return REVIEW_PROMPT_TEMPLATE.format(
            side_label=side_label,
            qty=qty,
            code=signal.code,
            name=name,
            trigger_price=float(signal.trigger_price or 0.0),
            strategy_name=signal.strategy_name or "(unknown)",
            rule_name=signal.rule_name or "(unspecified)",
            reason=signal.reason or "",
            cash=cash,
            total_assets=total_assets,
            open_positions=open_positions,
            current_position=current_position,
        )

    # ------------------------------------------------------------------
    # Agent invocation (with timeout)
    # ------------------------------------------------------------------

    def _call_agent_with_timeout(self, prompt: str, session_id: str) -> str:
        """Call the agent's chat() with a hard timeout.

        The agent's chat() is synchronous and may take minutes; we run it
        in a worker thread and enforce a timeout. On timeout we return an
        empty string (which the parser will turn into a fallback result).
        """
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
            # Thread is still running — we cannot kill it (Python limitation),
            # but we abandon it and return a fallback.
            logger.warning(
                "[AgentRiskReviewer] Agent timed out after %ss, abandoning",
                self.timeout_seconds,
            )
            raise TimeoutError(
                f"agent review exceeded {self.timeout_seconds}s timeout"
            )

        if not result_holder.get("success", False):
            err = result_holder.get("error") or "agent returned failure"
            raise RuntimeError(err)
        return str(result_holder.get("content", ""))

    # ------------------------------------------------------------------
    # Verdict parsing
    # ------------------------------------------------------------------

    def _parse_verdict(self, raw_text: str) -> AgentReviewResult:
        """Parse the agent's JSON verdict leniently.

        Falls back to keyword-based detection if the JSON is malformed:
        - Contains "approved" or "approve" / "确认" / "批准" -> approved=True
        - Contains "veto" / "reject" / "否决" / "拒绝" -> approved=False
        """
        if not raw_text or not raw_text.strip():
            return AgentReviewResult(
                approved=self.fallback_decision,
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
            # Try to extract a JSON object from the text.
            try:
                fixed = repair_json(raw_text, return_objects=True)
                if isinstance(fixed, dict):
                    verdict = fixed
                else:
                    # repair_json returned a string; try parsing again.
                    verdict = json.loads(fixed)
            except Exception:
                verdict = None

        if isinstance(verdict, dict) and "approved" in verdict:
            approved = bool(verdict.get("approved"))
            reason = str(verdict.get("reason") or "")[:200]
            try:
                confidence = float(verdict.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            concerns_raw = verdict.get("concerns") or []
            if isinstance(concerns_raw, list):
                concerns = [str(c) for c in concerns_raw][:5]
            else:
                concerns = [str(concerns_raw)]
            # Parse action with validation against the allowed set.
            valid_actions = {"approve", "reject", "cancel", "modify", "sell", "hold"}
            raw_action = str(verdict.get("action") or "").strip().lower()
            if raw_action and raw_action in valid_actions:
                action = raw_action
            else:
                action = "approve" if approved else "reject"
            code_val = verdict.get("code")
            code = str(code_val) if code_val is not None and str(code_val).strip() else None
            try:
                stop_loss = float(verdict.get("stop_loss")) if verdict.get("stop_loss") is not None else None
            except (TypeError, ValueError):
                stop_loss = None
            try:
                take_profit = float(verdict.get("take_profit")) if verdict.get("take_profit") is not None else None
            except (TypeError, ValueError):
                take_profit = None
            try:
                quantity = float(verdict.get("quantity")) if verdict.get("quantity") is not None else None
            except (TypeError, ValueError):
                quantity = None
            return AgentReviewResult(
                approved=approved,
                reason=reason or ("approved by agent" if approved else "vetoed by agent"),
                confidence=confidence,
                concerns=concerns,
                raw_response=raw_text,
                action=action,
                code=code,
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        # Fallback: keyword detection on the raw text.
        text_lower = raw_text.lower()
        if any(kw in text_lower for kw in ("approved", "approve", "confirm")):
            approved = True
        elif any(kw in text_lower for kw in ("veto", "reject", "deny", "否决", "拒绝")):
            approved = False
        else:
            # Cannot determine — apply fallback.
            return AgentReviewResult(
                approved=self.fallback_decision,
                reason="unparseable agent response, fallback applied",
                confidence=0.0,
                raw_response=raw_text,
                used_fallback=True,
            )

        return AgentReviewResult(
            approved=approved,
            reason="inferred from keyword (JSON parse failed)",
            confidence=0.5,
            raw_response=raw_text,
        )
