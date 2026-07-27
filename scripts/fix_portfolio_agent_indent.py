#!/usr/bin/env python3
# Fix indentation error in portfolio_manager_agent.py line 911

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# The issue is at line 911 (return { inside _handle_modify_order)
# Looking at the structure from earlier analysis:
# Line 902: try: (indent=8)
# Line 903: row = engine.order_mgr.modify_order( (indent=12)
# Lines 904-906: parameters with higher indent
# Line 907: ) closing paren (should match 903's indent level ideally)
# Line 911: return { has same indent as 907 but should be at try block indent

# The problem likely stems from inserted R1 fix code messing up the indentation.
# Let's find _handle_modify_order and rebuild its body correctly.

def find_function(lines, func_name):
    """Find start and end index of a function."""
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(f'def {func_name}'):
            start = i
            break
    if start is None:
        return None, None
    
    # Find end by looking for next def/class at top level (indent 0 or 4 depending on context)
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped and not stripped.startswith(' ') and not stripped.startswith('\t'):
            if end > start + 5:  # must have some body
                break
        end += 1
    return start, end

start, end = find_function(lines, '_handle_modify_order')
if start is None:
    print("ERROR: Could not find _handle_modify_order")
    exit(1)

print(f"_handle_modify_layer found at lines {start+1}-{end}")

# Rebuild the function body to correct indentation
# We know the expected structure from original code:
expected_body = '''    def _handle_modify_order(**kwargs) -> dict:
        order_id = int(kwargs.get("order_id") or 0)
        new_price = kwargs.get("new_price")
        new_quantity = kwargs.get("new_quantity")
        if order_id <= 0:
            return {"error": "order_id is required"}
        try:
            row = engine.order_mgr.modify_order(
                order_id,
                new_price=float(new_price) if new_price else None,
                new_quantity=float(new_quantity) if new_quantity else None,
            )
            # row is the replacement order (new id assigned by modify_order).
            # Return the NEW order_id so callers can track the replacement,
            # and include original_order_id for audit linkage.
            return {
                "status": "modified",
                "order_id": int(getattr(row, "id", 0) or 0),
                "original_order_id": order_id,
                "code": row.code,
            }
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}'''

# Replace lines[start:end] with corrected version
# But we need to preserve any preceding modifications (like R1 fixes)
# So instead, let's fix just the indentation around the problematic area

# Find the exact problematic region
for i in range(start, min(end, len(lines))):
    if 'return {' in lines[i] and 'status' in lines[i+1]:
        # This is the return statement that needs fixing
        # It should be indented at the same level as the try: (which is 8 spaces more than def)
        # Currently it seems to have extra indent
        
        # Check the indent level of this line
        current_indent = len(lines[i]) - len(lines[i].lstrip())
        # The expected indent for 'return {' inside try block should be 12 (same as 'row = ...')
        expected_try_indent = 12  # 8 (from def) + 4 (inside try)
        
        if current_indent != expected_try_indent:
            print(f"Fixing line {i+1}: indent {current_indent} -> {expected_try_indent}")
            # Recreate line with proper indent
            old_stripped = lines[i].lstrip()
            lines[i] = ' ' * expected_try_indent + old_stripped
        break

# Also ensure the lines following maintain consistent indentation
for i in range(start, min(end, len(lines))):
    line = lines[i]
    if line.strip().startswith(' "') or ('"' in line and line.strip().startswith('"')):
        # These are dict entries - they should be indented further than return
        pass  # leave as is, they looked correct

# Write back
with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Applied indentation fix.")
print("\nNow verifying all needed modifications are still present...")

# Quick sanity checks
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

checks = [
    ("hasattr(cancel_order)", 'if hasattr(engine.order_mgr, "cancel_order"):' in content),
    ("hasattr(modify_order)", 'if hasattr(engine.order_mgr, "modify_order"):' in content),
    ("order_type limit", 'default="limit"' in content and 'order_type' in content),
    ("R1 fix comments", '# R FIX' in content or '# R1 FIX' in content),
]

print("\nPost-fix verification:")
for name, passed in checks:
    status = "PASS" if passed else "MISSING"
    print(f"  [{status}] {name}")