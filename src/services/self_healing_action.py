# -*- coding: utf-8 -*-
"""
Self-Healing Action 抽象基类 —— L3 架构级自修复的统一动作模型。

将 ConfigAutoRollback 已验证的"检测 → 修复 → 验证"闭环抽象为基类，
所有 L3 修复动作（重启、回滚、降级、patch）继承此基类。

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 2 / Finding #5
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RepairStatus(str, Enum):
    """修复动作状态。"""
    PENDING = "pending"          # 待执行
    IN_PROGRESS = "in_progress"  # 执行中
    SUCCESS = "success"          # 修复成功（通过验证）
    FAILED = "failed"            # 修复失败（未通过验证）
    ESCALATED = "escalated"      # 已升级到更强的修复策略


@dataclass
class RepairRecord:
    """一次修复动作的完整记录。"""

    repair_id: str                              # "repair_{timestamp}_{hash}"
    action_type: str                            # "restart" | "rollback" | "degrade" | "patch"
    target: str                                 # 修复目标（module_id / config_key / capability_id）
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    status: str = RepairStatus.PENDING.value
    verification_result: Optional[bool] = None  # None = 未验证
    verification_detail: str = ""
    escalation_level: int = 0                   # 已升级次数
    escalated_to: Optional[str] = None          # 升级到的修复动作类型
    error_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class SelfHealingAction(ABC):
    """L3 自修复动作抽象基类。

    所有 L3 修复动作（重启、回滚、降级、patch）必须实现三个核心方法：
    1. _detect()  — 故障检测: 是否需要执行此修复？
    2. _repair()  — 修复执行: 执行修复动作
    3. _verify()  — 修复验证: 修复是否解决了问题？

    基类提供升级链（escalation chain）：当修复验证失败时自动升级到下一个策略。

    用法:
        class MyRestartAction(SelfHealingAction):
            def _detect(self, context: Dict[str, Any]) -> bool:
                return context.get("consecutive_failures", 0) >= 3

            def _repair(self, context: Dict[str, Any]) -> Tuple[bool, str]:
                # 执行重启
                return True, "restarted"

            def _verify(self, context: Dict[str, Any]) -> Tuple[bool, str]:
                # 验证健康
                return True, "healthy"
    """

    # 升级链：当此修复验证失败时，按顺序尝试的备选修复动作类型
    # 子类覆盖此字段定义自己的升级链
    escalation_chain: List[str] = []  # 如 ["restart", "rollback", "notify_human"]

    # 最大升级次数（防止无限升级）
    max_escalation_level: int = 3

    def __init__(
        self,
        action_type: str,
        target: str,
        on_escalate: Optional[Callable[[str, int, str], None]] = None,
        on_complete: Optional[Callable[[RepairRecord], None]] = None,
    ) -> None:
        self._action_type = action_type
        self._target = target
        self._on_escalate = on_escalate    # 升级回调
        self._on_complete = on_complete    # 完成回调
        self._repair_history: List[RepairRecord] = []

    # ---------- 抽象方法（子类必须实现） ----------

    @abstractmethod
    def _detect(self, context: Dict[str, Any]) -> bool:
        """检测是否需要执行此修复。

        Args:
            context: 故障上下文（健康指标、错误计数等）。

        Returns:
            True 如果需要修复。
        """
        ...

    @abstractmethod
    def _repair(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """执行修复动作。

        Args:
            context: 故障上下文。

        Returns:
            (success, detail_message)
        """
        ...

    @abstractmethod
    def _verify(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """验证修复是否解决了问题。

        Args:
            context: 修复后的上下文（应反映修复后状态）。

        Returns:
            (verified, detail_message)
        """
        ...

    # ---------- 模板方法 ----------

    def execute(self, context: Dict[str, Any]) -> RepairRecord:
        """执行完整的"检测 → 修复 → 验证"闭环。

        这是 SelfHealingAction 的核心模板方法。子类不应覆盖此方法——
        而是实现 _detect / _repair / _verify 三个抽象方法。

        Args:
            context: 故障上下文字典。

        Returns:
            RepairRecord: 完整的修复记录。
        """
        ts = int(time.time() * 1000)
        record = RepairRecord(
            repair_id=f"repair_{ts}_{self._action_type}",
            action_type=self._action_type,
            target=self._target,
            status=RepairStatus.IN_PROGRESS.value,
        )

        # Step 1: 检测
        if not self._detect(context):
            record.status = RepairStatus.PENDING.value
            record.verification_detail = "Detection returned False — no repair needed"
            self._repair_history.append(record)
            return record

        # Step 2: 修复
        try:
            ok, msg = self._repair(context)
            record.error_message = "" if ok else msg
        except Exception as exc:
            ok, msg = False, str(exc)
            record.error_message = msg
            logger.exception("Repair action '%s' raised", self._action_type)

        # Step 3: 验证
        if ok:
            verified, verify_msg = self._verify(context)
            record.verification_result = verified
            record.verification_detail = verify_msg

            if verified:
                record.status = RepairStatus.SUCCESS.value
            else:
                # 验证失败 → 尝试升级
                record.status = RepairStatus.FAILED.value
                escalated = self._try_escalate(record, context)
                if escalated:
                    record.status = RepairStatus.ESCALATED.value
        else:
            record.verification_result = False
            record.verification_detail = msg
            record.status = RepairStatus.FAILED.value
            # 修复本身失败 → 也尝试升级
            self._try_escalate(record, context)

        record.completed_at = datetime.now()
        self._repair_history.append(record)

        if self._on_complete:
            try:
                self._on_complete(record)
            except Exception:
                logger.exception("on_complete callback failed")

        return record

    def _try_escalate(
        self, record: RepairRecord, context: Dict[str, Any]
    ) -> bool:
        """尝试升级到下一个修复策略。

        从 escalation_chain 中按顺序选择下一个策略。
        如果已到达 max_escalation_level 或链已耗尽，不再升级。
        """
        if record.escalation_level >= self.max_escalation_level:
            logger.warning(
                "Max escalation level (%d) reached for action '%s' on '%s'",
                self.max_escalation_level, self._action_type, self._target,
            )
            return False

        chain = self.escalation_chain
        if not chain:
            return False

        # 找到当前 action_type 在 chain 中的位置，取下一个
        try:
            idx = chain.index(self._action_type)
            next_action = chain[idx + 1] if idx + 1 < len(chain) else chain[-1]
        except ValueError:
            next_action = chain[0]

        if next_action == self._action_type:
            return False  # 链已耗尽

        record.escalation_level += 1
        record.escalated_to = next_action

        if self._on_escalate:
            try:
                self._on_escalate(next_action, record.escalation_level, record.verification_detail)
            except Exception:
                logger.exception("on_escalate callback failed")

        logger.warning(
            "Self-healing escalated: %s → %s (level=%d, target=%s, reason=%s)",
            self._action_type, next_action,
            record.escalation_level, self._target, record.verification_detail,
        )

        return True

    def get_history(self, limit: int = 20) -> List[RepairRecord]:
        """获取修复历史。"""
        return self._repair_history[-limit:]

    def stats(self) -> Dict[str, Any]:
        """获取修复统计。"""
        total = len(self._repair_history)
        successes = sum(
            1 for r in self._repair_history
            if r.status == RepairStatus.SUCCESS.value
        )
        failures = sum(
            1 for r in self._repair_history
            if r.status == RepairStatus.FAILED.value
        )
        escalations = sum(
            1 for r in self._repair_history
            if r.status == RepairStatus.ESCALATED.value
        )
        return {
            "action_type": self._action_type,
            "target": self._target,
            "total_repairs": total,
            "successes": successes,
            "failures": failures,
            "escalations": escalations,
            "success_rate": successes / max(total, 1),
            "verification_rate": (
                sum(1 for r in self._repair_history if r.verification_result is not None)
                / max(total, 1)
            ),
        }
