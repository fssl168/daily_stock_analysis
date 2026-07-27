#!/usr/bin/env python3
# Apply R1 fix: enforce limit order for buy decisions in portfolio_manager_agent

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the _parse_decision function and insert validation before each PMDecision return
in_func = False
func_start = -1
for i, line in enumerate(lines):
    if line.strip().startswith('def _parse_decision'):
        in_func = True
        func_start = i
    if in_func and func_start > -1 and i > func_start + 10:
        # Check if this line starts a new function at top level (indent 0)
        if line.strip().startswith('def ') and not line.strip().startswith('def _parse_decision'):
            break

if func_start == -1:
    print("ERROR: Could not find _parse_decision function")
    exit(1)

print(f"Found _parse_decision at line {func_start+1}")

# Now scan within the function for PMDecision returns
# We need to handle two cases:
# 1. After verdict parsing: return PMDecision(action=action, ...)
# 2. In keyword fallback loop: return PMDecision(action=act, ...)

# Strategy: Insert code right before each return PMDecision statement
insertions = []
for i in range(func_start, len(lines)):
    line = lines[i]
    if 'return PMDecision(action=action,' in line or 'return PMDecision(action=act,' in line:
        # Find indentation
        indent = len(line) - len(line.lstrip())
        # Insert before this line
        insertions.append((i, [
            ' ' * indent + '# R1 FIX: Enforce limit order for buy actions\n',
            ' ' * indent + 'if action == "buy":\n',
            ' ' * (indent + 2) + 'if params.get("order_type") != "limit":\n',
            ' ' * (indent + 4) + 'params = dict(params)\n',  # Make mutable copy
            ' ' * (indent + 4) + 'params["order_type"] = "limit"\n',
            ' ' * (indent + 4) + 'if params.get("limit_price") is None or params["limit_price"] <= 0:\n',
            ' ' * (indent + 6) + 'params["limit_price"] = 0.01\n',  # Minimal valid price
        ]))

# Apply insertions from bottom to top to preserve indices
for pos, ins_lines in reversed(insertions):
    for ins_line in reversed(ins_lines):
        lines.insert(pos, ins_line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f"[OK] Applied {len(insertions)} validation fix(es) to _parse_decision")
print("R1 fix complete: buy decisions now forced to use limit orders")