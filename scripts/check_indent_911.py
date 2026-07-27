with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Lines 905-920:")
for i in range(904, min(len(lines), 920)):
    print(f"{i+1}: {repr(lines[i])}")