#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 Test for BP-001 - Image extraction API schema validation.
Lightweight version - validates Pydantic models without starting FastAPI app.
"""

import pytest
from api.v1.schemas.stocks import ExtractFromImageResponse, ExtractItem


class TestImageExtractionSchema:
    """Validate image extraction response schemas."""

    def test_extract_from_image_response_has_expected_fields(self):
        """ExtractFromImageResponse has all expected fields."""
        resp = ExtractFromImageResponse(
            codes=["600519"],
            items=[ExtractItem(code="600519", name="贵州茅台", confidence="high")],
            raw_text="raw output"
        )
        assert resp.codes == ["600519"]
        assert len(resp.items) == 1
        assert resp.raw_text == "raw output"

    def test_extract_item_default_confidence(self):
        """ExtractItem default confidence should be 'medium'."""
        item = ExtractItem(code="600519", name="贵州茅台")
        assert item.confidence == "medium"

    def test_extract_item_valid_confidence_values(self):
        """ExtractItem confidence should accept high/medium/low strings."""
        for conf in ["high", "medium", "low"]:
            item = ExtractItem(code="600519", confidence=conf)
            assert item.confidence == conf


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
