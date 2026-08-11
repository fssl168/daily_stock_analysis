# -*- coding: utf-8 -*-
"""
Local conftest for integration tests - bypasses root conftest that requires fastapi.
These tests only validate Pydantic schemas, not FastAPI app booting.
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import pytest

@pytest.fixture(scope="session")
def project_root():
    return ROOT
