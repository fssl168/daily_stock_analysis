import sys

with open('src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find start line
lines = content.split('\n')
start_line = None
for i, line in enumerate(lines):
    if 'register_paper_trading_tools' in line and line.strip().startswith('def '):
        start_line = i
        break

if start_line is None:
    print('Function not found')
    sys.exit(1)

# Find end - next top-level def
end = None
for i in range(start_line + 1, len(lines)):
    stripped = lines[i].lstrip()
    if stripped.startswith('def '):
        end = i
        break

if end is None:
    end = len(lines)

print(f'Lines {start_line+1} to {end}')
for j in range(start_line, end):
    print(lines[j])
