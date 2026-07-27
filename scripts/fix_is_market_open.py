#!/usr/bin/env python3
# Fix the is_market_open_now function's import error

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix: replace "from datetime as dt" with proper usage (already imported datetime above)
# The function should use datetime.now() directly since datetime is already imported at top
# Find the incorrect line and fix it
fixed_content = content.replace('from datetime as dt', '')
# Also need to ensure we're using datetime properly - since 'from datetime import datetime' exists
# Let's find the function body and fix it
import re

# Pattern: def is_market_open_now... body with "datetime.now()" or similar
def_match = re.search(r'def is_market_open_now\(market: str, now: Optional\[datetime\] = None\) -> bolc:.*?\n(.*?)\n\s*def|\n\nclass', content, re.DOTALL | re.MULTILINE)

# Simpler: just replace any erroneous 'as dt' references in that function area
# Look for the function and fix its body
lines = content.split('\n')
in_function = False
new_lines = []
for line in lines:
    if 'def is_market_open_now' in line:
        in_function = True
        new_lines.append(line)
        continue
    if in_function and line.strip() and not line.startswith(' ' * 4):
        # Likely end of function
        in_function = False
        new_lines.append(line)
        continue
    if in_function:
        # Fix potential errors in this line
        if 'as dt' in line:
            line = line.replace('as dt', '')
        new_lines.append(line)
        continue
    new_lines.append(line)

content = '\n'.join(new_lines)

# Also check if there's a missing import for 'time' (used in MARKET_SESSIONS)
if 'time' not in content.split('\n')[15:20]:  # check near imports
    # Ensure time is imported (it already is from earlier imports)
    pass

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed is_market_open_now function")