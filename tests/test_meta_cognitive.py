# -*- coding: utf-8 -*-
"""Tests for src/services/meta_cognitive.py — MetaCognitiveEngine (L4 统一元认知引擎)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    from src.services.meta_cognitive import MetaCognitiveEngine
    return MetaCognitiveEngine(auto_reflect=False)


# ---------------------------------------------------------------------------
# Episode lifecycle
# ---------------------------------------------------------------------------


def test_start_episode(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    assert ep.episode_id.startswith("ep_")
    assert ep.stock_code == "600519"
    assert ep.market == "A"


def test_start_episode_creates_unique_ids(engine):
    ep1 = engine.start_episode(stock_code="600519", market="A")
    time.sleep(0.01)
    ep2 = engine.start_episode(stock_code="600519", market="A")
    assert ep1.episode_id != ep2.episode_id


def test_record_reasoning(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    ok = engine.record_reasoning(
        ep.episode_id,
        step_type="data_gather",
        thought="技术指标显示超卖",
        sources=["rsi_daily", "macd_weekly"],
        direction="supporting",
        confidence=0.8,
        duration_ms=120.0,
    )
    assert ok is True
    assert len(ep.reasoning_steps) == 1
    assert ep.reasoning_steps[0]["type"] == "data_gather"


def test_record_reasoning_nonexistent(engine):
    ok = engine.record_reasoning("no_such_id", thought="test")
    assert ok is False


def test_record_decision(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    ok = engine.record_decision(
        ep.episode_id,
        action="hold",
        confidence=0.75,
        signals_considered=5,
        signals_dismissed=2,
        expected_outcome="横盘整理，持股观望",
    )
    assert ok is True
    assert ep.action == "hold"
    assert ep.decision_confidence == 0.75


def test_end_episode(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_reasoning(ep.episode_id, thought="分析开始", direction="neutral")
    engine.record_decision(ep.episode_id, action="watch", confidence=0.6, signals_considered=3)
    result = engine.end_episode(ep.episode_id)
    assert result is not None
    assert result.ended_at is not None
    assert result.self_awareness_score > 0


def test_get_recent_episodes(engine):
    for code in ["600519", "000001", "hk00700"]:
        ep = engine.start_episode(stock_code=code, market="A")
        engine.record_decision(ep.episode_id, action="hold", confidence=0.5)
        engine.end_episode(ep.episode_id)

    recent = engine.get_recent_episodes(limit=2)
    assert len(recent) == 2


def test_get_episodes_by_stock(engine):
    engine.start_episode(stock_code="600519", market="A")
    engine.start_episode(stock_code="000001", market="A")
    engine.start_episode(stock_code="600519", market="A")

    maotai = engine.get_episodes_by_stock("600519")
    assert len(maotai) == 2
    assert all(e.stock_code == "600519" for e in maotai)


# ---------------------------------------------------------------------------
# Bias detection (via episode flow)
# ---------------------------------------------------------------------------


def test_detect_confirmation_bias(engine):
    """Create an episode with all-supporting reasoning → confirmation bias."""
    ep = engine.start_episode(stock_code="600519", market="A")
    for i in range(4):
        engine.record_reasoning(
            ep.episode_id,
            step_type="analysis",
            thought=f"看涨理由 #{i+1}",
            sources=[f"src_{i}"],
            direction="supporting",
            confidence=0.9,
        )
    engine.record_decision(
        ep.episode_id, action="buy", confidence=0.92,
        signals_considered=4, signals_dismissed=0,
    )
    engine.end_episode(ep.episode_id)

    assert "confirmation" in ep.detected_biases


def test_detect_overconfidence(engine):
    """High confidence + few signals → overconfidence."""
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_reasoning(ep.episode_id, thought="只有一个信号", sources=["rsi"])
    engine.record_decision(
        ep.episode_id, action="buy", confidence=0.95,
        signals_considered=2, signals_dismissed=5,  # dismissed > considered
    )
    engine.end_episode(ep.episode_id)

    assert "overconfidence" in ep.detected_biases


def test_detect_framing_bias(engine):
    """All directional steps same direction → framing bias."""
    ep = engine.start_episode(stock_code="600519", market="A")
    for i in range(5):
        engine.record_reasoning(
            ep.episode_id,
            step_type="analysis",
            thought=f"支持论点 #{i+1}",
            direction="supporting",
        )
    engine.record_decision(ep.episode_id, action="buy", confidence=0.7, signals_considered=5)
    engine.end_episode(ep.episode_id)

    assert "framing" in ep.detected_biases


def test_no_bias_balanced_reasoning(engine):
    """Balanced reasoning should produce no biases."""
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_reasoning(ep.episode_id, thought="支持: RSI", direction="supporting")
    engine.record_reasoning(ep.episode_id, thought="反对: MACD", direction="opposing")
    engine.record_reasoning(ep.episode_id, thought="支持: 量价", direction="supporting")
    engine.record_reasoning(ep.episode_id, thought="反对: 大盘", direction="opposing")
    engine.record_decision(ep.episode_id, action="hold", confidence=0.6, signals_considered=5)
    engine.end_episode(ep.episode_id)

    assert len(ep.detected_biases) == 0


# ---------------------------------------------------------------------------
# Circularity detection
# ---------------------------------------------------------------------------


def test_no_circularity_with_few_episodes(engine):
    result = engine.detect_circularity()
    assert result is None


def test_circularity_with_repetitive_pattern(engine):
    """Create 5+ episodes with identical reasoning patterns."""
    for code in ["600519", "000001", "000002", "000003", "000004"]:
        ep = engine.start_episode(stock_code=code, market="A")
        # Same pattern each time
        for i, (thought, direction) in enumerate([
            ("RSI 超卖", "supporting"),
            ("成交量萎缩", "supporting"),
            ("MACD 金叉", "supporting"),
        ]):
            engine.record_reasoning(
                ep.episode_id,
                step_type="analysis",
                thought=thought,
                sources=["rsi", "volume", "macd"],
                direction=direction,
                confidence=0.8,
            )
        engine.record_decision(ep.episode_id, action="buy", confidence=0.8, signals_considered=3)
        engine.end_episode(ep.episode_id)

    result = engine.detect_circularity()
    assert result is not None
    assert result.similarity_score > 0.5


# ---------------------------------------------------------------------------
# Self-awareness score
# ---------------------------------------------------------------------------


def test_self_awareness_balanced(engine):
    """Balanced reasoning gets a high self-awareness score."""
    ep = engine.start_episode(stock_code="600519", market="A")
    for i in range(3):
        engine.record_reasoning(ep.episode_id, thought=f"支持 {i}", direction="supporting")
    for i in range(2):
        engine.record_reasoning(ep.episode_id, thought=f"反对 {i}", direction="opposing")
    engine.record_decision(
        ep.episode_id, action="hold", confidence=0.65,
        signals_considered=5, signals_dismissed=2,
    )
    engine.end_episode(ep.episode_id)

    assert ep.self_awareness_score > 0.5


def test_self_awareness_bare_minimum(engine):
    """Minimal engagement gets low score."""
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_decision(ep.episode_id, action="no_action", confidence=1.0)
    engine.end_episode(ep.episode_id)

    # No reasoning steps => low score
    assert ep.self_awareness_score < 0.3


# ---------------------------------------------------------------------------
# Introspection / reflection
# ---------------------------------------------------------------------------


def test_force_reflection(engine):
    for code in ["600519", "000001", "hk00700"]:
        ep = engine.start_episode(stock_code=code, market="A")
        engine.record_reasoning(ep.episode_id, thought="分析", direction="supporting")
        engine.record_decision(ep.episode_id, action="hold", confidence=0.6)
        engine.end_episode(ep.episode_id)

    report = engine.force_reflection()
    assert report is not None
    assert report.summary
    assert report.prompt_for_llm
    assert "自我反思" in report.prompt_for_llm


def test_introspection_prompt_empty_when_no_reflection(engine):
    assert engine.get_introspection_prompt() == ""


def test_introspection_prompt_after_reflection(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_decision(ep.episode_id, action="hold", confidence=0.5)
    engine.end_episode(ep.episode_id)

    engine.force_reflection()
    prompt = engine.get_introspection_prompt()
    assert "Meta-Cognitive Introspection" in prompt


# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------


def test_record_outcome(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_decision(ep.episode_id, action="buy", confidence=0.8,
                           expected_outcome="短期上涨 5%")
    engine.end_episode(ep.episode_id)

    ok = engine.record_outcome(ep.episode_id, actual_outcome="实际下跌 3%", deviation=-0.08)
    assert ok is True
    assert ep.actual_outcome == "实际下跌 3%"
    assert ep.outcome_deviation == -0.08


def test_record_outcome_nonexistent(engine):
    ok = engine.record_outcome("no_such_id", "nope")
    assert ok is False


# ---------------------------------------------------------------------------
# get_self_report
# ---------------------------------------------------------------------------


def test_get_self_report(engine):
    for code in ["600519", "000001", "hk00700"]:
        ep = engine.start_episode(stock_code=code, market="A")
        engine.record_reasoning(ep.episode_id, thought=f"分析 {code}", direction="supporting")
        engine.record_decision(ep.episode_id, action="hold", confidence=0.5)
        engine.end_episode(ep.episode_id)

    report = engine.get_self_report()
    assert report["total_decisions"] >= 3
    assert "bias_profile" in report
    assert "self_awareness_avg" in report
    assert report["introspection_available"] is False  # auto_reflect is off

    # After a reflection
    engine.force_reflection()
    report2 = engine.get_self_report()
    assert report2["introspection_available"] is True
    assert report2["reflections_completed"] >= 1


# ---------------------------------------------------------------------------
# Stats interface
# ---------------------------------------------------------------------------


def test_stats(engine):
    s = engine.stats()
    assert isinstance(s, dict)
    assert s["total_episodes"] == 0
    assert s["reflection_count"] == 0

    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_decision(ep.episode_id, action="hold", confidence=0.5)
    engine.end_episode(ep.episode_id)

    s2 = engine.stats()
    assert s2["total_episodes"] == 1
    assert s2["action_distribution"]["hold"] == 1


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset(engine):
    ep = engine.start_episode(stock_code="600519", market="A")
    engine.record_decision(ep.episode_id, action="buy", confidence=0.8)
    engine.end_episode(ep.episode_id)

    assert engine.stats()["total_episodes"] == 1

    engine.reset()
    assert engine.stats()["total_episodes"] == 0
    assert engine.get_introspection_prompt() == ""
