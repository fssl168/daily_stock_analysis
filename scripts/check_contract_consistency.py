#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约一致性检查脚本（防漂移）。

检查四层契约：

1. **配置三处同步**（失败级）：`config.py` 的每个 `paper_trading_*` 字段是否在
   `_load_from_env` 有赋值（防死配置），赋值读取的 env key 是否出现在 `.env.example`
   （防用户配置无效）。

2. **核心 dataclass to_dict 覆盖**（失败级）：核心数据载体类的所有字段必须进入
   `to_dict`（防序列化契约漂移，如 `TradeResult` 曾漏 `trade_id`）。

3. **API schema 健康**（失败级）：`api/v1/schemas/`、`src/schemas/` 下 Pydantic
   `BaseModel` 字段必须有类型注解。

4. **自动发现 dataclass**（默认汇总警告 / `--strict` 明细并失败）：扫描
   `paper_trading/`、`src/agent/` 下其余 `@dataclass` 类，检查 to_dict 覆盖。
   部分序列化类（策略配置/诊断类）可能合法省略，故默认不作为 CI 门禁。

用法：
    python scripts/check_contract_consistency.py
    python scripts/check_contract_consistency.py --strict
    # 退出码 0 = 通过；1 = 存在失败级漂移（CI 门禁）

只读静态代码，不联网、不碰数据库。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="ignore")


# 核心数据载体类：to_dict 必须全覆盖（遗漏 = 序列化契约漂移）。
CORE_DATACLASSES: list[tuple[str, str]] = [
    ("paper_trading/trading_engine.py", "TradeResult"),
    ("paper_trading/strategies/engine/rule_engine.py", "Signal"),
    ("paper_trading/account.py", "AccountSnapshot"),
    ("paper_trading/performance.py", "PerformanceMetrics"),
    ("paper_trading/battle_plan.py", "BattlePlan"),
    ("paper_trading/battle_plan.py", "HoldingPlan"),
    ("paper_trading/battle_plan.py", "CandidatePlan"),
    ("paper_trading/sltp_calculator.py", "SLTPResult"),
    ("paper_trading/risk.py", "RiskDecision"),
    ("src/agent/portfolio_manager_agent.py", "PMDecision"),
]

DISCOVER_DIRS = ["paper_trading", "src/agent"]
API_SCHEMA_DIRS = ["api/v1/schemas", "src/schemas"]


# ---------------------------------------------------------------------------
# 通用解析
# ---------------------------------------------------------------------------

def _class_block(src: str, cls: str) -> str | None:
    """返回类体（含字段与方法，到下一个顶层类/装饰器或文件尾）。"""
    m = re.search(
        r"^class " + cls + r"\b[^\n]*:\s*\n(.*?)(?=\n(?:class |def |@)|\Z)",
        src, re.S | re.M,
    )
    return m.group(1) if m else None


def _fields_in_block(block: str) -> list[str]:
    """类体恰好 4 空格缩进的字段名（排除方法体内更深缩进）。"""
    return re.findall(r"^ {4}([a-z_]+):", block, re.M)


def _optional_none_fields(block: str) -> set[str]:
    """`Optional[...] = None` 的字段（内部辅助字段，to_dict 可合法省略）。"""
    return set(
        re.findall(r"^ {4}([a-z_]+):\s*Optional\[[^\]]+\]\s*=\s*None", block, re.M)
    )


def _to_dict_keys(block: str) -> set[str]:
    """to_dict 返回 dict 的 key 集合（`"key":` 模式）。"""
    td = re.search(
        r"def to_dict.*?\n(.*?)(?=\n    def |\nclass |\n@|\Z)", block, re.S
    )
    if not td:
        return set()
    return set(re.findall(r"\"([a-z_]+)\":", td.group(1)))


# ---------------------------------------------------------------------------
# 1. 配置三处同步
# ---------------------------------------------------------------------------

def check_config_contract() -> list[str]:
    findings: list[str] = []
    cfg_src = _read("src/config.py")
    env_ex = _read(".env.example")

    dataclass_fields = set(
        re.findall(r"^    (paper_trading_[a-z0-9_]+):", cfg_src, re.M)
    )

    load_start = cfg_src.find("def _load_from_env")
    if load_start < 0:
        findings.append("config.py 未找到 _load_from_env")
        return findings
    body = cfg_src[load_start:]
    next_toplevel = re.search(r"\n(?=def |class |@)", body)
    if next_toplevel:
        body = body[: next_toplevel.start()]

    assigned = set(re.findall(r"^            (paper_trading_[a-z0-9_]+)=", body, re.M))

    dead = sorted(dataclass_fields - assigned)
    if dead:
        findings.append("死配置（字段有定义但 _load_from_env 未赋值，永远用默认值）:")
        for f in dead:
            findings.append(f"  - {f}")

    for field in sorted(assigned):
        m = re.search(r"^            " + re.escape(field) + r"=.*", body, re.M)
        if not m:
            continue
        seg = body[m.end(): m.end() + 300]
        keys = re.findall(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)", seg)
        keys += re.findall(r"_get_env_file_value\(\s*['\"]([A-Z0-9_]+)", seg)
        if keys and not any(k in env_ex for k in keys):
            findings.append(
                f"env key 不在 .env.example（用户配置无效）: {field} <- {keys}"
            )

    return findings


