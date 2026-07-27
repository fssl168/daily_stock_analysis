with open('D:/leanpython/daily_stock_analysis/paper_trading/trading_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_method = False
found_flush = False
for i, line in enumerate(lines):
    if '_persist_agent_verdict' in line and 'def' in line:
        in_method = True
    if in_method and not found_flush:
        if 'session.flush()' in line:
            print(f"Found session.flush() at line {i+1}")
            # Show next few lines
            for j in range(i, min(i+15, len(lines))):
                print(f"{j+1}: {lines[j].rstrip()}")
            found_flush = True
        # Check if we exited the method
        if line.strip().startswith('def ') and i > 0:
            break

if not found_flush:
    print("Could not find session.flush() in _persist_agent_verdict")
    # Search more broadly
    for i, line in enumerate(lines):
        if 'session.flush()' in line:
            print(f"Found flush elsewhere at line {i+1}: {line.strip()}")