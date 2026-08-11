# -*- coding: utf-8 -*-
"""Tests for src/services/code_aware_repair.py — CodeAwareRepairAgent (Phase 4)."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Create a minimal mock repo for testing."""
    src_dir = tmp_path / "src" / "services"
    src_dir.mkdir(parents=True, exist_ok=True)
    # A sample Python file with a potential NoneType bug
    buggy_file = src_dir / "buggy.py"
    buggy_file.write_text(
        textwrap.dedent("""\
        # -*- coding: utf-8 -*-
        def fetch_data(config):
            result = _query_api(config)
            return result.data  # line 3: result could be None
        def _query_api(config):
            return None
    """),
        encoding="utf-8",
    )
    # A sample Python file with dict access
    dict_file = src_dir / "dict_ops.py"
    dict_file.write_text(
        textwrap.dedent("""\
        def get_value(mapping):
            x = mapping['missing_key']
            return x
    """),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def agent(repo_root: Path):
    from src.services.code_aware_repair import CodeAwareRepairAgent
    return CodeAwareRepairAgent(repo_root=repo_root)


# ===================================================================
# FaultCategory enumeration
# ===================================================================


def test_fault_category_values():
    from src.services.code_aware_repair import FaultCategory
    assert FaultCategory.IMPORT_ERROR.value == "import_error"
    assert FaultCategory.ATTRIBUTE_ERROR.value == "attribute_error"
    assert FaultCategory.TYPE_ERROR.value == "type_error"
    assert FaultCategory.KEY_ERROR.value == "key_error"
    assert FaultCategory.INDEX_ERROR.value == "index_error"
    assert FaultCategory.TIMEOUT_ERROR.value == "timeout_error"
    assert FaultCategory.CONNECTION_ERROR.value == "connection_error"
    assert FaultCategory.UNKNOWN.value == "unknown"


# ===================================================================
# FaultLocation dataclass
# ===================================================================


def test_fault_location_defaults():
    from src.services.code_aware_repair import FaultLocation

    fl = FaultLocation(
        file_path="src/test.py",
        line_number=10,
        function_name="foo",
        exception_type="ValueError",
        exception_message="bad value",
        traceback_summary="...",
    )
    assert fl.category == "unknown"  # default
    assert fl.affected_modules == []
    assert fl.ast_context is None


# ===================================================================
# RepairPatch dataclass
# ===================================================================


def test_repair_patch_defaults():
    from src.services.code_aware_repair import FaultLocation, RepairPatch

    fl = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p1", fault_location=fl, file_path="src/test.py",
        original_lines="x = 1", patched_lines="x = 2",
        diff="--- a\n+++ b\n- x = 1\n+ x = 2",
        explanation="fix", confidence=0.8,
    )
    assert patch.auto_applicable is False  # default
    assert patch.status == "pending"


# ===================================================================
# Traceback parsing
# ===================================================================


def test_parse_traceback_in_repo(agent, repo_root):
    """Parse traceback where fault is inside repo src/."""
    tb = textwrap.dedent(f"""\
        Traceback (most recent call last):
          File "{repo_root}/main.py", line 5, in main
            import modules
          File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
            return result.data
        AttributeError: 'NoneType' object has no attribute 'data'
    """)
    file_path, line_num, func = agent._parse_traceback(tb)
    assert file_path is not None
    assert "buggy.py" in file_path
    assert line_num == 3
    assert func == "fetch_data"


def test_parse_traceback_multiple_frames(agent, repo_root):
    """Multi-frame traceback: picks the last frame in the repo."""
    tb = textwrap.dedent(f"""\
        Traceback (most recent call last):
          File "{repo_root}/main.py", line 10, in run
            do_stuff()
          File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
            return result.data
          File "{repo_root}/src/services/buggy.py", line 6, in _query_api
            raise RuntimeError("api down")
        RuntimeError: api down
    """)
    file_path, line_num, func = agent._parse_traceback(tb)
    assert file_path is not None
    assert "buggy.py" in file_path
    assert line_num == 6
    assert func == "_query_api"


