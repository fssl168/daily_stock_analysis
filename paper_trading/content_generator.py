# -*- coding: utf-8 -*-
"""Daily report / voice script generator (P2-A).

Turns a day of paper-trading activity into publishable content:

1. **Daily report** — a long-form Markdown article covering:
   - Account snapshot (capital / cash / net value / return %)
   - Today's trades (with fees, slippage, PnL)
   - Today's PM agent decisions (with reasons, confidence)
   - Today's reflection notes (fund-manager notes wall)
   - Next-day battle plan (three scenarios + candidate stocks + SLTP)
   - Optional LLM-generated narrative paragraph

2. **Voice script** — a short, TTS-friendly summary suitable for podcast
   narration or a DingTalk/Lark voice broadcast. Keeps the script under
   ~600 Chinese characters so it fits comfortably in a 2-minute voice clip.

Both outputs are persisted to disk under ``output_dir`` (default
``data/paper_trading/reports``) so external pushers (P2-B) and the WebUI
can pick them up without regenerating.

Public API::

    >>> gen = build_content_generator(config=config, account_id=1)
    >>> result = gen.generate_daily_report()
    >>> print(result.markdown)
    >>> print(result.voice_script)
    >>> result.report_path  # Path to saved .md file
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.storage import (
    DatabaseManager,
    Account,
    PaperBattlePlan,
    PaperDecision,
    PaperNetValue,
    PaperOrder,
    PaperPosition,
    PaperReflection,
    PaperTrade,
    get_db,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = "data/paper_trading/reports"
DEFAULT_VOICE_MAX_CHARS = 600
DEFAULT_NARRATIVE_TIMEOUT_SECONDS = 120.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class DailyReportResult:
    """Container for a single day's generated content."""

    target_date: date
    account_id: int
    markdown: str
    voice_script: str
    narrative: str = ""
    report_path: Optional[Path] = None
    voice_path: Optional[Path] = None
    used_fallback: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_date": self.target_date.isoformat() if self.target_date else None,
            "account_id": self.account_id,
            "markdown": self.markdown,
            "voice_script": self.voice_script,
            "narrative": self.narrative,
            "report_path": str(self.report_path) if self.report_path else None,
            "voice_path": str(self.voice_path) if self.voice_path else None,
            "used_fallback": self.used_fallback,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# ContentGenerator
# ---------------------------------------------------------------------------


