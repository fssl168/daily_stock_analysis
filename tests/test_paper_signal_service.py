# -*- coding: utf-8 -*-
"""paper_signal_service 单元测试：action→side 映射 / 幂等转换 / 兜底 job。

聚焦可离线验证的纯逻辑（不依赖真实 TradingEngine 下单）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.services.paper_signal_service import (
    _BUY_ACTIONS,
    _SELL_ACTIONS,
    _HOLD_ACTIONS,
    _side_for_action,
    _suggested_quantity_for_signal,
    get_default_trading_account,
    convert_and_place,
    convert_pending_signals_job,
)


# ── 1. action → side 映射 ───────────────────────────────────

class TestActionToSide:
    @pytest.mark.parametrize("action", sorted(_BUY_ACTIONS))
    def test_buy_actions_map_to_buy(self, action):
        assert _side_for_action(action) == "buy"

    @pytest.mark.parametrize("action", sorted(_SELL_ACTIONS))
    def test_sell_actions_map_to_sell(self, action):
        assert _side_for_action(action) == "sell"

    @pytest.mark.parametrize("action", sorted(_HOLD_ACTIONS))
    def test_hold_actions_map_to_none(self, action):
        assert _side_for_action(action) is None

    def test_unknown_action_maps_to_none(self):
        assert _side_for_action("mystery_action") is None

    def test_case_insensitive(self):
        assert _side_for_action("BUY") == "buy"
        assert _side_for_action("Reduce") == "sell"

    def test_empty_action_maps_to_none(self):
        assert _side_for_action("") is None
        assert _side_for_action(None) is None


# ── 2. 建议数量 ─────────────────────────────────────────────

class TestSuggestedQuantity:
    def test_buy_suggests_one_lot(self):
        sig = SimpleNamespace(action="buy")
        assert _suggested_quantity_for_signal(sig, account_id=3) == 100.0

    def test_sell_suggests_none(self):
        sig = SimpleNamespace(action="sell")
        assert _suggested_quantity_for_signal(sig, account_id=3) is None


# ── 3. 幂等转换（mock 掉真实下单路径）───────────────────────

class TestConvertAndPlaceIdempotent:
    def _fake_repo(self, monkeypatch, status="active"):
        """替换 DecisionSignalRepository，记录 update_status 调用。"""
        calls = {"updates": []}

        class FakeRepo:
            def update_status(self, signal_id, *, status, metadata_json=None,
                              replace_metadata=False):
                calls["updates"].append((signal_id, status))
                return SimpleNamespace(id=signal_id, status=status)

        monkeypatch.setattr(
            "src.repositories.decision_signal_repo.DecisionSignalRepository",
            lambda *a, **k: FakeRepo(),
        )
        return calls

    def test_non_active_signal_skipped(self, monkeypatch):
        calls = self._fake_repo(monkeypatch)
        sig = SimpleNamespace(id=1, status="expired", action="buy")
        result = convert_and_place(sig)
        assert result["converted"] is False
        assert result["reason"] == "status=expired"
        assert calls["updates"] == []  # 不触碰非 active

    def test_hold_action_marks_consumed_no_order(self, monkeypatch):
        calls = self._fake_repo(monkeypatch)
        sig = SimpleNamespace(
            id=7, status="active", action="hold",
            stock_code="000001", stock_name="平安银行",
            market="cn", reason="观望",
        )
        result = convert_and_place(sig)
        assert result["converted"] is True
        assert result["order_created"] is False
        assert result["side"] is None
        assert calls["updates"] == [(7, "consumed")]

    def test_missing_price_marks_consumed_no_order(self, monkeypatch):
        calls = self._fake_repo(monkeypatch)
        sig = SimpleNamespace(
            id=8, status="active", action="buy",
            stock_code="600519", stock_name="贵州茅台",
            market="cn", entry_low=None, entry_high=None, reason="x",
        )
        result = convert_and_place(sig)
        assert result["converted"] is True
        assert result["order_created"] is False
        assert "missing_price_or_code" in result["reason"]
        assert calls["updates"] == [(8, "consumed")]

    def test_no_paper_account_skips(self, monkeypatch):
        self._fake_repo(monkeypatch)
        monkeypatch.setattr(
            "src.services.paper_signal_service.get_default_trading_account",
            lambda market: None,
        )
        sig = SimpleNamespace(
            id=9, status="active", action="buy",
            stock_code="600519", stock_name="贵州茅台",
            market="cn", entry_low=1600.0, entry_high=1650.0, reason="x",
        )
        result = convert_and_place(sig)
        assert result["converted"] is False
        assert result["reason"] == "no_paper_account"

    def test_second_call_is_skipped_after_consumed(self, monkeypatch):
        """幂等：status 已变 consumed 后再次调用直接跳过。"""
        calls = self._fake_repo(monkeypatch)
        sig = SimpleNamespace(
            id=10, status="consumed", action="buy",
            stock_code="600519", stock_name="贵州茅台",
            market="cn", entry_low=1600.0, reason="x",
        )
        result = convert_and_place(sig)
        assert result["converted"] is False
        assert result["reason"] == "status=consumed"
        assert calls["updates"] == []

    def test_real_flow_marks_consumed_before_submit(self, monkeypatch):
        """真实路径：buy + 有价格 → 走 TradingEngine.submit_signal。"""
        updated = {"called": False}

        class FakeRepo:
            def update_status(self, signal_id, *, status, metadata_json=None,
                              replace_metadata=False):
                updated["called"] = True
                return SimpleNamespace(id=signal_id, status=status)

        monkeypatch.setattr(
            "src.repositories.decision_signal_repo.DecisionSignalRepository",
            lambda *a, **k: FakeRepo(),
        )

        class FakeSignal:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        class FakeResult:
            status = "executed"

            def to_dict(self):
                return {"status": "executed"}

        submitted = {}

        class FakeTradingEngine:
            def submit_signal(self, **kw):
                submitted.update(kw)
                return FakeResult()

        monkeypatch.setattr(
            "paper_trading.trading_engine.TradingEngine",
            lambda *a, **k: FakeTradingEngine(),
        )
        monkeypatch.setattr(
            "paper_trading.strategies.Signal",
            FakeSignal,
        )
        from paper_trading.order import OrderType  # noqa: F401 — 确保真实枚举可用

        sig = SimpleNamespace(
            id=11, status="active", action="buy",
            stock_code="600519", stock_name="贵州茅台",
            market="cn", entry_low=1600.0, entry_high=1650.0, reason="测试买入",
        )
        result = convert_and_place(sig, order_type="market")
        assert result["converted"] is True
        assert result["order_created"] is True
        assert result["side"] == "buy"
        assert updated["called"] is True
        assert submitted["account_id"] == 3
        assert submitted["signal"].code == "600519"
        assert submitted["signal"].side == "buy"


# ── 4. 兜底 job ─────────────────────────────────────────────

class TestConvertPendingJob:
    def test_job_counts_scanned_and_converted(self, monkeypatch):
        """mock repo.list 返回混合状态信号，验证统计。"""
        signals = [
            SimpleNamespace(id=1, status="active", action="hold",
                            stock_code="A", stock_name="x", market="cn",
                            entry_low=None, entry_high=None, reason=""),
            SimpleNamespace(id=2, status="active", action="watch",
                            stock_code="B", stock_name="y", market="cn",
                            entry_low=None, entry_high=None, reason=""),
            SimpleNamespace(id=3, status="active", action="buy",
                            stock_code="C", stock_name="z", market="cn",
                            entry_low=10.0, entry_high=None, reason=""),
            SimpleNamespace(id=4, status="expired", action="buy",
                            stock_code="D", stock_name="w", market="cn",
                            entry_low=10.0, entry_high=None, reason=""),
        ]

        class FakeRepo:
            def expire_due_signals(self, now=None):
                return 0

            def list(self, **kwargs):
                return signals, len(signals)

            def update_status(self, signal_id, *, status, metadata_json=None,
                              replace_metadata=False):
                return SimpleNamespace(id=signal_id, status=status)

        monkeypatch.setattr(
            "src.repositories.decision_signal_repo.DecisionSignalRepository",
            lambda *a, **k: FakeRepo(),
        )
        # 每条都走 convert_and_place 的真实逻辑（hold/watch→consumed，
        # buy 有价格→会尝试 TradingEngine… 这里 mock 掉下单）
        monkeypatch.setattr(
            "paper_trading.trading_engine.TradingEngine",
            lambda *a, **k: SimpleNamespace(
                submit_signal=lambda **kw: SimpleNamespace(
                    status="executed", to_dict=lambda: {"status": "executed"}
                )
            ),
        )
        monkeypatch.setattr(
            "paper_trading.strategies.Signal",
            lambda **kw: SimpleNamespace(**kw),
        )

        summary = convert_pending_signals_job()
        assert summary["scanned"] == 4
        # hold/watch 转换成功（无订单）；buy 转换成功（有订单）；expired 跳过
        assert summary["converted"] == 3
        assert summary["failed"] == 0

    def test_job_handles_no_active_signals(self, monkeypatch):
        class FakeRepo:
            def expire_due_signals(self, now=None):
                return 0

            def list(self, **kwargs):
                return [], 0

        monkeypatch.setattr(
            "src.repositories.decision_signal_repo.DecisionSignalRepository",
            lambda *a, **k: FakeRepo(),
        )
        summary = convert_pending_signals_job()
        assert summary == {"scanned": 0, "converted": 0, "skipped": 0, "failed": 0}


# ── 5. 默认账户解析 ─────────────────────────────────────────

class TestDefaultAccount:
    def test_get_default_trading_account_returns_int(self):
        acct = get_default_trading_account("cn")
        assert acct is None or isinstance(acct, int)
