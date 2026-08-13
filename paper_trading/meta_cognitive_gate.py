# -*- coding: utf-8 -*-
"""L4 元认知信号闸门 — 让认知偏差检测影响交易决策。

在信号提交给 TradingEngine 之前，用 L4 元认知引擎（BiasDetector /
CircularityDetector）检测信号背后的认知偏差，并按偏差类型调节信号：

- OVERCONFIDENCE（过度自信）: 决策置信度高但证据不足 → 仓位 ×0.5
- CONFIRMATION（确认偏差）  : 只找支持自己的信息 → 仓位 ×0.7
- ANCHORING（锚定）         : 过度依赖初始参考价 → 仓位 ×0.7
- RECENCY（近因偏差）       : 只看近期忽略长期 → 仓位 ×0.8
- FRAMING（框架效应）       : 表述方式影响判断 → 仓位 ×0.8
- 多个偏差叠加               → 仓位 ×0.3（接近过滤）
- 严重偏差（含循环论证）     → 直接过滤（不放行）

Usage:
    from paper_trading.meta_cognitive_gate import L4SignalGate, L4GateResult
    gate = L4SignalGate()
    verdict = gate.evaluate(signal, price=..., code=...)
    if verdict.allowed:
        engine.submit_signal(..., signal=verdict.adjusted_signal)
    else:
        logger.info("L4 blocked: %s", verdict.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 各偏差类型的仓位调节系数
_BIAS_QUANTITY_FACTOR: Dict[str, float] = {
    "overconfidence": 0.5,
    "confirmation": 0.7,
    "anchoring": 0.7,
    "recency": 0.8,
    "framing": 0.8,
}
# 严重偏差：直接过滤
_BLOCKING_BIASES = frozenset({"circularity"})


@dataclass
class L4GateResult:
    """L4 闸门判定结果。"""

    allowed: bool                          # 是否放行
    adjusted_signal: Optional[Any] = None  # 调节后的信号（None = 原信号）
    quantity_factor: float = 1.0           # 仓位调节系数
    biases: List[str] = field(default_factory=list)   # 检测到的偏差
    findings: List[Any] = field(default_factory=list) # 完整 BiasFinding
    reason: str = ""                       # 说明


class L4SignalGate:
    """将 L4 元认知检测接入交易信号流。

    对每个信号构造 CognitiveEpisode（信号置信度 / 参考信号数 /
    推理步骤方向），跑 BiasDetector 检测，按偏差调节或过滤。
    """

    def __init__(self, auto_reflect: bool = True) -> None:
        from src.services.meta_cognitive import (
            BiasDetector,
            CircularityDetector,
            CognitiveEpisode,
        )

        self._bias_detector = BiasDetector()
        self._circularity_detector = CircularityDetector()
        self._episode_cls = CognitiveEpisode

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def evaluate(
        self,
        signal: Any,
        *,
        code: str = "",
        price: float = 0.0,
        market: str = "cn",
        signals_considered: int = 0,
        signals_dismissed: int = 0,
        confidence: Optional[float] = None,
        reasoning_steps: Optional[List[Dict[str, Any]]] = None,
        all_episodes: Optional[List[Any]] = None,
    ) -> L4GateResult:
        """评估一个信号是否放行，以及如何调节。"""
        try:
            episode = self._build_episode(
                signal=signal,
                code=code or getattr(signal, "code", ""),
                market=market,
                signals_considered=signals_considered,
                signals_dismissed=signals_dismissed,
                confidence=confidence,
                reasoning_steps=reasoning_steps,
            )
            findings = self._bias_detector.detect(episode, all_episodes or [])

            # 循环论证检测（严重偏差）
            circularity = self._circularity_detector.detect()
            if circularity is not None:
                findings = findings or []
                # 循环论证视为阻断级
                return self._block(
                    signal,
                    biases=["circularity"],
                    reason=f"L4 circularity detected: {circularity.pattern[:120]}",
                )

            if not findings:
                return L4GateResult(
                    allowed=True,
                    adjusted_signal=signal,
                    quantity_factor=1.0,
                    biases=[],
                    findings=[],
                    reason="L4 clear",
                )

            # 有偏差 → 计算调节
            bias_names = [f.bias_type.value for f in findings]
            factor = self._combined_factor(bias_names)
            adjusted = self._adjust_quantity(signal, factor)

            if factor <= 0.3:
                return self._block(
                    signal,
                    biases=bias_names,
                    reason=f"L4 multiple biases ({bias_names}) -> blocked",
                )

            return L4GateResult(
                allowed=True,
                adjusted_signal=adjusted,
                quantity_factor=factor,
                biases=bias_names,
                findings=findings,
                reason=f"L4 bias(s) {bias_names} -> quantity x{factor:.1f}: "
                       f"{[f.suggestion for f in findings][:1]}",
            )
        except Exception as exc:  # noqa: BLE001 — 闸门失败必须放行（fail-open）
            logger.warning("L4SignalGate evaluate failed (fail-open): %s", exc)
            return L4GateResult(
                allowed=True,
                adjusted_signal=signal,
                quantity_factor=1.0,
                biases=[],
                findings=[],
                reason=f"L4 gate error (fail-open): {exc}",
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _build_episode(
        self,
        *,
        signal: Any,
        code: str,
        market: str,
        signals_considered: int,
        signals_dismissed: int,
        confidence: Optional[float],
        reasoning_steps: Optional[List[Dict[str, Any]]],
    ) -> Any:
        import time
        import hashlib

        ep_id = f"ep_{int(time.time())}_{hashlib.md5(str(code).encode()).hexdigest()[:6]}"
        conf = (
            confidence
            if confidence is not None
            else self._signal_confidence(signal)
        )
        steps = reasoning_steps or self._default_reasoning_steps(signal, conf)

        ep = self._episode_cls(
            episode_id=ep_id,
            stock_code=code,
            market="A" if market in ("cn", "A") else market.upper(),
            action=getattr(signal, "side", "hold"),
            decision_confidence=conf,
            signals_considered=signals_considered,
            signals_dismissed=signals_dismissed,
            reasoning_steps=steps,
        )
        return ep

    @staticmethod
    def _signal_confidence(signal: Any) -> float:
        """从信号推断置信度（Signal 无 confidence 字段，用 reason 里的 conf= 解析）。"""
        reason = str(getattr(signal, "reason", "") or "")
        import re

        m = re.search(r"conf=([0-9.]+)", reason)
        if m:
            try:
                return min(1.0, max(0.0, float(m.group(1))))
            except ValueError:
                pass
        # 融合信号默认中高置信度；策略信号默认 0.7
        return 0.7

    @staticmethod
    def _default_reasoning_steps(signal: Any, confidence: float) -> List[Dict[str, Any]]:
        """构造最小推理步骤（含方向性，供确认偏差检测）。"""
        side = getattr(signal, "side", "hold")
        direction = "supporting" if side in ("buy", "sell") else "neutral"
        return [
            {
                "step": 1,
                "type": "verdict",
                "thought": f"{side} signal from {getattr(signal, 'strategy_name', '?')}",
                "sources": [getattr(signal, "strategy_name", "")],
                "confidence": confidence,
                "duration_ms": 0.0,
                "direction": direction,
            }
        ]

    @staticmethod
    def _combined_factor(bias_names: List[str]) -> float:
        """多偏差叠加：逐个相乘，但至少保留 0.3。"""
        factor = 1.0
        for name in bias_names:
            factor *= _BIAS_QUANTITY_FACTOR.get(name, 1.0)
        return max(0.3, min(1.0, factor))

    @staticmethod
    def _adjust_quantity(signal: Any, factor: float) -> Any:
        """按系数调节信号仓位。若 Signal 不可变则返回原信号（由调用方处理）。"""
        if factor >= 1.0:
            return signal
        try:
            qty = getattr(signal, "suggested_quantity", None)
            if qty:
                from paper_trading.strategies import Signal

                return Signal(
                    side=getattr(signal, "side"),
                    code=getattr(signal, "code"),
                    name=getattr(signal, "name"),
                    strategy_name=getattr(signal, "strategy_name"),
                    rule_name=getattr(signal, "rule_name"),
                    trigger_price=getattr(signal, "trigger_price"),
                    suggested_quantity=float(qty) * factor,
                    reason=f"{getattr(signal, 'reason', '')} | L4_adj x{factor:.1f}",
                )
        except Exception as exc:
            logger.debug("L4 adjust_quantity failed, keep original: %s", exc)
        return signal

    @staticmethod
    def _block(signal: Any, *, biases: List[str], reason: str) -> L4GateResult:
        logger.info("L4 BLOCKED: %s %s (%s)", getattr(signal, "side", "?"),
                    getattr(signal, "code", "?"), reason)
        return L4GateResult(
            allowed=False,
            adjusted_signal=None,
            quantity_factor=0.0,
            biases=biases,
            findings=[],
            reason=reason,
        )
