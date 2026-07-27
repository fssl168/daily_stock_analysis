with open('D:/leanpython/daily_stock_analysis/paper_trading/trading_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _persist_agent_verdict start
start = -1
for i, line in enumerate(lines):
    if '_persist_agent_verdict' in line and line.strip().startswith('def '):
        start = i
        break

if start >= 0:
    print(f"_persist_agent_verdict starts at line {start+1}")
    # Print until we see another def/class at top level (indent 0) or end of file
    for i in range(start, min(start+100, len(lines))):
        stripped = lines[i].strip()
        if stripped.startswith('def ') and i > start:
            print(f"... ends before line {i+1} (new method)")
            break
        if stripped.startswith('class ') and i > start:
            print(f"... ends before line {i+1} (new class)")
            break
        print(f"{i+1}: {lines[i].rstrip()}")
else:
    print("Method not found")