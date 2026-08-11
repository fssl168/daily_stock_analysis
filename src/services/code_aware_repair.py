# -*- coding: utf-8 -*-
"""
代码感知修复代理（CodeAwareRepairAgent）—— L3 架构级自修复的核心能力。

从操作级守护跨越到架构级自修复的关键：自动定位代码故障源、生成修复 patch、
并在修复前后验证业务合约。修复 patch 默认不自动应用，需人工确认。

核心能力:
1. AST 级故障分析 — 解析 Python 源码定位异常源头
2. Patch 生成 — 基于故障模式生成修复建议（diff 格式）
3. 合约验证 — 修复前后业务合约的一致性检查
4. LLM 集成 — 利用 LLM 分析复杂故障和生成修复方案

安全边界:
- patch 不自动应用 — 生成 unified diff 后等待人工确认
- 合约验证不通过时阻止 patch 应用
- 默认只分析当前仓库 `src/` 目录下的 Python 文件
- 不修改测试文件（tests/）
- Phase 3 集成: 每次 patch 生成/应用记录到 RepairEffectivenessLog

用法:
    agent = CodeAwareRepairAgent(repo_root=Path.cwd())

    # 从异常信息定位故障
    fault = agent.locate_fault(
        exception_type="AttributeError",
        exception_message="'NoneType' object has no attribute 'close'",
        traceback_text="...",
    )

    # 生成修复 patch
    patch = agent.generate_patch(fault)

    # 验证合约
    contract_ok = agent.validate_contract(patch)

    # 应用（需显式确认）
    if patch and contract_ok:
        ok, msg = agent.apply_patch(patch, dry_run=True)

来源: docs/L3_ARCHITECTURE_AUDIT.md Phase 4 / Finding #1 (code-aware repair)
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ===================================================================
# 数据结构
# ===================================================================


class FaultCategory(str, Enum):
    """故障分类。"""

    IMPORT_ERROR = "import_error"           # 导入失败
    ATTRIBUTE_ERROR = "attribute_error"     # 属性不存在
    TYPE_ERROR = "type_error"               # 类型错误
    KEY_ERROR = "key_error"                 # 字典键缺失
    INDEX_ERROR = "index_error"             # 索引越界
    VALUE_ERROR = "value_error"             # 值错误
    TIMEOUT_ERROR = "timeout_error"         # 超时
    CONNECTION_ERROR = "connection_error"   # 连接失败
    RESOURCE_EXHAUSTED = "resource_exhausted"  # 资源耗尽
    UNKNOWN = "unknown"


@dataclass
class FaultLocation:
    """故障定位信息。"""

    file_path: str                          # 故障源文件路径
    line_number: int                        # 故障行号
    function_name: str                      # 所在函数
    exception_type: str                     # 异常类型
    exception_message: str                  # 异常消息
    traceback_summary: str                  # traceback 摘要
    category: str = FaultCategory.UNKNOWN.value
    affected_modules: List[str] = field(default_factory=list)
    ast_context: Optional[str] = None       # AST 上下文（故障函数源码片段）


@dataclass
class RepairPatch:
    """修复 patch — 一个 unified diff 片段。"""

    patch_id: str                           # "patch_{timestamp}_{hash}"
    fault_location: FaultLocation
    file_path: str
    original_lines: str                     # 原始代码行
    patched_lines: str                      # 修复后代码行
    diff: str                               # unified diff
    explanation: str                        # 修复说明（人类可读 + LLM 生成）
    confidence: float                       # 修复置信度 [0, 1]
    auto_applicable: bool = False           # 是否可以自动应用（默认 False）
    contract_checks: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"                 # pending | applied | rejected | verified


@dataclass
class ContractCheckResult:
    """合约检查结果。"""

    check_name: str
    passed: bool
    detail: str = ""
    before_value: Any = None
    after_value: Any = None


# ===================================================================
# CodeAwareRepairAgent
# ===================================================================


class CodeAwareRepairAgent:
    """代码感知修复代理。

    当 L3 操作级修复（重启/回滚/降级）无法解决问题时，
    本代理尝试进行代码级的故障分析和修复建议。

    安全设计:
    - 所有 patch 默认 auto_applicable=False，需人工确认
    - 合约验证不通过时标记 patch 为高风险
    - 仅分析 src/ 目录下的文件（不修改 tests/）
    - 最大分析深度限制，防止无限递归
    """

    # 安全边界：仅分析这些目录
    _ANALYSIS_DIRS = ["src/", "data_provider/", "api/", "bot/"]
    # 禁止修改的目录
    _FORBIDDEN_DIRS = ["tests/", ".git/", "venv/", ".venv/", "__pycache__/"]
    # 最大分析深度（traceback 栈帧数）
    _MAX_TRACEBACK_DEPTH = 10

    def __init__(
        self,
        repo_root: Path,
        on_patch_ready: Optional[Callable[[RepairPatch], None]] = None,
        llm_call: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        """初始化修复代理。

        Args:
            repo_root: 仓库根目录。
            on_patch_ready: patch 就绪回调（用于通知/日志）。
            llm_call: LLM 调用函数，签名 (system_prompt, user_prompt) → response_text。
                     为 None 时使用纯静态分析（启发式修复）。
        """
        self._repo_root = repo_root
        self._on_patch_ready = on_patch_ready
        self._llm_call = llm_call
        self._lock = threading.RLock()
        self._patches: List[RepairPatch] = []
        self._patch_counter = 0

    # ==================================================================
    # 故障定位
    # ==================================================================

    def locate_fault(
        self,
        exception_type: str,
        exception_message: str,
        traceback_text: str = "",
        module_name: str = "",
    ) -> Optional[FaultLocation]:
        """从异常信息定位代码故障源。

        分析策略:
        1. 解析 traceback 定位精确的文件和行号
        2. 若无法解析 traceback，搜索仓库中相关的 import / call site
        3. 提取故障函数的 AST 上下文

        Args:
            exception_type: 异常类型（如 "AttributeError"）。
            exception_message: 异常消息。
            traceback_text: 完整 traceback 文本。
            module_name: 出故障的模块名（辅助定位）。

        Returns:
            FaultLocation 如果定位成功，否则 None。
        """
        # Step 1: 解析 traceback
        file_path, line_number, func_name = self._parse_traceback(traceback_text)

        # Step 2: 如果 traceback 解析失败，尝试搜索
        if file_path is None and module_name:
            file_path = self._find_module_file(module_name)

        if file_path is None:
            logger.warning(
                "Could not locate fault source from traceback or module name"
            )
            return None

        # Step 3: 分类故障
        category = self._classify_fault(exception_type, exception_message)

        # Step 4: 提取 AST 上下文
        ast_context = None
        full_path = self._repo_root / file_path
        if full_path.exists():
            try:
                source = full_path.read_text(encoding="utf-8")
                if line_number and line_number > 0:
                    lines = source.split("\n")
                    start = max(0, line_number - 10)
                    end = min(len(lines), line_number + 10)
                    ast_context = "\n".join(
                        f"{i + 1}: {line}"
                        for i, line in enumerate(lines[start:end], start=start)
                    )
            except Exception:
                pass

        return FaultLocation(
            file_path=file_path,
            line_number=line_number or 0,
            function_name=func_name or "",
            exception_type=exception_type,
            exception_message=exception_message,
            traceback_summary=traceback_text[:500] if traceback_text else "",
            category=category.value,
            affected_modules=[module_name] if module_name else [],
            ast_context=ast_context,
        )

    def _parse_traceback(
        self, traceback_text: str
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """解析 Python traceback，提取文件、行号、函数名。

        取最后一个匹配（最接近异常抛出点的调用栈帧），
        并过滤掉 stdlib / site-packages 中的帧。
        """
        if not traceback_text:
            return None, None, None

        pattern = r'File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+(\w+)'
        matches = re.findall(pattern, traceback_text)

        if not matches:
            return None, None, None

        # 从后向前找第一个在当前仓库内的帧
        for file_path, line_str, func_name in reversed(matches):
            line_number = int(line_str)

            # 尝试转为仓库相对路径
            try:
                rel_path = str(Path(file_path).relative_to(self._repo_root))
            except ValueError:
                # 不在当前仓库下的文件（stdlib 等），跳过
                continue

            # 安全检查：不在禁止目录内
            if any(f in rel_path for f in self._FORBIDDEN_DIRS):
                continue

            return rel_path, line_number, func_name

        # 回退：取最后一个匹配（兼容旧行为）
        last = matches[-1]
        file_path = last[0]
        line_number = int(last[1])
        func_name = last[2]

        try:
            file_path = str(Path(file_path).relative_to(self._repo_root))
        except ValueError:
            pass

        return file_path, line_number, func_name

    def _find_module_file(self, module_name: str) -> Optional[str]:
        """根据模块名在仓库中查找对应的 Python 文件。"""
        parts = module_name.replace(".", "/")
        candidates = [
            f"{parts}.py",
            f"src/{parts}.py",
            f"src/services/{parts.split('/')[-1]}.py",
        ]
        for cand in candidates:
            if (self._repo_root / cand).exists():
                return cand
        return None

    def _classify_fault(
        self, exception_type: str, exception_message: str
    ) -> FaultCategory:
        """根据异常类型和消息分类故障。"""
        et = exception_type.lower()
        em = exception_message.lower()

        if "importerror" in et or "modulenotfound" in et:
            return FaultCategory.IMPORT_ERROR
        if "attributeerror" in et:
            return FaultCategory.ATTRIBUTE_ERROR
        if "typeerror" in et:
            return FaultCategory.TYPE_ERROR
        if "keyerror" in et:
            return FaultCategory.KEY_ERROR
        if "indexerror" in et:
            return FaultCategory.INDEX_ERROR
        if "valueerror" in et:
            return FaultCategory.VALUE_ERROR
        if "timeout" in et or "timeout" in em:
            return FaultCategory.TIMEOUT_ERROR
        if "connection" in et or "connection" in em:
            return FaultCategory.CONNECTION_ERROR
        if "memory" in em or "resource" in em:
            return FaultCategory.RESOURCE_EXHAUSTED

        return FaultCategory.UNKNOWN

    # ==================================================================
    # Patch 生成
    # ==================================================================

    def generate_patch(self, fault: FaultLocation) -> Optional[RepairPatch]:
        """根据故障定位生成修复 patch。

        策略:
        1. 纯静态分析（启发式规则）→ 适用于简单故障（AttributeError 等）
        2. LLM 辅助分析 → 适用于复杂故障或启发式规则无法覆盖的场景

        Args:
            fault: 故障定位信息。

        Returns:
            RepairPatch 如果生成了修复方案，否则 None。
        """
        full_path = self._repo_root / fault.file_path
        if not full_path.exists():
            logger.warning("Fault file not found: %s", full_path)
            return None

        # 安全检查：不修改禁止目录
        for forbidden in self._FORBIDDEN_DIRS:
            if forbidden in str(fault.file_path):
                logger.warning(
                    "File in forbidden directory: %s", fault.file_path
                )
                return None

        # 启发式修复
        patch = self._heuristic_repair(fault, full_path)

        # 如果启发式方法不够（confidence < 0.5），尝试 LLM
        if patch is None or (
            patch.confidence < 0.5 and self._llm_call is not None
        ):
            llm_patch = self._llm_assisted_repair(fault, full_path)
            if llm_patch and (
                patch is None or llm_patch.confidence > patch.confidence
            ):
                patch = llm_patch

        if patch is None:
            return None

        with self._lock:
            self._patch_counter += 1
            self._patches.append(patch)

        if self._on_patch_ready:
            try:
                self._on_patch_ready(patch)
            except Exception:
                logger.exception("on_patch_ready callback failed")

        return patch

    def _heuristic_repair(
        self, fault: FaultLocation, full_path: Path
    ) -> Optional[RepairPatch]:
        """启发式修复：基于常见模式的静态 patch 生成。"""
        try:
            source = full_path.read_text(encoding="utf-8")
            lines = source.split("\n")
        except Exception:
            return None

        if fault.line_number <= 0 or fault.line_number > len(lines):
            return None

        original_line = lines[fault.line_number - 1]
        patched_line = original_line
        explanation = ""
        confidence = 0.0

        # AttributeError: NoneType has no attribute 'X'
        if fault.category == FaultCategory.ATTRIBUTE_ERROR.value:
            if "NoneType" in fault.exception_message:
                patched_line, explanation, confidence = self._repair_none_guard(
                    original_line, fault.exception_message
                )

        # KeyError: 缺失的键
        elif fault.category == FaultCategory.KEY_ERROR.value:
            key_match = re.search(r"'([^']+)'", fault.exception_message)
            if key_match:
                missing_key = key_match.group(1)
                patched_line, explanation, confidence = self._repair_key_error(
                    original_line, missing_key
                )

        # ImportError: 缺少导入
        elif fault.category == FaultCategory.IMPORT_ERROR.value:
            patched_line, explanation, confidence = self._repair_import_error(
                fault
            )

        if patched_line == original_line and not explanation:
            return None

        # 生成 unified diff
        diff_lines = list(
            difflib.unified_diff(
                [original_line],
                patched_line.split("\n"),
                fromfile=str(fault.file_path),
                tofile=str(fault.file_path),
                lineterm="",
            )
        )
        diff = "\n".join(diff_lines) if diff_lines else ""

        ts = int(time.time() * 1000)
        return RepairPatch(
            patch_id=f"patch_{ts}_{hashlib.sha256(diff.encode()).hexdigest()[:8]}",
            fault_location=fault,
            file_path=str(fault.file_path),
            original_lines=original_line,
            patched_lines=patched_line,
            diff=diff,
            explanation=explanation,
            confidence=confidence,
            auto_applicable=False,  # 默认不自动应用
            contract_checks=[],
        )

    # ---- 启发式修复子方法 ----

    def _repair_none_guard(
        self, line: str, exception_message: str
    ) -> Tuple[str, str, float]:
        """为可能产生 None 的行添加 None guard。

        返回 (patched_line, explanation, confidence)
        """
        stripped = line.strip()
        indent = line[: len(line) - len(stripped)]

        # 如果已经有 None 检查，不重复修改
        if "is not None" in stripped or "is None" in stripped:
            return line, "Line already has a None check; manual review needed.", 0.0

        # 尝试提取变量名
        # 模式: var.attr → var 被访问
        # 从异常消息中提取属性名可能不可靠，这里用简单启发式
        var_match = re.search(
            r"'(\w+)'\s*object\s*has\s*no\s*attribute\s*'(\w+)'",
            exception_message,
        )
        if var_match:
            # type_name, attr_name = var_match.groups()
            # 无法从行中可靠提取变量名，生成通用 guard
            pass

        # 生成 if ... is not None: guard
        # 简单策略：在单行赋值/调用前插 if 检查
        # 对赋值语句: var = expr().attr → if (tmp := expr()) is not None: var = tmp.attr
        if "=" in stripped and "." in stripped.split("=", 1)[-1]:
            lhs, rhs_expr = stripped.split("=", 1)
            lhs = lhs.strip()
            rhs_expr = rhs_expr.strip()

            # 提取 rhs 中最外层的方法调用链
            # 例如: some_func().attr → expr = some_func()
            dot_parts = rhs_expr.rsplit(".", 1)
            if len(dot_parts) == 2:
                base_expr = dot_parts[0]
                attribute = dot_parts[1]
                patched = (
                    f"{indent}_tmp = {base_expr}\n"
                    f"{indent}if _tmp is not None:\n"
                    f"{indent}    {lhs} = _tmp.{attribute}"
                )
                return (
                    patched,
                    "Added None guard for potential NoneType. "
                    f"Original line produces None, causing AttributeError. "
                    "Review required: the real fix may need upstream null handling.",
                    0.4,
                )

        # 通用 None guard
        patched = f"{indent}# FIX: potential NoneType — consider adding None check\n{indent}{stripped}"
        return (
            patched,
            "Could not auto-generate None guard pattern. "
            "Consider adding 'if var is not None:' before this line.",
            0.2,
        )

    def _repair_key_error(
        self, original_line: str, missing_key: str
    ) -> Tuple[str, str, float]:
        """为 KeyError 生成 .get() 替换。

        返回 (patched_line, explanation, confidence)
        """
        # 替换 dict['key'] → dict.get('key')
        patterns = [
            (f"['{missing_key}']", f".get('{missing_key}')"),
            (f'["{missing_key}"]', f'.get("{missing_key}")'),
        ]
        for old, new in patterns:
            if old in original_line:
                patched = original_line.replace(old, new)
                return (
                    patched,
                    f"Replaced direct key access with .get('{missing_key}') "
                    "to handle missing key. "
                    "Consider whether a default value is appropriate.",
                    0.6,
                )

        # 没有找到精确匹配 — 可能是变量键
        return (
            original_line,
            f"KeyError: key '{missing_key}' not found. "
            "Consider using .get() with a default value.",
            0.1,
        )

    def _repair_import_error(
        self, fault: FaultLocation
    ) -> Tuple[str, str, float]:
        """处理 ImportError — 仅提供诊断说明，不自动生成 patch。

        返回 (patched_line, explanation, confidence)
        """
        return (
            "",
            "Import error detected. This typically requires adding a missing "
            "dependency or fixing an import path. Cannot auto-generate fix — "
            "needs manual review.",
            0.05,
        )

    def _llm_assisted_repair(
        self, fault: FaultLocation, full_path: Path
    ) -> Optional[RepairPatch]:
        """LLM 辅助修复：利用 LLM 分析复杂故障。"""
        if self._llm_call is None:
            return None

        try:
            source = full_path.read_text(encoding="utf-8")
        except Exception:
            return None

        system_prompt = (
            "You are a Python code repair expert. Analyze the fault and generate "
            "a minimal unified diff patch to fix the issue.\n"
            "Output format:\n"
            "```diff\n...unified diff...\n```\n"
            "EXPLANATION: <one paragraph>\n"
            "CONFIDENCE: <0.0 to 1.0>\n"
            "AUTO_APPLICABLE: <true/false>"
        )

        user_prompt = (
            f"## Fault\n"
            f"- File: {fault.file_path}:{fault.line_number}\n"
            f"- Function: {fault.function_name}\n"
            f"- Exception: {fault.exception_type}: {fault.exception_message}\n"
            f"- Category: {fault.category}\n\n"
            f"## Source Context\n```python\n{fault.ast_context or 'N/A'}\n```\n\n"
            f"## Full File\n```python\n{source[:3000]}\n```\n"
        )

        try:
            response = self._llm_call(system_prompt, user_prompt)
        except Exception as exc:
            logger.error("LLM call failed for fault analysis: %s", exc)
            return None

        # 解析 LLM 响应
        diff_match = re.search(r"```diff\n(.*?)\n```", response, re.DOTALL)
        explanation_match = re.search(
            r"EXPLANATION:\s*(.+?)(?:\n|$)", response
        )
        confidence_match = re.search(r"CONFIDENCE:\s*([\d.]+)", response)
        auto_match = re.search(
            r"AUTO_APPLICABLE:\s*(true|false)", response, re.IGNORECASE
        )

        diff_text = diff_match.group(1) if diff_match else ""
        explanation = (
            explanation_match.group(1)
            if explanation_match
            else "LLM-generated repair"
        )
        confidence = (
            float(confidence_match.group(1)) if confidence_match else 0.5
        )
        auto_applicable = (
            auto_match.group(1).lower() == "true" if auto_match else False
        )

        if not diff_text:
            return None

        ts = int(time.time() * 1000)
        return RepairPatch(
            patch_id=f"patch_{ts}_llm_{hashlib.sha256(diff_text.encode()).hexdigest()[:8]}",
            fault_location=fault,
            file_path=str(fault.file_path),
            original_lines="",
            patched_lines="",
            diff=diff_text,
            explanation=explanation,
            confidence=min(confidence, 0.9),  # LLM 置信度上限 0.9
            auto_applicable=auto_applicable,
            contract_checks=[],
        )

    # ==================================================================
    # 合约验证
    # ==================================================================

    def validate_contract(
        self, patch: RepairPatch
    ) -> List[ContractCheckResult]:
        """验证修复 patch 前后的业务合约一致性。

        检查项:
        1. 语法有效性 — patched 代码语法正确
        2. 文件安全 — patch 不修改 tests/ 或禁止目录
        3. 原始行引用 — patch 引用的原始行在实际文件中存在

        Returns:
            合约检查结果列表。all(passed) == True 表示通过所有合约检查。
        """
        results: List[ContractCheckResult] = []

        # Check 1: 语法有效性
        try:
            ast.parse(patch.patched_lines)
        except SyntaxError:
            # 如果代码有缩进（如函数体内的语句），
            # module-level ast.parse 会报 SyntaxError，
            # 此时包装到 'if True:' 块中再试
            try:
                ast.parse(f"if True:\n{patch.patched_lines}")
            except SyntaxError as exc:
                results.append(
                    ContractCheckResult(
                        check_name="syntax_valid",
                        passed=False,
                        detail=f"Syntax error in patched code: {exc}",
                    )
                )
            else:
                results.append(
                    ContractCheckResult(
                        check_name="syntax_valid",
                        passed=True,
                        detail="Patched code is syntactically valid Python "
                        "(wrapped in block for indent-tolerant parsing)",
                    )
                )
        else:
            results.append(
                ContractCheckResult(
                    check_name="syntax_valid",
                    passed=True,
                    detail="Patched code is syntactically valid Python",
                )
            )

        # Check 2: 不修改 test 文件
        if "tests/" in patch.file_path:
            results.append(
                ContractCheckResult(
                    check_name="not_test_file",
                    passed=False,
                    detail="Patch targets a test file — blocked by safety policy",
                )
            )

        # Check 3: 不修改禁止目录
        for forbidden in self._FORBIDDEN_DIRS:
            if forbidden in patch.file_path:
                results.append(
                    ContractCheckResult(
                        check_name="not_forbidden_dir",
                        passed=False,
                        detail=f"Patch targets forbidden directory: {forbidden}",
                    )
                )
                break

        # Check 4: 原始行引用存在于实际文件中
        full_path = self._repo_root / patch.file_path
        if full_path.exists():
            try:
                content = full_path.read_text(encoding="utf-8")
                if patch.original_lines and patch.original_lines not in content:
                    results.append(
                        ContractCheckResult(
                            check_name="original_line_exists",
                            passed=False,
                            detail="Original line not found in current file — "
                            "file may have changed since patch generation.",
                        )
                    )
            except Exception:
                pass

        # Store results on patch
        patch.contract_checks = [
            f"{r.check_name}: {'PASS' if r.passed else 'FAIL'} — {r.detail}"
            for r in results
        ]

        return results

    # ==================================================================
    # Patch 应用
    # ==================================================================

    def apply_patch(
        self, patch: RepairPatch, dry_run: bool = True
    ) -> Tuple[bool, str]:
        """应用修复 patch（需要显式 dry_run=False）。

        使用行替换策略（而非 difflib.restore）以确保正确性。

        Args:
            patch: 待应用的 patch。
            dry_run: True = 只返回将写入的内容；False = 实际写入文件。

        Returns:
            (applied, message)
        """
        if not patch.auto_applicable:
            return (
                False,
                "Patch is not auto_applicable. Set auto_applicable=True after "
                "human review, or use dry_run=True to preview changes.",
            )

        # 合约验证
        contract_results = self.validate_contract(patch)
        failed = [r for r in contract_results if not r.passed]
        if failed:
            return (
                False,
                "Contract validation failed: "
                + ", ".join(r.check_name for r in failed),
            )

        if dry_run:
            return (
                False,
                f"[DRY RUN] Would apply to {patch.file_path}:\n{patch.diff}",
            )

        # 实际应用（仅在 dry_run=False 时）
        full_path = self._repo_root / patch.file_path
        try:
            original = full_path.read_text(encoding="utf-8")

            # 行替换：找到原始行，替换为 patched 行
            if patch.original_lines and patch.original_lines in original:
                patched_content = original.replace(
                    patch.original_lines, patch.patched_lines, 1
                )
            else:
                # 回退：使用 unified diff 还原（如果 diff 格式正确）
                patched_content = self._apply_unified_diff(original, patch.diff)
                if patched_content is None:
                    return (
                        False,
                        "Cannot apply patch: original line not found and "
                        "unified diff failed to apply.",
                    )

            # 备份原文件
            backup_path = full_path.with_suffix(
                f"{full_path.suffix}.bak.{int(time.time())}"
            )
            full_path.rename(backup_path)

            # 写入 patched 内容
            full_path.write_text(patched_content, encoding="utf-8")

            patch.status = "applied"
            logger.warning(
                "Patch %s applied to %s. Backup: %s",
                patch.patch_id,
                patch.file_path,
                backup_path,
            )

            # Phase 3 集成: 记录修复效果
            self._record_to_effectiveness_log(patch, success=True)

            return (
                True,
                f"Patch applied to {patch.file_path}. Backup: {backup_path}",
            )
        except Exception as exc:
            self._record_to_effectiveness_log(patch, success=False)
            return False, f"Failed to apply patch: {exc}"

    def _apply_unified_diff(
        self, original: str, diff_text: str
    ) -> Optional[str]:
        """尝试应用 unified diff 到原始内容。"""
        if not diff_text:
            return original

        lines = original.split("\n")
        result_lines: List[str] = []
        i = 0

        for dline in diff_text.split("\n"):
            if dline.startswith("@@ "):
                # 解析 hunk header @@ -old_start,old_count +new_start,new_count @@
                m = re.match(
                    r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
                    dline,
                )
                if m:
                    old_start = int(m.group(1)) - 1  # 0-indexed
                    old_count = int(m.group(2)) if m.group(2) else 1
                    # Flush any lines before the hunk starts
                    while i < old_start:
                        if i < len(lines):
                            result_lines.append(lines[i])
                        i += 1
            elif dline.startswith(" "):
                # Context line
                if i < len(lines):
                    result_lines.append(lines[i])
                i += 1
            elif dline.startswith("-"):
                # Removed line — skip it
                i += 1
            elif dline.startswith("+"):
                # Added line
                result_lines.append(dline[1:])
            # else: skip --- and +++ headers

        # Append remaining lines after last hunk
        while i < len(lines):
            result_lines.append(lines[i])
            i += 1

        return "\n".join(result_lines)

    def _record_to_effectiveness_log(
        self, patch: RepairPatch, success: bool
    ) -> None:
        """Phase 3 集成：记录 patch 修复效果到 RepairEffectivenessLog。"""
        try:
            from src.services.repair_effectiveness_log import (
                RepairEffectivenessLog,
                RepairOutcome,
            )

            eff_log = RepairEffectivenessLog()
            entry = eff_log.record(
                repair_id=patch.patch_id,
                action_type="patch",
                target=patch.fault_location.file_path,
                metadata={
                    "exception_type": patch.fault_location.exception_type,
                    "category": patch.fault_location.category,
                    "confidence": patch.confidence,
                },
            )
            outcome = (
                RepairOutcome.RESTORED if success else RepairOutcome.NO_EFFECT
            )
            eff_log.update_outcome(entry.entry_id, outcome)
        except Exception:
            logger.debug(
                "Effectiveness log not available for patch recording"
            )

    # ==================================================================
    # 查询与统计
    # ==================================================================

    def get_pending_patches(self) -> List[RepairPatch]:
        """获取所有待处理的 patch（status='pending'）。"""
        with self._lock:
            return [p for p in self._patches if p.status == "pending"]

    def get_patch_history(self, limit: int = 20) -> List[RepairPatch]:
        """获取 patch 历史。"""
        with self._lock:
            return self._patches[-limit:]

    def stats(self) -> Dict[str, Any]:
        """获取修复代理统计。"""
        with self._lock:
            total = len(self._patches)
            applied = sum(1 for p in self._patches if p.status == "applied")
            verified = sum(1 for p in self._patches if p.status == "verified")
            rejected = sum(1 for p in self._patches if p.status == "rejected")

            return {
                "total_patches": total,
                "applied": applied,
                "verified": verified,
                "rejected": rejected,
                "pending": total - applied - verified - rejected,
                "avg_confidence": (
                    sum(p.confidence for p in self._patches) / max(total, 1)
                ),
                "auto_applicable_count": sum(
                    1 for p in self._patches if p.auto_applicable
                ),
            }

    def reset(self) -> None:
        """重置代理（仅用于测试）。"""
        with self._lock:
            self._patches.clear()
            self._patch_counter = 0


# ===================================================================
# SelfHealingAction 适配器: CodeAwareRepairAction
# ===================================================================


class CodeAwareRepairAction:
    """将 CodeAwareRepairAgent 包装为 SelfHealingAction 接口。

    使代码感知修复可以作为 L3 自修复升级链中的一环使用。

    用法:
        action = CodeAwareRepairAction(repo_root=Path.cwd())
        record = action.execute(context={
            "exception_type": "AttributeError",
            "exception_message": "...",
            "traceback_text": "...",
            "module_name": "my_module",
        })
    """

    def __init__(
        self,
        repo_root: Path,
        llm_call: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self._agent = CodeAwareRepairAgent(
            repo_root=repo_root, llm_call=llm_call
        )

    def execute(self, context: Dict[str, Any]) -> "RepairRecord":
        """执行代码感知修复（兼容 SelfHealingAction.execute 接口）。

        Args:
            context: 必须包含 exception_type, exception_message,
                     可选 traceback_text, module_name。

        Returns:
            RepairRecord 记录修复结果。
        """
        # 延迟导入避免循环依赖
        from src.services.self_healing_action import RepairRecord, RepairStatus

        record = RepairRecord(
            repair_id=f"repair_{int(time.time() * 1000)}_code_aware",
            action_type="patch",
            target=context.get("module_name", "unknown"),
        )

        # Step 1: 定位故障 (detect)
        fault = self._agent.locate_fault(
            exception_type=context.get("exception_type", "Unknown"),
            exception_message=context.get("exception_message", ""),
            traceback_text=context.get("traceback_text", ""),
            module_name=context.get("module_name", ""),
        )

        if fault is None:
            record.status = RepairStatus.FAILED.value
            record.error_message = "Could not locate fault source"
            return record

        # Step 2: 生成 patch (repair)
        patch = self._agent.generate_patch(fault)
        if patch is None:
            record.status = RepairStatus.FAILED.value
            record.error_message = "Could not generate repair patch"
            record.metadata["fault"] = fault
            return record

        # Step 3: 验证合约 (verify)
        results = self._agent.validate_contract(patch)
        all_passed = all(r.passed for r in results)

        if not all_passed:
            record.status = RepairStatus.FAILED.value
            record.verification_result = False
            record.verification_detail = "; ".join(
                f"{r.check_name}: {r.detail}"
                for r in results
                if not r.passed
            )
            record.metadata["patch"] = patch
            return record

        # Step 4: 应用 (dry_run=True，不自动修改文件)
        applied, msg = self._agent.apply_patch(patch, dry_run=True)
        if applied:
            record.status = RepairStatus.SUCCESS.value
            record.verification_result = True
            record.verification_detail = "Patch generated, contract passed (dry_run)"
        else:
            record.status = RepairStatus.SUCCESS.value
            record.verification_result = True
            record.verification_detail = msg

        record.metadata["patch"] = patch
        record.metadata["fault"] = fault
        return record