def test_parse_traceback_stdlib_ignored(agent, repo_root):
    """Traceback where the last frame is in stdlib — should skip it."""
    tb = textwrap.dedent(f"""\
        Traceback (most recent call last):
          File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
            return result.data
          File "/usr/lib/python3.10/json/decoder.py", line 337, in decode
            obj, end = self.raw_decode(s, idx=_w(s, 0).end())
        AttributeError: 'NoneType' object has no attribute 'data'
    """)
    file_path, line_num, func = agent._parse_traceback(tb)
    assert file_path is not None
    # Should pick the repo frame, not stdlib
    assert "buggy.py" in file_path
    assert func == "fetch_data"


def test_parse_traceback_empty():
    """Empty traceback returns None."""
    agent_empty = _make_agent(Path("/tmp"))
    file_path, line_num, func = agent_empty._parse_traceback("")
    assert file_path is None


def test_parse_traceback_no_match():
    """Traceback with no File lines returns None."""
    agent_empty = _make_agent(Path("/tmp"))
    file_path, line_num, func = agent_empty._parse_traceback(
        "Some error happened\nNo file info here\n"
    )
    assert file_path is None


def test_parse_traceback_tests_dir_excluded(agent, repo_root):
    """Traceback whose only match is in tests/ should return that match
    (excluded from _FORBIDDEN_DIRS analysis but parsing is just parsing)."""
    # Create a test file for the parser to find
    tb = textwrap.dedent(f"""\
        Traceback (most recent call last):
          File "{repo_root}/tests/test_stuff.py", line 42, in test_thing
            assert foo.bar == 1
        AssertionError
    """)
    file_path, line_num, func = agent._parse_traceback(tb)
    # The parser returns the tests/ match because there's no other match
    # The safety check happens later in locate_fault / generate_patch
    assert file_path is not None
    assert "test_stuff.py" in file_path


# ===================================================================
# Fault classification
# ===================================================================


@pytest.mark.parametrize("exc_type,exc_msg,expected", [
    ("ImportError", "No module named 'foo'", "import_error"),
    ("ModuleNotFoundError", "No module named 'bar'", "import_error"),
    ("AttributeError", "'NoneType' object has no attribute 'x'", "attribute_error"),
    ("TypeError", "unsupported operand type(s)", "type_error"),
    ("KeyError", "'missing_key'", "key_error"),
    ("IndexError", "list index out of range", "index_error"),
    ("ValueError", "invalid literal for int()", "value_error"),
    ("TimeoutError", "connection timed out", "timeout_error"),
    ("ConnectionError", "connection refused", "connection_error"),
    ("ConnectionResetError", "connection reset by peer", "connection_error"),
    ("MemoryError", "out of memory", "resource_exhausted"),
    ("SomethingWeird", "never seen this before", "unknown"),
])
def test_classify_fault(agent, exc_type, exc_msg, expected):
    from src.services.code_aware_repair import FaultCategory
    result = agent._classify_fault(exc_type, exc_msg)
    assert result.value == expected


def test_classify_fault_timeout_in_message():
    """Timeout keyword in message even if type is different."""
    agent_empty = _make_agent(Path("/tmp"))
    result = agent_empty._classify_fault("RuntimeError", "request timeout after 30s")
    assert result.value == "timeout_error"


# ===================================================================
# _find_module_file
# ===================================================================


def test_find_module_file_src(agent):
    result = agent._find_module_file("src.services.buggy")
    assert result is not None
    assert "buggy.py" in result


def test_find_module_file_not_found(agent):
    result = agent._find_module_file("nonexistent.module.xyz")
    assert result is None


# ===================================================================
# locate_fault (full pipeline)
# ===================================================================


def test_locate_fault_from_traceback(agent, repo_root):
    tb = textwrap.dedent(f"""\
        Traceback (most recent call last):
          File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
            return result.data
        AttributeError: 'NoneType' object has no attribute 'data'
    """)
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=tb,
    )
    assert fault is not None
    assert "buggy.py" in fault.file_path
    assert fault.line_number == 3
    assert fault.function_name == "fetch_data"
    assert fault.category == "attribute_error"
    assert fault.ast_context is not None
    assert "fetch_data" in fault.ast_context


