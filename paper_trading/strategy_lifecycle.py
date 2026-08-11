# -*- coding: utf-8 -*-
"""StrategyLifecycle — 策略生命周期状态机（P1 / T10）.

实现依据: docs/architecture/realtime_quant_system_design.md §5.1
任务规格: .claude/specs/quant-p1/dev-plan.md T10

状态机:
  DRAFT    -> [BACKTEST, DRAFT]
  BACKTEST -> [PAPER, DRAFT]
  PAPER    -> [REVIEW, DRAFT]
  REVIEW   -> [LIVE, DRAFT]
  LIVE     -> [PAUSED, DRAFT]
  PAUSED   -> [LIVE, DRAFT]
  RETIRED  -> [DRAFT]  # 退休后只能重新起草
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional


class StrategyState(str, Enum):
    """策略生命周期状态."""

    DRAFT = "DRAFT"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    REVIEW = "REVIEW"
    LIVE = "LIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"


class LifecycleTransitionError(Exception):
    """非法状态转移异常，携带策略名与当前/目标状态."""

    def __init__(self, strategy_name: str, current: StrategyState, target: StrategyState):
        self.strategy_name = strategy_name
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition: {current.value} -> {target.value} for strategy '{strategy_name}'"
        )


class StrategyLifecycle:
    """策略生命周期状态机.

    storage 可注入（默认内存 dict），便于测试隔离：
    - None: 每次创建实例使用独立的默认内存 dict
    - Callable: 调用工厂获得存储 dict（同一工厂可复用底层存储实现共享状态）
    - dict: 直接复用传入的存储
    """

    STATE_MACHINE: Dict[StrategyState, frozenset] = {
        StrategyState.DRAFT: frozenset({StrategyState.BACKTEST, StrategyState.DRAFT}),
        StrategyState.BACKTEST: frozenset({StrategyState.PAPER, StrategyState.DRAFT}),
        StrategyState.PAPER: frozenset({StrategyState.REVIEW, StrategyState.DRAFT}),
        StrategyState.REVIEW: frozenset({StrategyState.LIVE, StrategyState.DRAFT}),
        StrategyState.LIVE: frozenset({StrategyState.PAUSED, StrategyState.DRAFT}),
        StrategyState.PAUSED: frozenset({StrategyState.LIVE, StrategyState.DRAFT}),
        StrategyState.RETIRED: frozenset({StrategyState.DRAFT}),
    }

    def __init__(self, storage: Optional[Callable[[], Dict[str, StrategyState]]] = None):
        self.state_machine = self.STATE_MACHINE
        if storage is None:
            self._storage: Dict[str, StrategyState] = {}
        elif callable(storage):
            self._storage = storage()
        elif isinstance(storage, dict):
            self._storage = storage
        else:
            raise TypeError("storage must be None, a callable, or a dict")
        self.approvals: List[Dict] = []

    def get_state(self, strategy_name: str) -> StrategyState:
        """返回策略当前状态；未知策略默认注册为 DRAFT."""
        if strategy_name not in self._storage:
            self._storage[strategy_name] = StrategyState.DRAFT
        return self._storage[strategy_name]

    def transition(
        self,
        strategy_name: str,
        new_state: StrategyState | str,
        operator: str = "",
    ) -> StrategyState:
        """执行状态转移.

        非法转移抛出 :class:`LifecycleTransitionError`（含当前/目标状态）；
        合法转移记录审批日志到 ``self.approvals`` 并返回新状态。
        """
        if not isinstance(new_state, StrategyState):
            new_state = StrategyState(new_state)
        current = self.get_state(strategy_name)
        if new_state not in self.state_machine[current]:
            raise LifecycleTransitionError(strategy_name, current, new_state)
        self._storage[strategy_name] = new_state
        self.approvals.append(
            {
                "strategy_name": strategy_name,
                "from": current,
                "to": new_state,
                "operator": operator,
                "timestamp": datetime.now(),
            }
        )
        return new_state

    def is_live(self, strategy_name: str) -> bool:
        """策略是否处于 LIVE 状态."""
        return self.get_state(strategy_name) == StrategyState.LIVE

    def list_strategies(self) -> Dict[str, StrategyState]:
        """返回全部策略及其当前状态（副本，不影响内部存储）."""
        return dict(self._storage)

    def get_approval_history(self, strategy_name: str) -> List[Dict]:
        """返回指定策略的全部审批记录（按发生顺序）."""
        return [a for a in self.approvals if a["strategy_name"] == strategy_name]
