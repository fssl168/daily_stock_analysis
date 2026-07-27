with open('D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py', 'r') as f:
    lines = f.readlines()

print("Lines 9-25:")
for i in range(max(8, len(lines)-20), min(len(lines), 25)):
    print(f"{i+1}: {repr(lines[i])}")