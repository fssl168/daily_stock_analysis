#!/usr/bin/env python3
# Add is_market_open_now and other missing functions to market_listener.py

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Check if is_market_open_now already exists
if 'def is_market_open_now' in content:
    print("Function already present, skipping")
else:
    # Find the place to insert - after imports but before MarketListener class definition
    # Look for the line after the last import statement
    lines = content.split('\n')
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('class MarketListener'):
            insert_idx = i
            break
    
    if insert_idx is not None:
        # Insert function just before the class
        new_function = '''\n\n# Market session windows (simplified)\nMARKET_SESSIONS = {\n    "cn": [time(9, 30), time(11, 30), time(13, 0), time(15, 0)],\n}\n\n\ndef is_market_open_now(market: str, now: Optional[datetime] = None) -> bool:\n    \"\"\"Check if the given market is open at the current time.\"\"\"\n    from datetime as dt\n    if now is None:\n        now = dt.datetime.now()\n    if market not in MARKET_SESSIONS:\n        return False\n    current_time = now.time()\n    sessions = MARKET_SESSIONS[market]\n    for i in range(0, len(sessions), 2):\n        start = sessions[i]\n        end = sessions[i + 1]\n        if start <= current_time < end:\n            return True\n    return False\n'''
        content = content[:insert_idx] + new_function + content[insert_idx:]
        print("Added is_market_open_now function")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")