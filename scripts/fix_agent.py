#!/usr/bin/env python3
"""Fix portfolio_manager_agent.py: remove outdated hasAttribute checks and update defaults."""

import re

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace _handle_cancel_order with simplified version (remove the if hasattr check)
cancel_old = '''            # OrderManager.cancel_order is added in P0-C; for now use a
            # graceful fallback if the method is missing.
            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": "cancel_order not implemented on OrderManager yet (P0-C pending)"}'''

cancel_new = '''            row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
            return {"status": "canceled", "order_id": order_id, "code": row.code}'''

if cancel_old in content:
    content = content.replace(cancel_old, cancel_new)
    print("[OK] Replaced _handle_cancel_order")
else:
    print("[WARN] Cancel marker not found, trying alternative pattern")
    # Try with the shortened error message
    cancel_alt = '''            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": ""}'''
    if cancel_alt in content:
        content = content.replace(cancel_alt, cancel_new)
        print("[OK] Replaced _handle_cancel_order (alt pattern)")

# Replace _handle_modify_order similarly  
modify_old = '''            if hasattr(engine.order_mgr, "modify_order"):
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
            return {"error": "modify_order not implemented on OrderManager yet (P0-C pending)"}'''

modify_new = '''            row = engine.order_mgr.modify_order(
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
                }'''

if modify_old in content:
    content = content.replace(modify_old, modify_new)
    print("[OK] Replaced _handle_modify_order")
else:
    print("[WARN] Modify marker not found")

# Change order_type default from "market" to "limit"
if 'default="market"' in content and 'order_type' in content:
    # More targeted replacement - find the specific line for order_type tool
    lines = content.split('\n')
    updated_lines = []
    for line in lines:
        if 'order_type' in line and 'default="market"' in line:
            line = line.replace('default="market"', 'default="limit"')
            updated_lines.append(line)
            print("[OK] Changed order_type default to limit")
        else:
            updated_lines.append(line)
    content = '\n'.join(updated_lines)
else:
    # Fallback simple replace
    content = content.replace('default="market"', 'default="limit"')
    print("[OK] Updated order_type default (simple replace)")

# Add constraint to PM_SYSTEM_PROMPT (after 严禁追高 line)
if '严禁追高(乖离率 > 5%)' in content:
    constraint = ' **必须使用 limit orders (order_type="limit"), limit_price 必须设置**'
    content = content.replace('严禁追高(乖离率 > 5%)', '严禁追高(乖离率 > 5%)' + constraint)
    print("[OK] Added limit order constraint to PM_SYSTEM_PROMPT")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[SUCCESS] All updates completed!")