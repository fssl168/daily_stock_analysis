#!/usr/bin/env python3
# Simple parametrize: replace hardcoded 20% in _check_dynamic_sltp with config-based threshold

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_method = False
method_found = False

for i, line in enumerate(lines):
    if '_check_dynamic_sltp' in line and line.strip().startswith('def'):
        in_method = True
    if in_method and not method_found:
        # Look for the hard-coded 20% check
        if '20%' in line or '0.20' in line or '1.20' in line:
            print(f"Found potential threshold at line {i+1}: {line.rstrip()}")
            # Replace with attribute access
            if '1.20' in line:
                lines[i] = line.replace('1.20', 'getattr(self, "sltp_dynamic_threshold_pct", 20.0) / 100.0 + 1')
                method_found = True
                print("Replaced threshold value")
            elif '0.20' in line:
                lines[i] = line.replace('0.20', 'getattr(self, "sltp_dynamic_threshold_pct", 20.0) / 100.0')
                method_found = True
                print("Replaced threshold value")

# Also add the attribute to __init__ if not present
attr_added = False
for i, line in enumerate(lines):
    if 'def __init__(self,' in line and 'MarketListener' in lines[i-1]:
        # Find where to insert - after some self. assignments
        for j in range(i, min(i+50, len(lines))):
            if 'self.' in lines[j] and '=' in lines[j] and 'super' not in lines[j]:
                # Insert after this assignment
                lines.insert(j+1, '        self.sltp_dynamic_threshold_pct = getattr(config, "paper_trading_sltp_dynamic_threshold_pct", 20.0)\n')
                attr_added = True
                print("Added sltp_dynamic_threshold_params to __init__")
                break
        if attr_added:
            break

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Parametrization complete!")