# -*- coding: utf-8 -*-
"""Smoke test for PaperTradingNotifier (P2-B).

Validates:
1. Module imports successfully.
2. PaperTradingNotifier can be instantiated without any webhooks configured.
3. push_battle_plan / push_reflection / push_daily_summary return
   well-formed PushResult lists (channel="skipped" when no channels configured).
4. Markdown renderers produce expected content.
5. _chunk_text splits long content correctly.
6. DingTalk URL signing produces a URL with timestamp & sign params.
7. Factory build_paper_trading_notifier works.
8. With a fake lark/dingtalk URL (mocked requests.post), the dispatch path
   is exercised end-to-end and returns success=False on HTTP error.
"""

from __future__ import annotations

import sys
import tempfile
import types
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from paper_trading.notification_integration import (
        PaperTradingNotifier,
        PushResult,
        build_paper_trading_notifier,
    )

    # 1. Instantiate with no webhooks (uses real config but no paper_trading_*)
    notifier = PaperTradingNotifier(
        config=types.SimpleNamespace(
            paper_trading_lark_webhook_url=None,
            paper_trading_dingtalk_webhook_url=None,
            paper_trading_dingtalk_secret=None,
            paper_trading_broadcast_enabled=False,
        ),
        account_id=1,
    )
    assert notifier.lark_webhook_url is None
    assert notifier.dingtalk_webhook_url is None
    assert notifier.broadcast_enabled is False
    print("[OK] PaperTradingNotifier instantiates with no webhooks")

    # 2. push_battle_plan with no channels -> skipped
    plan = {
        "date": "2026-07-26",
        "sentiment_score": 65,
        "main_theme": "科技成长",
        "market_review": "今日市场震荡走强。",
        "holdings_plans": [
            {
                "code": "600519",
                "name": "贵州茅台",
                "strong_scenario": "跌破止损减仓50%",
                "neutral_scenario": "持有不动",
                "weak_scenario": "突破前高加仓10%",
                "stop_loss": 1800,
                "take_profit_1": 1900,
                "take_profit_2": 1950,
            }
        ],
        "candidates": [
            {
                "code": "000001",
                "name": "平安银行",
                "technical_score": 7.5,
                "auction_condition": "竞价放量",
                "intraday_trigger": "突破5日线",
                "position_ratio": 0.2,
                "stop_loss": 11.5,
                "take_profit_1": 12.5,
                "take_profit_2": 13.0,
            }
        ],
    }
    results = notifier.push_battle_plan(plan)
    assert len(results) == 1, f"expected 1 result, got {len(results)}"
    assert results[0].channel == "skipped", f"expected skipped, got {results[0].channel}"
    assert results[0].success is False
    print("[OK] push_battle_plan returns skipped when no channels configured")

    # 3. push_reflection with no channels -> skipped
    reflection = {
        "scope": "trade",
        "subject": "茅台首次建仓",
        "summary": "按计划建仓，成交价符合预期。",
        "takeaway": "耐心等待回踩确认后再加仓。",
        "lessons": ["严格执行止损"],
        "tags": "建仓",
        "mood": "positive",
        "code": "600519",
    }
    results = notifier.push_reflection(reflection)
    assert len(results) == 1
    assert results[0].channel == "skipped"
    print("[OK] push_reflection returns skipped when no channels configured")

    # 4. push_daily_summary with no channels -> skipped
    daily_report = types.SimpleNamespace(
        markdown="# dummy report\n\nhello",
        voice_script="",
        target_date=date(2026, 7, 26),
        account_id=1,
    )
    results = notifier.push_daily_summary(daily_report)
    assert len(results) == 1
    assert results[0].channel == "skipped"
    print("[OK] push_daily_summary returns skipped when no channels configured")

    # 5. Validate markdown rendering of battle plan
    md = notifier._render_battle_plan_markdown(plan, "2026-07-26")
    assert "计划日期" in md and "情绪分" in md
    assert "持仓应对方案" in md and "候选标的" in md
    assert "600519" in md and "000001" in md
    print(f"[OK] battle plan markdown rendered ({len(md)} chars)")

    # 6. Validate markdown rendering of reflection
    md_r = notifier._render_reflection_markdown(reflection)
    assert "茅台首次建仓" in md_r
    assert "Takeaway" in md_r
    assert "严格执行止损" in md_r
    print(f"[OK] reflection markdown rendered ({len(md_r)} chars)")

    # 7. _chunk_text splits long content
    long_text = "para1\n\n" + ("a" * 5000) + "\n\npara3"
    chunks = PaperTradingNotifier._chunk_text(long_text, 1000)
    assert len(chunks) >= 2, "should split into multiple chunks"
    for chunk in chunks:
        assert len(chunk.encode("utf-8")) <= 1000 or len(chunks) == 1
    print(f"[OK] _chunk_text split {len(long_text)} chars into {len(chunks)} chunks")

    # 8. _chunk_text keeps short content as single chunk
    short_text = "short content"
    chunks = PaperTradingNotifier._chunk_text(short_text, 1000)
    assert len(chunks) == 1 and chunks[0] == short_text
    print("[OK] _chunk_text preserves short content")

    # 9. DingTalk URL signing
    signed_url = PaperTradingNotifier._sign_dingtalk_url(
        "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        "SECxxxxxxxx",
    )
    assert "timestamp=" in signed_url and "sign=" in signed_url
    print("[OK] DingTalk URL signing produces signed URL")

    # 10. Factory
    factory_notifier = build_paper_trading_notifier(account_id=1)
    assert isinstance(factory_notifier, PaperTradingNotifier)
    print("[OK] build_paper_trading_notifier factory works")

    # 11. Mocked HTTP dispatch: lark webhook returns 200 + code=0 -> success
    fake_lark_notifier = PaperTradingNotifier(
        config=types.SimpleNamespace(
            paper_trading_lark_webhook_url="https://fake-lark.example.com/hook",
            paper_trading_dingtalk_webhook_url=None,
            paper_trading_dingtalk_secret=None,
            paper_trading_broadcast_enabled=False,
        ),
        account_id=1,
    )
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"code": 0, "msg": "ok"}
    with patch("paper_trading.notification_integration.requests.post", return_value=fake_response) as mock_post:
        results = fake_lark_notifier.push_battle_plan(plan)
        assert mock_post.call_count >= 1, "requests.post should be called"
        assert any(r.channel == "lark" and r.success for r in results), \
            f"expected lark success, got {results}"
    print(f"[OK] mocked lark dispatch: {results[0].to_dict()}")

    # 12. Mocked HTTP dispatch: dingtalk returns 200 + errcode=0 -> success
    fake_dingtalk_notifier = PaperTradingNotifier(
        config=types.SimpleNamespace(
            paper_trading_lark_webhook_url=None,
            paper_trading_dingtalk_webhook_url="https://oapi.dingtalk.com/robot/send?access_token=fake",
            paper_trading_dingtalk_secret=None,
            paper_trading_broadcast_enabled=False,
        ),
        account_id=1,
    )
    with patch("paper_trading.notification_integration.requests.post", return_value=fake_response) as mock_post:
        results = fake_dingtalk_notifier.push_reflection(reflection)
        assert mock_post.call_count >= 1
        assert any(r.channel == "dingtalk" and r.success for r in results), \
            f"expected dingtalk success, got {results}"
    print(f"[OK] mocked dingtalk dispatch: {results[0].to_dict()}")

    # 13. Mocked HTTP failure: 500 -> success=False
    fail_response = MagicMock()
    fail_response.status_code = 500
    fail_response.text = "Internal Server Error"
    with patch("paper_trading.notification_integration.requests.post", return_value=fail_response):
        results = fake_lark_notifier.push_battle_plan(plan)
        assert any(r.channel == "lark" and not r.success for r in results)
    print(f"[OK] mocked lark HTTP 500 returns failure: {results[0].to_dict()}")

    # 14. PushResult.to_dict
    pr = PushResult(channel="lark", success=True, extra={"content_type": "battle_plan"})
    d = pr.to_dict()
    assert d["channel"] == "lark" and d["success"] is True
    assert d["extra"]["content_type"] == "battle_plan"
    print("[OK] PushResult.to_dict serializes correctly")

    print("\nAll P2-B smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
