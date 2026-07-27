with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _handle_modify_order definition
start = None
for i, line in enumerate(lines):
    if '_handle_modify_order' in line and line.strip().startswith('def _handle_modify_order'):
        start = i
        break

if start is not None:
    print(f"_handle_modify_order starts at line {start+1}")
    # Show next 30 lines with their actual indentation level (count leading spaces)
    for i in range(start, min(start+40, len(lines))):
        line = lines[i]
        leading = len(line) - len(line.lstrip())
        print(f"{i+1:3}: indent={leading:2} {line.rstrip()}")
else:
    print("Function not found")