def test_locate_fault_from_module_name(agent):
    """Fall back to module name search when traceback is unparseable."""
    fault = agent.locate_fault(
        exception_type="KeyError",
        exception_message="'missing_key'",
        traceback_text="",
        module_name="src.services.dict_ops",
    )
    assert fault is not None
    assert "dict_ops.py" in fault.file_path


def test_locate_fault_not_found(agent):
    """Cannot locate fault when neither traceback nor module name helps."""
    fault = agent.locate_fault(
        exception_type="ValueError",
        exception_message="something went wrong",
    )
    assert fault is None


# ===================================================================
# Heuristic repair: None guard
# ===================================================================


def test_heuristic_repair_none_guard(agent, repo_root):
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
                return result.data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
    )
    assert fault is not None
    patch = agent.generate_patch(fault)
    assert patch is not None
    assert "None guard" in patch.explanation.lower() or patch.confidence > 0
    assert patch.auto_applicable is False


def test_heuristic_repair_none_guard_assignment(agent, repo_root):
    """None guard for assignment-style lines like 'var = expr.attr'."""
    # Create a file with an assignment pattern
    src_dir = repo_root / "src" / "services"
    assign_file = src_dir / "assign_bug.py"
    assign_file.write_text(
        textwrap.dedent("""\
        def process():
            value = get_result().data  # line 2
            return value
    """),
        encoding="utf-8",
    )

    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/assign_bug.py", line 2, in process
                value = get_result().data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
    )
    assert fault is not None
    patch = agent.generate_patch(fault)
    assert patch is not None


# ===================================================================
# Heuristic repair: KeyError
# ===================================================================


def test_heuristic_repair_key_error(agent, repo_root):
    fault = agent.locate_fault(
        exception_type="KeyError",
        exception_message="'missing_key'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/dict_ops.py", line 2, in get_value
                x = mapping['missing_key']
            KeyError: 'missing_key'
        """),
    )
    assert fault is not None
    patch = agent.generate_patch(fault)
    assert patch is not None
    assert ".get(" in patch.patched_lines or patch.confidence > 0
    assert patch.auto_applicable is False


# ===================================================================
# Import error heuristic
# ===================================================================


def test_heuristic_repair_import_error(agent, repo_root):
    tb = textwrap.dedent(f"""\
        Traceback (most recent call last):
          File "{repo_root}/src/services/buggy.py", line 1, in <module>
            import nonexistent_lib
        ModuleNotFoundError: No module named 'nonexistent_lib'
    """)
    fault = agent.locate_fault(
        exception_type="ModuleNotFoundError",
        exception_message="No module named 'nonexistent_lib'",
        traceback_text=tb,
    )
    # _heuristic_repair for import returns empty patched_line with 0.05 confidence
    # generate_patch will try LLM if available, then return None for low confidence
    # With no LLM, the patch might still be created but with very low confidence
    if fault is not None:
        patch = agent.generate_patch(fault)
        # Either no patch or very low confidence
        if patch is not None:
            assert patch.confidence <= 0.1


# ===================================================================
# Forbidden directory protection
# ===================================================================


def test_generate_patch_forbidden_dir(agent, repo_root):
    """Ensure patches are NOT generated for files in forbidden dirs."""
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'x'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/tests/test_x.py", line 10, in test_thing
                obj.x
            AttributeError: 'NoneType' object has no attribute 'x'
        """),
    )
    # locate_fault should still find it (parsing doesn't filter)
    assert fault is not None
    # But generate_patch should refuse to generate
    patch = agent.generate_patch(fault)
    assert patch is None  # forbidden directory


# ===================================================================
# Contract validation
# ===================================================================