class ContentGenerator:
    """Generate daily Markdown reports + voice scripts from paper-trading data.

    The generator is intentionally stateless between calls — each invocation
    reads fresh data from the database so reports can be regenerated after
    late-arriving reflections or battle-plan updates.
    """

    def __init__(
        self,
        config: Optional[Any] = None,
        db_manager: Optional[DatabaseManager] = None,
        account_id: int = 0,
        output_dir: Optional[Path] = None,
        reflection_engine: Optional[Any] = None,
        battle_plan_generator: Optional[Any] = None,
        trading_engine: Optional[Any] = None,
        skills: Optional[List[str]] = None,
        narrative_timeout_seconds: Optional[float] = None,
        voice_max_chars: int = DEFAULT_VOICE_MAX_CHARS,
    ):
        if config is None:
            from src.config import get_config
            config = get_config()
        if db_manager is None:
            db_manager = get_db()

        self.config = config
        self.db = db_manager
        self.account_id = int(account_id or 0)
        self.reflection_engine = reflection_engine
        self.battle_plan_generator = battle_plan_generator
        self.trading_engine = trading_engine
        self.skills = skills

        # Resolve output dir from config attr, then constructor arg, then default
        cfg_dir = getattr(config, "paper_trading_report_output_dir", None)
        self.output_dir = Path(output_dir or cfg_dir or DEFAULT_OUTPUT_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.voice_max_chars = int(voice_max_chars)
        self.narrative_timeout_seconds = float(
            narrative_timeout_seconds
            or getattr(config, "paper_trading_narrative_timeout_seconds", DEFAULT_NARRATIVE_TIMEOUT_SECONDS)
        )

        # Lazy LLM executor for narrative generation
        self._executor: Optional[Any] = None
        self._executor_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_daily_report(
        self,
        target_date: Optional[date] = None,
        save: bool = True,
        use_llm: bool = True,
    ) -> DailyReportResult:
        """Generate the full daily report + voice script.

        Args:
            target_date: Trading day to summarize. Defaults to today.
            save: When True, persist outputs to ``self.output_dir``.
            use_llm: When True, attempt LLM-based narrative generation.
                Falls back to rule-based narrative on any failure.

        Returns:
            :class:`DailyReportResult` with markdown + voice_script populated.
        """
        target_date = target_date or date.today()
        try:
            data = self._collect_daily_data(target_date)
            markdown = self._render_markdown_report(data)

            narrative = ""
            used_fallback = False
            if use_llm:
                try:
                    narrative = self._call_llm_for_narrative(data)
                except Exception as exc:
                    logger.warning(
                        "[ContentGenerator] LLM narrative failed, falling back: %s",
                        exc,
                    )
                    narrative = self._fallback_narrative(data)
                    used_fallback = True
            else:
                narrative = self._fallback_narrative(data)
                used_fallback = True

            if narrative:
                markdown = markdown.rstrip() + "\n\n## AI 综述\n\n" + narrative + "\n"

            voice_script = self._render_voice_script(data, narrative)

            report_path = None
            voice_path = None
            if save:
                report_path = self._save_to_file(
                    markdown, f"daily_report_{target_date.isoformat()}.md"
                )
                voice_path = self._save_to_file(
                    voice_script, f"voice_script_{target_date.isoformat()}.txt"
                )

            return DailyReportResult(
                target_date=target_date,
                account_id=self.account_id,
                markdown=markdown,
                voice_script=voice_script,
                narrative=narrative,
                report_path=report_path,
                voice_path=voice_path,
                used_fallback=used_fallback,
            )
        except Exception as exc:
            logger.error("[ContentGenerator] generate_daily_report failed: %s", exc, exc_info=True)
            return DailyReportResult(
                target_date=target_date,
                account_id=self.account_id,
                markdown="",
                voice_script="",
                error=str(exc),
            )

    def generate_voice_script(
        self,
        target_date: Optional[date] = None,
        save: bool = True,
    ) -> str:
        """Generate only the voice script (TTS-friendly short summary).

        Args:
            target_date: Trading day to summarize. Defaults to today.
            save: When True, persist to ``self.output_dir``.

        Returns:
            Voice script string.
        """
        target_date = target_date or date.today()
        data = self._collect_daily_data(target_date)
        narrative = self._fallback_narrative(data)
        script = self._render_voice_script(data, narrative)
        if save:
            self._save_to_file(script, f"voice_script_{target_date.isoformat()}.txt")
        return script

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _collect_daily_data(self, target_date: date) -> Dict[str, Any]:
        """Gather all paper-trading data for the target date.

        Returns a dict with keys:
          - account: account snapshot dict
          - positions: list of position dicts (current holdings)
          - trades: list of trade dicts executed on target_date
          - orders: list of order dicts created on target_date
          - decisions: list of PM agent decisions on target_date
          - reflections: list of reflection notes on target_date
          - battle_plan: optional battle plan dict for target_date
          - net_value_today: optional net-value point for target_date
        """
        if self.account_id <= 0:
            raise ValueError("account_id must be > 0 to collect daily data")

        account = self._fetch_account_snapshot()
        positions = self._fetch_positions()
        trades = self._fetch_trades_on(target_date)
        orders = self._fetch_orders_on(target_date)
        decisions = self._fetch_decisions_on(target_date)
        reflections = self._fetch_reflections_on(target_date)
        battle_plan = self._fetch_battle_plan(target_date)
        net_value_today = self._fetch_net_value_on(target_date)

        return {
            "target_date": target_date,
            "account": account,
            "positions": positions,
            "trades": trades,
            "orders": orders,
            "decisions": decisions,
            "reflections": reflections,
            "battle_plan": battle_plan,
            "net_value_today": net_value_today,
        }

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    def _render_markdown_report(self, data: Dict[str, Any]) -> str:
        """Render the full Markdown daily report.

        Structure:
          1. Header (title + date + account_id)
          2. Account snapshot
          3. Today's trades
          4. Today's PM agent decisions
          5. Today's reflection notes
          6. Current holdings
          7. Next-day battle plan (if available)
        """
        target_date = data["target_date"]
        account = data["account"] or {}
        positions = data["positions"] or []
        trades = data["trades"] or []
        orders = data["orders"] or []
        decisions = data["decisions"] or []
        reflections = data["reflections"] or []
        battle_plan = data["battle_plan"]
        net_value = data.get("net_value_today") or {}

        lines: List[str] = []
        lines.append(f"# 📊 纸面交易日报 - {target_date.isoformat()}")
        lines.append("")
        lines.append(
            f"> 账户 ID: **{self.account_id}**  |  "
            f"初始本金: ¥{account.get('initial_capital', 0):.2f}  |  "
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        lines.append("")

        # --- Account snapshot ---
        lines.append("## 一、账户概览")
        lines.append("")
        cash = float(account.get("cash", 0) or 0)
        frozen = float(account.get("frozen_cash", 0) or 0)
        market_value = float(account.get("market_value", 0) or 0)
        total_assets = float(account.get("total_assets", 0) or 0)
        pnl_pct = float(account.get("pnl_pct", 0) or 0)
        initial_capital = float(account.get("initial_capital", 1000) or 1000)
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 初始本金 | ¥{initial_capital:.2f} |")
        lines.append(f"| 可用现金 | ¥{cash:.2f} |")
        lines.append(f"| 冻结资金 | ¥{frozen:.2f} |")
        lines.append(f"| 持仓市值 | ¥{market_value:.2f} |")
        lines.append(f"| 总资产 | ¥{total_assets:.2f} |")
        lines.append(f"| 累计收益率 | **{pnl_pct:+.2f}%** |")
        if net_value:
            lines.append(
                f"| 当日净值 | {float(net_value.get('net_value', 0) or 0):.4f} "
                f"(当日收益率 {float(net_value.get('return_pct', 0) or 0):+.2f}%) |"
            )
        lines.append("")

        # --- Today's trades ---
        lines.append("## 二、今日成交")
        if trades:
            lines.append("")
            lines.append("| 时间 | 代码 | 名称 | 方向 | 成交量 | 成交价 | 金额 | 费用 |")
            lines.append("|------|------|------|------|--------|--------|------|------|")
            total_amount = 0.0
            total_fee = 0.0
            for t in trades:
                filled_at = t.get("traded_at") or t.get("filled_at") or t.get("created_at") or ""
                filled_time = filled_at[11:16] if len(filled_at) >= 16 else "--"
                qty = float(t.get("quantity", 0) or 0)
                price = float(t.get("price", 0) or 0)
                fee = float(t.get("fee", 0) or 0)
                amount = qty * price
                total_amount += amount
                total_fee += fee
                lines.append(
                    f"| {filled_time} | {t.get('code', '')} | {t.get('name', '') or '-'} | "
                    f"{'买入' if t.get('side') == 'buy' else '卖出'} | "
                    f"{qty:.0f} | {price:.4f} | ¥{amount:.2f} | ¥{fee:.2f} |"
                )
            lines.append(f"| | | | | | **合计** | **¥{total_amount:.2f}** | **¥{total_fee:.2f}** |")
        else:
            lines.append("")
            lines.append("_今日无成交记录_")
        lines.append("")

        # --- Today's PM agent decisions ---
        lines.append("## 三、AI 基金经理决策")
        if decisions:
            for d in decisions:
                created_at = d.get("created_at") or ""
                t_str = created_at[11:16] if len(created_at) >= 16 else "--"
                action = d.get("action", "hold")
                confidence = float(d.get("confidence", 0) or 0)
                action_emoji = {"buy": "🟢", "sell": "🔴", "hold": "⚪"}.get(action, "⚪")
                lines.append(
                    f"### {action_emoji} [{t_str}] {action.upper()} {d.get('code', '')} "
                    f"{d.get('name', '') or ''}"
                )
                lines.append(f"- 置信度: **{confidence * 100:.0f}%**")
                if d.get("params"):
                    try:
                        params_str = json.dumps(d["params"], ensure_ascii=False)
                        lines.append(f"- 参数: `{params_str}`")
                    except (TypeError, ValueError):
                        pass
                if d.get("reason"):
                    lines.append(f"- 决策理由: {d['reason']}")
                if d.get("used_fallback"):
                    lines.append("- _⚠️ 使用了兜底决策_")
                lines.append("")
        else:
            lines.append("")
            lines.append("_今日无 AI 决策记录_")
        lines.append("")

        # --- Today's reflection notes ---
        lines.append("## 四、基金经理复盘笔记")
        if reflections:
            for r in reflections:
                mood = r.get("mood", "neutral")
                mood_emoji = {"positive": "😊", "negative": "😰", "neutral": "😐"}.get(mood, "😐")
                lines.append(f"### {mood_emoji} {r.get('subject', '(无标题)')}")
                lines.append(f"- 范围: {r.get('scope', 'adhoc')}  |  标签: {r.get('tags', '') or '-'}")
                if r.get("summary"):
                    lines.append(f"- 摘要: {r['summary']}")
                if r.get("takeaway"):
                    lines.append(f"- **Takeaway**: {r['takeaway']}")
                lessons = r.get("lessons") or []
                if lessons:
                    lines.append("- 经验教训:")
                    for lesson in lessons:
                        lines.append(f"  - {lesson}")
                lines.append("")
        else:
            lines.append("")
            lines.append("_今日无复盘笔记_")
        lines.append("")

        # --- Current holdings ---
        lines.append("## 五、当前持仓")
        if positions:
            lines.append("")
            lines.append("| 代码 | 名称 | 持仓 | 成本 | 现价 | 市值 | 浮动盈亏 |")
            lines.append("|------|------|------|------|------|------|----------|")
            for p in positions:
                qty = float(p.get("quantity", 0) or 0)
                avg_cost = float(p.get("avg_cost", 0) or 0)
                last_price = float(p.get("last_price", 0) or 0)
                market_val = qty * last_price
                pnl_pct_pos = ((last_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0
                lines.append(
                    f"| {p.get('code', '')} | {p.get('name', '') or '-'} | "
                    f"{qty:.0f} | {avg_cost:.4f} | {last_price:.4f} | "
                    f"¥{market_val:.2f} | {pnl_pct_pos:+.2f}% |"
                )
        else:
            lines.append("")
            lines.append("_当前无持仓_")
        lines.append("")

        # --- Battle plan ---
        if battle_plan:
            lines.append("## 六、次日作战卡")
            lines.append("")
            plan_date = battle_plan.get("date") or target_date.isoformat()
            sentiment = battle_plan.get("sentiment_score") or 0
            main_theme = battle_plan.get("main_theme") or ""
            lines.append(f"**计划日期**: {plan_date}  |  **情绪分**: {sentiment}/100  |  **主线**: {main_theme}")
            if battle_plan.get("market_review"):
                lines.append("")
                lines.append("### 市场综述")
                lines.append(battle_plan["market_review"])

            holdings_plans = battle_plan.get("holdings_plans") or []
            if holdings_plans:
                lines.append("")
                lines.append("### 持仓应对方案")
                for h in holdings_plans:
                    lines.append(f"#### {h.get('code', '')} {h.get('name', '') or ''}")
                    if h.get("strong_scenario"):
                        lines.append(f"- 🟢 强势: {h['strong_scenario']}")
                    if h.get("neutral_scenario"):
                        lines.append(f"- 🟡 中性: {h['neutral_scenario']}")
                    if h.get("weak_scenario"):
                        lines.append(f"- 🔴 弱势: {h['weak_scenario']}")
                    sl = h.get("stop_loss")
                    tp1 = h.get("take_profit_1")
                    tp2 = h.get("take_profit_2")
                    lines.append(
                        f"- 三线: SL={sl or 'N/A'} TP1={tp1 or 'N/A'} TP2={tp2 or 'N/A'}"
                    )
                    lines.append("")

            candidates = battle_plan.get("candidates") or []
            if candidates:
                lines.append("")
                lines.append("### 候选标的")
                for c in candidates:
                    lines.append(
                        f"#### {c.get('code', '')} {c.get('name', '') or ''} "
                        f"(评分: {float(c.get('technical_score', 0) or 0):.1f})"
                    )
                    lines.append(f"- 集合竞价: {c.get('auction_condition', '(暂无)')}")
                    lines.append(f"- 盘中触发: {c.get('intraday_trigger', '(暂无)')}")
                    lines.append(
                        f"- 建议仓位: {float(c.get('position_ratio', 0) or 0) * 100:.0f}%"
                    )
                    sl = c.get("stop_loss")
                    tp1 = c.get("take_profit_1")
                    tp2 = c.get("take_profit_2")
                    lines.append(
                        f"- 三线: SL={sl or 'N/A'} TP1={tp1 or 'N/A'} TP2={tp2 or 'N/A'}"
                    )
                    lines.append("")

        lines.append("---")
        lines.append(
            f"_本报告由 PaperTrading ContentGenerator 自动生成 · "
            f"账户 {self.account_id} · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Voice script rendering
    # ------------------------------------------------------------------

    def _render_voice_script(self, data: Dict[str, Any], narrative: str = "") -> str:
        """Render a short TTS-friendly voice script.

        Targets ~{voice_max_chars} Chinese characters so it fits in a
        2-minute voice clip. Focuses on the most actionable info:
        account performance, today's trades, key decisions, and
        next-day plan summary.
        """
        target_date = data["target_date"]
        account = data["account"] or {}
        trades = data["trades"] or []
        decisions = data["decisions"] or []
        battle_plan = data["battle_plan"]
        positions = data["positions"] or []

        lines: List[str] = []
        lines.append(f"今天是 {target_date.year} 年 {target_date.month} 月 {target_date.day} 日，"
                     f"纸面交易日报如下。")

        # Account summary
        total_assets = float(account.get("total_assets", 0) or 0)
        pnl_pct = float(account.get("pnl_pct", 0) or 0)
        initial_capital = float(account.get("initial_capital", 1000) or 1000)
        direction = "盈利" if pnl_pct >= 0 else "亏损"
        lines.append(
            f"账户初始本金 {initial_capital:.0f} 元，"
            f"当前总资产 {total_assets:.2f} 元，"
            f"累计{direction} {abs(pnl_pct):.2f}%。"
        )

        # Today's trades summary
        if trades:
            buy_count = sum(1 for t in trades if t.get("side") == "buy")
            sell_count = len(trades) - buy_count
            total_amount = sum(
                float(t.get("quantity", 0) or 0) * float(t.get("price", 0) or 0)
                for t in trades
            )
            lines.append(
                f"今日共成交 {len(trades)} 笔，"
                f"其中买入 {buy_count} 笔，卖出 {sell_count} 笔，"
                f"成交总额 {total_amount:.2f} 元。"
            )
        else:
            lines.append("今日无成交。")

        # Key decisions
        if decisions:
            top_decision = decisions[0]
            action = top_decision.get("action", "hold")
            action_text = {"buy": "买入", "sell": "卖出", "hold": "持有"}.get(action, "持有")
            lines.append(
                f"AI 基金经理今日主要决策：{action_text} "
                f"{top_decision.get('code', '')} {top_decision.get('name', '') or ''}，"
                f"置信度 {float(top_decision.get('confidence', 0) or 0) * 100:.0f}%。"
            )

        # Positions summary
        if positions:
            pos_count = len(positions)
            total_market_value = sum(
                float(p.get("quantity", 0) or 0) * float(p.get("last_price", 0) or 0)
                for p in positions
            )
            lines.append(f"当前持有 {pos_count} 只股票，总市值 {total_market_value:.2f} 元。")

        # Battle plan preview
        if battle_plan:
            sentiment = int(battle_plan.get("sentiment_score", 0) or 0)
            sentiment_text = (
                "偏多" if sentiment >= 60 else ("偏空" if sentiment < 40 else "中性")
            )
            main_theme = battle_plan.get("main_theme") or ""
            candidates = battle_plan.get("candidates") or []
            lines.append(
                f"次日作战卡：市场情绪{sentiment_text}（{sentiment}分），"
                f"主线题材「{main_theme}」。"
            )
            if candidates:
                top_candidate = candidates[0]
                lines.append(
                    f"重点关注：{top_candidate.get('code', '')} "
                    f"{top_candidate.get('name', '') or ''}，"
                    f"建议仓位 {float(top_candidate.get('position_ratio', 0) or 0) * 100:.0f}%。"
                )

        # Optional LLM narrative trimmed to remaining budget
        if narrative:
            script_so_far = "".join(lines)
            remaining = self.voice_max_chars - len(script_so_far)
            if remaining > 30:
                trimmed = narrative[:remaining].rstrip()
                if len(narrative) > remaining:
                    trimmed += "..."
                lines.append(trimmed)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM narrative
    # ------------------------------------------------------------------

    def _call_llm_for_narrative(self, data: Dict[str, Any]) -> str:
        """Call the LLM to generate a narrative paragraph for the day.

        Uses the agent executor factory (same as AgentRiskReviewer /
        ReflectionEngine) so configuration is consistent across modules.
        Hard timeout enforced by a daemon worker thread.
        """
        executor = self._get_executor()
        if executor is None:
            raise RuntimeError("LLM executor unavailable")

        prompt = self._build_narrative_prompt(data)
        session_id = f"content-gen-{int(time.time())}"

        result_text = self._call_agent_with_timeout(executor, prompt, session_id)
        if not result_text:
            raise RuntimeError("LLM returned empty narrative")
        return result_text.strip()

    def _build_narrative_prompt(self, data: Dict[str, Any]) -> str:
        """Build the narrative-generation prompt from daily data."""
        account = data["account"] or {}
        trades = data["trades"] or []
        decisions = data["decisions"] or []
        reflections = data["reflections"] or []
        battle_plan = data["battle_plan"]

        # Compact summary to keep prompt token usage reasonable
        summary_lines = [
            f"账户总资产: ¥{float(account.get('total_assets', 0) or 0):.2f}",
            f"累计收益率: {float(account.get('pnl_pct', 0) or 0):+.2f}%",
            f"今日成交笔数: {len(trades)}",
            f"今日 AI 决策数: {len(decisions)}",
            f"今日复盘笔记数: {len(reflections)}",
        ]
        if decisions:
            top = decisions[0]
            summary_lines.append(
                f"主要决策: {top.get('action', '')} {top.get('code', '')} "
                f"(置信度 {float(top.get('confidence', 0) or 0) * 100:.0f}%)"
            )
        if battle_plan:
            summary_lines.append(
                f"次日情绪分: {battle_plan.get('sentiment_score', 0)}/100, "
                f"主线: {battle_plan.get('main_theme', '')}"
            )

        return (
            "你是纸面交易系统的内容编辑，请基于以下当日数据生成一段 200-300 字的"
            "中文综述，用于发布到公众号/播客。\n\n"
            "要求：\n"
            "1. 突出当日操作亮点和市场判断\n"
            "2. 给出次日操作建议的概述\n"
            "3. 语气自然流畅，避免使用表格或列表\n"
            "4. 不要包含 emoji 或特殊符号\n\n"
            "当日数据：\n" + "\n".join(summary_lines)
        )

    def _fallback_narrative(self, data: Dict[str, Any]) -> str:
        """Rule-based narrative used when LLM is unavailable."""
        account = data["account"] or {}
        trades = data["trades"] or []
        decisions = data["decisions"] or []
        battle_plan = data["battle_plan"]

        total_assets = float(account.get("total_assets", 0) or 0)
        pnl_pct = float(account.get("pnl_pct", 0) or 0)
        direction = "盈利" if pnl_pct >= 0 else "亏损"

        parts: List[str] = []
        parts.append(
            f"今日账户总资产 {total_assets:.2f} 元，累计{direction} {abs(pnl_pct):.2f}%。"
        )

        if trades:
            buy_count = sum(1 for t in trades if t.get("side") == "buy")
            sell_count = len(trades) - buy_count
            parts.append(
                f"全天共完成 {len(trades)} 笔成交（买 {buy_count} 卖 {sell_count}），"
            )
        else:
            parts.append("今日未发生成交，")

        if decisions:
            top = decisions[0]
            action_text = {"buy": "买入", "sell": "卖出", "hold": "持有"}.get(
                top.get("action", "hold"), "持有"
            )
            parts.append(
                f"AI 基金经理建议{action_text} {top.get('code', '')}，"
                f"置信度 {float(top.get('confidence', 0) or 0) * 100:.0f}%。"
            )

        if battle_plan:
            sentiment = int(battle_plan.get("sentiment_score", 0) or 0)
            sentiment_text = (
                "偏多" if sentiment >= 60 else ("偏空" if sentiment < 40 else "中性")
            )
            parts.append(f"次日市场情绪{sentiment_text}，可关注相关主线机会。")

        return "".join(parts)

    def _get_executor(self):
        """Lazy-build the LLM executor (thread-safe)."""
        if self._executor is not None:
            return self._executor
        with self._executor_lock:
            if self._executor is not None:
                return self._executor
            try:
                from src.agent.factory import build_agent_executor
                self._executor = build_agent_executor(self.config, skills=self.skills)
                logger.info("[ContentGenerator] LLM executor built successfully")
            except Exception as exc:
                logger.warning("[ContentGenerator] Failed to build LLM executor: %s", exc)
                self._executor = None
        return self._executor

    def _call_agent_with_timeout(self, executor: Any, prompt: str, session_id: str) -> str:
        """Call executor.chat() with a hard timeout via daemon thread."""
        result_holder: Dict[str, Any] = {"text": "", "error": None}

        def worker():
            try:
                result = executor.chat(message=prompt, session_id=session_id)
                if result and getattr(result, "success", False):
                    result_holder["text"] = result.content or ""
                else:
                    err = getattr(result, "error", None) if result else "no result"
                    result_holder["error"] = err or "agent returned failure"
            except Exception as exc:
                result_holder["error"] = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=self.narrative_timeout_seconds)

        if thread.is_alive():
            # Still running — give up; daemon thread will be cleaned up at process exit
            raise TimeoutError(
                f"LLM narrative timed out after {self.narrative_timeout_seconds}s"
            )
        if result_holder["error"]:
            raise RuntimeError(result_holder["error"])
        return result_holder["text"]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_to_file(self, content: str, filename: str) -> Path:
        """Save content to ``output_dir/filename`` and return the path."""
        path = self.output_dir / filename
        try:
            path.write_text(content, encoding="utf-8")
            logger.info("[ContentGenerator] saved %s (%d chars)", path, len(content))
        except OSError as exc:
            logger.error("[ContentGenerator] failed to save %s: %s", path, exc)
            raise
        return path

    # ------------------------------------------------------------------
    # Data fetchers
    # ------------------------------------------------------------------

    def _fetch_account_snapshot(self) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            row = session.get(Account, self.account_id)
            if row is None:
                return {}
            # Compute market value from positions
            positions = (
                session.execute(
                    select(PaperPosition).where(
                        PaperPosition.account_id == self.account_id,
                        PaperPosition.quantity > 0,
                    )
                ).scalars().all()
            )
            market_value = sum(
                float(p.quantity or 0) * float(p.last_price or 0) for p in positions
            )
            total_assets = float(row.cash or 0) + float(row.frozen_cash or 0) + market_value
            initial_capital = float(row.initial_capital or 1000)
            pnl_pct = (
                (total_assets - initial_capital) / initial_capital * 100
                if initial_capital > 0
                else 0.0
            )
            return {
                "id": row.id,
                "name": row.name,
                "initial_capital": initial_capital,
                "cash": float(row.cash or 0),
                "frozen_cash": float(row.frozen_cash or 0),
                "status": row.status,
                "market_value": market_value,
                "total_assets": total_assets,
                "pnl_pct": pnl_pct,
            }

    def _fetch_positions(self) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(PaperPosition)
                    .where(
                        PaperPosition.account_id == self.account_id,
                        PaperPosition.quantity > 0,
                    )
                    .order_by(desc(PaperPosition.quantity))
                ).scalars().all()
            )
            return [
                {
                    "code": r.code,
                    "name": r.name,
                    "quantity": float(r.quantity or 0),
                    "available_quantity": float(r.available_quantity or 0),
                    "avg_cost": float(r.avg_cost or 0),
                    "last_price": float(r.last_price or 0),
                    "stop_loss": float(r.stop_loss) if r.stop_loss is not None else None,
                    "take_profit": float(r.take_profit) if r.take_profit is not None else None,
                    "take_profit_2": float(r.take_profit_2) if r.take_profit_2 is not None else None,
                }
                for r in rows
            ]

    def _fetch_trades_on(self, target_date: date) -> List[Dict[str, Any]]:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(PaperTrade)
                    .where(
                        PaperTrade.account_id == self.account_id,
                        PaperTrade.traded_at >= start,
                        PaperTrade.traded_at < end,
                    )
                    .order_by(PaperTrade.traded_at)
                ).scalars().all()
            )
            return [
                {
                    "id": r.id,
                    "code": r.code,
                    "name": r.name,
                    "side": r.side,
                    "quantity": float(r.quantity or 0),
                    "price": float(r.price or 0),
                    "fee": float(r.fee or 0),
                    "order_id": r.order_id,
                    "traded_at": r.traded_at.isoformat() if r.traded_at else None,
                }
                for r in rows
            ]

    def _fetch_orders_on(self, target_date: date) -> List[Dict[str, Any]]:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(PaperOrder)
                    .where(
                        PaperOrder.account_id == self.account_id,
                        PaperOrder.created_at >= start,
                        PaperOrder.created_at < end,
                    )
                    .order_by(PaperOrder.created_at)
                ).scalars().all()
            )
            return [
                {
                    "id": r.id,
                    "code": r.code,
                    "name": r.name,
                    "side": r.side,
                    "order_type": r.order_type,
                    "price": float(r.price) if r.price is not None else None,
                    "quantity": float(r.quantity or 0),
                    "filled_quantity": float(r.filled_quantity or 0),
                    "filled_price_avg": float(r.filled_price_avg or 0),
                    "status": r.status,
                    "strategy_name": r.strategy_name,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "filled_at": r.filled_at.isoformat() if r.filled_at else None,
                }
                for r in rows
            ]

    def _fetch_decisions_on(self, target_date: date) -> List[Dict[str, Any]]:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(PaperDecision)
                    .where(
                        PaperDecision.account_id == self.account_id,
                        PaperDecision.created_at >= start,
                        PaperDecision.created_at < end,
                    )
                    .order_by(PaperDecision.created_at)
                ).scalars().all()
            )
            results: List[Dict[str, Any]] = []
            for r in rows:
                params: Dict[str, Any] = {}
                if r.params_json:
                    try:
                        parsed = json.loads(r.params_json)
                        if isinstance(parsed, dict):
                            params = parsed
                    except (ValueError, TypeError):
                        params = {}
                results.append(
                    {
                        "id": r.id,
                        "action": r.action,
                        "code": r.code,
                        "name": r.name,
                        "params": params,
                        "reason": r.reason or "",
                        "confidence": float(r.confidence or 0.0),
                        "used_fallback": bool(r.status == "skipped" and r.action == "hold"),
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                )
            return results

    def _fetch_reflections_on(self, target_date: date) -> List[Dict[str, Any]]:
        start = datetime.combine(target_date, datetime.min.time())
        end = start + timedelta(days=1)
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(PaperReflection)
                    .where(
                        PaperReflection.account_id == self.account_id,
                        PaperReflection.created_at >= start,
                        PaperReflection.created_at < end,
                    )
                    .order_by(PaperReflection.created_at)
                ).scalars().all()
            )
            results: List[Dict[str, Any]] = []
            for r in rows:
                lessons: List[str] = []
                if r.lessons_json:
                    try:
                        parsed = json.loads(r.lessons_json)
                        if isinstance(parsed, list):
                            lessons = [str(s) for s in parsed]
                    except (ValueError, TypeError):
                        lessons = []
                results.append(
                    {
                        "id": r.id,
                        "scope": r.scope or "adhoc",
                        "subject": r.subject or "",
                        "summary": r.summary or "",
                        "takeaway": r.takeaway or "",
                        "lessons": lessons,
                        "tags": r.tags or "",
                        "mood": r.mood or "neutral",
                        "trade_id": r.trade_id,
                        "order_id": r.order_id,
                        "code": r.code,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                )
            return results

    def _fetch_battle_plan(self, target_date: date) -> Optional[Dict[str, Any]]:
        """Fetch the battle plan for target_date (or the latest one available)."""
        with self.db.session_scope() as session:
            row = (
                session.execute(
                    select(PaperBattlePlan)
                    .where(
                        PaperBattlePlan.account_id == self.account_id,
                        PaperBattlePlan.date == target_date,
                    )
                    .order_by(desc(PaperBattlePlan.created_at))
                    .limit(1)
                ).scalars().first()
            )
            if row is None:
                # Fall back to the latest plan within the last 7 days
                row = (
                    session.execute(
                        select(PaperBattlePlan)
                        .where(
                            PaperBattlePlan.account_id == self.account_id,
                            PaperBattlePlan.date >= target_date - timedelta(days=7),
                            PaperBattlePlan.date <= target_date,
                        )
                        .order_by(desc(PaperBattlePlan.created_at))
                        .limit(1)
                    ).scalars().first()
                )
            if row is None:
                return None

            holdings_raw = []
            candidates_raw = []
            try:
                holdings_raw = json.loads(row.holdings_plans_json or "[]")
            except (ValueError, TypeError):
                pass
            try:
                candidates_raw = json.loads(row.candidates_json or "[]")
            except (ValueError, TypeError):
                pass

            return {
                "id": row.id,
                "date": row.date.isoformat() if row.date else None,
                "holdings_plans": holdings_raw if isinstance(holdings_raw, list) else [],
                "candidates": candidates_raw if isinstance(candidates_raw, list) else [],
                "market_review": row.market_review or "",
                "sentiment_score": int(row.sentiment_score or 50),
                "main_theme": row.main_theme or "",
                "used_fallback": bool(row.used_fallback),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }

    def _fetch_net_value_on(self, target_date: date) -> Optional[Dict[str, Any]]:
        with self.db.session_scope() as session:
            row = (
                session.execute(
                    select(PaperNetValue)
                    .where(
                        PaperNetValue.account_id == self.account_id,
                        PaperNetValue.date == target_date,
                    )
                    .limit(1)
                ).scalars().first()
            )
            # Lazy import to avoid cycle when storage has not exposed PaperNetValue at module level
            if row is None:
                return None
            return {
                "date": row.date.isoformat() if row.date else None,
                "cash": float(row.cash or 0),
                "market_value": float(row.market_value or 0),
                "net_value": float(row.net_value or 0),
                "return_pct": float(row.return_pct or 0),
            }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_content_generator(
    config: Optional[Any] = None,
    account_id: int = 0,
    db_manager: Optional[DatabaseManager] = None,
    reflection_engine: Optional[Any] = None,
    battle_plan_generator: Optional[Any] = None,
    trading_engine: Optional[Any] = None,
    output_dir: Optional[Path] = None,
    skills: Optional[List[str]] = None,
    narrative_timeout_seconds: Optional[float] = None,
) -> ContentGenerator:
    """Build a :class:`ContentGenerator` wired to the rest of the system.

    Args:
        config: Application config. If None, ``get_config()`` is called.
        account_id: Paper trading account id (must be > 0 for generation).
        db_manager: Database manager. If None, uses the global singleton.
        reflection_engine: Optional :class:`ReflectionEngine` for reuse.
        battle_plan_generator: Optional :class:`BattlePlanGenerator`.
        trading_engine: Optional :class:`TradingEngine`.
        output_dir: Where to save generated reports/voice scripts.
        skills: Skills to activate on the LLM executor.
        narrative_timeout_seconds: Hard timeout for LLM narrative calls.

    Returns:
        A configured :class:`ContentGenerator`.
    """
    if config is None:
        from src.config import get_config
        config = get_config()
    if db_manager is None:
        db_manager = get_db()
    if account_id <= 0:
        account_id = int(getattr(config, "paper_trading_default_account_id", 0) or 0)

    return ContentGenerator(
        config=config,
        db_manager=db_manager,
        account_id=account_id,
        output_dir=output_dir,
        reflection_engine=reflection_engine,
        battle_plan_generator=battle_plan_generator,
        trading_engine=trading_engine,
        skills=skills,
        narrative_timeout_seconds=narrative_timeout_seconds,
    )
