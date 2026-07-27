#!/usr/bin/env python3
# Fix R: enforce limit order for buy decisions in _parse_decision

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line numbers of the three PMDecision returns in _parse_decision
# We'll insert validation before the ones where action could be "buy"

def find_returns_in_func(start_line, func_name):
    """Find all return PMDecision lines within a function."""
    results = []
    in_func = False
    for i in range(len(lines)):
        if lines[i].strip().startswith(f'def {func_name}'):
            in_func = True
        if in_func and i > start_line:
            # Check if we exited function (next def at same indent)
            if lines[i].strip().startswith('def ') and i > start_line + 10:
                break
            if 'return PMDecision' in lines[i]:
                results.append(i)
    return results

# Start from where _parse_definition begins
parse_start = None
for i, line in enumerate(lines):
    if '_parse_decision' in line and line.strip().startswith('def _parse_decision'):
        parse_start = i
        break

if parse_start is None:
    print("ERROR: Could not find _parse_decision")
    exit(1)

pm_decision_returns = find_returns_in_func(parse_start, '_parse_decision')
print(f"Found {len(pm_decision_returns)} PMDecision returns in _parse_decision at lines: {[p+1 for p in pm_decision_returns]}")

# For each return, check if we should add validation
# We need to inject code before the return statement that checks if action == "buy"
# and modifies params accordingly

for ret_idx in pm_decision_returns:
    line = lines[ret_idx]
    # Determine which block this return is in based on context before it
    # Look backward for the surrounding logic
    
    # Simple approach: just insert validation before this return for ALL returns
    # That's safe but might be overkill. Let's be more selective.
    
    # Look at previous lines to determine the context
    context_lines = lines[max(0, ret_idx-5):ret_idx]
    context_text = '\n'.join(context_lines)
    
    if 'verdict' in context_text and 'action = str(verdict.get' in context_text:
        # This is the main verdict processing return - action could be anything including buy
        print(f"  -> Will add validation before return at line {ret_idx+1} (verdict path)")
        indent = len(line) - len(line.lstrip())
        # Insert validation code before this return
        insert_pos = ret_idx
        new_code = [
            ' ' * indent + '# R FIX: Enforce limit order for buy actions\n',
            ' ' * indent + 'if action == "buy":\n',
            ' ' * (indent + 2) + 'if isinstance(params, dict) and params.get("order_type") != "limit":\n',
            ' ' * (indent + 4) + 'params = dict(params)\n',  # Make sure it\'s mutable
            ' ' * (indent + 4) + 'params["order_type"] = "limit"\n',
            ' ' * (indent + 4) + 'if params.get("limit_price") is None or params["limit_price"] <= 0:\n',
            ' ' * (indent + 6) + 'params["limit_price"] = 0.01\n',
        ]
        for code_line in reversed(new_code):
            lines.insert(insert_pos, code_line)
        
    elif 'verdict' in context_text and 'action = str(verdict.get' in context_text:
        # This is the main verdict processing return - action could be anything including buy
        print(f"  -> Will add validation before return at line {ret_idx+1} (verdict path)")
        indent = len(line) - len(line.lstrip())
        insert_pos = ret_idx
        new_code = [
            ' ' * indent + '# R FIX: Enforce limit order for buy actions\n',
            ' ' * indent + 'if action == "buy":\n',
            ' ' * (indent + 2) + 'if isinstance(params, dict) and ("order_type" not in params or params.get("order_type") != "limit"):\n',
            ' ' * (indent + 4) + 'params = dict(params)\n',
            ' ' * (indent + 4) + 'params["order_type"] = "limit"\n',
            ' ' * (indent + 4) + 'if params.get("limit_price") is None or params["limit_price"] <= 0:\n',
            ' ' * (indent + 6) + 'params["limit_price"] = 0.01\n',
        ]
        for code_line in reversed(new_code):
            lines.insert(insert_pos, code_line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\n[R FIX] Applied validation to _parse_decision returns")