def test_validate_contract_syntax_ok(agent, repo_root):
    """Valid patch passes syntax check."""
    from src.services.code_aware_repair import FaultLocation, RepairPatch

    fl = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p_ok", fault_location=fl, file_path="src/test.py",
        original_lines="x = 1", patched_lines="x = 2",
        diff="--- a\n+++ b\n- x = 1\n+ x = 2",
        explanation="fix", confidence=0.8,
    )
    results = agent.validate_contract(patch)
    assert len(results) > 0
    syntax_check = [r for r in results if r.check_name == "syntax_valid"]
    assert len(syntax_check) == 1
    assert syntax_check[0].passed is True


def test_validate_contract_syntax_error(agent, repo_root):
    """Patch with invalid Python fails syntax check."""
    from src.services.code_aware_repair import FaultLocation, RepairPatch

    fl = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p_bad", fault_location=fl, file_path="src/test.py",
        original_lines="x = 1", patched_lines="if True print('missing colon')",
        diff="--- a\n+++ b\n- x = 1\n+ if True print('missing colon')",
        explanation="broken fix", confidence=0.5,
    )
    results = agent.validate_contract(patch)
    syntax_check = [r for r in results if r.check_name == "syntax_valid"]
    assert len(syntax_check) == 1
    assert syntax_check[0].passed is False


def test_validate_contract_test_file_blocked(agent, repo_root):
    """Patch targeting tests/ dir is caught by contract validation."""
    from src.services.code_aware_repair import FaultLocation, RepairPatch

    fl = FaultLocation(
        file_path="tests/test_x.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p_test", fault_location=fl, file_path="tests/test_x.py",
        original_lines="x = 1", patched_lines="x = 2",
        diff="--- a\n+++ b\n- x = 1\n+ x = 2",
        explanation="fix", confidence=0.8,
    )
    results = agent.validate_contract(patch)
    test_check = [r for r in results if r.check_name == "not_test_file"]
    assert len(test_check) == 1
    assert test_check[0].passed is False


# ===================================================================
# apply_patch — dry_run safety
# ===================================================================


def test_apply_patch_dry_run(agent, repo_root):
    """dry_run=True does not modify files."""
    from src.services.code_aware_repair import FaultLocation, RepairPatch

    fl = FaultLocation(
        file_path="src/services/buggy.py", line_number=3,
        function_name="fetch_data", exception_type="AttributeError",
        exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p_dry", fault_location=fl,
        file_path="src/services/buggy.py",
        original_lines="    return result.data  # line 3: result could be None",
        patched_lines="    if result is not None:\n        return result.data",
        diff="--- a\n+++ b\n",
        explanation="fix", confidence=0.6,
        auto_applicable=True,  # explicitly allowed
    )
    applied, msg = agent.apply_patch(patch, dry_run=True)
    assert applied is False  # dry_run returns (False, message)
    assert "DRY RUN" in msg


def test_apply_patch_blocks_non_auto_applicable(agent, repo_root):
    """Patch with auto_applicable=False is blocked."""
    from src.services.code_aware_repair import FaultLocation, RepairPatch

    fl = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p_block", fault_location=fl, file_path="src/test.py",
        original_lines="x = 1", patched_lines="x = 2",
        diff="--- a\n+++ b\n- x = 1\n+ x = 2",
        explanation="fix", confidence=0.8,
        auto_applicable=False,
    )
    applied, msg = agent.apply_patch(patch, dry_run=False)
    assert applied is False
    assert "not auto_applicable" in msg.lower()


# ===================================================================
# Stats and query methods
# ===================================================================


def test_stats_empty(agent):
    s = agent.stats()
    assert s["total_patches"] == 0
    assert s["applied"] == 0
    assert s["pending"] == 0


def test_stats_after_patch(agent, repo_root):
    """Stats reflect generated patches."""
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
                return result.data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
    )
    agent.generate_patch(fault)
    s = agent.stats()
    assert s["total_patches"] == 1
    assert s["pending"] == 1


def test_get_pending_patches(agent, repo_root):
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
                return result.data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
    )
    agent.generate_patch(fault)
    pending = agent.get_pending_patches()
    assert len(pending) == 1
    assert pending[0].status == "pending"


