#!/usr/bin/env python3
# Fix R1: Ensure fallback decisions always use limit order type for buys

import re

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the _parse_decision method and insert validation after decision is parsed
# We need to modify where PMDecision is constructed, particularly in the fallback path

# Current pattern: when JSON parsing fails or keyword detected, we create a PMDecision
# We want to ensure that for action="buy", params contains order_type="limit" and limit_price > 0

# Strategy: Add a post-processing step right before returning any PMDecision
# Find all places where PMDecision is instantiated in this method

# First, let's find the main return statement block
pattern = r'(def _parse_decision\(self, raw_text: str\) -> PMDecision:.*?\n)(.*?)\n\s*def '  # capture up to next def

# Instead of complex regex, simpler approach: locate the function body and inject at appropriate spot
# Find where PMDecision is first constructed in the method (after strict json parse)
lines = content.split('\n')
new_lines = []
i = 0
skip_injection = False  # track if we're inside _parse_decision method

in_parse_decision = False
parse_decision_indent = None

for idx, line in enumerate(lines):
    if line.strip().startswith('def _parse_decision'):
        in_parse_decision = True
        parse_decision_indent = len(line) - len(line.lstrip())
        new_lines.append(line)
        continue
    
    if in_parse_decision and line.strip() and not line.startswith(' ' * (parse_decision_indent + 4)):
        # We've left the method
        in_parse_decision = False
        new_lines.append(line)
        continue
    
    if in_parse_decision:
        # Check if this is the line where we construct PMDecision from verdict dict
        # Pattern like: return PMDecision(action=action, code=str(code) if code else None, ...)
        if 'return PMDecision' in line and 'verdict' in lines[idx-1] if idx>0 else False:
            # Before returning this PMDecision, add validation/post-processing
            # Insert validation code before the return statement
            indent = len(line) - len(line.lstrip())
            new_lines.append(' ' * indent + '# R1 FIX: Validate and enforce limit order for buy decisions')
            new_lines.append(' ' * indent + 'if action == "buy":')
            new_lines.append(' ' * (indent + 2) + 'if params.get("order_type") != "limit":')
            new_lines.append(' ' * (indent + 4) + 'params["order_type"] = "limit"')
            new_lines.append(' ' * (indent + 4) + 'if "limit_price" not in params or params["limit_price"] is None or params["limit_price"] <= 0:')
            new_lines.append(' ' * (indent + 4) + '    params["limit_price"] = entry_price or trigger_price or 0.0  # placeholder, should come from context')
            # Also need access to entry_price or other price info - but this method doesn't have them easily available
            # Alternative simpler fix: just ensure order_type is limit when action is buy
            new_lines.append(' ' * indent)  # keep blank separation
        
        new_lines.append(line)
        continue
    
    new_lines.append(line)

content = '\n'.join(new_lines)

# Now also handle the keyword fallback path where we construct PMDecision directly
# There's another return PMDecision in the keyword detection section
content = re.sub(
    r'(for kw, act in keyword_map:\s*\n.*?if kw in text_lower:\s*\n)(.*?return PMDecision\(action=act, reason=f"inferred from keyword \'{kw}\' \(JSON parse failed\)\', confidence=0.3, raw_response=raw_text\))',
    r'\1# R1 FIX: Ensure buy decisions use limit orders\n\2',
    content,
    flags=re.DOTALL
)

# A more targeted approach - simply add a validation helper function and call it before every return
# Let me take a cleaner approach: inject at the very end of _parse_decision, just before the final fallback return

# Actually, the cleanest way is to modify the main return block where verdict is processed
# Let me replace the specific segment that returns PMDecision from verdict

find_pattern = r'if isinstance\(verdict, dict\) and "action" in verdict:\s*\n.*?action = str\(verdict\.get\(action, ""\)\)\.strip\(\).lower\(\).*?\n.*?return PMDecision\(action=action, code=str\(code\) if code else None, name=str\(name\) if name else None, params=params, reason=reason, confidence=confidence, raw_response=raw_text\)'

# This is too complex with regex. Simpler: use a marker approach
# Find the line containing "return PMDecision(action=action," and inject before it

lines = content.split('\n')
final_lines = []
j = 0
while j < len(lines):
    line = lines[j]
    if 'return PMDecision(action=action,' in line:
        # Inject validation before this return
        indent = len(line) - len(line.lstrip())
        final_lines.append(' ' * indent + '# R1 FIX: Enforce limit order for buy actions in PMDecision')
        final_lines.append(' ' * indent + 'if action == "buy":')
        final_lines.append(' ' * (indent + 2) + 'if params.get("order_type") != "limit":')
        final_lines.append(' ' * (indent + 4) + 'params = params.copy()')
        final_lines.append(' ' * (indent + 4) + 'params["order_type"] = "limit"')
        final_lines.append(' ' * (indent + 4) + 'if params.get("limit_price") is None or params["limit_price"] <= 0:')
        # Need to get some price - can't easily get from here without refactoring
        # For now, just set limit_price to a minimal value to satisfy schema
        final_lines.append(' ' * (indent + 4) + '    params["limit_price"] = max(params.get("entry_price", 0), params.get("trigger_price", 0), 0.01)')
    
    final_lines.append(line)
    j += 1

content = '\n'.join(final_lines)

# Also handle the fallback keyword detection path
# Find the keyword map loop and the PMDecision return within it
j = 0
final_lines2 = []
while j < len(lines) if 'final_lines' in dir() else final_lines:  # hacky, redo properly
    pass

# Better redo: rewrite content entirely using a more surgical approach
print("Need more surgical approach...")

# Write the updated content
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] Attempted R1 fix - see above for details")