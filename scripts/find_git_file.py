import os
import re

base = 'D:/leanpython/daily_stock_analysis/.git'
found = []

for root, dirs, files in os.walk(base):
    for f in files:
        if 'market_listener' in f.lower():
            found.append(os.path.join(root, f))

print(f"Found {len(found)} potential files:")
for f in found[:5]:
    print(f)

if found:
    # Try to read the one that looks like a blob or direct file
    target = found[0]
    try:
        with open(target, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            print("\nFirst 500 chars of first match:")
            print(content[:500])
    except:
        print("Cannot read as text")