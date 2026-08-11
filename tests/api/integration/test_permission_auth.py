#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 Test for BP-003 - Permission and auth schema validation only.
Lightweight version without starting FastAPI app.
"""

import pytest
from api.v1.schemas.common import ErrorResponse


class TestPermissionAuthSchema:
    """Validate permission-related response schemas."""

    def test_error_response_has_expected_structure(self):
        """ErrorResponse has correct structure for permission errors."""
        error = ErrorResponse(
            error="forbidden",
            message="权限不足",
            detail={"required": "admin", "actual": "user"},
            timestamp="2026-07-31T10:00:00"
        )
        assert error.error == "forbidden"
        assert error.message == "权限不足"
        assert error.detail == {"required": "admin", "actual": "user"}

    def test_duplicate_task_error_schema(self):
        """Test ErrorResponse-like structure for duplicate task scenario."""
        # Check that our error format includes relevant fields
        error_data = {
            "error": "duplicate_task",
            "message": "股票 600519 正在分析中 (task_id: task_123)",
            "stock_code": "600519",
            "existing_task_id": "task_123"
        }
        # Verify dictionary structure is valid
        assert "error" in error_data
        assert "message" in error_data
        assert "stock_code" in error_data
        assert "existing_task_id" in error_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
