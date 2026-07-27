with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Lines 910-925:")
for i in range(909, min(len(lines), 925)):
    print(f"{i+1}: {lines[i].rstrip()}")