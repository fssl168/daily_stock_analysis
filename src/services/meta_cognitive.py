# -*- coding: utf-8 -*-
"""
L4 统一元认知引擎（MetaCognitiveEngine）。

系统不仅执行分析，更要理解自己「为什么这么做」、「有没有偏见」、「是否在循环」。
这是让股票分析系统具备自我意识的核心模块。

核心能力：
1. 决策追踪（CognitiveEpisode）：记录每次分析的完整决策链路
2. 偏差检测（BiasDetector）：5 类认知偏差自动检测
3. 循环检测（CircularityDetector）：检测系统是否陷入思维循环
4. 反思引擎（ReflectionEngine）：触发条件满足时自动执行深度反思
5. 内省报告（IntrospectionReport）：生成可供 LLM 反馈的自我认知 Prompt

来源: 参考 laap-AGI meta_cognitive.py，针对 DSA 股票分析场景重设计。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ===================================================================
# 决策行动分类（与 DecisionSignalService 对齐）
# ===================================================================


class DecisionAction(str, Enum):
    """决策行动分类 — 与 src/services/decision_signal_service.py 对齐。"""

    BUY = "buy"
    ADD = "add"
    HOLD = "hold"
    REDUCE = "reduce"
    SELL = "sell"
    WATCH = "watch"
    AVOID = "avoid"
    ALERT = "alert"
    NO_ACTION = "no_action"   # meta: 没有产生任何决策


# ===================================================================
# 认知偏差类型
# ===================================================================


class BiasType(str, Enum):
    """认知偏差类型 — 参考 laap-AGI 的 5 类偏差检测。"""

    CONFIRMATION = "confirmation"    # 确认偏差：只找支持已有判断的证据
    ANCHORING = "anchoring"          # 锚定偏差：过度依赖初始值
    OVERCONFIDENCE = "overconfidence"  # 过度自信：无视反面信号
    RECENCY = "recency"              # 近期偏差：过度关注近期事件
    FRAMING = "framing"              # 框架偏差：被表述方式左右判断


# ===================================================================
# 反思触发条件
# ===================================================================


class ReflectionTrigger(str, Enum):
    """反思触发条件。"""

    DECISION_COUNT = "decision_count"        # 累积 N 次决策后触发
    BIAS_DETECTED = "bias_detected"          # 检测到认知偏差
    OUTCOME_SURPRISE = "outcome_surprise"    # 决策结果与预期严重不符
    CIRCULARITY = "circularity"              # 检测到思维循环
    TIMED_INTERVAL = "timed_interval"        # 定时触发
    MANUAL = "manual"                         # 手动触发


# ===================================================================
# 核心数据结构
# ===================================================================


@dataclass
class CognitiveEpisode:
    """认知片段 — 一次完整决策的思维记录。

    每个 episode 记录：面对的 context → 推理过程 → 做出的决策 → 结果。
    这是元认知的最基础数据单元。
    """

    episode_id: str                          # "ep_{timestamp}_{hash}"
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None

    # 上下文
    stock_code: str = ""
    market: str = ""                         # "A" | "HK" | "US"
    market_phase: str = ""                   # 当时的市场阶段
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    # 推理过程（结构化记录）
    reasoning_steps: List[Dict[str, Any]] = field(default_factory=list)
    # 每个 step: {"step": int, "type": "data_gather"|"analysis"|"synthesis"|"verdict",
    #             "thought": str, "sources": List[str], "confidence": float, "duration_ms": float}

    # 决策结果
    action: str = DecisionAction.NO_ACTION.value
    decision_confidence: float = 0.0         # 系统对决策的置信度 [0, 1]
    signals_considered: int = 0              # 此次决策参考了多少信号
    signals_dismissed: int = 0               # 有多少信号被忽略

    # 元认知注解
    detected_biases: List[str] = field(default_factory=list)
    circularity_detected: bool = False
    self_awareness_score: float = 0.0        # 系统对自身认知质量的评分 [0, 1]

    # 事后验证
    expected_outcome: str = ""               # 预期结果简述
    actual_outcome: Optional[str] = None     # 实际结果（后续回填）
    outcome_deviation: Optional[float] = None  # 预期 vs 实际偏差


@dataclass
class BiasFinding:
    """偏差检测结果。"""

    bias_type: BiasType
    detected_at: datetime
    episode_id: str
    confidence: float                        # 检测置信度 [0, 1]
    evidence: List[str]                      # 具体证据
    suggestion: str                          # 纠正建议


@dataclass
class CircularityReport:
    """思维循环检测报告。"""

    detected_at: datetime
    pattern: str                             # 循环模式描述
    loop_length: int                         # 循环长度（多少步重复）
    involved_episodes: List[str]             # 涉及的分析 episode
    similarity_score: float                  # 循环内决策的相似度 [0, 1]
    break_suggestion: str                    # 打破循环的建议


@dataclass
class IntrospectionReport:
    """内省报告 — 生成供 LLM 自我反思的完整 Prompt。"""

    generated_at: datetime
    summary: str                             # 自我认知摘要
    biases_detected: List[BiasFinding] = field(default_factory=list)
    circularities: List[CircularityReport] = field(default_factory=list)
    decision_patterns: Dict[str, Any] = field(default_factory=dict)
    improvement_hints: List[str] = field(default_factory=list)
    prompt_for_llm: str = ""                 # 可注入下一次 LLM 调用的自省 Prompt


# ===================================================================
# 偏差检测器
# ===================================================================


class BiasDetector:
    """认知偏差检测器。

    在决策链中自动检测 5 类偏差。
    """

    def __init__(self) -> None:
        # 存储最近的信号与决策，用于偏差分析
        self._recent_signals: deque = deque(maxlen=50)
        self._recent_decisions: deque = deque(maxlen=50)
        self._stock_mentions: Dict[str, int] = {}  # stock → mention count

    def detect(
        self, episode: CognitiveEpisode, all_episodes: Optional[List[CognitiveEpisode]] = None
    ) -> List[BiasFinding]:
        """对一个认知片段执行全量偏差检测。"""
        findings: List[BiasFinding] = []

        for check in [
            self._check_confirmation,
            self._check_anchoring,
            self._check_overconfidence,
            self._check_recency,
            self._check_framing,
        ]:
            try:
                result = check(episode, all_episodes or [])
                if result:
                    findings.append(result)
            except Exception as exc:
                logger.debug("Bias check failed: %s", exc)

        # 回写到 episode
        episode.detected_biases = [f.bias_type.value for f in findings]
        return findings

    # ---------- 确认偏差 ----------

    def _check_confirmation(
        self, episode: CognitiveEpisode, all_episodes: List[CognitiveEpisode]
    ) -> Optional[BiasFinding]:
        """检测确认偏差：系统是否只关注支持已决策方向的信息。

        指标：
        - 推理步骤中支持决策方向的比例 > 80%
        - 与之前相同股票的历史 episode 决策方向高度一致
        """
        if not episode.reasoning_steps or not episode.signals_considered:
            return None

        # 统计推理步骤中支持 vs 反对的比例
        supporting = 0
        opposing = 0
        for step in episode.reasoning_steps:
            direction = step.get("direction", "neutral")
            if direction == "supporting":
                supporting += 1
            elif direction == "opposing":
                opposing += 1

        total_directional = supporting + opposing
        if total_directional == 0:
            return None

        supporting_ratio = supporting / total_directional

        # 如果支持比例超过 80%，且至少 3 个步骤有方向性
        if supporting_ratio > 0.8 and total_directional >= 3:
            return BiasFinding(
                bias_type=BiasType.CONFIRMATION,
                detected_at=datetime.now(),
                episode_id=episode.episode_id,
                confidence=min(supporting_ratio, 1.0),
                evidence=[
                    f"supporting/opposing ratio: {supporting}/{opposing}",
                    f"steps considered: {episode.signals_considered}, dismissed: {episode.signals_dismissed}",
                ],
                suggestion="强制要求分析中至少包含 2 个反对观点的详细论述",
            )

        return None

    # ---------- 锚定偏差 ----------

    def _check_anchoring(
        self, episode: CognitiveEpisode, all_episodes: List[CognitiveEpisode]
    ) -> Optional[BiasFinding]:
        """检测锚定偏差：系统是否过度依赖第一个数据点。

        指标：
        - 同一股票前次 episode 的决策方向与本次一致
        - 决策置信度异常高（>0.9）但支撑信号不足
        """
        if episode.decision_confidence < 0.9:
            return None

        # 查找同一股票的前次 episode
        same_stock = [
            e for e in all_episodes
            if e.stock_code == episode.stock_code
            and e.episode_id != episode.episode_id
            and e.action == episode.action
        ]

        if len(same_stock) >= 2 and episode.signals_considered < 5:
            return BiasFinding(
                bias_type=BiasType.ANCHORING,
                detected_at=datetime.now(),
                episode_id=episode.episode_id,
                confidence=0.7,
                evidence=[
                    f"same action '{episode.action}' on {episode.stock_code} "
                    f"repeated {len(same_stock)} times",
                    f"only {episode.signals_considered} signals considered",
                ],
                suggestion="要求系统从零开始重新评估，不带入前次结论",
            )

        return None

    # ---------- 过度自信 ----------

    def _check_overconfidence(
        self, episode: CognitiveEpisode, all_episodes: List[CognitiveEpisode]
    ) -> Optional[BiasFinding]:
        """检测过度自信：系统对自己决策过度自信但缺乏足够支撑。

        指标：
        - decision_confidence > 0.85
        - signals_considered < 5 或 dismissed > considered
        """
        if episode.decision_confidence < 0.85:
            return None

        low_evidence = episode.signals_considered < 5
        high_dismissal = (
            episode.signals_dismissed > episode.signals_considered
            and episode.signals_considered > 0
        )

        if low_evidence or high_dismissal:
            evidence = []
            if low_evidence:
                evidence.append(f"only {episode.signals_considered} signals considered")
            if high_dismissal:
                evidence.append(
                    f"dismissed {episode.signals_dismissed} vs "
                    f"considered {episode.signals_considered} signals"
                )

            return BiasFinding(
                bias_type=BiasType.OVERCONFIDENCE,
                detected_at=datetime.now(),
                episode_id=episode.episode_id,
                confidence=0.8,
                evidence=evidence,
                suggestion="降低置信度，增加信号覆盖。考虑延迟决策等待更多数据。",
            )

        return None

    # ---------- 近期偏差 ----------

    def _check_recency(
        self, episode: CognitiveEpisode, all_episodes: List[CognitiveEpisode]
    ) -> Optional[BiasFinding]:
        """检测近期偏差：系统过度关注最近的信息而忽略长期趋势。

        指标：
        - 推理步骤中超过 70% 的数据引用在最近 24h 内
        - 有可用的历史对比数据但未被引用
        """
        if not episode.reasoning_steps:
            return None

        now = datetime.now()
        recent_count = 0
        total_count = 0

        for step in episode.reasoning_steps:
            for src in step.get("sources", []):
                total_count += 1
                # 如果 source 包含 "recent" / "latest" / 日期在 24h 内
                if any(kw in str(src).lower() for kw in ("recent", "latest", "today")):
                    recent_count += 1

        if total_count >= 4 and recent_count / total_count > 0.7:
            return BiasFinding(
                bias_type=BiasType.RECENCY,
                detected_at=datetime.now(),
                episode_id=episode.episode_id,
                confidence=min(recent_count / total_count, 1.0),
                evidence=[
                    f"{recent_count}/{total_count} data sources are recent",
                ],
                suggestion="强制引用至少 1 个长期历史数据源，对比 30/90 日趋势",
            )

        return None

    # ---------- 框架偏差 ----------

    def _check_framing(
        self, episode: CognitiveEpisode, all_episodes: List[CognitiveEpisode]
    ) -> Optional[BiasFinding]:
        """检测框架偏差：被问题表述方式左右判断。

        指标：
        - 推理步骤中存在单一叙事框架（"bull case" / "bear case" 极端集中）
        - 所有步骤方向一致（全部 supporting 或全部 opposing）
        """
        if not episode.reasoning_steps:
            return None

        directions = [
            step.get("direction", "neutral")
            for step in episode.reasoning_steps
            if step.get("direction") in ("supporting", "opposing")
        ]

        if len(directions) >= 4 and len(set(directions)) == 1:
            direction = directions[0]
            return BiasFinding(
                bias_type=BiasType.FRAMING,
                detected_at=datetime.now(),
                episode_id=episode.episode_id,
                confidence=0.75,
                evidence=[
                    f"all {len(directions)} directional steps are '{direction}'",
                    "no counter-narrative present",
                ],
                suggestion=f"要求系统生成 {'bear' if direction == 'supporting' else 'bull'} case 并认真评估",
            )

        return None


# ===================================================================
# 循环检测器
# ===================================================================


class CircularityDetector:
    """思维循环检测器。

    检测系统是否在不同股票 / 不同时间点上重复同样的分析模式和结论，
    即「用同样的方法看不同的问题，得出同样的答案」。
    """

    # 滑动窗口大小
    _WINDOW_SIZE = 20
    # 相似度阈值（超过此值视为循环）
    _SIMILARITY_THRESHOLD = 0.75

    def __init__(self) -> None:
        self._action_history: deque = deque(maxlen=self._WINDOW_SIZE)
        self._reasoning_fingerprints: deque = deque(maxlen=self._WINDOW_SIZE)

    def record(self, episode: CognitiveEpisode) -> None:
        """记录 episode 到循环检测器。"""
        self._action_history.append({
            "episode_id": episode.episode_id,
            "stock_code": episode.stock_code,
            "action": episode.action,
            "timestamp": episode.started_at,
        })

        # 生成推理指纹（对 reasoning_steps 做轻量哈希）
        fp = self._fingerprint(episode)
        self._reasoning_fingerprints.append({
            "episode_id": episode.episode_id,
            "fingerprint": fp,
        })

    def detect(self) -> Optional[CircularityReport]:
        """检测是否存在思维循环。

        判断标准：
        - 最近 N 个 episode 的推理指纹相似度 > 阈值
        - 且涉及不同股票（说明是思维模式重复，而非同一股票的持续关注）
        """
        if len(self._reasoning_fingerprints) < 5:
            return None

        recent = list(self._reasoning_fingerprints)[-5:]
        fps = [r["fingerprint"] for r in recent]

        # 计算成对相似度
        similarities = []
        for i in range(len(fps)):
            for j in range(i + 1, len(fps)):
                sim = self._jaccard_similarity(fps[i], fps[j])
                similarities.append(sim)

        avg_sim = sum(similarities) / len(similarities) if similarities else 0

        if avg_sim < self._SIMILARITY_THRESHOLD:
            return None

        # 确认涉及不同股票
        involved_episodes = [r["episode_id"] for r in recent]
        stocks = set()
        for entry in self._action_history:
            if entry["episode_id"] in involved_episodes:
                stocks.add(entry["stock_code"])

        if len(stocks) < 2:
            return None  # 同一只股票的持续关注不算循环

        return CircularityReport(
            detected_at=datetime.now(),
            pattern=f"相似推理模式已重复 {len(recent)} 次（相似度 {avg_sim:.2f}）",
            loop_length=len(recent),
            involved_episodes=involved_episodes,
            similarity_score=avg_sim,
            break_suggestion=(
                "建议：1) 引入新的数据维度 2) 改变分析框架 "
                "3) 咨询外部信号 4) 暂停该模式并标记为待突破"
            ),
        )

    def _fingerprint(self, episode: CognitiveEpisode) -> Set[str]:
        """从 episode 的推理步骤中提取指纹。"""
        tokens: Set[str] = set()
        for step in episode.reasoning_steps:
            # 提取步骤类型
            tokens.add(f"type:{step.get('type', '')}")
            # 提取方向
            tokens.add(f"dir:{step.get('direction', 'neutral')}")
            # 提取置信度区间
            conf = step.get("confidence", 0)
            tokens.add(f"conf:{int(conf * 10)}")
            # 提取使用的数据源
            for src in step.get("sources", []):
                tokens.add(f"src:{str(src)[:30]}")
        return tokens

    @staticmethod
    def _jaccard_similarity(a: Set[str], b: Set[str]) -> float:
        """Jaccard 相似度。"""
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union if union > 0 else 0.0


# ===================================================================
# 反思引擎
# ===================================================================


class ReflectionEngine:
    """反思引擎 — 在满足触发条件时执行深度自我反思。

    工作方式：
    1. 监听 trigger 事件（偏差检测 / 循环检测 / 定时 / 决策计数）
    2. 收集相关 episode 数据
    3. 分析决策模式
    4. 生成改进建议
    5. 产出可供 LLM 注入的内省 Prompt
    """

    # 每 N 个决策后自动触发反思
    _DECISION_COUNT_TRIGGER = 10
    # 两次反思之间的最小间隔（秒）
    _MIN_REFLECTION_INTERVAL = 300  # 5 分钟

    def __init__(self) -> None:
        self._last_reflection_at: Optional[datetime] = None
        self._reflection_count = 0
        self._reflection_history: deque = deque(maxlen=50)
        self._lock = threading.Lock()

    def should_reflect(
        self,
        trigger: ReflectionTrigger,
        episode_count: int,
        bias_count: int,
        circularity: Optional[CircularityReport],
    ) -> bool:
        """判断是否应该触发反思。"""
        now = datetime.now()

        # 最小间隔保护
        if self._last_reflection_at:
            if (now - self._last_reflection_at).total_seconds() < self._MIN_REFLECTION_INTERVAL:
                return False

        if trigger == ReflectionTrigger.DECISION_COUNT:
            return episode_count >= self._DECISION_COUNT_TRIGGER
        if trigger == ReflectionTrigger.BIAS_DETECTED:
            return bias_count >= 2  # 两个以上偏差
        if trigger == ReflectionTrigger.CIRCULARITY:
            return circularity is not None
        if trigger == ReflectionTrigger.MANUAL:
            return True

        return False

    def reflect(
        self,
        episodes: List[CognitiveEpisode],
        bias_findings: List[BiasFinding],
        circularity_report: Optional[CircularityReport],
    ) -> IntrospectionReport:
        """执行深度反思，生成内省报告。

        Args:
            episodes: 最近的认知片段列表。
            bias_findings: 最近检测到的偏差。
            circularity_report: 循环检测结果（如存在）。

        Returns:
            IntrospectionReport: 包含分析、建议和内省 Prompt 的完整报告。
        """
        with self._lock:
            self._reflection_count += 1
            self._last_reflection_at = datetime.now()

            # 1. 分析决策模式
            patterns = self._analyze_decision_patterns(episodes)

            # 2. 提炼改进建议
            improvement_hints = self._derive_improvements(
                patterns, bias_findings, circularity_report
            )

            # 3. 生成内省 Prompt
            prompt = self._build_introspection_prompt(
                episodes, patterns, bias_findings,
                circularity_report, improvement_hints,
            )

            # 4. 构建报告
            report = IntrospectionReport(
                generated_at=datetime.now(),
                summary=self._generate_summary(
                    episodes, patterns, bias_findings, circularity_report
                ),
                biases_detected=bias_findings,
                circularities=[circularity_report] if circularity_report else [],
                decision_patterns=patterns,
                improvement_hints=improvement_hints,
                prompt_for_llm=prompt,
            )

            self._reflection_history.append({
                "timestamp": report.generated_at,
                "summary": report.summary,
                "bias_count": len(bias_findings),
                "improvement_count": len(improvement_hints),
            })

            logger.info(
                "Reflection #%d: %d biases, %d hints, %d patterns",
                self._reflection_count, len(bias_findings),
                len(improvement_hints), len(patterns),
            )

            return report

    def _analyze_decision_patterns(
        self, episodes: List[CognitiveEpisode]
    ) -> Dict[str, Any]:
        """分析最近决策的模式。"""
        if not episodes:
            return {"note": "no episodes to analyze"}

        actions = [e.action for e in episodes]
        confidences = [e.decision_confidence for e in episodes if e.decision_confidence > 0]
        biases = [b for e in episodes for b in e.detected_biases]

        # 行动分布
        action_dist = {}
        for a in actions:
            action_dist[a] = action_dist.get(a, 0) + 1

        # 置信度趋势
        if confidences:
            half = len(confidences) // 2 or 1
            early_conf = sum(confidences[:half]) / half
            late_conf = sum(confidences[half:]) / (len(confidences) - half or 1)
            confidence_trend = "rising" if late_conf > early_conf else "falling"
        else:
            early_conf = late_conf = confidence_trend = "N/A"

        # 自我意识评分趋势
        awareness_scores = [e.self_awareness_score for e in episodes if e.self_awareness_score > 0]
        avg_awareness = sum(awareness_scores) / len(awareness_scores) if awareness_scores else 0

        return {
            "total_episodes": len(episodes),
            "action_distribution": action_dist,
            "dominant_action": max(action_dist, key=action_dist.get) if action_dist else "none",
            "avg_confidence": sum(confidences) / len(confidences) if confidences else 0,
            "confidence_trend": confidence_trend,
            "early_avg_confidence": early_conf,
            "late_avg_confidence": late_conf,
            "bias_types_detected": list(set(biases)),
            "bias_rate": len(biases) / len(episodes) if episodes else 0,
            "avg_self_awareness": avg_awareness,
            "stocks_analyzed": len(set(e.stock_code for e in episodes if e.stock_code)),
        }

    def _derive_improvements(
        self,
        patterns: Dict[str, Any],
        bias_findings: List[BiasFinding],
        circularity_report: Optional[CircularityReport],
    ) -> List[str]:
        """从分析结果中推导改进建议。"""
        hints: List[str] = []

        # 偏差相关
        bias_types = set(f.bias_type for f in bias_findings)
        if BiasType.CONFIRMATION in bias_types:
            hints.append("在每次分析中强制加入「反面论证」环节，要求系统列出至少 3 条反对当前结论的理由")
        if BiasType.ANCHORING in bias_types:
            hints.append("对同一只股票的连续分析，要求从零开始独立评估，不引用前次结论作为锚点")
        if BiasType.OVERCONFIDENCE in bias_types:
            hints.append("降低决策置信度上限，当支撑信号不足 5 个时，限制 confidence ≤ 0.7")
        if BiasType.RECENCY in bias_types:
            hints.append("每次分析必须引用至少 1 个 30 日以上的历史趋势数据点")
        if BiasType.FRAMING in bias_types:
            hints.append("要求每次分析同时生成 bull case 和 bear case，并分别评分")

        # 循环相关
        if circularity_report:
            hints.append(circularity_report.break_suggestion)

        # 置信度趋势
        if patterns.get("confidence_trend") == "falling":
            hints.append("系统置信度持续下降，检查数据源质量或分析模型是否需要更新")

        # 行动单一化
        dominant = patterns.get("dominant_action", "")
        if dominant and patterns.get("action_distribution", {}).get(dominant, 0) / max(patterns.get("total_episodes", 1), 1) > 0.8:
            hints.append(f"决策过度集中在 '{dominant}'，可能存在惯性思维，建议引入随机化或外部校验")

        return hints

    def _build_introspection_prompt(
        self,
        episodes: List[CognitiveEpisode],
        patterns: Dict[str, Any],
        bias_findings: List[BiasFinding],
        circularity_report: Optional[CircularityReport],
        hints: List[str],
    ) -> str:
        """构造内省 Prompt。

        这个 Prompt 将被注入到下一次 LLM 分析调用中，
        让 LLM 在看到新数据前先「反思自己」。
        """
        parts = [
            "## 系统自我反思（Meta-Cognitive Introspection）",
            "",
            "在开始新一轮分析之前，请先审阅以下关于你自身决策质量的反馈：",
            "",
        ]

        # 决策统计
        parts.append("### 决策统计")
        parts.append(f"- 最近 {patterns.get('total_episodes', 0)} 次决策")
        parts.append(f"- 主要行动: {patterns.get('dominant_action', 'N/A')}")
        parts.append(f"- 平均置信度: {patterns.get('avg_confidence', 0):.2f}")
        parts.append(f"- 置信度趋势: {patterns.get('confidence_trend', 'N/A')}")
        parts.append("")

        # 偏差提醒
        if bias_findings:
            parts.append("### 检测到的认知偏差（请注意避免）")
            for bf in bias_findings:
                parts.append(f"- **{bf.bias_type.value}**: {bf.suggestion}")
            parts.append("")

        # 循环提醒
        if circularity_report:
            parts.append("### 思维循环警告")
            parts.append(f"- {circularity_report.pattern}")
            parts.append(f"- {circularity_report.break_suggestion}")
            parts.append("")

        # 改进建议
        if hints:
            parts.append("### 本次需要应用的改进")
            for i, hint in enumerate(hints, 1):
                parts.append(f"{i}. {hint}")
            parts.append("")

        parts.append("请在分析中时刻保持对这些偏差的警觉，对自身的推理过程保持批判性审视。")

        return "\n".join(parts)

    def _generate_summary(
        self,
        episodes: List[CognitiveEpisode],
        patterns: Dict[str, Any],
        bias_findings: List[BiasFinding],
        circularity_report: Optional[CircularityReport],
    ) -> str:
        """生成自然语言摘要。"""
        n = len(episodes)
        action = patterns.get("dominant_action", "无")
        bias_count = len(bias_findings)
        circular = "检测到思维循环" if circularity_report else "无思维循环"

        return (
            f"过去 {n} 个决策中，主导行动为 '{action}'，"
            f"平均置信度 {patterns.get('avg_confidence', 0):.2f}，"
            f"趋势 {patterns.get('confidence_trend', 'N/A')}。"
            f"检测到 {bias_count} 个认知偏差，{circular}。"
        )


# ===================================================================
# 统一元认知引擎
# ===================================================================


class MetaCognitiveEngine:
    """统一元认知引擎（L4 核心）。

    聚合：BiasDetector + CircularityDetector + ReflectionEngine，
    为每个决策 episode 提供完整的元认知服务。

    用法：
        engine = MetaCognitiveEngine()
        episode = engine.start_episode(stock_code="600519", market="A")
        engine.record_reasoning(episode.episode_id, step_data)
        engine.record_decision(episode.episode_id, action="hold", confidence=0.75)
        engine.end_episode(episode.episode_id)
        report = engine.get_introspection_prompt()  # 用于注入 LLM
    """

    def __init__(self, auto_reflect: bool = True) -> None:
        self._bias_detector = BiasDetector()
        self._circularity_detector = CircularityDetector()
        self._reflection_engine = ReflectionEngine()

        # Episode 存储
        self._episodes: OrderedDict[str, CognitiveEpisode] = OrderedDict()
        self._max_episodes = 500  # 最多保留 500 个 episode
        self._episode_counter = 0

        # 最近一次内省报告
        self._latest_introspection: Optional[IntrospectionReport] = None

        # 自动反思开关
        self._auto_reflect = auto_reflect

        # Phase 1: L3→L4 系统观察历史（SystemEventBus 集成）
        self._system_observations: List[Dict[str, Any]] = []

        self._lock = threading.RLock()

    # ==================================================================
    # Episode 生命周期
    # ==================================================================

    def start_episode(
        self,
        stock_code: str = "",
        market: str = "",
        market_phase: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> CognitiveEpisode:
        """开始一个新的认知片段。

        Args:
            stock_code: 分析的股票代码。
            market: 市场（A/HK/US）。
            market_phase: 当前市场阶段。
            context: 上下文快照（价格、指数、新闻摘要等）。

        Returns:
            CognitiveEpisode: 新创建的片段，用 episode_id 标识。
        """
        with self._lock:
            ts = int(time.time() * 1000)
            raw = f"{stock_code}_{ts}_{self._episode_counter}"
            ep_hash = hashlib.md5(raw.encode()).hexdigest()[:8]

            episode = CognitiveEpisode(
                episode_id=f"ep_{ts}_{ep_hash}",
                stock_code=stock_code,
                market=market,
                market_phase=market_phase,
                context_snapshot=context or {},
                started_at=datetime.now(),
            )

            self._episodes[episode.episode_id] = episode
            self._episode_counter += 1

            # LRU 清理
            while len(self._episodes) > self._max_episodes:
                self._episodes.popitem(last=False)

            logger.debug("Episode started: %s for %s", episode.episode_id, stock_code)
            return episode

    def record_reasoning(
        self,
        episode_id: str,
        step_type: str = "analysis",
        thought: str = "",
        sources: Optional[List[str]] = None,
        direction: str = "neutral",
        confidence: float = 0.0,
        duration_ms: float = 0.0,
    ) -> bool:
        """记录一个推理步骤。

        Args:
            episode_id: 所属 episode。
            step_type: "data_gather" | "analysis" | "synthesis" | "verdict"
            thought: 推理思考内容。
            sources: 引用的数据源。
            direction: "supporting" | "opposing" | "neutral"
            confidence: 该步骤的置信度。
            duration_ms: 该步骤耗时。

        Returns:
            bool: 是否成功记录。
        """
        with self._lock:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return False

            ep.reasoning_steps.append({
                "step": len(ep.reasoning_steps) + 1,
                "type": step_type,
                "thought": thought,
                "sources": sources or [],
                "direction": direction,
                "confidence": confidence,
                "duration_ms": duration_ms,
            })
            return True

    def record_decision(
        self,
        episode_id: str,
        action: str,
        confidence: float = 0.0,
        signals_considered: int = 0,
        signals_dismissed: int = 0,
        expected_outcome: str = "",
    ) -> bool:
        """记录决策结果。

        Args:
            episode_id: 所属 episode。
            action: 决策行动（buy/hold/sell/watch/avoid/alert 等）。
            confidence: 决策置信度。
            signals_considered: 参考的信号数量。
            signals_dismissed: 忽略的信号数量。
            expected_outcome: 预期结果描述。

        Returns:
            bool: 是否成功记录。
        """
        with self._lock:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return False

            ep.action = action
            ep.decision_confidence = confidence
            ep.signals_considered = signals_considered
            ep.signals_dismissed = signals_dismissed
            ep.expected_outcome = expected_outcome
            return True

    def end_episode(self, episode_id: str) -> Optional[CognitiveEpisode]:
        """结束一个认知片段，执行偏差检测和循环检测。

        1. 偏差检测
        2. 循环检测
        3. 计算自我意识评分
        4. 若满足条件，触发自动反思
        """
        with self._lock:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return None

            ep.ended_at = datetime.now()

            # 偏差检测
            all_episodes = list(self._episodes.values())
            bias_findings = self._bias_detector.detect(ep, all_episodes)

            # 循环检测
            self._circularity_detector.record(ep)
            circularity = self._circularity_detector.detect()

            # 自我意识评分
            ep.self_awareness_score = self._compute_self_awareness(ep, bias_findings)

            # 自动反思
            if self._auto_reflect:
                total_bias = sum(
                    len(e.detected_biases) for e in all_episodes[-20:]
                )
                trigger = (
                    ReflectionTrigger.BIAS_DETECTED if total_bias >= 2
                    else ReflectionTrigger.DECISION_COUNT
                )
                if self._reflection_engine.should_reflect(
                    trigger, len(all_episodes), total_bias, circularity
                ):
                    recent_bias_findings = []
                    for e in all_episodes[-20:]:
                        for bt in e.detected_biases:
                            recent_bias_findings.append(
                                BiasFinding(
                                    bias_type=BiasType(bt),
                                    detected_at=e.started_at,
                                    episode_id=e.episode_id,
                                    confidence=0.5,
                                    evidence=[],
                                    suggestion="",
                                )
                            )
                    self._latest_introspection = self._reflection_engine.reflect(
                        all_episodes[-20:], recent_bias_findings, circularity
                    )
                    logger.info(
                        "Auto-reflection triggered after episode %s", episode_id
                    )

            return ep

    def record_outcome(
        self, episode_id: str, actual_outcome: str, deviation: Optional[float] = None
    ) -> bool:
        """回填实际结果（用于事后学习）。

        Args:
            episode_id: episode ID。
            actual_outcome: 实际结果描述。
            deviation: 预期 vs 实际的偏差值。
        """
        with self._lock:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return False
            ep.actual_outcome = actual_outcome
            ep.outcome_deviation = deviation
            return True

    # ==================================================================
    # L3 → L4 事件接收（Phase 1: SystemEventBus 集成）
    # ==================================================================

    def on_system_event(self, event: Any) -> None:
        """接收并处理来自 SystemEventBus 的 L3 系统事件。

        这是 L3→L4 双向反馈链路的关键入口。L3 模块通过 SystemEventBus
        发布降级/回滚/重启事件，本方法接收并转化为元认知的认知输入。

        处理逻辑（按事件类型分发）：
        - DEGRADATION_TRANSITION → 记录系统压力变化，供后续反思参考
        - CONFIG_ROLLBACK_EXECUTED → 记录配置变更，标记为潜在风险上下文
        - MODULE_RESTARTED / MODULE_RESTART_FAILED → 记录模块健康事件
        - REFLECTION_COMPLETED → 忽略（避免自循环）

        Args:
            event: SystemEvent 实例（从 event_bus 订阅接收）。
        """
        # 延迟导入避免循环依赖
        from src.services.event_bus import SystemEventType

        event_type = getattr(event, 'event_type', None)
        if event_type is None:
            return

        with self._lock:
            sev = getattr(event, 'severity', None)
            src = getattr(event, 'source', 'unknown')

            # 元认知记录：将系统事件转化为自我观察
            if event_type == SystemEventType.DEGRADATION_TRANSITION:
                self._system_observations.append({
                    "type": "degradation",
                    "timestamp": datetime.now().isoformat(),
                    "from_level": event.payload.get("from_level", "?"),
                    "to_level": event.payload.get("to_level", "?"),
                    "capabilities": event.payload.get("capabilities_affected", []),
                    "triggers": event.payload.get("trigger_signals", []),
                })
                logger.info("L4 observed degradation: %s → %s",
                           event.payload.get("from_level"),
                           event.payload.get("to_level"))

            elif event_type == SystemEventType.CONFIG_ROLLBACK_EXECUTED:
                self._system_observations.append({
                    "type": "rollback",
                    "timestamp": datetime.now().isoformat(),
                    "snapshot_id": event.payload.get("snapshot_id", ""),
                    "success": event.payload.get("success", False),
                    "restored_keys": event.payload.get("restored_keys", []),
                })

            elif event_type in (
                SystemEventType.MODULE_RESTARTED,
                SystemEventType.MODULE_RESTART_FAILED,
            ):
                self._system_observations.append({
                    "type": "module_restart",
                    "timestamp": datetime.now().isoformat(),
                    "module": event.payload.get("module_name", src),
                    "success": event_type == SystemEventType.MODULE_RESTARTED,
                    "message": event.payload.get("message", ""),
                })

            # 限制观察历史大小
            if len(self._system_observations) > 200:
                self._system_observations = self._system_observations[-200:]

    def get_system_observations(
        self, limit: int = 50, observation_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """获取 L4 记录的系统观察历史。

        Args:
            limit: 返回条数上限。
            observation_type: 按类型筛选（"degradation"/"rollback"/"module_restart"）。

        Returns:
            系统观察列表（最近优先）。
        """
        with self._lock:
            obs = list(self._system_observations)
            if observation_type:
                obs = [o for o in obs if o.get("type") == observation_type]
            return obs[-limit:]

    # ==================================================================
    # 内省
    # ==================================================================

    def force_reflection(self) -> IntrospectionReport:
        """强制触发一次深度反思。"""
        with self._lock:
            all_episodes = list(self._episodes.values())
            recent = all_episodes[-20:]
            circularity = self._circularity_detector.detect()

            bias_findings: List[BiasFinding] = []
            for ep in recent:
                for bt in ep.detected_biases:
                    bias_findings.append(
                        BiasFinding(
                            bias_type=BiasType(bt),
                            detected_at=ep.started_at,
                            episode_id=ep.episode_id,
                            confidence=0.5,
                            evidence=[],
                            suggestion="",
                        )
                    )

            self._latest_introspection = self._reflection_engine.reflect(
                recent, bias_findings, circularity
            )
            # 后台自主生成: 报告产出后自动进入调整提案门控 (白名单, 仅提案
            # 不应用, 除非 ADJUSTMENT_AUTO_APPLY)。
            try:
                hints = list(getattr(self._latest_introspection, "improvement_hints", []) or [])
                if hints:
                    from src.services.adjustment_engine import get_adjustment_engine

                    reflection_id = str(
                        getattr(self._latest_introspection, "generated_at", "")
                    )
                    get_adjustment_engine().propose(
                        hints, reflection_id=reflection_id
                    )
            except Exception as exc:
                logger.debug("L4 auto adjustment proposal failed: %s", exc)
            return self._latest_introspection

    def get_introspection_prompt(self) -> str:
        """获取最新的内省 Prompt，用于注入下一次 LLM 调用。

        调用方在构建 LLM Prompt 时，将此方法的返回值拼接到系统 Prompt 中，
        实现「系统在分析前先反思自己」的元认知闭环。
        """
        if self._latest_introspection:
            return self._latest_introspection.prompt_for_llm
        return ""

    def get_latest_introspection(self) -> Optional[IntrospectionReport]:
        """获取最近一次完整内省报告。"""
        return self._latest_introspection

    # ==================================================================
    # 查询
    # ==================================================================

    def get_episode(self, episode_id: str) -> Optional[CognitiveEpisode]:
        """获取指定 episode。"""
        with self._lock:
            return self._episodes.get(episode_id)

    def get_recent_episodes(self, limit: int = 20) -> List[CognitiveEpisode]:
        """获取最近的 episode 列表。"""
        with self._lock:
            return list(self._episodes.values())[-limit:]

    def get_episodes_by_stock(self, stock_code: str, limit: int = 20) -> List[CognitiveEpisode]:
        """获取指定股票的 episode 历史。"""
        with self._lock:
            return [
                e for e in self._episodes.values()
                if e.stock_code == stock_code
            ][-limit:]

    def detect_circularity(self) -> Optional[CircularityReport]:
        """手动触发循环检测。"""
        return self._circularity_detector.detect()

    def stats(self) -> Dict[str, Any]:
        """兼容 health_check 的 stats() 接口。"""
        with self._lock:
            all_episodes = list(self._episodes.values())
            recent = all_episodes[-20:]

            action_dist = {}
            for e in recent:
                action_dist[e.action] = action_dist.get(e.action, 0) + 1

            bias_dist = {}
            for e in recent:
                for b in e.detected_biases:
                    bias_dist[b] = bias_dist.get(b, 0) + 1

            return {
                "total_episodes": len(all_episodes),
                "recent_episodes": len(recent),
                "active_episodes": sum(1 for e in all_episodes if e.ended_at is None),
                "action_distribution": action_dist,
                "bias_distribution": bias_dist,
                "avg_self_awareness": (
                    sum(e.self_awareness_score for e in recent if e.self_awareness_score > 0)
                    / max(sum(1 for e in recent if e.self_awareness_score > 0), 1)
                ),
                "reflection_count": self._reflection_engine._reflection_count,
                "latest_introspection_at": (
                    self._latest_introspection.generated_at.isoformat()
                    if self._latest_introspection else None
                ),
            }

    def get_self_report(self) -> Dict[str, Any]:
        """生成综合自我报告 — 类似 laap-AGI 的 get_self_report()。"""
        with self._lock:
            all_episodes = list(self._episodes.values())
            recent = all_episodes[-50:]

            # 偏差统计
            bias_counts: Dict[str, int] = {}
            for e in all_episodes:
                for b in e.detected_biases:
                    bias_counts[b] = bias_counts.get(b, 0) + 1

            # 结果偏差统计（有实际回填的 episode）
            outcome_episodes = [e for e in all_episodes if e.outcome_deviation is not None]
            avg_deviation = (
                sum(abs(e.outcome_deviation) for e in outcome_episodes) / len(outcome_episodes)
                if outcome_episodes else 0
            )

            # 自我意识变化趋势
            awareness_scores = [
                e.self_awareness_score for e in all_episodes
                if e.self_awareness_score > 0
            ][-30:]

            circularity = self._circularity_detector.detect()

            return {
                "generated_at": datetime.now().isoformat(),
                "total_decisions": len(all_episodes),
                "outcome_tracked": len(outcome_episodes),
                "avg_outcome_deviation": avg_deviation,
                "bias_profile": bias_counts,
                "self_awareness_trend": awareness_scores[-10:] if awareness_scores else [],
                "self_awareness_avg": (
                    sum(awareness_scores) / len(awareness_scores)
                    if awareness_scores else 0
                ),
                "circularity": (
                    {"pattern": circularity.pattern, "similarity": circularity.similarity_score}
                    if circularity else None
                ),
                "reflections_completed": self._reflection_engine._reflection_count,
                "introspection_available": self._latest_introspection is not None,
                # Phase 1: L3→L4 系统观察数据
                "system_observations_count": len(self._system_observations),
                "recent_system_observations": (
                    self._system_observations[-20:] if self._system_observations else []
                ),
            }

    # ==================================================================
    # 内部
    # ==================================================================

    def _compute_self_awareness(
        self, episode: CognitiveEpisode, bias_findings: List[BiasFinding]
    ) -> float:
        """计算自我意识评分。

        评分因素：
        - 是否有推理步骤记录（基础分）
        - 是否检测到偏差并记录（加分：说明系统知道自己可能有偏见）
        - 是否有明确方向性（supporting + opposing = 更全面）
        - 置信度是否合理（不是盲目的 1.0 也不是无信息的 0.0）
        - 是否区分了考虑的信号和忽略的信号
        """
        score = 0.0

        # 基础分：有推理步骤
        if episode.reasoning_steps:
            score += 0.2

        # 加分：推理步骤数量
        score += min(len(episode.reasoning_steps) * 0.05, 0.2)

        # 加分：有方向性平衡
        directions = [
            s.get("direction", "neutral") for s in episode.reasoning_steps
        ]
        has_supporting = "supporting" in directions
        has_opposing = "opposing" in directions
        if has_supporting and has_opposing:
            score += 0.2
        elif has_supporting or has_opposing:
            score += 0.1

        # 加分：置信度在合理区间
        if 0.3 <= episode.decision_confidence <= 0.85:
            score += 0.15
        elif 0 < episode.decision_confidence < 0.3:
            score += 0.05  # 太低说明不确定

        # 加分：区分了信号
        if episode.signals_considered > 0:
            score += 0.1
        if episode.signals_dismissed > 0:
            score += 0.05

        # 扣分：检测到偏差（系统知道问题 → 额外的自知之明）
        # 但偏差本身说明认知质量有问题 → 净效果中性偏正
        score += min(len(bias_findings) * 0.03, 0.1)

        return min(score, 1.0)

    def reset(self) -> None:
        """重置引擎（用于测试）。"""
        with self._lock:
            self._episodes.clear()
            self._episode_counter = 0
            self._bias_detector = BiasDetector()
            self._circularity_detector = CircularityDetector()
            self._reflection_engine = ReflectionEngine()
            self._latest_introspection = None
