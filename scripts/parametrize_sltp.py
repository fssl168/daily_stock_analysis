#!/usr/bin/env python3
# Parametrize P1-A: replace hard-coded 20% threshold with config parameter

import re

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the _check_dynamic_sltp method we added earlier
# Look for the line with hardcoded 20% threshold
method_match = re.search(r'def _check_dynamic_sltp\(self, market: str\)[\s\S]*?(?=\n\s*def |\Z)', content)
if not method_match:
    print("ERROR: Could not find _check_dynamic_sltp method")
    exit(1)

print("Found _check_dynamic_sltp method")

# Replace the hardcoded 20% (0.20) with a config value
# We need to get the threshold from self.config or default config
# The pattern: if latest > pos.avg_cost * 1.20:  -> threshold should be configurable

# Simple replacement first - use a variable that will be set in __init__
old_code = 'if latest > pos.avg_cost * 1.20:'
new_code = '''if latest > pos.avg_cost * getattr(self, "sltp_dynamic_threshold_pct", 0.20) / 100.0:'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("[OK] Replaced hardcoded threshold in method body")
else:
    # Try alternative formatting
    old_code_alt = 'if profit_pct > 0.20:'
    if old_code_alt in content:
        content = content.replace(old_code_alt, 'if profit_pct >= getattr(self, "sltp_dynamic_threshold_pct", 0.20) / 100.0:')
        print("[OK] Replaced alternative hardcoded threshold")
    else:
        print("[WARN] Hardcoded threshold pattern not found exactly, checking content...")
        # Just log what's there
        print(method_match.group(0)[:500])

# Also add threshold parameter to MarketListener.__init__ if not present
init_match = re.search(r'def __init__\(self.*?\n).*?self\.\w+\s*=', content, re.DOTALL | re.MULTILINE)
# Simpler: check if there's already sltp_dynamic_threshold attribute
if 'sltp_dynamic_threshold_pct' not in content:
    # Need to add this to __init__ and store it
    # Find the __init__ method
    init_start = content.find('def __init__(self,')
    if init_start >= 0:
        # Find the end of __init__ (before next method)
        init_end = content.find('\n    def ', init_start + 1)
        if init_end == -1:
            init_end = len(content)
        
        # Insert after existing self._attribute assignments but before super/other setup
        insert_pos = init_start
        # Find a good spot - after some self.xxx = assignment
        for i in range(init_start, min(init_start+200, init_end)):
            if content[i:i+6] == 'self.__' or (i > init_start and content[i:i+4] == 'self.' and '=' in content[i:i+10]):
                # Insert after this assignment
                insert_pos = i + content[i:].find('\n') + 1
                break
        
        # Insert config parameter with default
        insert_code = '\n        self.sltp_dynamic_threshold_pct = float(getattr(config, "paper_trading_sltp_dynamic_threshold_pct", 20.0))\n'
        content = content[:insert_pos] + insert_code + content[insert_pos:]
        print("[OK] Added sltp_dynamic_threshold_pct to MarketListener.__init__")
    else:
        print("[WARN] Could not find __init__ to add parameter")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("[P1-A PARAMETRIZATION] Done")