# -*- coding: utf-8 -*-
"""Tests for observability API endpoints (L1/L2/L3/L4 passive observation)."""

import unittest

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from api.v1.endpoints import observability
from src.services.bootstrap_event_bus import bootstrap_event_bus
from src.services.event_bus import (
    EventSeverity,
    SystemEvent,
    SystemEventBus,
    SystemEventType,
)


def _reset_bus() -> SystemEventBus:
    """重置 EventBus 单例并重新装配，保证测试隔离。"""
    SystemEventBus.reset_instance()
    return bootstrap_event_bus()


class EventRedactionTestCase(unittest.TestCase):
    """payload 脱敏逻辑测试。"""

    def setUp(self) -> None:
        self.bus = _reset_bus()

    def test_agent_tool_arguments_truncated(self):
        """agent_tool_call 的 arguments 应截断到 200 字符。"""
        self.bus.publish(SystemEvent(
            event_id="t1",
            event_type=SystemEventType.AGENT_TOOL_CALL,
            severity=EventSeverity.INFO,
            source="agent_runner",
            payload={"tool_names": ["search"], "arguments": "x" * 500},
        ))
        event = self.bus.get_recent_events(limit=1)[0]
        out = observability._event_to_dict(event)
        self.assertEqual(out["event_type"], "agent_tool_call")
        self.assertLessEqual(len(out["payload_redacted"]["arguments"]), 203)  # 200 + "..."
        self.assertTrue(out["payload_redacted"]["arguments"].endswith("..."))

    def test_notification_payload_reduced(self):
        """notification_* 事件只保留渠道概要，不暴露内部配置。"""
        self.bus.publish(SystemEvent(
            event_id="t2",
            event_type=SystemEventType.NOTIFICATION_SENT,
            severity=EventSeverity.INFO,
            source="notification_service",
            payload={"success": True, "route_type": "report", "internal_endpoint": "http://x"},
        ))
        event = self.bus.get_recent_events(limit=1)[0]
        out = observability._event_to_dict(event)
        self.assertNotIn("internal_endpoint", out["payload_redacted"])
        self.assertIn("success", out["payload_redacted"])

    def test_llm_usage_keeps_only_model_and_tokens(self):
        """llm_usage 只保留 model/tokens，不暴露密钥。"""
        self.bus.publish(SystemEvent(
            event_id="t3",
            event_type=SystemEventType.LLM_USAGE,
            severity=EventSeverity.INFO,
            source="llm",
            payload={"model": "gemini-pro", "tokens": 123, "api_key": "sk-xxx"},
        ))
        event = self.bus.get_recent_events(limit=1)[0]
        out = observability._event_to_dict(event)
        self.assertNotIn("api_key", out["payload_redacted"])
        self.assertEqual(out["payload_redacted"]["model"], "gemini-pro")

    def test_plain_payload_passthrough(self):
        """普通事件 payload 不应被脱敏误伤。"""
        self.bus.publish(SystemEvent(
            event_id="t4",
            event_type=SystemEventType.PIPELINE_COMPLETED,
            severity=EventSeverity.INFO,
            source="pipeline",
            payload={"stock_code": "600519", "success": True},
        ))
        event = self.bus.get_recent_events(limit=1)[0]
        out = observability._event_to_dict(event)
        self.assertEqual(out["payload_redacted"]["stock_code"], "600519")


class EventListingTestCase(unittest.TestCase):
    """事件流查询逻辑测试。"""

    def setUp(self) -> None:
        self.bus = _reset_bus()
        for i in range(5):
            self.bus.publish(SystemEvent(
                event_id=f"e{i}",
                event_type=SystemEventType.PIPELINE_COMPLETED,
                severity=EventSeverity.INFO,
                source="pipeline",
                payload={"stock_code": f"60051{i}", "success": True},
            ))

    def test_list_events_pagination(self):
        """事件列表分页应返回正确切片。"""
        result = observability.list_events(page=1, page_size=2)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 2)
        self.assertLessEqual(len(result["items"]), 2)

    def test_list_events_filter_by_type(self):
        """按事件类型过滤应只返回匹配类型。"""
        result = observability.list_events(event_type="pipeline_completed", page=1, page_size=10)
        self.assertGreaterEqual(result["total"], 5)
        for item in result["items"]:
            self.assertEqual(item["event_type"], "pipeline_completed")

    def test_event_stats_shape(self):
        """事件统计应包含 bus/l4/l3 三块。"""
        result = observability.event_stats()
        self.assertIn("bus", result)
        self.assertIn("l4_meta_observer", result)
        self.assertIn("l3_config_observer", result)


class MetaEndpointTestCase(unittest.TestCase):
    """L4 元认知端点测试。"""

    def setUp(self) -> None:
        self.bus = _reset_bus()

    def test_meta_observations_empty(self):
        """无观察时返回空列表。"""
        result = observability.meta_observations(limit=50)
        self.assertIn("items", result)
        self.assertIn("count", result)

    def test_meta_reflect_no_engine_returns_404(self):
        """未装配 MetaCognitiveEngine 时 reflect 返回 404。"""
        from fastapi import HTTPException
        # 强制清空 engine 引用以模拟未装配
        import src.services.bootstrap_event_bus as beb
        old = beb._META_OBSERVER
        beb._META_OBSERVER = None
        try:
            with self.assertRaises(HTTPException) as ctx:
                observability.meta_reflect()
            self.assertEqual(ctx.exception.status_code, 404)
        finally:
            beb._META_OBSERVER = old


class HealthTrendTestCase(unittest.TestCase):
    """健康趋势端点测试。"""

    def setUp(self) -> None:
        self.bus = _reset_bus()

    def test_health_trend_aggregates(self):
        """健康趋势应从 HEALTH_CHECK_COMPLETED 事件聚合。"""
        for i in range(3):
            self.bus.publish(SystemEvent(
                event_id=f"h{i}",
                event_type=SystemEventType.HEALTH_CHECK_COMPLETED,
                severity=EventSeverity.WARNING if i % 2 else EventSeverity.INFO,
                source="health_check_daemon",
                payload={"unhealthy_count": i, "unhealthy_components": []},
            ))
        result = observability.health_trend(limit=100)
        self.assertGreaterEqual(result["count"], 3)
        for item in result["items"]:
            self.assertIn("timestamp", item)
            self.assertIn("unhealthy_count", item)


class AdjustmentApiTestCase(unittest.TestCase):
    """REV-203: 调整端点类型校验 + 白名单门控。"""

    def setUp(self) -> None:
        self.bus = _reset_bus()

    def test_apply_unsafe_param_400(self):
        """白名单外参数（如 ORDER_SIZE）应返回 400。"""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            observability.adjustment_apply({"param_name": "ORDER_SIZE", "param_value": 1000})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_apply_invalid_max_steps_400(self):
        """AGENT_MAX_STEPS 非整数应返回 400。"""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            observability.adjustment_apply({"param_name": "AGENT_MAX_STEPS", "param_value": "abc"})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_apply_invalid_profile_400(self):
        """AGENT_CONTEXT_COMPRESSION_PROFILE 非法档位应返回 400。"""
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            observability.adjustment_apply(
                {"param_name": "AGENT_CONTEXT_COMPRESSION_PROFILE", "param_value": "extreme"}
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_apply_valid_max_steps_ok(self):
        """AGENT_MAX_STEPS=12 应成功应用（归一化为 int）。"""
        result = observability.adjustment_apply({"param_name": "AGENT_MAX_STEPS", "param_value": "12"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["param_name"], "AGENT_MAX_STEPS")


if __name__ == "__main__":
    unittest.main()
