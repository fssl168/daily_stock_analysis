# -*- coding: utf-8 -*-
"""Hooks for integrating paper trading signals with external analysis results.

This module provides utility functions that can be called from outside the
analysis pipeline to push AI-generated trading signals into the paper trading
system, without needing to modify the core analysis code itself.

Usage example:
    from paper_trading.hooks import push_ai_signal_from_decision

    def handle_stock_result(stock_code, result):
        # After analyzing a stock and getting an AnalysisResult object
        if result.ai_decision and result.ai_decision.is_clear_trade_signal():
            push_ai_signal_from_decision(result.ai_decision)
"""

from __future__ import annotations

import logging
from typing import Optional, Any

from src.paper_trading_signal_queue import AIAnalysisSignal, init_signal_queue

logger = logging.getLogger(__name__)


def push_ai_signal_from_decision(decision: Any) -> None:
    """
    Push an AI decision as a trading signal into the paper trading system.

    Args:
        decision: An object representing the AI's trading decision. It should have at least
            the following attributes:
            - code: str (stock code)
            - side: str ("buy" or "sell")
            - name: str (stock name)
            - trigger_price: float (entry price)
            - suggested_quantity: Optional[float] (optional)
            - reason: str (explanation of the decision)
            - confidence: Optional[float] (optional, default 1.0)

    The signal is only pushed if:
    - Paper trading is enabled (paper_trading_enabled=True)
    - AI signal source is enabled (paper_trading_enable_ai_signal_source=True)
    - Confidence meets minimum threshold (paper_trading_ai_signal_min_confidence)
    - The side is either "buy" or "sell"
    """
    # Get config to check settings
    from src.config import get_config
    try:
        cfg = get_config()
    except Exception as exc:
        logger.warning("Failed to get config for AI signal push: %s", exc)
        return

    # Check if AI signal source is enabled
    if not getattr(cfg, "paper_trading_enable_ai_signal_source", False):
        return

    # Validate required attributes
    required_attrs = ["code", "side", "name", "trigger_price", "reason"]
    for attr in required_attrs:
        if not hasattr(decision, attr):
            logger.warning("Decision missing required attribute '%s', skipping push", attr)
            return

    # Check side
    side = decision.side
    if side not in ("buy", "sell"):
        logger.warning("Invalid decision side '%s', must be 'buy' or 'sell'", side)
        return

    # Check confidence threshold
    confidence = getattr(decision, "confidence", 1.0)
    min_conf = getattr(cfg, "paper_trading_ai_signal_min_confidence", 0.7)
    if confidence < min_conf:
        logger.debug(
            "Decision confidence %.2f below threshold %.2f, skipping push",
            confidence, min_conf,
        )
        return

    # Build the signal
    signal = AIAnalysisSignal(
        code=decision.code,
        side=side,
        name=decision.name,
        trigger_price=float(decision.trigger_price),
        suggested_quantity=getattr(decision, "suggested_quantity", None),
        reason=str(decision.reason),
        strategy_name="ai_decision_hook",
        confidence=float(confidence),
    )

    # Push to queue
    try:
        q = init_signal_queue()
        success = q.push(signal)
        if success:
            logger.info(
                "AI signal pushed to queue: %s %s (confidence=%.2f)",
                side, decision.code, confidence,
            )
        else:
            logger.warning("AI signal dropped due to full queue")
    except Exception as exc:
        logger.error("Failed to push AI signal: %s", exc)


def init_paper_trading_signal_queue(maxsize: int = 1000) -> None:
    """Initialize the global signal queue. Call this once at application startup."""
    from src.paper_trading_signal_queue import init_signal_queue as inner_init
    inner_init(maxsize=maxsize)
