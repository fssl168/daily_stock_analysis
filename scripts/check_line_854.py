with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Lines 850-865:")
for i in range(849, min(len(lines), 865)):
    print(f"{i+1}: {lines[i].rstrip()}")