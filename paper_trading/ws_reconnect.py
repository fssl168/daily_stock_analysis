# -*- coding: utf-8 -*-
"""WebSocket 断线重连的指数退避策略（T17 架构层，纯逻辑、无网络依赖）.

实现依据: docs/architecture/realtime_quant_system_design.md §2.1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ReconnectPolicy:
    """WebSocket 重连的指数退避策略.

    默认行为：首次失败等待 1s，之后每次连续失败等待时间 ×2，上限 30s，
    连接成功后重置回 1s。

    属性:
        initial_backoff: 首次重连前的等待秒数
        multiplier: 每次连续失败后等待时间的放大系数
        max_backoff: 等待时间的上限（秒）
        reset_on_success: 连接成功后是否把退避重置回 initial_backoff
        max_retries: 最大连续失败重试次数；None 表示无限重试
    """

    initial_backoff: float = 1.0
    multiplier: float = 2.0
    max_backoff: float = 30.0
    reset_on_success: bool = True
    max_retries: Optional[int] = None

    def __post_init__(self) -> None:
        if self.initial_backoff < 0:
            raise ValueError("initial_backoff must be >= 0")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be > 0")
        if self.max_backoff < 0:
            raise ValueError("max_backoff must be >= 0")
        if self.max_retries is not None and self.max_retries < 0:
            raise ValueError("max_retries must be None or a non-negative integer")


def exponential_backoff(policy: ReconnectPolicy, retry: int) -> float:
    """返回第 ``retry`` 次连续失败（从 0 开始）时应等待的秒数.

    ``retry=0`` 对应 ``initial_backoff``，之后每次 ×multiplier，封顶 max_backoff。
    """
    if retry < 0:
        raise ValueError("retry must be >= 0")
    backoff = policy.initial_backoff * (policy.multiplier ** retry)
    return min(backoff, policy.max_backoff)
