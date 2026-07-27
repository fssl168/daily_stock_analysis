with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Lines 900-930 with visible indentation:")
for i in range(899, min(len(lines), 930)):
    line = lines[i]
    stripped = line.rstrip()
    # Show leading spaces as dots
    dots = ' ' * (len(line) - len(stripped)) + '.' if len(line) > len(stripped) else ''
    print(f"{i+1:3}: {stripped:70} {dots}")