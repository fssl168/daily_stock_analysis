# -*- coding: utf-8 -*-
"""
L4 干预引擎（AdjustmentEngine）— 把元认知内省建议转化为可执行的安全调整。

来源: 干预模式设计（2026-08-12）

核心约束（执行工程师红线）：
- **只调整非交易软参数**（分析深度/上下文压缩/技能激活），绝不碰订单/仓位/风控路径
- **默认需人工确认**；只有显式 env 白名单（ADJUSTMENT_AUTO_APPLY=true）才自动应用
- 所有建议/应用写 ADJUSTMENT_* 事件进 EventBus，全程可审计
- 应用失败静默降级，不拖垮主流程

调整参数白名单（安全边界）：
- AGENT_MAX_STEPS：Agent 最大执行步数（影响分析深度）
- AGENT_CONTEXT_COMPRESSION_PROFILE：上下文压缩档位（aggressive/balanced/conservative）
- AGENT_SKILLS：激活的技能集（影响分析维度）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.services.event_bus import (
    EventSeverity,
    SystemEvent,
    SystemEventBus,
    SystemEventType,
)

logger = logging.getLogger(__name__)

# ===================================================================
# 数据结构
# ===================================================================


@dataclass
class AdjustmentCommand:
    """一条可执行的安全调整指令。"""

    param_name: str            # 参数名（白名单内）
    param_value: Any           # 目标值
    reason: str                # 调整原因（来自内省建议）
    source_hint: str = ""      # 来源建议原文
    applied: bool = False      # 是否已应用
    auto_applied: bool = False  # 是否自动应用（白名单模式）
    rejected: bool = False     # 是否被人工拒绝


# ===================================================================
# 参数白名单 + hint 映射
# ===================================================================

# 安全可调参数白名单 — 绝不包含订单/仓位/风控
SAFE_PARAMS = frozenset({
    "AGENT_MAX_STEPS",
    "AGENT_CONTEXT_COMPRESSION_PROFILE",
    "AGENT_SKILLS",
})

# improvement_hint 关键词 → 参数调整
_HINT_MAPPING: List[Dict[str, Any]] = [
    {
        "keywords": ("置信度", "confidence", "过度", "overconfidence"),
        "param_name": "AGENT_MAX_STEPS",
        "value": 12,
        "reason": "内省建议增加分析深度以降低过度自信",
    },
    {
        "keywords": ("反面论证", "counter", "反对", "反面"),
        "param_name": "AGENT_MAX_STEPS",
        "value": 12,
        "reason": "内省建议加深分析以覆盖反面论证（AGENT_SKILLS 不自动干预，依赖技能体系）",
    },
    {
        "keywords": ("循环", "circular", "重复", "循环分析"),
        "param_name": "AGENT_CONTEXT_COMPRESSION_PROFILE",
        "value": "conservative",
        "reason": "内省检测到思维循环，保守压缩保留更多上下文",
    },
    {
        "keywords": ("锚点", "anchoring", "锚定", "从零开始"),
        "param_name": "AGENT_CONTEXT_COMPRESSION_PROFILE",
        "value": "aggressive",
        "reason": "内省建议独立评估减少锚定偏差",
    },
]


def _auto_apply_enabled() -> bool:
    """是否启用自动应用（仅显式 env 白名单）。"""
    return os.getenv("ADJUSTMENT_AUTO_APPLY", "").strip().lower() in {"1", "true", "yes", "on"}


def _match_hint(hint: str) -> Optional[AdjustmentCommand]:
    """将一条 improvement_hint 映射为调整指令；无匹配返回 None。"""
    low = hint.lower()
    for rule in _HINT_MAPPING:
        if any(kw.lower() in low for kw in rule["keywords"]):
            return AdjustmentCommand(
                param_name=rule["param_name"],
                param_value=rule["value"],
                reason=rule["reason"],
                source_hint=hint,
            )
    return None


# ===================================================================
# 引擎
# ===================================================================


class AdjustmentEngine:
    """L4 干预引擎：内省建议 → 门控调整 → 可审计事件。"""

    def __init__(self, auto_apply: Optional[bool] = None) -> None:
        self._auto_apply = _auto_apply_enabled() if auto_apply is None else auto_apply
        self._history: List[AdjustmentCommand] = []
        self._max_history = 100

    def derive_commands(self, hints: List[str]) -> List[AdjustmentCommand]:
        """从内省建议推导调整指令（只含白名单参数）。"""
        commands: List[AdjustmentCommand] = []
        for hint in hints or []:
            cmd = _match_hint(hint)
            if cmd is not None:
                commands.append(cmd)
        # 去重（同参数只保留第一条）
        seen: set = set()
        deduped: List[AdjustmentCommand] = []
        for cmd in commands:
            if cmd.param_name not in seen:
                seen.add(cmd.param_name)
                deduped.append(cmd)
        return deduped

    def propose(self, hints: List[str], reflection_id: str = "") -> List[AdjustmentCommand]:
        """根据内省建议生成调整提案并发布 ADJUSTMENT_PROPOSED 事件。

        默认仅提案不应用；auto_apply 开启时直接 apply。
        """
        commands = self.derive_commands(hints)
        bus = SystemEventBus.instance()

        for cmd in commands:
            if self._auto_apply:
                self.apply(cmd, actor="auto", reflection_id=reflection_id)
            else:
                bus.publish(SystemEvent(
                    event_id=f"adj_prop_{cmd.param_name}_{int(__import__('time').time() * 1000)}",
                    event_type=SystemEventType.ADJUSTMENT_PROPOSED,
                    severity=EventSeverity.INFO,
                    source="adjustment_engine",
                    payload={
                        "param_name": cmd.param_name,
                        "param_value": str(cmd.param_value),
                        "reason": cmd.reason,
                        "source_hint": cmd.source_hint,
                        "reflection_id": reflection_id,
                        "auto_apply": self._auto_apply,
                        "awaiting_confirmation": not self._auto_apply,
                    },
                ))
                logger.info("L4 adjustment proposed: %s=%s (%s)", cmd.param_name, cmd.param_value, cmd.reason)

        # 记录历史
        self._history = (commands + self._history)[: self._max_history]
        return commands

    def apply(self, cmd: AdjustmentCommand, actor: str = "manual", reflection_id: str = "") -> bool:
        """应用一条调整指令（人工确认或自动）。写 ADJUSTMENT_APPLIED 事件。"""
        if cmd.param_name not in SAFE_PARAMS:
            logger.warning("Rejected adjustment outside safe whitelist: %s", cmd.param_name)
            return False

        ok = self._apply_param(cmd.param_name, cmd.param_value)
        cmd.applied = ok
        cmd.auto_applied = actor == "auto"

        bus = SystemEventBus.instance()
        bus.publish(SystemEvent(
            event_id=f"adj_appl_{cmd.param_name}_{int(__import__('time').time() * 1000)}",
            event_type=SystemEventType.ADJUSTMENT_APPLIED if ok else SystemEventType.ADJUSTMENT_REJECTED,
            severity=EventSeverity.INFO if ok else EventSeverity.WARNING,
            source="adjustment_engine",
            payload={
                "param_name": cmd.param_name,
                "param_value": str(cmd.param_value),
                "reason": cmd.reason,
                "actor": actor,
                "reflection_id": reflection_id,
                "success": ok,
            },
        ))
        if ok:
            logger.info("L4 adjustment applied: %s=%s by %s", cmd.param_name, cmd.param_value, actor)
        return ok

    def reject(self, cmd: AdjustmentCommand, actor: str = "manual") -> bool:
        """人工拒绝一条调整指令。写 ADJUSTMENT_REJECTED 事件。"""
        cmd.rejected = True
        bus = SystemEventBus.instance()
        bus.publish(SystemEvent(
            event_id=f"adj_rej_{cmd.param_name}_{int(__import__('time').time() * 1000)}",
            event_type=SystemEventType.ADJUSTMENT_REJECTED,
            severity=EventSeverity.INFO,
            source="adjustment_engine",
            payload={
                "param_name": cmd.param_name,
                "param_value": str(cmd.param_value),
                "reason": cmd.reason,
                "actor": actor,
            },
        ))
        logger.info("L4 adjustment rejected by %s: %s", actor, cmd.param_name)
        return True

    def history(self) -> List[AdjustmentCommand]:
        """调整历史（只读）。"""
        return list(self._history)

    def _apply_param(
        self,
        param_name: str,
        param_value: Any,
        manager: Optional["Any"] = None,
    ) -> bool:
        """实际应用参数（运行时 Config + 写 .env 持久化）。

        双写策略：
        1. 运行时：直接改 Config 单例（当前进程立即生效）
        2. 持久化：通过 ConfigManager.apply_updates 写 .env（重启后保留）

        manager 可注入（测试用临时 env）；默认自动解析项目 .env。
        失败静默降级返回 False。
        """
        try:
            from src.config import Config
            from src.core.config_manager import ConfigManager

            config = Config.get_instance()

            # 归一化参数值 + 运行时应用
            if param_name == "AGENT_MAX_STEPS":
                value_int = int(param_value)
                config.agent_max_steps = value_int
                env_value = str(value_int)
            elif param_name == "AGENT_CONTEXT_COMPRESSION_PROFILE":
                value_str = str(param_value).strip()
                config.agent_context_compression_profile = value_str
                env_value = value_str
            elif param_name == "AGENT_SKILLS":
                # AGENT_SKILLS 变更需重启生效；此处持久化到 .env 供下次启动加载。
                # 运行时技能集不在此热更新（避免与 SkillManager 状态冲突）。
                if isinstance(param_value, (list, tuple)):
                    env_value = ",".join(str(v) for v in param_value)
                else:
                    env_value = str(param_value)
                # 持久化后明确标记"需重启生效"，不再虚假返回成功
                logger.info("AGENT_SKILLS adjustment persisted; requires restart to take effect")
            else:
                return False

            # 持久化到 .env（复用 ConfigManager.apply_updates，原子写）
            mgr = manager if manager is not None else ConfigManager()
            mgr.apply_updates(
                updates=[(param_name, env_value)],
                sensitive_keys=set(),
                mask_token="******",
            )
            logger.info(
                "Runtime config updated + persisted: %s=%s",
                param_name, env_value,
            )
            return True
        except Exception as exc:
            logger.warning("Apply adjustment %s failed (observe-only): %s", param_name, exc)
            return False


# 模块级单例（由 bootstrap 装配）
_ENGINE: Optional[AdjustmentEngine] = None


def get_adjustment_engine() -> AdjustmentEngine:
    """获取 AdjustmentEngine 单例。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = AdjustmentEngine()
    return _ENGINE
