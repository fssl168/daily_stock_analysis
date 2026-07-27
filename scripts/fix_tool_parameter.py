#!/usr/bin/env python3
# Fix ToolParameter line for order_type in portfolio_manager_agent.py

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# The erroneous line is currently: ToolParameter(name="order_type"...default="limit"),
# We need to replace it with proper definition.
old = 'ToolParameter(name="order_type"...default="limit")'
new = 'ToolParameter(name="order_type", type="string", description="Order type.", enum=["market", "limit"], required=False, default="limit")'

if old in content:
    content = content.replace(old, new)
    print("Fixed ToolParameter line")
else:
    # Try with trailing comma included
    old2 = 'ToolParameter(name="order_type"...default="limit",'
    if old2 in content:
        content = content.replace(old2, new + ',')
        print("Fixed ToolParameter line with comma")
    else:
        print("Could not find exact pattern; doing broader substitution")
        # Replace any occurrence of "...default=\"limit\"" with proper parameters
        import re
        content = re.sub(r'ToolParameter\(name="order_type"\s*\.*?default="limit"', new, content)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("[FIX COMPLETE]")