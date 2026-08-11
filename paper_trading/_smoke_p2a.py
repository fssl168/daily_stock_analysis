# -*- coding: utf-8 -*-
"""Smoke test for ContentGenerator (P2-A).

Validates:
1. Module imports successfully.
2. ContentGenerator can be instantiated with a temp DB + account.
3. generate_daily_report(use_llm=False) returns a DailyReportResult
   with non-empty markdown + voice_script.
4. generate_voice_script() returns a non-empty string.
5. Output files are saved to the temp output dir.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows: use tempfile dir to avoid file lock issues with sqlite
os.environ.setdefault("PAPER_TRADING_DB_URL", f"sqlite:///{tempfile.gettempdir()}/smoke_p2a.db")
os.environ.setdefault("PAPER_TRADING_DB_MODE", "sqlite")


def _cleanup_db():
    db_path = Path(tempfile.gettempdir()) / "smoke_p2a.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


def main() -> int:
    from src.storage import (
        DatabaseManager,
        Account,
        PaperBattlePlan,
        PaperDecision,
        PaperNetValue,
        PaperReflection,
        PaperTrade,
        get_db,
    )

    # Clean any leftover DB from a previous interrupted run before starting.
    _cleanup_db()
    # Reset singleton and instantiate with explicit sqlite url
    DatabaseManager.reset_instance()
    db_url = f"sqlite:///{tempfile.gettempdir()}/smoke_p2a.db"
    db = DatabaseManager(db_url=db_url)

    # Create account via manager
    from paper_trading.account import PaperAccountManager
    account_mgr = PaperAccountManager(db_manager=db)
    account_mgr.get_or_create_account(
        name="smoke_p2a", initial_capital=1000.0
    )
    # Re-fetch account id safely (avoid detached instance)
    with db.session_scope() as session:
        from sqlalchemy import select
        acc_obj = session.execute(
            select(Account).where(Account.name == "smoke_p2a")
        ).scalar_one()
        acc_id = acc_obj.id

    # Insert a sample trade today
    today = date.today()
    with db.session_scope() as session:
        trade = PaperTrade(
            account_id=acc_id,
            order_id=0,
            code="600519",
            name="贵州茅台",
            side="buy",
            quantity=10.0,
            price=1850.0,
            amount=18500.0,
            fee=8.0,
            traded_at=datetime.now(),
        )
        decision = PaperDecision(
            account_id=acc_id,
            action="buy",
            code="600519",
            name="贵州茅台",
            confidence=0.78,
            reason="技术面突破且量能放大",
            params_json='{"price": 1850, "qty": 10}',
            status="executed",
            created_at=datetime.now(),
        )
        reflection = PaperReflection(
            account_id=acc_id,
            scope="trade",
            subject="茅台首次建仓",
            summary="按计划建仓，成交价符合预期。",
            takeaway="耐心等待回踩确认后再加仓。",
            lessons_json='["严格执行止损"]',
            tags="建仓",
            mood="positive",
            code="600519",
            created_at=datetime.now(),
        )
        net_value = PaperNetValue(
            account_id=acc_id,
            date=today,
            cash=150.0,
            market_value=18500.0,
            total_assets=18650.0,
            net_value=1.0 + 0.05,
            return_pct=5.0,
        )
        battle_plan = PaperBattlePlan(
            account_id=acc_id,
            date=today,
            market_review="今日市场震荡走强，科技板块表现活跃。",
            sentiment_score=65,
            main_theme="科技成长",
            holdings_plans_json=(
                '[{"code":"600519","name":"贵州茅台",'
                '"strong_scenario":"跌破止损减仓50%",'
                '"neutral_scenario":"持有不动",'
                '"weak_scenario":"突破前高加仓10%",'
                '"stop_loss":1800,"take_profit_1":1900,"take_profit_2":1950}]'
            ),
            candidates_json=(
                '[{"code":"000001","name":"平安银行",'
                '"technical_score":7.5,'
                '"auction_condition":"竞价放量",'
                '"intraday_trigger":"突破5日线",'
                '"position_ratio":0.2,'
                '"stop_loss":11.5,"take_profit_1":12.5,"take_profit_2":13.0}]'
            ),
            used_fallback=False,
            created_at=datetime.now(),
        )
        session.add_all([trade, decision, reflection, net_value, battle_plan])

    # Build ContentGenerator with use_llm=False
    from paper_trading.content_generator import (
        ContentGenerator,
        DailyReportResult,
        build_content_generator,
    )

    output_dir = Path(tempfile.gettempdir()) / "smoke_p2a_reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    gen = ContentGenerator(
        account_id=acc_id,
        db_manager=db,
        output_dir=output_dir,
        voice_max_chars=600,
        narrative_timeout_seconds=5.0,
    )

    # 1. generate_daily_report (LLM disabled)
    result = gen.generate_daily_report(
        target_date=today, save=True, use_llm=False
    )
    assert isinstance(result, DailyReportResult), "result must be DailyReportResult"
    assert result.error is None, f"unexpected error: {result.error}"
    assert result.markdown, "markdown must not be empty"
    assert result.voice_script, "voice_script must not be empty"
    assert result.used_fallback is True, "should use fallback when use_llm=False"
    assert result.report_path and result.report_path.exists(), "report file not saved"
    assert result.voice_path and result.voice_path.exists(), "voice file not saved"
    print(f"[OK] generate_daily_report: {len(result.markdown)} chars markdown, "
          f"{len(result.voice_script)} chars voice script")
    print(f"  report_path: {result.report_path}")
    print(f"  voice_path:  {result.voice_path}")

    # 2. validate markdown content
    assert "纸面交易日报" in result.markdown, "missing report title"
    assert "账户概览" in result.markdown, "missing account section"
    assert "今日成交" in result.markdown, "missing trades section"
    assert "AI 基金经理决策" in result.markdown, "missing decisions section"
    assert "基金经理复盘笔记" in result.markdown, "missing reflections section"
    assert "当前持仓" in result.markdown, "missing positions section"
    assert "次日作战卡" in result.markdown, "missing battle plan section"
    print("[OK] markdown content sections present")

    # 3. validate voice script
    assert "纸面交易日报" in result.voice_script, "voice script missing intro"
    assert "账户初始本金" in result.voice_script, "voice script missing account info"
    print("[OK] voice script content valid")

    # 4. generate_voice_script independently
    voice_only = gen.generate_voice_script(target_date=today, save=False)
    assert isinstance(voice_only, str) and voice_only, "voice_only must be non-empty str"
    print(f"[OK] generate_voice_script: {len(voice_only)} chars")

    # 5. build_content_generator factory
    factory_gen = build_content_generator(
        account_id=acc_id, db_manager=db, output_dir=output_dir
    )
    assert isinstance(factory_gen, ContentGenerator), "factory must return ContentGenerator"
    print("[OK] build_content_generator factory works")

    print("\nAll P2-A smoke tests passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _cleanup_db()
