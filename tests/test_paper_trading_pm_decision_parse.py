# -*- coding: utf-8 -*-
"""T-11 tests: PM decision parsing must not present low-confidence
keyword inference as a real decision.

Root cause: ``_parse_decision``'s keyword-detection fallback returned
``used_fallback=False`` (e.g. "inferred from keyword '买入' (JSON parse failed)")
with confidence 0.3 — recorded as if it were a real decision.
"""

from __future__ import annotations

import json

from src.agent.portfolio_manager_agent import PortfolioManagerAgent


def _agent() -> PortfolioManagerAgent:
    return PortfolioManagerAgent(config=None, account_id=1, fallback_action="hold")


def test_strict_json_parsed_not_fallback():
    d = _agent()._parse_decision(
        json.dumps(
            {"action": "buy", "code": "600000", "confidence": 0.8, "reason": "ok"},
            ensure_ascii=False,
        )
    )
    assert d.used_fallback is False
    assert d.action == "buy"
    assert d.confidence == 0.8


def test_keyword_inference_marked_fallback():
    d = _agent()._parse_decision("综合分析后我认为应该买入该股票，理由如下：趋势向好")
    assert d.used_fallback is True  # 不再被当作真实决策
    assert d.action == "buy"
    assert d.confidence <= 0.3
    assert "fallback" in d.reason.lower()


def test_empty_response_fallback():
    d = _agent()._parse_decision("")
    assert d.used_fallback is True
    assert d.action == "hold"
    assert d.confidence == 0.0


def test_unparseable_response_fallback():
    d = _agent()._parse_decision("asdfghjkl no keywords here at all")
    assert d.used_fallback is True
    assert d.action == "hold"
