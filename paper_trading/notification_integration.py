# -*- coding: utf-8 -*-
"""Paper-trading notification integration (P2-B).

Pushes paper-trading content to messaging channels:

1. **Battle plan** — next-day operations card (three scenarios + candidates)
2. **Reflection notes** — fund-manager notes wall (single note or daily digest)
3. **Daily summary** — full daily report markdown + voice script metadata

Two transport layers are supported and used in order:

- **Targeted transport** (``_send_lark`` / ``_send_dingtalk``): direct webhook
  POSTs to dedicated paper-trading channels. Configured via
  ``PAPER_TRADING_LARK_WEBHOOK_URL`` / ``PAPER_TRADING_DINGTALK_WEBHOOK_URL``.
  Use these when you want paper-trading notifications isolated from the main
  analysis notifications.
- **Broadcast transport**: delegates to the existing
  :class:`src.notification.NotificationService`, which already fans out to
  every globally-configured channel (feishu/wechat/telegram/email/...). Used
  only when ``PAPER_TRADING_BROADCAST_ENABLED=true``. When neither targeted
  webhooks nor broadcast_enabled is configured, push methods return a
  ``skipped`` :class:`PushResult`.

Public API::

    >>> from paper_trading.notification_integration import (
    ...     PaperTradingNotifier,
    ...     build_paper_trading_notifier,
    ... )
    >>> notifier = build_paper_trading_notifier(account_id=1)
    >>> notifier.push_battle_plan(battle_plan_dict)
    >>> notifier.push_reflection(reflection_dict)
    >>> notifier.push_daily_summary(daily_report_result)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_BYTES_LARK = 28000  # lark interactive card limit ~30KB
DEFAULT_MAX_BYTES_DINGTALK = 18000  # dingtalk markdown limit ~20KB


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PushResult:
    """Outcome of a single push operation."""

    channel: str  # "lark" / "dingtalk" / "broadcast" / "skipped"
    success: bool
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "success": self.success,
            "error": self.error,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# PaperTradingNotifier
# ---------------------------------------------------------------------------


class PaperTradingNotifier:
    """Push paper-trading content to lark / dingtalk / broadcast channels.

    Construction is intentionally cheap — the underlying
    :class:`NotificationService` is built lazily on first broadcast to avoid
    forcing every paper-trading import to pay the notification-service
    initialization cost.
    """

    def __init__(
        self,
        config: Optional[Any] = None,
        account_id: int = 0,
        lark_webhook_url: Optional[str] = None,
        dingtalk_webhook_url: Optional[str] = None,
        dingtalk_secret: Optional[str] = None,
        broadcast_enabled: Optional[bool] = None,
        lark_max_bytes: int = DEFAULT_MAX_BYTES_LARK,
        dingtalk_max_bytes: int = DEFAULT_MAX_BYTES_DINGTALK,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        verify_ssl: bool = True,
    ):
        if config is None:
            from src.config import get_config
            config = get_config()
        self.config = config
        self.account_id = int(account_id or 0)

        # Resolve webhook URLs: explicit arg > config attr > None
        self.lark_webhook_url = (
            lark_webhook_url
            or getattr(config, "paper_trading_lark_webhook_url", None)
            or getattr(config, "lark_webhook_url", None)
        )
        self.dingtalk_webhook_url = (
            dingtalk_webhook_url
            or getattr(config, "paper_trading_dingtalk_webhook_url", None)
            or getattr(config, "dingtalk_webhook_url", None)
        )
        self.dingtalk_secret = (
            dingtalk_secret
            or getattr(config, "paper_trading_dingtalk_secret", None)
            or getattr(config, "dingtalk_secret", None)
        )

        if broadcast_enabled is None:
            broadcast_enabled = bool(
                getattr(config, "paper_trading_broadcast_enabled", False)
            )
        self.broadcast_enabled = bool(broadcast_enabled)

        self.lark_max_bytes = int(lark_max_bytes)
        self.dingtalk_max_bytes = int(dingtalk_max_bytes)
        self.timeout_seconds = int(timeout_seconds)
        self.verify_ssl = bool(verify_ssl)

        # Lazy-initialized broadcast service
        self._broadcast_service: Optional[Any] = None

    # ------------------------------------------------------------------
    # Public push methods
    # ------------------------------------------------------------------

    def push_battle_plan(
        self,
        battle_plan: Dict[str, Any],
        target_date: Optional[Any] = None,
    ) -> List[PushResult]:
        """Push the next-day battle plan.

        Args:
            battle_plan: Battle plan dict from BattlePlanGenerator.to_dict()
                or ContentGenerator._fetch_battle_plan.
            target_date: Optional date for the header. Defaults to
                battle_plan["date"] or today.

        Returns:
            List of :class:`PushResult`, one per channel attempted.
        """
        if not battle_plan:
            return [PushResult(channel="skipped", success=False, error="empty battle plan")]

        title_date = target_date or battle_plan.get("date") or datetime.now().date()
        markdown = self._render_battle_plan_markdown(battle_plan, title_date)
        header = f"📋 次日作战卡 · {title_date}"
        return self._dispatch(header, markdown, content_type="battle_plan")

    def push_reflection(
        self,
        reflection: Dict[str, Any],
    ) -> List[PushResult]:
        """Push a single reflection note (fund-manager note).

        Args:
            reflection: Reflection dict from ReflectionEngine or
                ContentGenerator._fetch_reflections_on.

        Returns:
            List of :class:`PushResult`.
        """
        if not reflection:
            return [PushResult(channel="skipped", success=False, error="empty reflection")]

        markdown = self._render_reflection_markdown(reflection)
        subject = reflection.get("subject") or "基金经理复盘笔记"
        header = f"📝 {subject}"
        return self._dispatch(header, markdown, content_type="reflection")

    def push_daily_summary(
        self,
        daily_report: Any,
    ) -> List[PushResult]:
        """Push the daily report summary.

        Args:
            daily_report: Either a :class:`DailyReportResult` instance or a
                dict with keys {markdown, voice_script, target_date, account_id}.

        Returns:
            List of :class:`PushResult`.
        """
        markdown = self._extract_markdown(daily_report)
        if not markdown:
            return [PushResult(channel="skipped", success=False, error="empty markdown")]

        target_date = self._extract_date(daily_report)
        header = f"📊 纸面交易日报 · {target_date}"
        return self._dispatch(header, markdown, content_type="daily_summary")

    # ------------------------------------------------------------------
    # Dispatch logic
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        header: str,
        markdown: str,
        content_type: str,
    ) -> List[PushResult]:
        """Send to all configured channels.

        Order: dedicated lark -> dedicated dingtalk -> broadcast (if enabled).

        When neither targeted webhooks nor broadcast_enabled is configured,
        returns a single ``skipped`` result so callers can distinguish
        "nothing attempted" from "attempted but failed".
        """
        results: List[PushResult] = []

        if self.lark_webhook_url:
            try:
                ok = self._send_lark(header, markdown)
                results.append(
                    PushResult(channel="lark", success=ok, extra={"content_type": content_type})
                )
            except Exception as exc:
                logger.error("[PaperTradingNotifier] lark push failed: %s", exc)
                results.append(
                    PushResult(channel="lark", success=False, error=str(exc),
                               extra={"content_type": content_type})
                )

        if self.dingtalk_webhook_url:
            try:
                ok = self._send_dingtalk(header, markdown)
                results.append(
                    PushResult(channel="dingtalk", success=ok,
                               extra={"content_type": content_type})
                )
            except Exception as exc:
                logger.error("[PaperTradingNotifier] dingtalk push failed: %s", exc)
                results.append(
                    PushResult(channel="dingtalk", success=False, error=str(exc),
                               extra={"content_type": content_type})
                )

        if self.broadcast_enabled:
            try:
                ok = self._broadcast(markdown)
                results.append(
                    PushResult(channel="broadcast", success=ok,
                               extra={"content_type": content_type})
                )
            except Exception as exc:
                logger.error("[PaperTradingNotifier] broadcast failed: %s", exc)
                results.append(
                    PushResult(channel="broadcast", success=False, error=str(exc),
                               extra={"content_type": content_type})
                )

        if not results:
            logger.warning(
                "[PaperTradingNotifier] no channels configured; skip %s", content_type
            )
            results.append(
                PushResult(channel="skipped", success=False,
                           error="no channels configured",
                           extra={"content_type": content_type})
            )

        return results

    # ------------------------------------------------------------------
    # Targeted transports
    # ------------------------------------------------------------------

    def _send_lark(self, header: str, markdown: str) -> bool:
        """Send to feishu/lark via interactive card (lark_md).

        Splits content if it exceeds ``lark_max_bytes``.
        """
        if not self.lark_webhook_url:
            return False

        chunks = self._chunk_text(markdown, self.lark_max_bytes)
        total = len(chunks)
        all_ok = True
        for idx, chunk in enumerate(chunks, start=1):
            title = header if total == 1 else f"{header} ({idx}/{total})"
            payload = {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": title[:50],
                        }
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": chunk},
                        }
                    ],
                },
            }
            ok = self._post_json(self.lark_webhook_url, payload)
            all_ok = all_ok and ok
        return all_ok

    def _send_dingtalk(self, header: str, markdown: str) -> bool:
        """Send to dingtalk via markdown message.

        DingTalk markdown messages have a ~20KB limit per message; longer
        content is split into multiple messages.
        """
        if not self.dingtalk_webhook_url:
            return False

        url = self.dingtalk_webhook_url
        if self.dingtalk_secret:
            url = self._sign_dingtalk_url(url, self.dingtalk_secret)

        chunks = self._chunk_text(markdown, self.dingtalk_max_bytes)
        total = len(chunks)
        all_ok = True
        for idx, chunk in enumerate(chunks, start=1):
            title = header if total == 1 else f"{header} ({idx}/{total})"
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title[:50],
                    "text": chunk,
                },
            }
            ok = self._post_json(url, payload)
            all_ok = all_ok and ok
        return all_ok

    # ------------------------------------------------------------------
    # Broadcast transport (delegates to NotificationService)
    # ------------------------------------------------------------------

    def _broadcast(self, markdown: str) -> bool:
        """Delegate to the global NotificationService (multi-channel fan-out)."""
        service = self._get_broadcast_service()
        if service is None:
            logger.warning(
                "[PaperTradingNotifier] broadcast service unavailable; skip"
            )
            return False
        try:
            return bool(service.send(markdown))
        except Exception as exc:
            logger.error("[PaperTradingNotifier] broadcast send error: %s", exc)
            return False

    def _get_broadcast_service(self):
        """Lazy-build the global NotificationService."""
        if self._broadcast_service is not None:
            return self._broadcast_service
        try:
            from src.notification import NotificationService
            self._broadcast_service = NotificationService()
            logger.info("[PaperTradingNotifier] broadcast service initialized")
        except Exception as exc:
            logger.warning(
                "[PaperTradingNotifier] failed to init broadcast service: %s", exc
            )
            self._broadcast_service = None
        return self._broadcast_service

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _post_json(self, url: str, payload: Dict[str, Any]) -> bool:
        """POST JSON to a webhook and inspect the response.

        Returns True when the response indicates success (status 200 AND
        the JSON body's code/StatusCode field is 0).
        """
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout_seconds,
                verify=self.verify_ssl,
            )
        except requests.RequestException as exc:
            logger.error("[PaperTradingNotifier] HTTP error: %s", exc)
            return False

        if response.status_code != 200:
            logger.error(
                "[PaperTradingNotifier] HTTP %s: %s",
                response.status_code,
                response.text[:300],
            )
            return False

        try:
            body = response.json()
        except ValueError:
            # Some webhooks return plain text; treat 200 as success.
            return True

        # Lark uses "code", dingtalk uses "errcode", some use "StatusCode"
        code = body.get("code", body.get("errcode", body.get("StatusCode")))
        if code in (0, None):
            return True

        msg = body.get("msg") or body.get("errmsg") or "unknown error"
        logger.error(
            "[PaperTradingNotifier] webhook returned code=%s msg=%s", code, msg
        )
        return False

    # ------------------------------------------------------------------
    # DingTalk signature
    # ------------------------------------------------------------------

    @staticmethod
    def _sign_dingtalk_url(url: str, secret: str) -> str:
        """Append timestamp & sign to a dingtalk webhook URL."""
        import base64
        import hashlib
        import hmac
        import time
        import urllib.parse

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{url}&timestamp={timestamp}&sign={sign}"

    # ------------------------------------------------------------------
    # Chunking helper
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_text(text: str, max_bytes: int) -> List[str]:
        """Split ``text`` into chunks each <= ``max_bytes`` UTF-8 bytes.

        Splits on paragraph boundaries (``\\n\\n``) when possible; falls back
        to line boundaries; final fallback is hard character truncation (the
        truncation point is the largest character offset whose UTF-8 byte
        length fits within ``max_bytes``).
        """
        if not text:
            return []
        if len(text.encode("utf-8")) <= max_bytes:
            return [text]

        chunks: List[str] = []
        current: List[str] = []
        current_bytes = 0

        # Try paragraph-boundary splits first
        paragraphs = text.split("\n\n")
        for para in paragraphs:
            para_bytes = len(para.encode("utf-8")) + 2  # include "\n\n"
            if para_bytes > max_bytes:
                # Flush current
                if current:
                    chunks.append("\n\n".join(current))
                    current = []
                    current_bytes = 0
                # Hard-split this paragraph by lines
                for line in para.split("\n"):
                    line_bytes = len(line.encode("utf-8")) + 1
                    # Line itself exceeds limit -> byte-level truncation
                    if line_bytes > max_bytes:
                        PaperTradingNotifier._append_truncated(
                            chunks, line, max_bytes
                        )
                        continue
                    if current_bytes + line_bytes > max_bytes:
                        if current:
                            chunks.append("\n".join(current))
                        current = [line]
                        current_bytes = line_bytes
                    else:
                        current.append(line)
                        current_bytes += line_bytes
                continue

            if current_bytes + para_bytes > max_bytes:
                if current:
                    chunks.append("\n\n".join(current))
                current = [para]
                current_bytes = para_bytes
            else:
                current.append(para)
                current_bytes += para_bytes

        if current:
            chunks.append("\n\n".join(current) if len(paragraphs) > 1 else "\n".join(current))

        return chunks or [text]

    @staticmethod
    def _append_truncated(chunks: List[str], line: str, max_bytes: int) -> None:
        """Append ``line`` to ``chunks`` in segments each <= ``max_bytes``."""
        encoded = line.encode("utf-8")
        if len(encoded) <= max_bytes:
            chunks.append(line)
            return
        # Decode incrementally to avoid splitting a multi-byte char
        start = 0
        while start < len(encoded):
            end = min(start + max_bytes, len(encoded))
            # Walk back until the slice decodes cleanly
            while end > start:
                try:
                    segment = encoded[start:end].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    end -= 1
            else:
                segment = ""
            if segment:
                chunks.append(segment)
            start = end

    # ------------------------------------------------------------------
    # Markdown renderers
    # ------------------------------------------------------------------

    def _render_battle_plan_markdown(
        self, plan: Dict[str, Any], target_date: Any
    ) -> str:
        """Render a battle plan dict into a compact markdown summary."""
        lines: List[str] = []
        sentiment = int(plan.get("sentiment_score", 50) or 50)
        sentiment_text = (
            "偏多" if sentiment >= 60 else ("偏空" if sentiment < 40 else "中性")
        )
        main_theme = plan.get("main_theme") or ""
        lines.append(f"**计划日期**: {target_date}")
        lines.append(f"**情绪分**: {sentiment}/100 ({sentiment_text})")
        if main_theme:
            lines.append(f"**主线题材**: {main_theme}")
        lines.append("")

        review = plan.get("market_review")
        if review:
            lines.append("### 市场综述")
            lines.append(review)
            lines.append("")

        holdings = plan.get("holdings_plans") or []
        if holdings:
            lines.append("### 持仓应对方案")
            for h in holdings:
                lines.append(
                    f"- **{h.get('code', '')} {h.get('name', '') or ''}**"
                )
                if h.get("strong_scenario"):
                    lines.append(f"  - 🟢 强势: {h['strong_scenario']}")
                if h.get("neutral_scenario"):
                    lines.append(f"  - 🟡 中性: {h['neutral_scenario']}")
                if h.get("weak_scenario"):
                    lines.append(f"  - 🔴 弱势: {h['weak_scenario']}")
                sl = h.get("stop_loss")
                tp1 = h.get("take_profit_1")
                tp2 = h.get("take_profit_2")
                lines.append(f"  - 三线: SL={sl or 'N/A'} TP1={tp1 or 'N/A'} TP2={tp2 or 'N/A'}")
            lines.append("")

        candidates = plan.get("candidates") or []
        if candidates:
            lines.append("### 候选标的")
            for c in candidates:
                lines.append(
                    f"- **{c.get('code', '')} {c.get('name', '') or ''}** "
                    f"(评分 {float(c.get('technical_score', 0) or 0):.1f})"
                )
                if c.get("auction_condition"):
                    lines.append(f"  - 集合竞价: {c['auction_condition']}")
                if c.get("intraday_trigger"):
                    lines.append(f"  - 盘中触发: {c['intraday_trigger']}")
                pos_ratio = float(c.get("position_ratio", 0) or 0)
                lines.append(f"  - 建议仓位: {pos_ratio * 100:.0f}%")
                sl = c.get("stop_loss")
                tp1 = c.get("take_profit_1")
                tp2 = c.get("take_profit_2")
                lines.append(f"  - 三线: SL={sl or 'N/A'} TP1={tp1 or 'N/A'} TP2={tp2 or 'N/A'}")
            lines.append("")

        lines.append(
            f"_由 PaperTradingNotifier 推送 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
        )
        return "\n".join(lines)

    def _render_reflection_markdown(self, r: Dict[str, Any]) -> str:
        """Render a single reflection note as markdown."""
        mood = r.get("mood", "neutral")
        mood_emoji = {"positive": "😊", "negative": "😰", "neutral": "😐"}.get(
            mood, "😐"
        )
        lines: List[str] = []
        subject = r.get("subject") or "基金经理复盘笔记"
        lines.append(f"### {mood_emoji} {subject}")
        lines.append(
            f"- 范围: {r.get('scope', 'adhoc')}  |  标签: {r.get('tags', '') or '-'}"
        )
        if r.get("summary"):
            lines.append(f"- 摘要: {r['summary']}")
        if r.get("takeaway"):
            lines.append(f"- **Takeaway**: {r['takeaway']}")
        lessons = r.get("lessons") or []
        if lessons:
            lines.append("- 经验教训:")
            for lesson in lessons:
                lines.append(f"  - {lesson}")
        if r.get("code"):
            lines.append(f"- 关联标的: {r['code']}")
        lines.append("")
        lines.append(
            f"_由 PaperTradingNotifier 推送 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers for daily_report input
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_markdown(daily_report: Any) -> str:
        """Extract markdown text from a DailyReportResult or dict."""
        if daily_report is None:
            return ""
        # DailyReportResult dataclass
        markdown = getattr(daily_report, "markdown", None)
        if markdown:
            return markdown
        # dict-like
        if isinstance(daily_report, dict):
            return str(daily_report.get("markdown") or "")
        return ""

    @staticmethod
    def _extract_date(daily_report: Any) -> str:
        """Extract a date string from a DailyReportResult or dict."""
        target_date = getattr(daily_report, "target_date", None)
        if target_date is None and isinstance(daily_report, dict):
            target_date = daily_report.get("target_date")
        if target_date is None:
            return datetime.now().date().isoformat()
        if hasattr(target_date, "isoformat"):
            return target_date.isoformat()
        return str(target_date)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_paper_trading_notifier(
    config: Optional[Any] = None,
    account_id: int = 0,
) -> PaperTradingNotifier:
    """Build a :class:`PaperTradingNotifier` from application config.

    Args:
        config: Application config. If None, ``get_config()`` is called.
        account_id: Paper trading account id (for logging / future routing).

    Returns:
        A configured :class:`PaperTradingNotifier`.
    """
    if config is None:
        from src.config import get_config
        config = get_config()
    return PaperTradingNotifier(config=config, account_id=account_id)


__all__ = [
    "PaperTradingNotifier",
    "PushResult",
    "build_paper_trading_notifier",
]
