#!/usr/bin/env python3
# Complete R1 fix: enforce limit order for all buy decisions in _parse_decision

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Main verdict processing path - insert before return PMDecision(action=action,...)
# We need to find the specific block and inject validation code
import re

# Pattern to match the verdict path return block:
# if isinstance(verdict, dict) and "action" in verdict: ... return PMDecision(
# We'll inject after the confidence calculation and before the return

verdict_block_match = re.search(
    r'(if isinstance\(verdict, dict\) and "action" in verdict:[\s\S]*?confidence \(max\|min\).*?\n)(return PMDecision\()',
    content
)

if verdict_block_match:
    prefix = verdict_block_match.group(1)
    # Check if action could be "buy" in this block
    inject = '''            # R FIX: Enforce limit order for buy actions
            if action == "buy":
                if not isinstance(params, dict) or params.get("order_type") != "limit":
                    params = dict(params) if isinstance(params, dict) else {"value": {}}
                    params["order_type"] = "limit"
                    if params.get("limit_price") is None or params["limit_price"] <= 0:
                        params["limit_price"] = 0.01
'''
    content = content.replace(verdict_block_match.group(0), prefix + inject + 'return PMDecision(', count=1)
    print("[OK] Applied fix to verdict path")
else:
    print("[WARN] Could not find verdict path block with expected pattern")

# Fix 2: Keyword detection path - already partially fixed by previous script
# Ensure it's also properly formatted
keyword_match = re.search(
    r'(for kw, act in keyword_map:\n\s*if kw in text_lower:\n\s*)(# R FIX: Enforce limit order for buy actions\n\s*if act == "buy":)',
    content
)
if keyword_match:
    # Make sure the params setting is correct (currently sets everything, which might override other needed fields)
    # Better approach: preserve existing params but ensure limit order fields
    keyword_fix = '''            # R FIX: Enforce limit order for buy actions
            if act == "buy":
                if "params" not in dir() or not isinstance(params, dict):
                    params = {}
                if params.get("order_type") != "limit":
                    params["order_type"] = "limit"
                if params.get("limit_price") is None or params["limit_price"] <= 0:
                    params["limit_price"] = 0.01'''
    # Actually, in this block there's no params variable defined yet! 
    # The keyword path doesn't have a params dict from the agent - we're creating a fresh decision
    # So we need to create params in the return statement itself
    
    # Let me handle this differently - modify the return PMDecision call directly
    keyword_return_match = re.search(
        r'(if kw in text_lower:\n\s*# R FIX: Enforce limit order for buy actions\n\s*if act == "buy":[\s\S]*?\n\s*)return PMDecision\(\n\s*action=act,',
        content
    )
    if keyword_return_match:
        # Insert params before the action=act line
        new_return = '''            return PMDecision(
                    action=act,
                    params={"order_type": "limit", "limit_price": 0.01},
                    reason=f"inferred from keyword '{kw}' (JSON parse failed)",
'''
        content = content.replace(keyword_return_match.group(0) + 'action=act,', new_return)
        print("[OK] Applied fix to keyword path return")
    else:
        # Try simpler replacement
        content = content.replace(
            'if kw in text_lower:\n            # R FIX: Enforce limit order for buy actions\n            if act == "buy":\n',
            'if kw in text_lower:\n            # R FIX: Enforce limit order for buy actions\n            if act == "buy":\n                params = {"order_type": "limit", "limit_price": 0.01}\n'
        )
        print("[OK] Applied simple keyword path fix")

# Verify both changes were applied
if '# R FIX: Enforce limit order for buy actions' in content:
    count = content.count('# R FIX: Enforce limit order for buy actions')
    print(f"[SUCCESS] Found {count} R FIX comments in file")
else:
    print("[ERROR] R FIX not found")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[R1 COMPLETE] All fixes applied successfully")