# ---------------------------------------------------------------------------
# 2. 核心 dataclass to_dict 覆盖
# ---------------------------------------------------------------------------

def check_core_dataclass_to_dict() -> list[str]:
    findings: list[str] = []
    for rel, cls in CORE_DATACLASSES:
        src = _read(rel)
        block = _class_block(src, cls)
        if block is None:
            findings.append(f"{rel}: 未找到 class {cls}（或块边界异常）")
            continue
        fields = _fields_in_block(block)
        if not fields:
            # 无字段（可能被误判为无字段）——标记待人工确认，不硬判失败。
            continue
        in_td = _to_dict_keys(block)
        ok_omitted = _optional_none_fields(block)
        missing = [f for f in fields if f not in in_td and f not in ok_omitted]
        if missing:
            findings.append(f"{rel}:{cls} to_dict 缺失字段 {missing}")
    return findings


# ---------------------------------------------------------------------------
# 3. API schema 健康
# ---------------------------------------------------------------------------

def check_api_schema_health() -> list[str]:
    findings: list[str] = []
    for rel in API_SCHEMA_DIRS:
        root = ROOT / rel
        if not root.exists():
            continue
        for f in sorted(root.glob("*.py")):
            src = f.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"^class (\w+)\(BaseModel\):", src, re.M):
                cls = m.group(1)
                block = _class_block(src, cls)
                if block is None:
                    continue
                # 裸字段：`name:` 后无类型。
                for fm in re.finditer(
                    r"^\s{4}([a-z_]+):\s*(?:#.*)?$", block, re.M
                ):
                    findings.append(
                        f"{rel}/{f.name}:{cls} 字段 {fm.group(1)} 缺少类型注解"
                    )
    return findings


# ---------------------------------------------------------------------------
# 5. serializer → Pydantic schema 契约 (P0-3)
# ---------------------------------------------------------------------------

# 已知 serializer 函数 ↔ Pydantic model 对: 序列化 dict 的 key 集合必须
# == schema 字段集合 (多余 = 输出超契约; 缺失 = 响应缺字段)。
SERIALIZER_SCHEMA_PAIRS: list[tuple[str, str, str, str]] = [
    ("api/v1/endpoints/paper_trading.py", "_row_to_decision_dict",
     "api/v1/schemas/paper_trading.py", "PMDecisionItem"),
    ("api/v1/endpoints/paper_trading.py", "_row_to_reflection_dict",
     "api/v1/schemas/paper_trading.py", "ReflectionNoteItem"),
    ("api/v1/endpoints/paper_trading.py", "_row_to_signal_dict",
     "api/v1/schemas/paper_trading.py", "SignalItem"),
]


def _serializer_dict_keys(src: str, func: str) -> set[str]:
    """提取 serializer 函数 return dict 的 key 集合 (静态近似)."""
    m = re.search(
        rf"def {func}\b[^\n]*:\n(.*?)(?=\n\s*(?:def |@)|\Z)", src, re.S
    )
    if not m:
        return set()
    body = m.group(1)
    return set(re.findall(r'"(\w+)":', body))


def check_serializer_schema_contract() -> list[str]:
    findings: list[str] = []
    for ep_rel, func, schema_rel, cls in SERIALIZER_SCHEMA_PAIRS:
        ep_src = _read(ep_rel)
        schema_src = _read(schema_rel)
        keys = _serializer_dict_keys(ep_src, func)
        if not keys:
            findings.append(f"契约: 未找到 {ep_rel}:{func} 的序列化 dict")
            continue
        block = _class_block(schema_src, cls)
        if block is None:
            findings.append(f"契约: 未找到 schema {schema_rel}:{cls}")
            continue
        schema_fields = set(re.findall(r"^\s{4}([a-z_]+):", block, re.M))
        extra = keys - schema_fields
        missing = schema_fields - keys
        if extra:
            findings.append(
                f"契约: {func} 输出多余字段(不在 {cls}): {sorted(extra)}"
            )
        if missing:
            findings.append(
                f"契约: {func} 缺失 schema 字段 {cls}: {sorted(missing)}"
            )
    return findings