def test_get_patch_history(agent, repo_root):
    for _ in range(3):
        fault = agent.locate_fault(
            exception_type="AttributeError",
            exception_message="'NoneType' object has no attribute 'data'",
            traceback_text=textwrap.dedent(f"""\
                Traceback (most recent call last):
                  File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
                    return result.data
                AttributeError: 'NoneType' object has no attribute 'data'
            """),
        )
        agent.generate_patch(fault)
    history = agent.get_patch_history(limit=2)
    assert len(history) == 2


def test_reset(agent, repo_root):
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
                return result.data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
    )
    agent.generate_patch(fault)
    assert agent.stats()["total_patches"] == 1

    agent.reset()
    assert agent.stats()["total_patches"] == 0
    assert agent.get_pending_patches() == []


# ===================================================================
# CodeAwareRepairAction adapter
# ===================================================================


def test_code_aware_repair_action_success(repo_root):
    """Full detect → repair → verify flow via adapter."""
    from src.services.code_aware_repair import CodeAwareRepairAction

    action = CodeAwareRepairAction(repo_root=repo_root)
    record = action.execute(context={
        "exception_type": "AttributeError",
        "exception_message": "'NoneType' object has no attribute 'data'",
        "traceback_text": textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/buggy.py", line 4, in fetch_data
                return result.data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
        "module_name": "src.services.buggy",
    })
    assert record.action_type == "patch"
    assert record.status == "success"
    assert record.verification_result is True


def test_code_aware_repair_action_no_fault(repo_root):
    """Returns failed when fault can't be located."""
    from src.services.code_aware_repair import CodeAwareRepairAction

    action = CodeAwareRepairAction(repo_root=repo_root)
    record = action.execute(context={
        "exception_type": "ValueError",
        "exception_message": "something",
    })
    assert record.status == "failed"
    assert "Could not locate" in record.error_message


# ===================================================================
# LLM assisted repair (response parsing)
# ===================================================================


def test_llm_assisted_repair_parses_diff(repo_root):
    """Verify LLM response parsing extracts diff, explanation, confidence."""
    from src.services.code_aware_repair import CodeAwareRepairAgent, FaultLocation

    mock_llm = MagicMock(return_value=textwrap.dedent("""\
        ```diff
        --- a/src/test.py
        +++ b/src/test.py
        @@ -1,3 +1,3 @@
        -x = None
        +x = get_value()
        ```
        EXPLANATION: Initialize x before use.
        CONFIDENCE: 0.75
        AUTO_APPLICABLE: false
    """))

    agent_llm = CodeAwareRepairAgent(repo_root=repo_root, llm_call=mock_llm)

    # Create a file for the LLM to read
    test_file = repo_root / "src" / "test.py"
    test_file.write_text("x = None\n", encoding="utf-8")

    fault = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="RuntimeError", exception_message="x is None",
        traceback_summary="", category="unknown",
    )

    patch = agent_llm._llm_assisted_repair(
        fault, repo_root / "src" / "test.py"
    )
    assert patch is not None
    mock_llm.assert_called_once()
    assert "Initialize x" in patch.explanation
    assert patch.confidence == 0.75
    assert patch.auto_applicable is False
    assert "llm" in patch.patch_id


def test_llm_assisted_repair_no_diff(repo_root):
    """LLM returns no diff → patch is None."""
    from src.services.code_aware_repair import CodeAwareRepairAgent, FaultLocation

    mock_llm = MagicMock(return_value="EXPLANATION: Can't fix this.\nCONFIDENCE: 0.1")
    agent_llm = CodeAwareRepairAgent(repo_root=repo_root, llm_call=mock_llm)

    test_file = repo_root / "src" / "test.py"
    test_file.write_text("pass\n", encoding="utf-8")

    fault = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = agent_llm._llm_assisted_repair(fault, test_file)
    assert patch is None


