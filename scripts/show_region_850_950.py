with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Region lines 850-950 (with repr to see whitespace):")
for i in range(849, min(len(lines), 950)):
    line = lines[i]
    stripped = line.rstrip()
    print(f"{i+1:3}: {repr(line)} -> {stripped[:80]}")