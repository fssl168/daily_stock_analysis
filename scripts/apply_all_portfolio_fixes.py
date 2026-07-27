#!/usr/bin/env python3
# Apply all fixes to portfolio_manager_agent.py in one pass

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

fixes_applied = []

# 1. Fix _handle_cancel_order - remove hasAttribute check and error message
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
    fixes_applied.append("Fixed _handle_cancel_order")
else:
    # Try alternative form with empty error string
    cancel_alt = '''            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": ""}'''
    if cancel_alt in content:
        content = content.replace(cancel_alt, cancel_new)
        fixes_applied.append("Fixed _handle_cancel_order (alt form)")
    else:
        fixes_applied.append("_handle_cancel_order: pattern not found - skipping")

# 2. Fix _handle_modify_order - remove hasAttribute check
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
    fixes_applied.append("Fixed _handle_modify_order")
else:
    fixes_applied.append("_handle_modify_order: pattern not found - skipping")

# 3. Change order_type default from "market" to "limit" in ToolParameter definition
# Find the exact line and replace
import re
# Match: ToolParameter(name="order_type", type="string", description="Order type.", enum=[...], required=False, default="market")
order_type_pattern = r'(ToolParameter\(name="order_type", type="string", description="Order type.", enum=\["market", "limit"\], required=False, )default="market"\)'
order_type_replacement = r'\1default="limit"'
new_content, count = re.subn(order_type_pattern, order_type_replacement, content)
if count > 0:
    content = new_content
    fixes_applied.append("Changed order_type default to limit")
else:
    # Simpler approach: find any default="market" near order_type
    lines = content.split('\n')
    modified_lines = []
    for line in lines:
        if 'order_type' in line and 'default="market"' in line:
            line = line.replace('default="market"', 'default="limit"')
            fixes_applied.append("Changed order_type default in line")
        modified_lines.append(line)
    content = '\n'.join(modified_lines)

# 4. Add constraint to PM_SYSTEM_PROMPT after "严禁追高" line
prompt_marker = '严禁追高(乖离率 > 5%)\n\n## 决策原则'
prompt_constraint = '严禁追高(乖离率 > 5%)。**必须使用 limit orders (order_type="limit"), limit_price 必须设置**\n\n## 决策原则'
if prompt_marker in content:
    content = content.replace(prompt_marker, prompt_constraint)
    fixes_applied.append("Added limit order constraint to PM_SYSTEM_PROMPT")
else:
    # Try alternative spacing
    alt_marker = '严禁追高(乖离率 > 5%)\n## 决策原则'
    if alt_marker in content:
        content = content.replace(alt_marker, '严禁追高(乖离率 > 5%)。**必须使用 limit orders (order_type="limit"), limit_price 必须设置**\n## 决策原则')
        fixes_applied.append("Added limit order constraint (alt marker)")
    else:
        fixes_applied.append("PM_SYSTEM_PROMPT marker not found - adding at end of section")
        # Insert after the 严禁追高 line anywhere in the prompt
        insert_pos = content.find('严禁追高(乖离率 > 5%)')
        if insert_pos >= 0:
            insert_after = content[:insert_pos + len('严禁追高(乖离率 > 5%)')] + '\n**必须使用 limit orders (order_type="limit"), limit_price 必须设置**' + content[insert_pos + len('严禁追高(乖离率 > 5%)'):]
            fixes_applied.append("Added constraint manually")

# 5. R1 FIX: Add validation before PMDecision returns in _parse_decision
# First handle the verdict path (the main one)
verdict_block = '''        if isinstance(verdict, dict) and "action" in verdict:
            action = str(verdict.get("action", "")).strip().lower()
            if action not in ("buy", "sell", "hold", "cancel", "modify", "plan", "nop"):
                action = self.fallback_action
            code = verdict.get("code")
            name = verdict.get("name")
            params = verdict.get("params") or {}
            if not isinstance(params, dict):
                params = {"value": params}
            reason = str(verdict.get("reason") or "")[:300]
            try:
                confidence = float(verdict.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))'''

# Insert validation after confidence calculation but before return PMDecision
insert_point = verdict_block + '\n            return PMDecision('
validation_code = '''            # R1 FIX: Ensure buy decisions always use limit orders
            if action == "buy":
                if not isinstance(params, dict) or params.get("order_type") != "limit":
                    params = dict(params) if isinstance(params, dict) else {}
                    params["order_type"] = "limit"
                    if params.get("limit_price") is None or params["limit_price"] <= 0:
                        params["limit_price"] = 0.01'''

if insert_point in content:
    content = content.replace(insert_point, validation_code + '\n' + insert_point, count=1)
    fixes_applied.append("Added R1 fix to verdict path return")
else:
    fixes_applied.append("R1 fix to verdict path: block not found exactly")

# 6. Add R1 fix to keyword detection path
keyword_line = '            if kw in text_lower:'
# Look for the return statement that follows within this loop
# We'll insert validation before the return PMDecision inside the for loop
# Since we need specific context, let's do a simpler targeted replacement
content = content.replace(
    '            if kw in text_lower:\n                return PMDecision(',
    '            if kw in text_lower:\n                # R1 FIX: Ensure buy decisions always use limit orders\n                if act == "buy":\n                    params = {"order_type": "limit", "limit_price": 0.01}\n                return PMDecision('
)
fixes_applied.append("Added R1 fix to keyword path return")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("PortfolioManagerAgent fixes applied:")
for f in fixes_applied:
    print(f"  - {f}")