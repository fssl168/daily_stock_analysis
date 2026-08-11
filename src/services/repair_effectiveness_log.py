# -*- coding: utf-8 -*-
"""
修复效果日志（RepairEffectivenessLog）—— L3 策略学习的数据基础。

记录每次修复动作的实际效果，周期性分析修复策略的有效性，
并将分析结果提供给 L4 元认知引擎作为学习输入。

核心数据流:
    L3 修复动作 → RepairEffectivenessEntry → 周期性 _analyze_effectiveness()
    → EffectivenessReport → 调整策略优先级 / L4 元认知输入

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 3 / Finding #4
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RepairOutcome(str, Enum):
    """修复结果分类。"""
    RESTORED = "restored"              # 修复后系统恢复正常
    DEGRADED_AFTER = "degraded_after"  # 修复后短期内再次故障
    NO_EFFECT = "no_effect"            # 修复无效果（故障持续）
    MADE_WORSE = "made_worse"          # 修复使情况更糟
    UNKNOWN = "unknown"                # 无法判断（观察窗口不足）


@dataclass
class RepairEffectivenessEntry:
    """单次修复效果记录。"""

    entry_id: str                               # "eff_{timestamp}_{hash}"
    repair_id: str                              # 关联的 RepairRecord.repair_id
    action_type: str                            # "restart" | "rollback" | "degrade"
    target: str                                 # 修复目标
    performed_at: datetime = field(default_factory=datetime.now)
    outcome: str = RepairOutcome.UNKNOWN.value
    time_to_next_failure_seconds: Optional[float] = None  # 修复后多久再次故障（None=未再故障）
    observation_window_seconds: int = 3600       # 观察窗口（默认 1 小时）
    pre_repair_health: Dict[str, Any] = field(default_factory=dict)
    post_repair_health: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "repair_id": self.repair_id,
            "action_type": self.action_type,
            "target": self.target,
            "performed_at": self.performed_at.isoformat(),
            "outcome": self.outcome,
            "time_to_next_failure_seconds": self.time_to_next_failure_seconds,
            "observation_window_seconds": self.observation_window_seconds,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepairEffectivenessEntry":
        return cls(
            entry_id=data["entry_id"],
            repair_id=data["repair_id"],
            action_type=data["action_type"],
            target=data["target"],
            performed_at=datetime.fromisoformat(data["performed_at"]),
            outcome=data.get("outcome", RepairOutcome.UNKNOWN.value),
            time_to_next_failure_seconds=data.get("time_to_next_failure_seconds"),
            observation_window_seconds=data.get("observation_window_seconds", 3600),
            metadata=data.get("metadata", {}),
        )


@dataclass
class EffectivenessReport:
    """修复效果分析报告——周期性产出，供 L3 策略调整和 L4 元认知使用。"""

    generated_at: datetime
    analysis_window_hours: int
    total_repairs: int
    by_action_type: Dict[str, Dict[str, Any]]   # action_type → {total, restored, degraded, no_effect, made_worse, effectiveness_score}
    by_target: Dict[str, Dict[str, Any]]         # target → 同上
    worst_performers: List[str]                  # 效果最差的修复策略（应降级）
    best_performers: List[str]                   # 效果最好的修复策略（应优先）
    recommendations: List[str]                   # 策略调整建议


class RepairEffectivenessLog:
    """修复效果日志——记录、分析、持久化修复动作的实际效果。

    用法:
        log = RepairEffectivenessLog(persist_path=Path("data/repair_effectiveness.json"))

        # 记录修复
        entry = log.record(
            repair_id="repair_xxx",
            action_type="restart",
            target="market_listener",
            pre_repair_health={"consecutive_failures": 3},
            post_repair_health={"healthy": True},
        )

        # 回填结果
        log.update_outcome(entry.entry_id, RepairOutcome.RESTORED, time_to_next_failure=3600)

        # 周期性分析
        report = log.analyze_effectiveness(window_hours=24)
    """

    _MAX_ENTRIES = 500

    def __init__(
        self,
        persist_path: Optional[Path] = None,
        observation_window_seconds: int = 3600,
    ) -> None:
        self._entries: List[RepairEffectivenessEntry] = []
        self._persist_path = persist_path
        self._observation_window_seconds = observation_window_seconds
        self._lock = threading.RLock()
        self._counter: int = 0

        # 从磁盘加载历史
        if persist_path and persist_path.exists():
            self._load()

    # ==================================================================
    # 记录
    # ==================================================================

    def record(
        self,
        repair_id: str,
        action_type: str,
        target: str,
        pre_repair_health: Optional[Dict[str, Any]] = None,
        post_repair_health: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RepairEffectivenessEntry:
        """记录一次修复动作。"""
        self._counter += 1
        ts = int(time.time() * 1000)
        entry = RepairEffectivenessEntry(
            entry_id=f"eff_{self._counter}_{ts}_{action_type}",
            repair_id=repair_id,
            action_type=action_type,
            target=target,
            observation_window_seconds=self._observation_window_seconds,
            pre_repair_health=pre_repair_health or {},
            post_repair_health=post_repair_health or {},
            metadata=metadata or {},
        )

        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._MAX_ENTRIES:
                self._entries = self._entries[-self._MAX_ENTRIES:]
            self._save()

        return entry

    def update_outcome(
        self,
        entry_id: str,
        outcome: RepairOutcome,
        time_to_next_failure_seconds: Optional[float] = None,
    ) -> bool:
        """回填修复效果。"""
        with self._lock:
            for entry in self._entries:
                if entry.entry_id == entry_id:
                    entry.outcome = outcome.value
                    entry.time_to_next_failure_seconds = time_to_next_failure_seconds
                    self._save()
                    return True
        return False

    # ==================================================================
    # 分析
    # ==================================================================

    def analyze_effectiveness(
        self, window_hours: int = 24
    ) -> EffectivenessReport:
        """分析指定窗口内的修复效果。

        为每个 (action_type, target) 组合计算 effectiveness_score:
            score = (restored - degraded_after - made_worse) / total
        正值表示修复有效，负值表示弊大于利，0 表示无效果。

        基于分析结果生成策略调整建议。
        """
        cutoff = datetime.now() - timedelta(hours=window_hours)

        with self._lock:
            recent = [e for e in self._entries if e.performed_at >= cutoff]

        if not recent:
            return EffectivenessReport(
                generated_at=datetime.now(),
                analysis_window_hours=window_hours,
                total_repairs=0,
                by_action_type={},
                by_target={},
                worst_performers=[],
                best_performers=[],
                recommendations=["No repair data in window — insufficient data for learning"],
            )

        # 按 action_type 聚合
        by_action: Dict[str, Dict[str, Any]] = {}
        for entry in recent:
            if entry.action_type not in by_action:
                by_action[entry.action_type] = {
                    "total": 0, "restored": 0, "degraded_after": 0,
                    "no_effect": 0, "made_worse": 0, "unknown": 0,
                }
            agg = by_action[entry.action_type]
            agg["total"] += 1
            agg[entry.outcome] = agg.get(entry.outcome, 0) + 1

        # 按 target 聚合
        by_target: Dict[str, Dict[str, Any]] = {}
        for entry in recent:
            if entry.target not in by_target:
                by_target[entry.target] = {
                    "total": 0, "restored": 0, "degraded_after": 0,
                    "no_effect": 0, "made_worse": 0, "unknown": 0,
                }
            agg = by_target[entry.target]
            agg["total"] += 1
            agg[entry.outcome] = agg.get(entry.outcome, 0) + 1

        # 计算 effectiveness_score
        scored_actions: List[Tuple[str, float]] = []
        for atype, agg in by_action.items():
            score = (
                agg["restored"] - agg["degraded_after"] - agg["made_worse"]
            ) / max(agg["total"], 1)
            agg["effectiveness_score"] = round(score, 3)
            scored_actions.append((atype, score))

        for tgt, agg in by_target.items():
            score = (
                agg["restored"] - agg["degraded_after"] - agg["made_worse"]
            ) / max(agg["total"], 1)
            agg["effectiveness_score"] = round(score, 3)

        scored_actions.sort(key=lambda x: x[1])

        # 生成建议
        recommendations: List[str] = []
        for atype, score in scored_actions:
            if score < -0.3:
                recommendations.append(
                    f"CRITICAL: '{atype}' has effectiveness_score={score:.2f} — "
                    f"consider disabling auto-{atype} and routing to human review"
                )
            elif score < 0:
                recommendations.append(
                    f"WARNING: '{atype}' has negative effectiveness ({score:.2f}) — "
                    f"reduce priority or increase verification strictness"
                )
            elif score > 0.5:
                recommendations.append(
                    f"GOOD: '{atype}' has high effectiveness ({score:.2f}) — "
                    f"keep as primary strategy"
                )

        return EffectivenessReport(
            generated_at=datetime.now(),
            analysis_window_hours=window_hours,
            total_repairs=len(recent),
            by_action_type=by_action,
            by_target=by_target,
            worst_performers=[a for a, s in scored_actions if s < 0][:3],
            best_performers=[a for a, s in scored_actions if s > 0][-3:],
            recommendations=recommendations,
        )

    # ==================================================================
    # 查询
    # ==================================================================

    def get_entries_by_target(
        self, target: str, limit: int = 50
    ) -> List[RepairEffectivenessEntry]:
        """获取指定目标的修复效果记录。"""
        with self._lock:
            return [e for e in self._entries if e.target == target][-limit:]

    def get_entries_by_action(
        self, action_type: str, limit: int = 50
    ) -> List[RepairEffectivenessEntry]:
        """获取指定修复类型的记录。"""
        with self._lock:
            return [e for e in self._entries if e.action_type == action_type][-limit:]

    def stats(self) -> Dict[str, Any]:
        """获取日志统计。"""
        with self._lock:
            total = len(self._entries)
            outcomes: Dict[str, int] = {}
            for e in self._entries:
                outcomes[e.outcome] = outcomes.get(e.outcome, 0) + 1

            return {
                "total_entries": total,
                "outcome_distribution": outcomes,
                "oldest_entry": (
                    self._entries[0].performed_at.isoformat() if self._entries else None
                ),
                "newest_entry": (
                    self._entries[-1].performed_at.isoformat() if self._entries else None
                ),
            }

    def reset(self) -> None:
        """重置日志（仅用于测试）。"""
        with self._lock:
            self._entries.clear()

    # ==================================================================
    # 持久化
    # ==================================================================

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = [e.to_dict() for e in self._entries[-self._MAX_ENTRIES:]]
            self._persist_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to save repair effectiveness log")

    def _load(self) -> None:
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._entries = [
                RepairEffectivenessEntry.from_dict(item)
                for item in data[-self._MAX_ENTRIES:]
            ]
        except Exception:
            logger.exception("Failed to load repair effectiveness log")
