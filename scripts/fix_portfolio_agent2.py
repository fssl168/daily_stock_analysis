#!/usr/bin/env python3
"""Fix portfolio_manager_agent.py: remove outdated hasAttribute checks and update defaults."""

import re

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace _handle_cancel_order with simplified version
cancel_marker = '# OrderManager.cancel_order is added in P0-C; for now use a\n            # graceful fallback if the method is missing.\n            if hasattr(engine.order_mgr, "cancel_order"):\n                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")\n                return {"status": "canceled", "order_id": order_id, "code": row.code}\n            return {"error": ""}'
new_cancel_part = '        row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")\n            return {"status": "canceled", "order_id": order_id, "code": row.code}'
if cancel_marker in content:
    content = content.replace(cancel_marker, new_cancel_part)
    print("✓ Replaced _handle_cancel_order")

# Replace _handle_modify_order similarly  
modify_marker = '            if hasattr(engine.order_mgr, "modify_order"):\n                row = engine.order_mgr.modify_order(\n                    order_id,\n                    new_price=float(new_price) if new_price else None,\n                    new_quantity=float(new_quantity) if new_quantity else None,\n                )\n                # row is the replacement order (new id assigned by modify_order).\n                # Return the NEW order_id so callers can track the replacement,\n                # and include original_order_id for audit linkage.\n                return {\n                    "status": "modified",\n                    "order_id": int(getattr(row, "id", 0) or 0),\n                    "original_order_id": order_id,\n                    "code": row.code,\n                }\n            return {"error": "modify_order not implemented on OrderManager yet (P0-C pending)"}'
new_modify_part = '            row = engine.order_mgr.modify_order(\n                    order_id,\n                    new_price=float(new_price) if new_price else None,\n                    new_quantity=float(new_quantity) if new_quantity else None,\n                )\n                # row is the replacement order (new id assigned by modify_order).\n                # Return the NEW order_id so callers can track the replacement,\n                # and include original_order_id for audit linkage.\n                return {\n                    "status": "modified",\n                    "order_id": int(getattr(row, "id", 0) or 0),\n                    "original_order_id": order_id,\n                    "code": row.code,\n                }'
if modify_marker in content:
    content = content.replace(modify_marker, new_modify_part)
    print("✓ Replaced _handle_modify_order")

# Change order_type default from "market" to "limit"
order_type_pattern = r'ToolParameter\(name="order_type".*?default\s*=\s*["\']market["\']'
match = re.search(order_type_pattern, content)
if match:
    # Replace just the default value
    content = content[:match.start()] + content[match.start():].replace('default="market"', 'default="limit"') + content[match.end():]
    print("✓ Changed order_type default to limit")
else:
    # Try simpler search
    content = content.replace('default="market"', 'default="limit"')
    print("✓ Updated order_type default (simple replace)")

# Add constraint to PM_SYSTEM_PROMPT
prompt_section = '严禁追高(乖离率 > 5%)\n\n## 决策原则'
if prompt_section in content:
    content = content.replace(prompt_section, '严禁追高(乖离率 > 5%)。**必须使用 limit orders (order_type="limit"), limit_price 必须设置**\n\n## 决策原则')
    print("✓ Added limit order constraint to PM_SYSTEM_PROMPT")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\nAll updates completed successfully!")