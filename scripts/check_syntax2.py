with open('D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py', 'r') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")
print("\nFirst 25 lines:")
for i in range(min(25, len(lines))):
    print(f"{i+1}: {lines[i].rstrip()}")