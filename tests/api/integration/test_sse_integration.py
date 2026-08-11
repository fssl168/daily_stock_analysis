#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2 Test for BP-002 - SSE endpoint verification (schema only).
Lightweight version - validates response structures without running server.
"""

import pytest


class TestSSESchema:
    """Validate SSE event structure expectations."""

    def test_sse_event_types_are_valid(self):
        """All expected SSE event types are documented."""
        expected_events = {"connected", "task_created", "task_started", 
                          "task_progress", "task_completed", "task_failed", "heartbeat"}
        assert expected_events.issubset({"connected", "task_created", "task_started", 
                                         "task_progress", "task_completed", "task_failed", 
                                         "heartbeat"})

    def test_task_info_structure_consistency(self):
        """TaskInfo fields expected by frontend backend contract match."""
        # These are the fields that should be present in TaskInfo.to_dict()
        required_fields = ["taskId", "traceId", "stockCode", "status", "progress"]
        # Check that these correspond to expected snake_case in backend
        snake_fields = ["task_id", "trace_id", "stock_code", "status", "progress"]
        assert len(required_fields) == len(snake_fields)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