# ---------------------------------------------------------------------------
# 6. prompt 声明字段 ⊆ 解析器读取字段 (P0-3)
# ---------------------------------------------------------------------------

# prompt 的 JSON 输出模板声明的字段名 vs 解析器 get() 的字段名。
PROMPT_PARSER_PAIRS: list[tuple[str, str, str, str]] = [
    # (prompt 文件, prompt 变量, 解析器文件, 解析器函数)
    ("src/agent/portfolio_manager_agent.py", "PM_SYSTEM_PROMPT",
     "src/agent/portfolio_manager_agent.py", "_parse_decision"),
    ("paper_trading/reflection.py", "REFLECTION_SYSTEM_PROMPT",
     "paper_trading/reflection.py", "_parse_reflection_json"),
]


def check_prompt_parser_contract() -> list[str]:
    findings: list[str] = []
    for prompt_rel, prompt_var, parser_rel, parser_fn in PROMPT_PARSER_PAIRS:
        src = _read(prompt_rel)
        pm = re.search(rf'{prompt_var}\s*=\s*"""(.*?)"""', src, re.S)
        if not pm:
            findings.append(f"契约: 未找到 prompt 变量 {prompt_rel}:{prompt_var}")
            continue
        prompt_text = pm.group(1)
        # prompt 模板声明的字段: JSON 示例里的 "field": 模式
        declared = set(re.findall(r'"([a-z_]+)":', prompt_text))
        fn = re.search(
            rf"def {parser_fn}\(.*?\):\n(.*?)(?:\n\ndef |\n@|\Z)", src, re.S
        )
        if not fn:
            findings.append(f"契约: 未找到解析器 {parser_rel}:{parser_fn}")
            continue
        parser_body = fn.group(1)
        # 解析器读取的字段: .get("field" 模式 (可能带默认值参数)
        read = set(re.findall(r'\.get\("([a-z_]+)"', parser_body))
        # 忽略纯数据键 (code/name 等由上层传入)
        ignored = {"code", "name"}
        missing = (declared - read) - ignored
        if missing:
            findings.append(
                f"契约: {prompt_var} 声明字段解析器未读取: {sorted(missing)}"
            )
    return findings


# ---------------------------------------------------------------------------
# 4. 自动发现 dataclass
# ---------------------------------------------------------------------------

def check_discovered_dataclass_to_dict(strict: bool) -> list[str]:
    """扫描额外 @dataclass 类，检查 to_dict 覆盖。

    默认返回汇总（警告级，不参与退出码）；--strict 输出明细并计失败。
    仅检查有 `to_dict` 的类（无 to_dict 视为用其他序列化，跳过）。
    """
    findings: list[str] = []
    core_keys = {f"{rel}:{cls}" for rel, cls in CORE_DATACLASSES}
    total_checked = 0
    for rel in DISCOVER_DIRS:
        root = ROOT / rel
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in str(f):
                continue
            src = f.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"^@dataclass[^\n]*\nclass (\w+):", src, re.M):
                cls = m.group(1)
                rel_path = f.relative_to(ROOT)
                key = f"{rel_path}:{cls}"
                if key in core_keys:
                    continue
                block = _class_block(src, cls)
                if block is None:
                    continue
                if "def to_dict" not in block:
                    continue  # 无 to_dict，用其他序列化，跳过
                fields = _fields_in_block(block)
                if len(fields) < 3:
                    continue
                total_checked += 1
                in_td = _to_dict_keys(block)
                ok_omitted = _optional_none_fields(block)
                missing = [f for f in fields if f not in in_td and f not in ok_omitted]
                if missing:
                    line = f"{key} to_dict 未覆盖 {missing}"
                    if strict:
                        findings.append(f"[strict] {line}")
    if not strict:
        findings.append(f"自动发现: 额外检查 {total_checked} 个 dataclass（--strict 查看明细）")
    return findings


# ---------------------------------------------------------------------------

def main() -> int:
    strict = "--strict" in sys.argv
    failures: list[str] = []
    notes: list[str] = []

    failures += check_config_contract()
    failures += check_core_dataclass_to_dict()
    failures += check_api_schema_health()
    failures += check_serializer_schema_contract()  # P0-3
    failures += check_prompt_parser_contract()      # P0-3
    notes += check_discovered_dataclass_to_dict(strict)

    if not failures:
        print("✅ 契约一致性检查通过")
        for n in notes:
            print("  ℹ " + n)
        return 0
    print("❌ 发现契约漂移:")
    for line in failures:
        print("  " + line)
    for n in notes:
        print("  ℹ " + n)
    return 1


if __name__ == "__main__":
    sys.exit(main())
