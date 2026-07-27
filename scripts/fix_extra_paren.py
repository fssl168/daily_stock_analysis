#!/usr/bin/env python3
file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the double closing parenthesis on order_type parameter
# Line should be: ToolParameter(name="order_type", ... default="limit"),
# Not: ToolParameter(name="order_type", ... default="limit")),

import re

# Pattern to find the problematic line with two closing parens after default="limit"
pattern = r'ToolParameter\(name="order_type"[^)]*default="limit"\)\)'
replacement = 'ToolParameter(name="order_type", type="string", description="Order type.", enum=["market", "limit"], required=False, default="limit")'

new_content, count = re.subn(pattern, replacement, content)
if count > 0:
    content = new_content
    print(f"Fixed {count} occurrence(s) of double paren on order_type")
else:
    # Try a simpler fix - remove one ')' that appears right after default="limit"
    content = content.replace('default="limit"))', 'default="limit"),')
    print("Applied simple paren fix")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed syntax error in portfolio_manager_agent.py")