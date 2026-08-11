# -*- coding: utf-8 -*-
"""
Observability API — L1/L2/L3/L4 全主动观察数据的前端消费入口。

来源: docs/FRONTEND_BACKEND_ALIGNMENT_PLAN.md Phase A

端点按 B 域四组：
- B-API-1 事件流查询（L1/L2/L3/L4 统一）
- B-API-2 L4 元认知（内省/系统观察/触发反思）
- B-API-3 L3 修复效果 + 配置回归
- B-API-4 健康趋势
- WS /ws/events 实时事件推送（复用 EventBus on_batch）

契约：
- 鉴权：/api/v1/* 由全局 auth 中间件保护，本 router 无需额外处理
- 分页：统一 page/page_size（默认 1/20，范围 1-100）
- 错误：复用 HTTPException + detail={error, message}（与 alerts.py 一致）
- 脱敏：SystemEventOut.payload_redacted 裁剪敏感字段
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from src.services.bootstrap_event_bus import (
    get_event_bus_stats,
    get_l3_config_observer,
    get_meta_cognitive_engine,
    get_repair_effectiveness_log,
)
from src.services.event_bus import EventSeverity, SystemEvent, SystemEventBus, SystemEventType

logger = logging.getLogger(__name__)

router = APIRouter()

# ===================================================================
# 脱敏辅助
# ===================================================================

_SENSITIVE_EVENT_TYPES = frozenset({
    "agent_tool_call",
    "agent_tool_result",
    "notification_sent",
    "notification_failed",
    "llm_usage",
    "llm_backend_switched",
    "config_changed",
})

_PAYLOAD_TRUNCATE = 200


def _redact_payload(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """裁剪事件 payload 中的敏感字段，返回脱敏后的 dict。

    - agent_tool_*: arguments/result 截断到 200 字符
    - notification_*: 隐藏渠道内部配置（只留 channel/success）
    - llm_usage: 只留 model/token 计数
    - config_changed: 只留 applied_keys 列表，不暴露值
    """
    if not isinstance(payload, dict):
        return {}

    et = event_type if isinstance(event_type, str) else str(event_type)

    if et in ("agent_tool_call", "agent_tool_result"):
        out = dict(payload)
        for key in ("arguments", "result", "result_preview"):
            if isinstance(out.get(key), str) and len(out[key]) > _PAYLOAD_TRUNCATE:
                out[key] = out[key][:_PAYLOAD_TRUNCATE] + "..."
        return out

    if et in ("notification_sent", "notification_failed"):
        return {
            "success": payload.get("success"),
            "route_type": payload.get("route_type"),
            "channels": payload.get("channels"),
        }

    if et == "llm_usage":
        return {
            "model": payload.get("model"),
            "tokens": payload.get("tokens") or payload.get("total_tokens"),
        }

    if et == "llm_backend_switched":
        return {"from": payload.get("from"), "to": payload.get("to")}

    if et == "config_changed":
        return {
            "applied_keys": payload.get("applied_keys"),
            "applied_count": payload.get("applied_count"),
            "reload_triggered": payload.get("reload_triggered"),
        }

    return payload


def _event_to_dict(event: SystemEvent) -> Dict[str, Any]:
    """将 SystemEvent 转为脱敏后的 dict。"""
    et = (
        event.event_type.value
        if isinstance(event.event_type, SystemEventType)
        else str(event.event_type)
    )
    return {
        "event_id": event.event_id,
        "event_type": et,
        "severity": (
            event.severity.value
            if isinstance(event.severity, EventSeverity)
            else str(event.severity)
        ),
        "source": event.source,
        "timestamp": event.timestamp.isoformat(),
        "payload_redacted": _redact_payload(et, event.payload),
        "correlation_id": event.correlation_id,
    }


def _get_bus() -> SystemEventBus:
    """获取 SystemEventBus 单例。"""
    return SystemEventBus.instance()


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


# ===================================================================
# B-API-1: 事件流查询
# ===================================================================


@router.get(
    "/events",
    summary="最近事件（分页/过滤）",
)
def list_events(
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    source: Optional[str] = Query(None, description="按来源过滤"),
    min_severity: Optional[str] = Query(None, description="最低严重度 debug/info/warning/error/critical"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
) -> Dict[str, Any]:
    try:
        bus = _get_bus()
        # 将 str 查询参数转换为枚举（get_recent_events 要求枚举类型）
        et_enum = None
        if event_type:
            try:
                et_enum = SystemEventType(event_type)
            except ValueError:
                pass
        sev_enum = None
        if min_severity:
            try:
                sev_enum = EventSeverity(min_severity)
            except ValueError:
                pass
        events = bus.get_recent_events(
            event_type=et_enum,
            source=source,
            min_severity=sev_enum,
            limit=page_size,
        )
        total = bus.get_event_count()
        # 简单分页：get_recent_events 已按时间倒序，取 page 对应的切片
        start = (page - 1) * page_size
        items = [_event_to_dict(e) for e in events[start:start + page_size]]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("List events failed", exc)


@router.get(
    "/events/stats",
    summary="事件统计（类型/来源/严重度分布）",
)
def event_stats() -> Dict[str, Any]:
    try:
        bus = _get_bus()
        bus_stats = bus.stats()
        observer_stats = get_event_bus_stats(bus)
        return {
            "bus": bus_stats,
            "l4_meta_observer": observer_stats.get("l4_meta_observer", {}),
            "l3_config_observer": observer_stats.get("l3_config_observer", {}),
        }
    except Exception as exc:
        raise _internal_error("Event stats failed", exc)


@router.get(
    "/events/correlation/{cid}",
    summary="按 correlation_id 追踪事件链",
)
def event_correlation(cid: str) -> Dict[str, Any]:
    try:
        bus = _get_bus()
        events = bus.get_events_by_correlation(cid)
        return {
            "correlation_id": cid,
            "items": [_event_to_dict(e) for e in events],
            "count": len(events),
        }
    except Exception as exc:
        raise _internal_error("Event correlation lookup failed", exc)


# ===================================================================
# B-API-2: L4 元认知
# ===================================================================


@router.get(
    "/meta/observations",
    summary="L4 系统观察历史",
)
def meta_observations(
    limit: int = Query(50, ge=1, le=200),
    observation_type: Optional[str] = Query(None, description="degradation/rollback/module_restart"),
) -> Dict[str, Any]:
    try:
        engine = get_meta_cognitive_engine()
        if engine is None:
            return {"items": [], "count": 0}
        items = engine.get_system_observations(
            limit=limit,
            observation_type=observation_type,
        )
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _internal_error("Meta observations failed", exc)


@router.get(
    "/meta/introspection",
    summary="最新内省报告",
)
def meta_introspection() -> Dict[str, Any]:
    try:
        engine = get_meta_cognitive_engine()
        if engine is None:
            raise _not_found("MetaCognitiveEngine not bootstrapped")
        report = engine.get_latest_introspection()
        if report is None:
            return {"report": None}
        # 裁剪为可序列化 dict
        return {"report": _introspection_to_dict(report)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Get introspection failed", exc)


@router.get(
    "/meta/stats",
    summary="L4 元认知统计",
)
def meta_stats() -> Dict[str, Any]:
    try:
        engine = get_meta_cognitive_engine()
        if engine is None:
            return {"stats": {}}
        return {"stats": engine.stats()}
    except Exception as exc:
        raise _internal_error("Meta stats failed", exc)


@router.post(
    "/meta/reflect",
    summary="触发一次反思（dry_run，仅产出报告）",
)
def meta_reflect() -> Dict[str, Any]:
    """触发 MetaCognitiveEngine.force_reflection()。

    dry_run 语义：仅生成内省报告，不调整任何策略、不触发任何修复。
    """
    try:
        engine = get_meta_cognitive_engine()
        if engine is None:
            raise _not_found("MetaCognitiveEngine not bootstrapped")
        report = engine.force_reflection()
        return {
            "ok": True,
            "report": _introspection_to_dict(report),
            "note": "observe-only: no policy adjustment applied",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Force reflection failed", exc)


def _introspection_to_dict(report: Any) -> Dict[str, Any]:
    """将 IntrospectionReport 转为可序列化 dict（裁剪）。"""
    if hasattr(report, "to_dict"):
        try:
            return report.to_dict()
        except Exception:
            pass
    d: Dict[str, Any] = {}
    for attr in ("report_id", "timestamp", "summary", "conclusions", "recommendations", "bias_findings"):
        if hasattr(report, attr):
            val = getattr(report, attr)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            d[attr] = val
    return d


# ===================================================================
# B-API-3: L3 修复效果 + 配置回归
# ===================================================================


@router.get(
    "/repairs",
    summary="修复记录列表",
)
def repairs(
    target: Optional[str] = Query(None, description="按目标模块过滤"),
    action_type: Optional[str] = Query(None, description="按动作类型过滤"),
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    try:
        log = get_repair_effectiveness_log()
        if log is None:
            return {"items": [], "count": 0}
        entries = log.get_entries_by_target(target) if target else log.stats().get("entries", [])
        if action_type:
            entries = log.get_entries_by_action(action_type)
        items = [e.to_dict() if hasattr(e, "to_dict") else e for e in entries][:limit]
        return {"items": items, "count": len(items)}
    except Exception as exc:
        raise _internal_error("Repairs list failed", exc)


@router.get(
    "/repairs/effectiveness",
    summary="修复效果分析报告",
)
def repairs_effectiveness(
    window_hours: int = Query(24, ge=1, le=720),
) -> Dict[str, Any]:
    try:
        log = get_repair_effectiveness_log()
        if log is None:
            return {"report": {}}
        report = log.analyze_effectiveness(window_hours=window_hours)
        if hasattr(report, "to_dict"):
            return {"report": report.to_dict(), "window_hours": window_hours}
        return {"report": report, "window_hours": window_hours}
    except Exception as exc:
        raise _internal_error("Repair effectiveness failed", exc)


@router.get(
    "/regressions",
    summary="配置回归观察记录",
)
def regressions() -> Dict[str, Any]:
    try:
        observer = get_l3_config_observer()
        if observer is None:
            return {"items": [], "count": 0}
        stats = observer.stats()
        return {"items": stats.get("regression_events", []), "count": stats.get("regression_events", 0)}
    except Exception as exc:
        raise _internal_error("Regressions failed", exc)


# ===================================================================
# B-API-4: 健康趋势
# ===================================================================


@router.get(
    "/health/trend",
    summary="健康检查历史趋势",
)
def health_trend(
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """从事件流过滤 HEALTH_CHECK_COMPLETED 聚合趋势。"""
    try:
        bus = _get_bus()
        events = bus.get_recent_events(
            event_type=SystemEventType.HEALTH_CHECK_COMPLETED,
            limit=limit,
        )
        trend = []
        for e in events:
            payload = e.payload or {}
            trend.append({
                "timestamp": e.timestamp.isoformat(),
                "unhealthy_count": payload.get("unhealthy_count", 0),
                "unhealthy_components": payload.get("unhealthy_components", []),
                "severity": (
                    e.severity.value if isinstance(e.severity, EventSeverity) else str(e.severity)
                ),
            })
        return {"items": trend, "count": len(trend)}
    except Exception as exc:
        raise _internal_error("Health trend failed", exc)


# ===================================================================
# WS 事件推送
# ===================================================================


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """实时推送新事件（复用 EventBus 订阅）。

    客户端连接后，通过 SystemEventBus 通配订阅接收新事件，
    序列化为脱敏 dict 推送。断开时自动清理订阅。
    """
    await websocket.accept()
    bus = _get_bus()
    _queue: List[Dict[str, Any]] = []

    def _on_event(event: SystemEvent) -> None:
        try:
            _queue.append(_event_to_dict(event))
        except Exception:
            logger.debug("ws event serialization failed", exc_info=True)

    try:
        bus.subscribe_all(_on_event)
    except Exception as exc:
        logger.warning("WS subscribe failed: %s", exc)
        await websocket.close(code=1011)
        return

    try:
        while True:
            # 等待客户端消息（用于保活）或收到新事件后推送
            import asyncio

            await asyncio.sleep(0.2)
            if _queue:
                batch = _queue[:50]
                del _queue[:50]
                await websocket.send_json({"events": batch})
            await websocket.receive_text()  # 客户端 ping 保活
    except WebSocketDisconnect:
        logger.info("WS /ws/events client disconnected")
    except Exception as exc:
        logger.debug("WS /ws/events loop ended: %s", exc)
    finally:
        try:
            bus.unsubscribe_all(_on_event)
        except Exception:
            pass
