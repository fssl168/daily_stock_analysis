# -*- coding: utf-8 -*-
"""Signal fusion engine (信号融合与冲突仲裁).

聚合多个策略对同一标的产生的 :class:`Signal`，输出单一的融合信号
:class:`FusedSignal`。参考架构文档 §4.2：

- MAJORITY_VOTE: 多数方向胜出（按信号数量投票，平票返回 None）
- WEIGHTED_VOTE: 按策略权重加权投票，优势方向占比 >= consensus_threshold
  才产生信号，否则返回 None（hold）
- CONFIDENCE_THRESHOLD: 信号置信度 >= 阈值的策略才参与融合
- ENSEMBLE: 集成模式，每个方向独立产生信号，不要求 60% 共识门槛

说明：现有 :class:`Signal`（paper_trading/strategies/engine/rule_engine.py）
没有置信度字段，因此 CONFIDENCE_THRESHOLD 模式下以策略权重（SoftMax
归一化后的 Sharpe）作为信号置信度的代理。

本模块只读复用现有 Signal 类型，不修改 rule_engine.py。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from paper_trading.strategies.engine.rule_engine import Signal

logger = logging.getLogger(__name__)


class FusionMethod(str, Enum):
    """信号融合方式。"""

    MAJORITY_VOTE = "majority_vote"  # 多数投票
    WEIGHTED_VOTE = "weighted_vote"  # 加权投票（策略近期 Sharpe 做权重）
    CONFIDENCE_THRESHOLD = "confidence"  # 置信度门槛
    ENSEMBLE = "ensemble"  # 集成（各策略独立开仓）


@dataclass
class FusedSignal:
    """融合后的单一信号。"""

    code: str
    side: str  # buy / sell / none
    confidence: float  # 融合后的置信度
    supporting_strategies: List[str]  # 支持该方向的策略
    opposing_strategies: List[str]  # 反对的策略
    weight: float  # 建议仓位权重 (0-1)
    method: FusionMethod
    details: Dict = field(default_factory=dict)


class SignalFusionEngine:
    """接收多个策略对同一股票的信号，输出融合后的单一信号。

    返回 None 表示无共识（hold）。
    """

    def __init__(
        self,
        method: FusionMethod = FusionMethod.WEIGHTED_VOTE,
        consensus_threshold: float = 0.60,
    ) -> None:
        self.method = method
        #: 加权共识 / 置信度阈值（对应 SIGNAL_FUSION_CONSENSUS_THRESHOLD=0.60）
        self.consensus_threshold = consensus_threshold
        #: strategy_name -> weight，由 update_weights_from_metrics 维护
        self._strategy_weights: Dict[str, float] = {}

    def update_weights_from_metrics(self, metrics: Dict[str, float]) -> None:
        """根据策略近期绩效动态更新权重。

        Args:
            metrics: {strategy_name: sharpe_ratio}，使用 SoftMax 归一化，
                Sharpe 越高权重越大；空输入清空权重。
        """
        if not metrics:
            self._strategy_weights = {}
            return
        values = [float(v) for v in metrics.values()]
        # 数值稳定性：先减去最大值再取 exp，避免溢出
        max_value = max(values)
        exp_values = [math.exp(v - max_value) for v in values]
        total = sum(exp_values)
        self._strategy_weights = {
            name: exp_v / total for name, exp_v in zip(metrics.keys(), exp_values)
        }

    # ------------------------------------------------------------------
    # 权重持久化（DB 支持，供重启后恢复真实权重）
    # ------------------------------------------------------------------

    def set_weights(self, weights: Dict[str, float]) -> None:
        """直接设置权重表（用于从 DB 加载）。"""
        self._strategy_weights = {str(k): float(v) for k, v in (weights or {}).items()}

    def get_weights(self) -> Dict[str, float]:
        """返回当前权重表（副本）。"""
        return dict(self._strategy_weights)

    def save_weights_to_db(self, *, batch_date: Optional[Any] = None) -> Dict[str, float]:
        """把当前权重持久化到 strategy_backtest_results 表（最新一批）。

        Returns:
            写入的权重表副本。
        """
        from datetime import date

        from src.storage import DatabaseManager, StrategyBacktestResult

        if not self._strategy_weights:
            return {}
        batch = batch_date or date.today()
        db = DatabaseManager.get_instance()
        stored = 0
        with db.get_session() as session:
            for name, weight in self._strategy_weights.items():
                try:
                    from sqlalchemy import select

                    existing = session.execute(
                        select(StrategyBacktestResult).where(
                            StrategyBacktestResult.strategy_name == name,
                            StrategyBacktestResult.batch_date == batch,
                            StrategyBacktestResult.eval_window_days == 250,
                            StrategyBacktestResult.engine_version == "v1",
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        existing.fusion_weight = weight
                        existing.computed_at = datetime.now()
                    else:
                        session.add(StrategyBacktestResult(
                            strategy_name=name,
                            batch_date=batch,
                            eval_window_days=250,
                            engine_version="v1",
                            fusion_weight=weight,
                        ))
                    stored += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("save weight %s failed: %s", name, exc)
            session.commit()
        logger.info("Fusion weights persisted: %s", stored)
        return dict(self._strategy_weights)

    @classmethod
    def load_weights_from_db(cls, *, batch_date: Optional[Any] = None) -> Dict[str, float]:
        """从 strategy_backtest_results 表加载最新权重。

        Args:
            batch_date: 指定批次日期；None 时加载最近一个批次的全部权重。

        Returns:
            {strategy_name: fusion_weight}。
        """
        from src.storage import DatabaseManager, StrategyBacktestResult

        db = DatabaseManager.get_instance()
        try:
            with db.get_session() as session:
                from sqlalchemy import select, desc, func

                if batch_date is None:
                    latest = session.execute(
                        select(func.max(StrategyBacktestResult.batch_date))
                        .where(StrategyBacktestResult.fusion_weight.is_not(None))
                    ).scalar()
                    if latest is None:
                        return {}
                    batch_date = latest
                rows = session.execute(
                    select(StrategyBacktestResult).where(
                        StrategyBacktestResult.batch_date == batch_date,
                        StrategyBacktestResult.fusion_weight.is_not(None),
                    )
                ).scalars().all()
                weights = {r.strategy_name: float(r.fusion_weight) for r in rows}
                logger.info("Fusion weights loaded from DB (batch=%s): %s", batch_date, weights)
                return weights
        except Exception as exc:  # noqa: BLE001 — 加载失败返回空（引擎用默认权重）
            logger.warning("load fusion weights failed: %s", exc)
            return {}

    def fuse(self, code: str, signals: List[Signal]) -> Optional[FusedSignal]:
        """融合多个策略的信号，返回 None 表示无共识（hold）。

        Args:
            code: 标的代码。
            signals: 各策略对该标的产生的信号列表。
        """
        if not signals:
            return None

        if self.method == FusionMethod.MAJORITY_VOTE:
            return self._majority_vote(code, signals)
        if self.method == FusionMethod.WEIGHTED_VOTE:
            return self._weighted_vote(code, signals)
        if self.method == FusionMethod.CONFIDENCE_THRESHOLD:
            return self._confidence_threshold(code, signals)
        if self.method == FusionMethod.ENSEMBLE:
            return self._ensemble(code, signals)
        raise ValueError(f"Unknown fusion method: {self.method!r}")

    # ------------------------------------------------------------------
    # 各融合方式实现
    # ------------------------------------------------------------------

    def _majority_vote(self, code: str, signals: List[Signal]) -> Optional[FusedSignal]:
        """多数投票：按信号数量（不加权）判断多数方向，平票返回 None。"""
        _, _, buy_strategies, sell_strategies = self._aggregate(signals)
        buy_n = len(buy_strategies)
        sell_n = len(sell_strategies)
        if buy_n == sell_n:
            return None  # 平票或无方向性信号，无多数
        if buy_n > sell_n:
            total = buy_n + sell_n
            return self._build(
                code,
                "buy",
                buy_n / total,
                buy_strategies,
                sell_strategies,
                FusionMethod.MAJORITY_VOTE,
                {"buy_votes": buy_n, "sell_votes": sell_n},
            )
        total = buy_n + sell_n
        return self._build(
            code,
            "sell",
            sell_n / total,
            sell_strategies,
            buy_strategies,
            FusionMethod.MAJORITY_VOTE,
            {"buy_votes": buy_n, "sell_votes": sell_n},
        )

    def _weighted_vote(self, code: str, signals: List[Signal]) -> Optional[FusedSignal]:
        """加权投票：优势方向占比 >= consensus_threshold 才产生信号，否则 None (hold)。"""
        buy_weight, sell_weight, buy_strategies, sell_strategies = self._aggregate(signals)
        total = buy_weight + sell_weight
        if total == 0:
            return None
        buy_ratio = buy_weight / total
        sell_ratio = sell_weight / total
        threshold = self.consensus_threshold
        if buy_ratio >= threshold:
            return self._build(
                code,
                "buy",
                buy_ratio,
                buy_strategies,
                sell_strategies,
                FusionMethod.WEIGHTED_VOTE,
                {"buy_ratio": buy_ratio, "sell_ratio": sell_ratio, "threshold": threshold},
            )
        if sell_ratio >= threshold:
            return self._build(
                code,
                "sell",
                sell_ratio,
                sell_strategies,
                buy_strategies,
                FusionMethod.WEIGHTED_VOTE,
                {"buy_ratio": buy_ratio, "sell_ratio": sell_ratio, "threshold": threshold},
            )
        return None  # 无共识，hold

    def _confidence_threshold(
        self, code: str, signals: List[Signal]
    ) -> Optional[FusedSignal]:
        """置信度门槛：信号置信度（策略权重）>= 阈值才参与融合。

        Signal 没有置信度字段，以策略权重作为信号置信度的代理；
        双方置信度相同时返回 None（无优势方向）。
        """
        threshold = self.consensus_threshold
        passing = [
            s
            for s in signals
            if s.side in ("buy", "sell")
            and self._strategy_weights.get(s.strategy_name, 1.0) >= threshold
        ]
        if not passing:
            return None
        buy_weight, sell_weight, buy_strategies, sell_strategies = self._aggregate(passing)
        if buy_weight == sell_weight:
            return None  # 双方置信度相同，无优势方向
        total = buy_weight + sell_weight
        if buy_weight > sell_weight:
            return self._build(
                code,
                "buy",
                buy_weight / total,
                buy_strategies,
                sell_strategies,
                FusionMethod.CONFIDENCE_THRESHOLD,
                {
                    "threshold": threshold,
                    "buy_confidence": buy_weight,
                    "sell_confidence": sell_weight,
                },
            )
        return self._build(
            code,
            "sell",
            sell_weight / total,
            sell_strategies,
            buy_strategies,
            FusionMethod.CONFIDENCE_THRESHOLD,
            {
                "threshold": threshold,
                "buy_confidence": buy_weight,
                "sell_confidence": sell_weight,
            },
        )

    def _ensemble(self, code: str, signals: List[Signal]) -> Optional[FusedSignal]:
        """集成模式：每个方向独立产生，不要求 60% 共识门槛，优势方向胜出。

        即使优势占比低于 0.60（例如 55/45）也会产生信号；双方权重相同返回 None。
        """
        buy_weight, sell_weight, buy_strategies, sell_strategies = self._aggregate(signals)
        total = buy_weight + sell_weight
        if total == 0:
            return None
        if buy_weight == sell_weight:
            return None  # 双方权重相同，无优势方向
        if buy_weight > sell_weight:
            return self._build(
                code,
                "buy",
                buy_weight / total,
                buy_strategies,
                sell_strategies,
                FusionMethod.ENSEMBLE,
                {"buy_weight": buy_weight, "sell_weight": sell_weight},
            )
        return self._build(
            code,
            "sell",
            sell_weight / total,
            sell_strategies,
            buy_strategies,
            FusionMethod.ENSEMBLE,
            {"buy_weight": buy_weight, "sell_weight": sell_weight},
        )

    # ------------------------------------------------------------------
    # 公共辅助
    # ------------------------------------------------------------------

    def _aggregate(
        self, signals: List[Signal]
    ) -> Tuple[float, float, List[str], List[str]]:
        """按方向聚合信号，返回 (buy_weight, sell_weight, buy_strategies, sell_strategies)。

        未设置权重的策略按 1.0 计；side=none 的信号不参与融合。
        """
        buy_weight = 0.0
        sell_weight = 0.0
        buy_strategies: List[str] = []
        sell_strategies: List[str] = []
        for s in signals:
            w = self._strategy_weights.get(s.strategy_name, 1.0)
            if s.side == "buy":
                buy_weight += w
                buy_strategies.append(s.strategy_name)
            elif s.side == "sell":
                sell_weight += w
                sell_strategies.append(s.strategy_name)
        return buy_weight, sell_weight, buy_strategies, sell_strategies

    @staticmethod
    def _build(
        code: str,
        side: str,
        confidence: float,
        supporting: List[str],
        opposing: List[str],
        method: FusionMethod,
        details: Dict,
    ) -> FusedSignal:
        """构造融合信号：建议仓位权重 = 置信度 * 0.5（最大半仓，与架构文档一致）。"""
        return FusedSignal(
            code=code,
            side=side,
            confidence=confidence,
            supporting_strategies=supporting,
            opposing_strategies=opposing,
            weight=confidence * 0.5,
            method=method,
            details=details,
        )

    # ------------------------------------------------------------------
    # Drift-based weight adjustment (T-010)
    # ------------------------------------------------------------------

    def update_weights_from_drift(self, drift_reports: Dict[str, Any]) -> None:
        """Adjust strategy weights based on drift-detector recommendations.

        Mapping:
        - ``reduce_weight`` → multiply weight by 0.5.
        - ``pause`` → set weight to 0.0 (strategy remains registered).
        - ``retire`` → remove the strategy from the weight table entirely.
        """
        for name, report in drift_reports.items():
            action = getattr(report, "recommended_action", "keep")
            if action == "reduce_weight":
                old = self._strategy_weights.get(name, 1.0)
                self._strategy_weights[name] = old * 0.5
                logger.info(
                    "Drift: %s → reduce_weight (%.2f → %.2f)", name, old, old * 0.5,
                )
            elif action == "pause":
                self._strategy_weights[name] = 0.0
                logger.warning("Drift: %s → paused (weight=0.0)", name)
            elif action == "retire":
                self._strategy_weights.pop(name, None)
                logger.critical("Drift: %s → retired (removed from active weights)", name)
