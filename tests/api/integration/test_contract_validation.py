#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2 Implementation: BP-011 - Interface Contract Tests (lightweight version)
These tests validate Pydantic schemas without requiring full app boot.
"""

import pytest
from pydantic import ValidationError

# Import only the schemas, not the full API app
from api.v1.schemas.analysis import AnalyzeRequest, TaskInfo, TaskStatusEnum
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.paper_trading import AccountCreateRequest


class TestSchemaContractValidation:
    """Test that Pydantic schemas work correctly."""

    def test_analyze_request_schema_accepts_all_fields(self):
        """AnalyzeRequest schema accepts all expected fields."""
        sample_payload = {
            "stock_code": "600519",
            "report_type": "detailed",
            "force_refresh": False,
            "async_mode": False,
            "analysis_phase": "auto",
            "stock_name": "贵州茅台",
            "original_query": "茅台",
            "selection_source": "manual",
            "notify": True,
            "report_language": "zh",
            "skills": ["bull_trend"]
        }
        
        request = AnalyzeRequest(**sample_payload)
        assert request.stock_code == "600519"
        assert request.report_type == "detailed"
        assert request.skills == ["bull_trend"]

    def test_task_info_schema_contains_all_required_fields(self):
        """TaskInfo Schema has all required fields."""
        task_data = {
            "task_id": "test-task-123",
            "trace_id": "trace-abc-def",
            "stock_code": "600519",
            "status": "processing",
            "progress": 50,
            "message": "正在分析...",
            "report_type": "detailed",
            "created_at": "2026-07-31T10:00:00",
            "original_query": "茅台",
            "selection_source": "manual"
        }
        
        task = TaskInfo(**task_data)
        assert task.task_id == "test-task-123"
        assert task.stock_code == "600519"
        assert task.status == "processing"
        assert task.progress == 50

    def test_error_response_schema_structure(self):
        """ErrorResponse Schema has correct structure."""
        error_data = {
            "error": "validation_error",
            "message": "请输入有效的股票代码",
            "detail": {"field": "stock_code", "issue": "format"},
            "timestamp": "2026-07-31T10:00:00"
        }
        
        error = ErrorResponse(**error_data)
        assert error.error == "validation_error"
        assert error.message == "请输入有效的股票代码"
        assert error.detail == {"field": "stock_code", "issue": "format"}

    def test_status_enum_values(self):
        """TaskStatusEnum values match expected strings."""
        assert TaskStatusEnum.PENDING.value == "pending"
        assert TaskStatusEnum.PROCESSING.value == "processing"
        assert TaskStatusEnum.COMPLETED.value == "completed"
        assert TaskStatusEnum.FAILED.value == "failed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
