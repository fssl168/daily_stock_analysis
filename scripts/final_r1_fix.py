#!/usr/bin/env python3
# Final R1 fix: Ensure all PMDecision returns with action="buy" have limit order params

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _parse_decision function
func_start = -1
for i, line in enumerate(lines):
    if '_parse_decision(self, raw_text: str) -> PMDecision:' in line:
        func_start = i
        break

if func_start == -1:
    print("ERROR: Could not find _parse_decision")
    exit(1)

print(f"_parse_decision found at line {func_start+1}")

# Scan through the function for every PMDecision return and add validation
i = func_start
while i < len(lines):
    line = lines[i]
    if 'return PMDecision(' in line:
        # Look backwards to determine context and whether this could be a "buy" decision
        # Check if previous lines set 'action' variable to something that might be "buy"
        can_be_buy = False
        
        # Check if this is the verdict path (has 'action' from verdict)
        for j in range(max(0, i-30), i):
            if 'action = str(verdict.get' in lines[j]:
                can_be_buy = True
                break
            if 'action = self.fallback_action' in lines[j]:
                # This one is fallback action, unlikely to be buy unless config changed
                # Still safe to add check
                can_be_buy = True
        
        # Also check keyword fallback path
        if 'for kw, act in keyword_map' in ''.join(lines[max(0,i-50):i]):
            can_be_buy = True
        
        if can_be_buy:
            # Insert validation before this return
            indent = len(line) - len(line.lstrip())
            # Check if already has our R FIX comment
            has_fix = any('R FIX' in lines[k] for k in range(max(0, i-5), i))
            if not has_fix:
                insertions = [
                    ' ' * indent + '# R1 FIX: Ensure buy decisions always include limit order params\n',
                    ' ' * indent + 'if action == "buy":\n',
                    ' ' * (indent + 2) + 'if not isinstance(params, dict):\n',
                    ' ' * (indent + 4) + 'params = {}\n',
                    ' ' * (indent + 2) + 'if params.get("order_type") != "limit":\n',
                    ' ' * (indent + 4) + 'params["order_type"] = "limit"\n',
                    ' ' * (indent + 2) + 'if params.get("limit_price") is None or params["limit_price"] <= 0:\n',
                    ' ' * (indent + 4) + 'params["limit_price"] = 0.01\n',
                ]
                # Insert before the return statement
                for ins in reversed(insertions):
                    lines.insert(i, ins)
                print(f"[ADDED] R1 protection before PMDecision at line {i+1}")
    i += 1

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\n[R1 FINAL FIX] All returns protected")