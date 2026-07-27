#!/usr/bin/env python3
file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and remove the problematic line at indent=1 (line 919 in 1-based)
# This is likely a stray line inserted during editing
clean_lines = []
stray_removed = False
for i, line in enumerate(lines):
    stripped = line.strip()
    # Check if this line has only whitespace plus exactly 1 leading space/char
    if (len(line) - len(line.lstrip())) == 1 and (stripped == '' or stripped == '('):
        if not stray_removed:
            print(f"Removing suspicious line at {i+1}: repr={repr(line)}")
            stray_removed = True
            continue  # skip this line
    clean_lines.append(line)

if stray_removed:
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(clean_lines)
    print("Removed stray indentation line")
else:
    print("No stray line found, checking nearby...")
    # Print lines around 919 to see what's there
    for i in range(max(0, 915), min(len(lines), 925)):
        print(f"{i+1}: {repr(lines[i])}")