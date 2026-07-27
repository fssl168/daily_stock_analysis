#!/usr/bin/env python3
"""Fix portfolio_manager_agent.py: remove outdated hasAttribute checks and update defaults."""

import re

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace _handle_cancel_order (remove hasAttribute check)
cancel_old = r'''(\s*def _handle_cancel_order.*?\n\s*try:\n)(\s*#\s*OrderManager\.cancel_order.*?\n\s*if hasattr\(engine\.order_mgr, "cancel_order"\):\n\s*row = engine\.order_mgr\.cancel_order\(.*\n\s*)if.*?error.*?cancel_order not implemented.*?(\\s*except)'''
cancel_new = r'\1        row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")\n        return {"status": "canceled", "order_id": order_id, "code": row.code}\n\2'
content = re.sub(cancel_old, cancel_new, content, flags=re.DOTALL)

# 2. Replace _handle_modify_order (remove hasAttribute check)
modify_old = r'''(\s*def _handle_modify_order.*?\n\s*if order_id <= 0:\n\s*return \{"error": "order_id is required"}\n\s*try:\n)(\s*if hasattr\(engine\.order_mgr, "modify_order"\):\n\s*row = engine\.order_mgr\.modify_order\(.*\n\s*)if.*?error.*?modify_order not implemented.*?(\\s*except)'''
modify_new = r'\1        row = engine.order_mgr.modify_order(\n            order_id,\n            new_price=float(new_price) if new_price else None,\n            new_quantity=float(new_quantity) if new_quantity else None,\n        )\n        # row is the replacement order (new id assigned by modify_order).\n        # Return the NEW order_id so callers can track the replacement,\n        # and include original_order_id for audit linkage.\n        return {\n            "status": "modified",\n            "order_id": int(getattr(row, "id", 0) or 0),\n            "original_order_id": order_id,\n            "code": row.code,\n        }\n\2'
content = re.sub(modify_old, modify_new, content, flags=re.DOTALL)

# 3. Update order_type default from "market" to "limit" in ToolParameter definition
content = re.sub(
    r'ToolParameter\(name="order_type".*?default="market"',
    r'ToolParameter(name="order_type"...default="limit"',
    content
)

# 4. Add limit order constraint in PM_SYSTEM_PROMPT (after "严禁追高" line)
prompt_update = re.sub(
    r'严禁追高\(乖离率 > 5%\)\。\n\n## 决策原则',
    '严禁追高(乖离率 > 5%)。**必须使用 limit orders (order_type="limit"), limit_price 必须设置**。\n\n## 决策原则',
    content
)

content = prompt_update

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✓ portfolio_manager_agent.py updated successfully!")
print("- Removed hasAttribute check in _handle_cancel_order")
print("- Removed hasAttribute check in _handle_modify_order")
print("- Changed order_type default from 'market' to 'limit'")
print("- Added limit order constraint in PM_SYSTEM_PROMPT")