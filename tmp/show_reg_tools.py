import sys

with open('src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_line = None
for i, line in enumerate(lines):
    if line.strip().startswith('def register_paper_trading_tools'):
        start_line = i
        break

if start_line is None:
    print('Function not found')
    sys.exit(1)

print(f'Start at line {start_line+1}')
# Print next 200 lines
for i in range(start_line, min(start_line + 250, len(lines))):
    print(lines[i].rstrip())