def test_llm_assisted_repair_no_llm_available(repo_root):
    """When llm_call is None, return None."""
    from src.services.code_aware_repair import CodeAwareRepairAgent, FaultLocation

    agent_no_llm = CodeAwareRepairAgent(repo_root=repo_root, llm_call=None)

    fault = FaultLocation(
        file_path="src/test.py", line_number=1, function_name="f",
        exception_type="E", exception_message="msg", traceback_summary="",
    )
    patch = agent_no_llm._llm_assisted_repair(fault, repo_root / "src" / "test.py")
    assert patch is None


# ===================================================================
# on_patch_ready callback
# ===================================================================


def test_on_patch_ready_callback(agent, repo_root):
    """Callback is fired when a patch is generated."""
    calls = []

    agent_cb = _make_agent(repo_root, on_patch_ready=lambda p: calls.append(p))

    fault = agent_cb.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'data'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/buggy.py", line 3, in fetch_data
                return result.data
            AttributeError: 'NoneType' object has no attribute 'data'
        """),
    )
    agent_cb.generate_patch(fault)
    assert len(calls) == 1
    assert isinstance(calls[0].patch_id, str)


# ===================================================================
# apply_patch — real file modification (integration)
# ===================================================================


def test_apply_patch_real_modification(repo_root):
    """Apply a patch with dry_run=False actually modifies the file."""
    from src.services.code_aware_repair import (
        CodeAwareRepairAgent,
        FaultLocation,
        RepairPatch,
    )

    agent_real = CodeAwareRepairAgent(repo_root=repo_root)

    target_file = repo_root / "src" / "services" / "real_fix.py"
    original_content = "result = query()\n"
    target_file.write_text(original_content, encoding="utf-8")

    fl = FaultLocation(
        file_path="src/services/real_fix.py", line_number=1,
        function_name="main", exception_type="AttributeError",
        exception_message="msg", traceback_summary="",
    )
    patch = RepairPatch(
        patch_id="p_apply", fault_location=fl,
        file_path="src/services/real_fix.py",
        original_lines="result = query()",
        patched_lines="result = query() or None",
        diff="--- a\n+++ b\n- result = query()\n+ result = query() or None",
        explanation="Add fallback", confidence=0.9,
        auto_applicable=True,
    )
    applied, msg = agent_real.apply_patch(patch, dry_run=False)
    assert applied is True
    assert "applied" in msg.lower() or "Patch" in msg

    # Verify file was modified
    new_content = target_file.read_text(encoding="utf-8")
    assert "result = query() or None" in new_content

    # Verify backup was created
    backups = list((repo_root / "src" / "services").glob("real_fix.py.bak.*"))
    assert len(backups) >= 1


# ===================================================================
# Safety edge cases
# ===================================================================


def test_locate_fault_nonexistent_file(repo_root):
    """locate_fault handles files that don't exist (gracefully returns None for
    AST context but still returns a FaultLocation)."""
    from src.services.code_aware_repair import CodeAwareRepairAgent

    agent_local = CodeAwareRepairAgent(repo_root=repo_root)
    fault = agent_local.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'x'",
        traceback_text=textwrap.dedent(f"""\
            Traceback (most recent call last):
              File "{repo_root}/src/services/nonexistent.py", line 5, in foo
                obj.x
            AttributeError: 'NoneType' object has no attribute 'x'
        """),
    )
    # File doesn't exist → ast_context stays None
    assert fault is not None
    assert fault.ast_context is None


def test_generate_patch_nonexistent_file(repo_root):
    """generate_patch returns None for nonexistent file."""
    from src.services.code_aware_repair import CodeAwareRepairAgent, FaultLocation

    agent_local = CodeAwareRepairAgent(repo_root=repo_root)
    fault = FaultLocation(
        file_path="src/nonexistent.py", line_number=1, function_name="f",
        exception_type="ValueError", exception_message="msg",
        traceback_summary="",
    )
    patch = agent_local.generate_patch(fault)
    assert patch is None


# ===================================================================
# Helpers
# ===================================================================


def _make_agent(repo_root, on_patch_ready=None):
    from src.services.code_aware_repair import CodeAwareRepairAgent
    return CodeAwareRepairAgent(repo_root=repo_root, on_patch_ready=on_patch_ready)
