#!/usr/bin/env python3
# Fix indentation error at line 967 in portfolio_manager_agent.py

file_path = 'D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Print lines around 967 to understand context
print("Lines 960-980:")
for i in range(959, min(len(lines), 980)):
    print(f"{i+1}: {repr(lines[i])}")

# The issue is likely an extra indent inserted earlier. Let's look for suspicious patterns.
# We'll simply re-indent the return block properly.

# Find the line with 'return {' at roughly line 967
for i in range(959, 980):
    if 'return {' in lines[i]:
        print(f"\nFound potential problem at line {i+1}")
        # Check if next lines have inconsistent indentation
        for j in range(i, min(i+10, len(lines))):
            print(f"{j+1}: {repr(lines[j])}")
        break