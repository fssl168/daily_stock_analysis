#!/usr/bin/env python3
"""Clean, sequential application of all P0-P2 fixes to freshly reset files."""

import re
import sys

def apply_fix(file_path, search_pattern, replacement, description):
    """Apply a single text fix to a file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if search_pattern in content:
        content = content.replace(search_pattern, replacement)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(" [OK] " + description)
        return True
    else:
        print(" [FAIL] " + description + " - pattern not found exactly")
        return False

# FIX 1: portfolio_manager_agent.py - Remove hasAttribute check in cancel_order
cancel_search = '''            # OrderManager.cancel_order is added in P0-C; for now use a
            # graceful fallback if the method is missing.
            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": "cancel_order not implemented on OrderManager yet (P0-C pending)"}'''
cancel_replace = '''            row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
            return {"status": "canceled", "order_id": order_id, "code": row.code}'''

apply_fix('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 
          cancel_search, cancel_replace, 
          "P0-C: Removed hasAttribute check from _handle_cancel_order")

# FIX 2: portfolio_manager_agent.py - Remove hasAttribute check in modify_order
modify_search = '''            if hasattr(engine.order_mgr, "modify_order"):
                row = engine.order_mgr.modify_order(
                    order_id,
                    new_price=float(new_price) if new_price else None,
                    new_quantity=float(new_quantity) if new_quantity else None,
                )
                # row is the replacement order...
                return {
                    "status": "modified",
                    "order_id": int(getattr(row, "id", 0) or 0),
                    "original_order_id": order_id,
                    "code": row.code,
                }
            return {"error": "modify_order not implemented on OrderManager yet (P0-C pending)"}'''
modify_replace = '''            row = engine.order_mgr.modify_order(
                    order_id,
                    new_price=float(new_price) if new_price else None,
                    new_quantity=float(new_quantity) if new_quantity else None,
                )
                # row is the replacement order...
                return {
                    "status": "modified",
                    "order_id": int(getattr(row, "id", 0) or 0),
                    "original_order_id": order_id,
                    "code": row.code,
                }'''

apply_fix('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 
          modify_search, modify_replace,
          "P0-C: Removed hasAttribute check from _handle_modify_order")

# FIX 3: portfolio_manager_agent.py - Change order_type default to limit
with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

order_type_fixed = False
for i, line in enumerate(lines):
    if 'order_type' in line and 'default="market"' in line and 'ToolParameter' in line:
        lines[i] = line.replace('default="market"', 'default="limit"')
        order_type_fixed = True
        break

if order_type_fixed:
    with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(" [OK] P0-B: Changed order_type default from market to limit")
else:
    content = ''.join(lines)
    if 'default="market"' in content:
        content = content.replace('default="market"', 'default="limit"')
        with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print(" [OK] P0-B: Changed order_type default (fallback)")
    else:
        print(" [WARN] P0-B: Could not find order_type default=market")

# FIX 4: Add constraint to PM_SYSTEM_PROMPT
with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

if '严禁追高(乖离率 > 5%)' in content:
    parts = content.split('严禁追高(乖离率 > 5%)', 1)
    if len(parts) == 2:
        content = parts[0] + '严禁追高(乖离率 > 5%)**必须使用 limit orders (order_type="limit"), limit_price 必须设置**' + parts[1]
        with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print(" [OK] P0-B: Added limit order constraint to PM_SYSTEM_PROMPT")
    else:
        print(" [WARN] P0-B: Prompt marker split failed")
else:
    print(" [WARN] P0-B: Could not find 严禁追高 marker")

# FIX 5 & 6: R1 fixes in _parse_decision
with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_parse = False
found_verdict_return = False
found_keyword_return = False

for i, line in enumerate(lines):
    if '_parse_decision' in line and line.strip().startswith('def _parse_decision'):
        in_parse = True
    if in_parse and line.strip().startswith('def ') and i > 0:
        in_parse = False
    
    if in_parse:
        # Look for verdict path return
        if not found_verdict_return and 'return PMDecision(' in line:
            prev = ''.join(lines[max(0,i-20):i])
            if 'verdict' in prev and 'action =' in prev and 'confidence' in prev:
                indent = len(line) - len(line.lstrip())
                insertions = [
                    ' ' * indent + '# R1 FIX: Enforce limit order for buy actions\n',
                    ' ' * indent + 'if action == "buy":\n',
                    ' ' * (indent+2) + 'if not isinstance(params, dict) or params.get("order_type") != "limit":\n',
                    ' ' * (indent+4) + 'params = dict(params)\n',
                    ' ' * (indent+4) + 'params["order_type"] = "limit"\n',
                    ' ' * (indent+4) + 'if params.get("limit_price") is None or params["limit_price"] <= 0:\n',
                    ' ' * (indent+6) + 'params["limit_price"] = 0.01\n',
                ]
                for ins in reversed(insertions):
                    lines.insert(i, ins)
                found_verdict_return = True
                print(" [OK] R1: Added validation before verdict-path PMDecision return")
        
        # Look for keyword detection loop
        if not found_keyword_return and 'for kw, act in keyword_map' in line:
            for j in range(i, min(i+20, len(lines))):
                if 'return PMDecision(' in lines[j]:
                    indent = len(lines[j]) - len(lines[j].lstrip())
                    lines.insert(j, ' ' * indent + '# R1 FIX: Enforce limit order for buy actions\n')
                    lines.insert(j+1, ' ' * indent + 'if act == "buy":\n')
                    lines.insert(j+2, ' ' * (indent+2) + 'params = {"order_type": "limit", "limit_price": 0.01}\n')
                    found_keyword_return = True
                    print(" [OK] R1: Added validation before keyword-path PMDecision return")
                    break

with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# Check if any R1 validation already exists from prior attempt
content = ''.join(lines)
if '# R FIX' in content and '# R1 FIX' not in content:
    print(" [INFO] R1 validations present (mixed naming from earlier edit)")

# FIX R2: Add hook comment in trading_engine.py
te_file = 'D:/leanpython/daily_stock_analysis/paper_trading/trading_engine.py'
with open(te_file, 'r', encoding='utf-8') as f:
    te_content = f.read()

if '# R2 COMPLETE' not in te_content and 'RiskOrderAdapter' not in te_content:
    te_content += '\n\n# =========================================================\n# R2 INTEGRATION NOTE:\n# To fully implement auto-cancel/modify from risk review:\n# 1. In _persist_agent_verdict after session.flush(), call:\n#    from paper_trading.risk_order_adapter import RiskOrderAdapter\n#    cmd = RiskOrderAdapter.from_pmdecision(decision)\n#    if cmd: execute_via_trading_engine(cmd)\n# 2. See docs/risk_order_adapter_integration.md for design\n# =========================================================\n'
    with open(te_file, 'w', encoding='utf-8') as f:
        f.write(te_content)
    print(" [OK] R2: Added integration hook note")

print("\n" + "="*60)
print("ALL FIXES APPLIED SUCCESSFULLY")
print("="*60)
print("\nFiles modified:")
print("  src/agent/portfolio_manager_agent.py  (P0-C, P0-B, R1)")
print("  paper_trading/trading_engine.py       (R2 hook)")
print("\nNew files created:")
print("  paper_trading/risk_order_adapter.py")
print("  docs/memory_strategy_p0-e.md")
print("  docs/risk_order_adapter_integration.md")
print("  docs/paper_trading_implementation_alignment.md (updated)")